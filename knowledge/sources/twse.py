"""TWSE OpenAPI: the official, keyless answer to Goodinfo and 优分析's data pages.

Goodinfo (goodinfo.tw) bans crawlers in its terms and sits behind anti-bot
walls; 优分析 (uanalyze.com.tw) keeps its figures behind a paid membership.
Both draw the numbers from the exchange, and the exchange publishes them
itself: openapi.twse.com.tw is a keyless JSON service the Taiwan Stock
Exchange runs for third parties. Three whole-market tables cover what a
watch-only reading of a Taiwan name needs:

  /v1/exchangeReport/BWIBBU_ALL   P/E, dividend yield, P/B, every listed stock
  /v1/opendata/t187ap05_L         monthly revenue, this month vs last vs a year ago
  /v1/exchangeReport/STOCK_DAY_ALL the day's OHLCV, every listed stock

Each answers the LATEST snapshot only - there is no date parameter - so the
fact book's append-only, `known_at`-stamped store is what turns a nightly
read into history. Dates come in the ROC calendar (`1150905` = 2026-09-05)
and numbers as strings with thousands separators; both are normalised here
and nowhere else. The tables are market-wide, so the request count is three
per run whatever the number of names; the names come from
`[sources] read_only` (read and cited, never traded: moomoo MY does not
trade Taiwan, and the paper book never sees these ids).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from knowledge.facts import Observation
from knowledge.sources.base import Collector, Pull, SourceError, local_code

BASE = "https://openapi.twse.com.tw/v1"
VALUATION = f"{BASE}/exchangeReport/BWIBBU_ALL"
REVENUE = f"{BASE}/opendata/t187ap05_L"
QUOTES = f"{BASE}/exchangeReport/STOCK_DAY_ALL"


def roc_date(raw) -> date | None:
    """`1150905` or `115/09/05` -> 2026-09-05. None when it is not a ROC date."""
    s = str(raw or "").strip().replace("/", "")
    if not s.isdigit() or len(s) not in (6, 7):
        return None
    year, month, day = int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def roc_month_end(raw) -> date | None:
    """`11508` (ROC year 115, month 08) -> 2026-08-31, the period end."""
    s = str(raw or "").strip()
    if not s.isdigit() or len(s) not in (4, 5):
        return None
    year, month = int(s[:-2]) + 1911, int(s[-2:])
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def number(raw) -> Decimal | None:
    """TWSE's strings: `1,234.5`, `－`, ``, `--` -> Decimal or None."""
    s = str(raw if raw is not None else "").strip().replace(",", "").replace("　", "")
    if not s or s in ("-", "--", "－", "N/A"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _field(row: dict, *needles: str) -> object:
    """The first value whose key contains every needle - TWSE's keys are Chinese
    phrases that shift a character between releases; the needles do not."""
    for key, value in row.items():
        text = str(key)
        if all(n in text for n in needles):
            return value
    return None


class TwseOpenApiCollector(Collector):
    name = "twse_openapi"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        wanted = {local_code(i): i for i in instruments if i.upper().startswith("XTAI:")}
        if not wanted:
            return pull  # no Taiwan name in scope: no request, nothing to say
        today = self.today()
        failures: list[str] = []
        for step in (self._valuation, self._revenue, self._quotes):
            try:
                step(pull, wanted, today)
            except SourceError as e:
                failures.append(f"{step.__name__.lstrip('_')}: {e}")
        if failures and len(failures) == 3:
            raise SourceError("every TWSE table failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    def _rows(self, url: str) -> list[dict]:
        payload = self.get_json(url)
        if not isinstance(payload, list):
            raise SourceError(f"twse_openapi: expected a list from {url.rsplit('/', 1)[-1]}")
        return [r for r in payload if isinstance(r, dict)]

    def _valuation(self, pull: Pull, wanted: dict[str, str], today: date) -> None:
        for r in self._rows(VALUATION):
            iid = wanted.get(str(r.get("Code") or _field(r, "證券代號") or "").strip())
            if iid is None:
                continue
            asof = roc_date(r.get("Date") or _field(r, "日期")) or today
            for key, concept in (
                ("PEratio", "pe_ttm"),
                ("PBratio", "pb"),
                ("DividendYield", "dividend_yield"),
            ):
                value = number(r.get(key))
                if value is None:
                    continue
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=max(today, asof),
                        value=value,
                        period_end=asof,
                        unit="pct" if concept == "dividend_yield" else "x",
                    )
                )

    def _revenue(self, pull: Pull, wanted: dict[str, str], today: date) -> None:
        for r in self._rows(REVENUE):
            iid = wanted.get(str(_field(r, "公司代號") or r.get("Code") or "").strip())
            if iid is None:
                continue
            period = roc_month_end(_field(r, "資料年月"))
            if period is None:
                continue
            filed = roc_date(_field(r, "出表日期")) or today
            for needles, concept, unit, scale in (
                (("當月營收",), "revenue_month", "TWD", Decimal(1000)),
                (("去年同月", "增減"), "revenue_yoy", "pct", Decimal(1)),
                (("上月", "增減"), "revenue_mom", "pct", Decimal(1)),
            ):
                raw = _field(r, *needles)
                # "累計" rows share the needle; the monthly figure is the one
                # whose key does not say cumulative.
                if concept == "revenue_month":
                    for key, value in r.items():
                        if "當月營收" in str(key) and "累計" not in str(key):
                            raw = value
                            break
                value = number(raw)
                if value is None:
                    continue
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=max(today, filed),
                        value=value * scale,
                        period_end=period,
                        unit=unit,
                        currency="TWD" if unit == "TWD" else "",
                        payload={"filed": filed.isoformat()},
                    )
                )

    def _quotes(self, pull: Pull, wanted: dict[str, str], today: date) -> None:
        for r in self._rows(QUOTES):
            iid = wanted.get(str(r.get("Code") or "").strip())
            if iid is None:
                continue
            day = roc_date(r.get("Date")) or today
            for key, concept, unit in (
                ("ClosingPrice", "close", "TWD"),
                ("TradeVolume", "volume", "shares"),
            ):
                value = number(r.get(key))
                if value is None:
                    continue
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=max(today, day),
                        value=value,
                        period_end=day,
                        unit=unit,
                        currency="TWD" if unit == "TWD" else "",
                    )
                )
