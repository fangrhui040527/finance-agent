"""Finnhub, for the US names: news with bodies, insiders, calendar, surprises, metrics.

The research register (docs/world-sources.html) and the Global Stock Analysis
APIs review both put Finnhub first for a developer-run platform: a generous
free tier (60 requests a minute), company news that carries a summary rather
than a headline, and the alternative-data pieces an analyst actually reads
before a result - who is buying inside the company, what the street expects,
and when the print lands.

Free-tier facts this adapter is shaped around, measured against the docs:

  * `company-news` is US-only on the free plan. A Bursa symbol returns [] or
    403; this collector is registered for XNAS/XNYS only, so it is never
    asked.
  * Six endpoints per name, three names: eighteen calls, well inside a
    minute's quota. Anything premium answers 403 and is recorded as a
    `PlanExcluded` note, not a failed pull.
  * Surprise and recommendation rows carry the period they DESCRIBE, not the
    day they were published. `known_at` is therefore the day we first saw the
    row - conservative, never earlier than true - which is what the
    point-in-time guard needs. That label can fall AFTER the day we saw it:
    Finnhub files NVIDIA's late-August print under the calendar quarter end,
    2026-09-30. Such a row is stored forward-labelled, never with known_at
    pushed out to the label - which is what this adapter did until
    2026-09-18, and it hid a public EPS print for 25 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from knowledge.facts import EventRecord, Observation, as_decimal
from knowledge.news.features import Article
from knowledge.sources.base import (
    Collector,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_date,
)

BASE = "https://finnhub.io/api/v1"

#: Finnhub metric key -> the concept name the rest of the system reads.
METRICS = {
    "peTTM": "pe_ttm",
    "pbAnnual": "pb",
    "epsTTM": "eps_ttm",
    "roeTTM": "roe_ttm",
    "netProfitMarginTTM": "net_margin_ttm",
    "revenueGrowthTTMYoy": "revenue_growth_ttm_yoy",
    "52WeekHigh": "high_52w",
    "52WeekLow": "low_52w",
    "beta": "beta",
    "dividendYieldIndicatedAnnual": "dividend_yield",
    "currentRatioQuarterly": "current_ratio",
    "totalDebt/totalEquityQuarterly": "debt_to_equity",
    "marketCapitalization": "market_cap_musd",
}

#: SEC Form 4 transaction codes that mean something to A8.
INSIDER_KINDS = {
    "P": "insider_buy",
    "S": "insider_sell",
    "A": "insider_award",
    "M": "insider_exercise",
    "F": "insider_tax_withholding",
    "G": "insider_gift",
}


class FinnhubCollector(Collector):
    name = "finnhub"
    key_env = "FINNHUB_API_KEY"
    #: Finnhub's 429 is a per-minute bucket; a second is the right first wait.
    RETRY_BASE_SECONDS = 1.0
    INSIDER_LOOKBACK = timedelta(days=45)
    CALENDAR_AHEAD = timedelta(days=120)

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        token = self.key
        pull = Pull()
        failures: list[str] = []
        for iid in instruments:
            try:
                pull.extend(self._one(iid, since, token))
            except PlanExcluded as e:
                pull.notes.append(str(e))
            except SourceError as e:
                failures.append(f"{iid}: {e}")
        if instruments and len(failures) == len(instruments):
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    # -- one name ----------------------------------------------------------------

    def _one(self, iid: str, since: datetime, token: str) -> Pull:
        symbol = local_code(iid).upper()
        today = self.today()
        pull = Pull()
        for step in (
            self._news,
            self._insiders,
            self._calendar,
            self._surprises,
            self._recommendations,
            self._metrics,
        ):
            try:
                step(pull, iid, symbol, since, token)
            except PlanExcluded as e:
                pull.notes.append(str(e))
        pull.notes = [n for n in pull.notes]
        _ = today
        return pull

    def _news(self, pull: Pull, iid: str, symbol: str, since: datetime, token: str) -> None:
        rows = self.get_json(
            f"{BASE}/company-news",
            {
                "symbol": symbol,
                "from": since.date().isoformat(),
                "to": self.today().isoformat(),
                "token": token,
            },
        )
        if not isinstance(rows, list):
            raise SourceError(f"finnhub company-news for {symbol}: expected a list")
        for r in rows:
            if not isinstance(r, dict) or not r.get("headline"):
                continue
            stamp = r.get("datetime")
            if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
                continue  # an undated story is not a story published now
            try:
                published = datetime.fromtimestamp(int(stamp), tz=UTC)
            except (ValueError, OSError, OverflowError):
                continue
            pull.articles.append(
                Article(
                    doc_id=f"finnhub:{r.get('id') or r.get('url')}",
                    title=str(r["headline"]),
                    body=str(r.get("summary") or ""),
                    source_domain=str(r.get("source") or "finnhub"),
                    published_at=published,
                    language="en",
                    instruments=[iid],
                    themes=[str(r["category"])] if r.get("category") else [],
                )
            )

    def _insiders(self, pull: Pull, iid: str, symbol: str, since: datetime, token: str) -> None:
        start = min(since.date(), self.today() - self.INSIDER_LOOKBACK)
        payload = self.get_json(
            f"{BASE}/stock/insider-transactions",
            {
                "symbol": symbol,
                "from": start.isoformat(),
                "to": self.today().isoformat(),
                "token": token,
            },
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise SourceError(f"finnhub insider-transactions for {symbol}: no data list")
        for t in rows:
            if not isinstance(t, dict):
                continue
            kind = INSIDER_KINDS.get(str(t.get("transactionCode") or "").upper())
            filed = parse_date(t.get("filingDate"))
            traded = parse_date(t.get("transactionDate"))
            if kind is None or filed is None:
                continue
            change = as_decimal(t.get("change")) or Decimal(0)
            price = as_decimal(t.get("transactionPrice"))
            name = str(t.get("name") or "an insider")
            verb = {
                "insider_buy": "bought",
                "insider_sell": "sold",
                "insider_award": "was awarded",
                "insider_exercise": "exercised",
                "insider_tax_withholding": "surrendered for tax",
                "insider_gift": "gifted",
            }[kind]
            title = f"{name} {verb} {abs(change):,.0f} shares" + (
                f" at {price}" if price is not None else ""
            )
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"{symbol}:{filed}:{name}:{t.get('transactionCode')}:{change}:{traded}",
                    instrument_id=iid,
                    kind=kind,
                    announced_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC),
                    effective_at=(
                        datetime(traded.year, traded.month, traded.day, tzinfo=UTC)
                        if traded
                        else None
                    ),
                    title=title,
                    payload={
                        "name": name,
                        "change": str(change),
                        "price": str(price) if price is not None else None,
                        "shares_after": t.get("share"),
                        "code": t.get("transactionCode"),
                    },
                )
            )

    def _calendar(self, pull: Pull, iid: str, symbol: str, since: datetime, token: str) -> None:
        today = self.today()
        payload = self.get_json(
            f"{BASE}/calendar/earnings",
            {
                "from": (today - timedelta(days=7)).isoformat(),
                "to": (today + self.CALENDAR_AHEAD).isoformat(),
                "symbol": symbol,
                "token": token,
            },
        )
        rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise SourceError(f"finnhub calendar/earnings for {symbol}: no earningsCalendar list")
        now = self._clock()
        for e in rows:
            if not isinstance(e, dict):
                continue
            when = parse_date(e.get("date"))
            if when is None:
                continue
            hour = str(e.get("hour") or "")
            label = {
                "bmo": "before the open",
                "amc": "after the close",
                "dmh": "during the session",
            }
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"{symbol}:earnings:{when}",
                    instrument_id=iid,
                    kind="earnings_result",
                    announced_at=now,
                    effective_at=datetime(when.year, when.month, when.day, tzinfo=UTC),
                    title=(
                        f"Q{e.get('quarter')} {e.get('year')} results {label.get(hour, hour)}".strip()
                    ),
                    payload={
                        "eps_estimate": e.get("epsEstimate"),
                        "revenue_estimate": e.get("revenueEstimate"),
                        "eps_actual": e.get("epsActual"),
                        "revenue_actual": e.get("revenueActual"),
                        "hour": hour,
                    },
                )
            )

    def _surprises(self, pull: Pull, iid: str, symbol: str, since: datetime, token: str) -> None:
        rows = self.get_json(f"{BASE}/stock/earnings", {"symbol": symbol, "token": token})
        if not isinstance(rows, list):
            raise SourceError(f"finnhub stock/earnings for {symbol}: expected a list")
        # This endpoint sends actual, estimate, surprise and the fiscal period a
        # print describes - not the day it was announced. So known_at is the
        # day we first saw the row: later than true, never earlier, which is
        # the side the guard needs. The period label is a calendar quarter end
        # and can lie after that day (NVIDIA's late-August print is filed under
        # 2026-09-30); then the row is forward-labelled. It is never clamped up
        # to the label, because a public figure dated a month into the future
        # is invisible to every as-of read until then.
        today = self.today()
        for s in rows:
            if not isinstance(s, dict):
                continue
            period = parse_date(s.get("period"))
            if period is None:
                continue
            for key, concept in (
                ("actual", "eps_actual"),
                ("estimate", "eps_estimate_at_print"),
                ("surprisePercent", "eps_surprise_pct"),
            ):
                value = as_decimal(s.get(key))
                if value is None:
                    continue
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=today,
                        value=value,
                        period_end=period,
                        currency="USD" if concept != "eps_surprise_pct" else "",
                        unit="pct" if concept == "eps_surprise_pct" else "per_share",
                        payload={"quarter": s.get("quarter"), "year": s.get("year")},
                        forward=period > today,
                    )
                )

    def _recommendations(
        self, pull: Pull, iid: str, symbol: str, since: datetime, token: str
    ) -> None:
        rows = self.get_json(f"{BASE}/stock/recommendation", {"symbol": symbol, "token": token})
        if not isinstance(rows, list):
            raise SourceError(f"finnhub stock/recommendation for {symbol}: expected a list")
        today = self.today()
        for r in rows[:3]:  # the current month and two before it; the rest is history
            if not isinstance(r, dict):
                continue
            period = parse_date(r.get("period"))
            if period is None:
                continue
            for key in ("strongBuy", "buy", "hold", "sell", "strongSell"):
                value = as_decimal(r.get(key))
                if value is None:
                    continue
                concept = "analyst_" + "".join(("_" + c.lower()) if c.isupper() else c for c in key)
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=today,
                        value=value,
                        period_end=period,
                        unit="analysts",
                    )
                )

    def _metrics(self, pull: Pull, iid: str, symbol: str, since: datetime, token: str) -> None:
        payload = self.get_json(
            f"{BASE}/stock/metric", {"symbol": symbol, "metric": "all", "token": token}
        )
        metric = payload.get("metric") if isinstance(payload, dict) else None
        if not isinstance(metric, dict):
            raise SourceError(f"finnhub stock/metric for {symbol}: no metric object")
        today = self.today()
        for key, concept in METRICS.items():
            value = as_decimal(metric.get(key))
            if value is None:
                continue
            pull.observations.append(
                Observation(self.name, iid, concept, known_at=today, value=value, period_end=None)
            )
