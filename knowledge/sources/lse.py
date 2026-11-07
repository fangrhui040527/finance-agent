"""London Strategic Edge (LSE Data 3.0): four collectors on one free key.

Added 2026-11-03 at the owner's request, after a keyed catalogue probe from a
runner that day. What the free key opens, as the catalogue answered:

  * Malaysia's macro series (about 90, from the inflation rate and the policy
    rate to industrial production, exports, PMI and M3) and its government
    bond yields (3Y, 5Y, 10Y, daily);
  * the economic calendar, Malaysia's 24 release kinds among 140,000 rows
    worldwide - the BNM rate decision, GDP, CPI, trade - with actual, previous,
    consensus and forecast;
  * the US options tape, every contract of NVDA, AAPL and MSFT with implied
    volatility and Greeks computed twice a day;
  * US stock candles, a second daily close beside Yahoo's.

It does NOT carry Bursa stocks or the FBM KLCI (US, UK and Canadian stocks
only), so it adds context for the Bursa names, not prices for them.

THE LICENCE, which is why every collector here checks one thing first. LSE's
terms (the `lse-data` client's README, and londonstrategicedge.com/terms):
data fetched with an LSE key may be used for one's own research, trading and
model training, and "may not be redistributed, resold, or otherwise made
available to third parties". This repository commits what it collects and
writes pages that quote it. So the collectors refuse to read anything unless
`LSE_REPO_PRIVATE` is "true", which collect.yml sets from the repository's own
visibility on every run. A public repository is a skip with that reason, never
a failure and never a fetch; making the repository public again would also
publish whatever LSE data its history already holds.

The API (https://download.londonstrategicedge.com/v3, documented in the
client's docs/api.md): every call is a GET with the key in the `X-API-Key`
header, never the URL. 60 calls a minute and 12.5 GiB of downloads a week on
the free plan, one download at a time. A file export answers the bytes at once
(200) or a prepared job (202) that is polled and then fetched.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from knowledge.facts import EventRecord, Observation, SeriesPoint, as_decimal
from knowledge.sources.base import (
    Collector,
    KeyRejected,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_date,
    parse_datetime,
)

BASE = "https://download.londonstrategicedge.com"
PRIVATE_ENV = "LSE_REPO_PRIVATE"
MACRO_PREFIX = "MACRO:"

NOT_PRIVATE = (
    "not collected: LSE's terms forbid making its data available to third parties, and this "
    f"repository is not known to be private ({PRIVATE_ENV} is not 'true'; collect.yml sets it "
    "from the repository's visibility)"
)


class LseCollector(Collector):
    """The shared part: the licence guard, the key header, files and jobs."""

    key_env = "LSE_API_KEY"
    #: 60 calls a minute on the free plan; every call counts, refused or not.
    SECONDS_BETWEEN_REQUESTS = 1.05
    #: How long a prepared export may take to build before the pull gives up.
    JOB_POLLS = 30
    JOB_POLL_SECONDS = 1.0

    def _guard(self) -> str:
        """The key, once the repository is known to be private."""
        if os.environ.get(PRIVATE_ENV, "").strip().lower() != "true":
            raise PlanExcluded(f"{self.name}: {NOT_PRIVATE}")
        return self.key

    def _headers(self, key: str) -> dict[str, str]:
        return {"X-API-Key": key, "Accept": "application/json"}

    def _json(self, key: str, path: str, **params):
        return self.get_json(BASE + path, params, self._headers(key))

    def _csv(self, key: str, path: str, **params) -> list[dict[str, str]]:
        """An export's rows. A 202 is a prepared job: poll it, then fetch it."""
        body = self.get_text(BASE + path, {**params, "format": "csv"}, self._headers(key))
        head = body.lstrip()[:1]
        if head == "{":
            job = json.loads(body)
            if not isinstance(job, dict) or not job.get("job_id"):
                raise SourceError(
                    f"{self.name}: an export answered JSON without a job: {body[:160]!r}"
                )
            body = self._job_file(key, job)
        return list(csv.DictReader(io.StringIO(body)))

    def _job_file(self, key: str, job: dict) -> str:
        status_url = str(job.get("status_url") or f"/v3/jobs/{job['job_id']}")
        for _ in range(self.JOB_POLLS):
            if job.get("status") == "ready":
                url = str(job.get("download_url") or f"/v3/jobs/{job['job_id']}/file")
                if not url.startswith("/"):
                    raise SourceError(
                        f"{self.name}: a job's file is off the server's origin: {url!r}"
                    )
                return self.get_text(BASE + url, None, self._headers(key))
            if job.get("status") in ("failed", "expired"):
                raise SourceError(
                    f"{self.name}: export job {job.get('status')}: {job.get('error')}"
                )
            (self._sleep or time.sleep)(self.JOB_POLL_SECONDS)
            job = self.get_json(BASE + status_url, None, self._headers(key))
        raise SourceError(f"{self.name}: export job still building after {self.JOB_POLLS} polls")

    @staticmethod
    def _no_rows(error: SourceError) -> bool:
        """LSE answers a selection with no stored rows with 404 data-unavailable."""
        return "HTTP 404" in str(error)


# -- 1. Malaysia's macro series and yields ----------------------------------------

#: (kind, code, title). Codes as the catalogue spelt them on 2026-11-03; a code
#: the catalogue drops is one series' note, not the pull's failure.
MY_SERIES: tuple[tuple[str, str, str], ...] = (
    ("observation", "MY.inflation_rate", "Malaysia inflation rate, % y/y (LSE)"),
    ("observation", "MY.inflation_rate_mom", "Malaysia inflation rate, % m/m (LSE)"),
    ("observation", "MY.interest_rate", "Malaysia policy rate (OPR), % (LSE)"),
    ("observation", "MY.unemployment_rate", "Malaysia unemployment rate, % (LSE)"),
    ("observation", "MY.industrial_production", "Malaysia industrial production, % y/y (LSE)"),
    ("observation", "MY.manufacturing_pmi", "Malaysia manufacturing PMI (LSE)"),
    ("observation", "MY.exports_yoy", "Malaysia exports, % y/y (LSE)"),
    ("observation", "MY.imports_yoy", "Malaysia imports, % y/y (LSE)"),
    ("observation", "MY.retail_sales_yoy", "Malaysia retail sales, % y/y (LSE)"),
    ("observation", "MY.producer_prices_change", "Malaysia producer prices, % y/y (LSE)"),
    ("observation", "MY.money_supply_m3", "Malaysia money supply M3 (LSE)"),
    ("observation", "MY.loans_to_private_sector", "Malaysia loans to the private sector (LSE)"),
    ("observation", "MY.leading_economic_index", "Malaysia leading economic index (LSE)"),
    ("observation", "MY.gdp_annual_growth_rate", "Malaysia GDP, % y/y (LSE)"),
    ("observation", "MY.foreign_exchange_reserves", "Malaysia FX reserves (LSE)"),
    ("yield", "MY3Y", "Malaysia 3Y government bond yield, % (LSE)"),
    ("yield", "MY5Y", "Malaysia 5Y government bond yield, % (LSE)"),
    ("yield", "MY10Y", "Malaysia 10Y government bond yield, % (LSE)"),
)
#: Read back this far every run: the store keeps a vintage only when a value
#: changes, so a revised month is a new row and an unchanged one costs nothing.
MACRO_LOOKBACK_DAYS = 400


class LseMalaysiaMacro(LseCollector):
    name = "lse_macro_my"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self._guard()
        pull = Pull()
        today = self.today()
        start = (today - timedelta(days=MACRO_LOOKBACK_DAYS)).isoformat()
        failures: list[str] = []
        for kind, code, title in MY_SERIES:
            try:
                reply = self._json(key, "/v3/macro/series", kind=kind, code=code, **{"from": start})
            except (PlanExcluded, KeyRejected):
                raise
            except SourceError as e:
                failures.append(f"{code}: {e}")
                continue
            rows = reply.get("rows") if isinstance(reply, dict) else None
            if not isinstance(rows, list):
                failures.append(f"{code}: no rows list in {str(reply)[:120]!r}")
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                day = parse_date(r.get("day"))
                value = as_decimal(r.get("value", r.get("close")))
                if day is None or value is None:
                    continue
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        f"LSE:{code}",
                        day,
                        value,
                        known_at=today,
                        payload={"title": title, "kind": kind},
                    )
                )
        if failures and len(failures) == len(MY_SERIES):
            raise SourceError("every series failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull


# -- 2. The economic calendar ------------------------------------------------------

#: The regions the book trades in. Malaysia's calendar is the one no other
#: collector here reads (jin10_calendar has been dead since 2026-09-05).
CALENDAR_REGIONS = ("MY", "US")
CALENDAR_BACK_DAYS = 3
CALENDAR_AHEAD_DAYS = 45


class LseCalendar(LseCollector):
    name = "lse_calendar"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self._guard()
        pull = Pull()
        today = self.today()
        window = {
            "from": (today - timedelta(days=CALENDAR_BACK_DAYS)).isoformat(),
            "to": (today + timedelta(days=CALENDAR_AHEAD_DAYS)).isoformat(),
        }
        failures: list[str] = []
        for region in CALENDAR_REGIONS:
            try:
                rows = self._csv(
                    key,
                    "/v3/reference/export",
                    dataset="economic_calendar",
                    country=region,
                    **window,
                )
            except (PlanExcluded, KeyRejected):
                raise
            except SourceError as e:
                if self._no_rows(e):
                    pull.notes.append(f"{region}: no calendar rows in the window")
                    continue
                failures.append(f"{region}: {e}")
                continue
            for r in rows:
                event = self._event(r, region)
                if event is not None:
                    pull.events.append(event)
        if failures and len(failures) == len(CALENDAR_REGIONS):
            raise SourceError("every region failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    def _event(self, r: dict[str, str], region: str) -> EventRecord | None:
        row_id = (r.get("id") or "").strip()
        name = (r.get("event") or "").strip()
        when = parse_datetime(r.get("datetime")) or parse_datetime(r.get("date"))
        if not row_id or not name or when is None:
            return None
        country = (r.get("region_code") or region).strip().upper() or region
        actual = _cell(r.get("actual"))
        consensus = _cell(r.get("consensus"))
        payload = {
            "country": country,
            "indicator": name,
            "period": _cell(r.get("period_hint")),
            "previous": _cell(r.get("previous")),
            "consensus": consensus,
            "forecast": _cell(r.get("forecast")),
        }
        if actual is not None:
            return EventRecord(
                source=self.name,
                event_id=f"{row_id}:print",
                instrument_id=f"{MACRO_PREFIX}{country}",
                kind="macro_print",
                announced_at=when,
                title=f"{country} {name}: {actual}"
                + (f" vs {consensus} expected" if consensus is not None else ""),
                payload={**payload, "actual": actual},
            )
        return EventRecord(
            source=self.name,
            event_id=f"{row_id}:scheduled",
            instrument_id=f"{MACRO_PREFIX}{country}",
            kind="macro_release",
            announced_at=when,
            title=f"{country} {name}"
            + (f" (consensus {consensus})" if consensus is not None else ""),
            payload=payload,
        )


def _cell(raw) -> str | None:
    s = str(raw or "").strip()
    return None if s in ("", "\\N", "null", "None", "nan") else s


# -- 3. US options: one daily line per name ---------------------------------------

#: The expiries read, in days to expiry, and the one the at-the-money line is
#: taken from: the expiry nearest 30 days, as a VIX-like horizon.
OPTIONS_MIN_DTE = 20
OPTIONS_MAX_DTE = 45
OPTIONS_TARGET_DTE = 30
OPTIONS_PAGE = 1000
OPTIONS_MAX_PAGES = 3


class LseOptions(LseCollector):
    name = "lse_options"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self._guard()
        pull = Pull()
        if not instruments:
            return pull
        pull.asked = list(instruments)
        today = self.today()
        errors: list[SourceError] = []
        for iid in instruments:
            symbol = local_code(iid).upper()
            try:
                rows, head = self._chain(key, symbol)
            except (PlanExcluded, KeyRejected):
                raise
            except SourceError as e:
                errors.append(e)
                pull.failed.append((iid, str(e)))
                pull.notes.append(f"{iid}: {e}")
                continue
            summary = summarise_chain(rows, head.get("underlying_price"))
            if summary is None:
                pull.notes.append(f"{iid}: no contract with an implied volatility in the window")
                continue
            as_of = parse_datetime(head.get("greeks_as_of"))
            period = as_of.date() if as_of else today
            meta = {
                "contracts": len(rows),
                "greeks_as_of": head.get("greeks_as_of"),
                "underlying_price": head.get("underlying_price"),
            }
            if summary.get("atm_iv") is not None:
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        "lse_atm_iv_30d",
                        known_at=max(today, period),
                        value=summary["atm_iv"],
                        period_end=period,
                        unit="iv (annualised)",
                        payload={**meta, **summary["atm"]},
                    )
                )
            if summary.get("put_call") is not None:
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        "lse_put_call_volume_ratio",
                        known_at=max(today, period),
                        value=summary["put_call"],
                        period_end=period,
                        unit="ratio",
                        payload={
                            **meta,
                            "put_volume": summary["put_volume"],
                            "call_volume": summary["call_volume"],
                        },
                    )
                )
        if errors and len(errors) == len(instruments):
            raise errors[0]
        pull.requests = self.requests
        return pull

    def _chain(self, key: str, symbol: str) -> tuple[list[dict], dict]:
        rows: list[dict] = []
        head: dict = {}
        for page in range(OPTIONS_MAX_PAGES):
            reply = self._json(
                key,
                "/v3/options/chain",
                underlying=symbol,
                min_dte=OPTIONS_MIN_DTE,
                max_dte=OPTIONS_MAX_DTE,
                limit=OPTIONS_PAGE,
                offset=page * OPTIONS_PAGE,
            )
            if not isinstance(reply, dict) or not isinstance(reply.get("rows"), list):
                raise SourceError(f"lse_options: no rows list for {symbol}: {str(reply)[:120]!r}")
            head = head or reply
            rows.extend(r for r in reply["rows"] if isinstance(r, dict))
            if len(reply["rows"]) < OPTIONS_PAGE:
                break
        return rows, head


def summarise_chain(rows: list[dict], underlying_price) -> dict | None:
    """The at-the-money IV at the expiry nearest 30 days, and put/call volume.

    At the money: the strike nearest the underlying's last price, the mean of
    its call's and put's implied volatility where both are known, else the one
    that is. None when no contract in the window carries an IV.
    """
    spot = as_decimal(underlying_price)
    by_expiry: dict[str, list[dict]] = defaultdict(list)
    put_volume = call_volume = Decimal(0)
    for r in rows:
        kind = str(r.get("contract_type") or "").lower()
        volume = as_decimal(r.get("volume_today")) or Decimal(0)
        if kind == "put":
            put_volume += volume
        elif kind == "call":
            call_volume += volume
        if r.get("expiry") and as_decimal(r.get("iv")) is not None:
            by_expiry[str(r["expiry"])].append(r)
    out: dict = {
        "put_volume": int(put_volume),
        "call_volume": int(call_volume),
        "put_call": (put_volume / call_volume).quantize(Decimal("0.0001")) if call_volume else None,
        "atm_iv": None,
        "atm": {},
    }
    if spot is not None and by_expiry:

        def dte_of(expiry: str) -> int:
            first = by_expiry[expiry][0]
            return int(as_decimal(first.get("dte")) or 0)

        expiry = min(by_expiry, key=lambda e: abs(dte_of(e) - OPTIONS_TARGET_DTE))
        contracts = by_expiry[expiry]
        strikes = {k for k in (as_decimal(c.get("strike")) for c in contracts) if k is not None}
        strike = min(strikes, key=lambda k: abs(k - spot)) if strikes else None
        ivs = [as_decimal(c["iv"]) for c in contracts if as_decimal(c.get("strike")) == strike]
        ivs = [v for v in ivs if v is not None] if strike is not None else []
        if ivs:
            out["atm_iv"] = (sum(ivs, Decimal(0)) / len(ivs)).quantize(Decimal("0.0001"))
            out["atm"] = {
                "expiry": expiry,
                "dte": dte_of(expiry),
                "strike": str(strike),
                "legs": len(ivs),
            }
    if out["atm_iv"] is None and out["put_call"] is None:
        return None
    return out


# -- 4. US daily closes, a second source ------------------------------------------

PRICES_LOOKBACK_DAYS = 10


class LsePrices(LseCollector):
    name = "lse_prices"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self._guard()
        pull = Pull()
        if not instruments:
            return pull
        pull.asked = list(instruments)
        today = self.today()
        start = (today - timedelta(days=PRICES_LOOKBACK_DAYS)).isoformat()
        errors: list[SourceError] = []
        for iid in instruments:
            symbol = local_code(iid).upper()
            try:
                rows = self._csv(
                    key,
                    "/v3/export",
                    market="us_stocks",
                    symbol=symbol,
                    timeframe="1d",
                    adjusted="1",
                    data="candles",
                    **{"from": start, "to": today.isoformat()},
                )
            except (PlanExcluded, KeyRejected):
                raise
            except SourceError as e:
                if self._no_rows(e):
                    pull.notes.append(f"{iid}: no daily candles since {start}")
                    continue
                errors.append(e)
                pull.failed.append((iid, str(e)))
                pull.notes.append(f"{iid}: {e}")
                continue
            for r in rows:
                when = parse_datetime(r.get("time"))
                close = as_decimal(r.get("close"))
                if when is None or close is None:
                    continue
                day = when.astimezone(UTC).date()
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        "lse_close",
                        known_at=max(today, day),
                        value=close,
                        period_end=day,
                        unit="USD",
                        payload={k: r.get(k) for k in ("open", "high", "low", "volume", "trades")},
                    )
                )
        if errors and len(errors) == len(instruments):
            raise errors[0]
        pull.requests = self.requests
        return pull


__all__ = [
    "LseCalendar",
    "LseMalaysiaMacro",
    "LseOptions",
    "LsePrices",
    "summarise_chain",
]
