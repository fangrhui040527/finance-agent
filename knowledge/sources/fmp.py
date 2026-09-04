"""Financial Modeling Prep: statements, estimates, targets, rating changes, transcripts.

The research review singles FMP out for depth of history and for the
earnings-call transcript endpoint - management's own words, dated. On the
free plan (250 calls a day) the statements and estimates are there; some of
the rest answers with a JSON "Error Message" naming the plan. That answer is
recorded as a note and the pull continues, because a plan boundary on one
endpoint says nothing about the eight others.

Point-in-time is handled with care here because this is where it matters
most: a quarter's revenue is stamped `known_at = filingDate` (the day the
10-Q went in), never the period end. `core.market.pointintime` refuses a
Fact whose known_at precedes its period end, and this adapter never builds
one.

Two slots use it: `us_preopen` daily for the cheap, fast-moving pieces
(rating changes, the earnings date, the target consensus); `weekly` for the
statements, estimates and the latest transcript.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from knowledge.facts import Document, EventRecord, Observation, as_decimal
from knowledge.sources.base import (
    Collector,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_date,
    parse_datetime,
)

BASE = "https://financialmodelingprep.com/stable"

INCOME = {
    "revenue": "revenue",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "netIncome": "net_income",
    "ebitda": "ebitda",
    "eps": "eps",
    "epsDiluted": "eps_diluted",
}
CASHFLOW = {
    "operatingCashFlow": "cash_from_operations",
    "freeCashFlow": "free_cash_flow",
    "capitalExpenditure": "capex",
}
BALANCE = {
    "totalDebt": "total_debt",
    "cashAndCashEquivalents": "cash",
    "totalStockholdersEquity": "equity",
    "totalAssets": "total_assets",
}


class FmpCollector(Collector):
    name = "fmp"
    key_env = "FMP_API_KEY"
    QUARTERS = 8

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self.key
        pull = Pull()
        failures: list[str] = []
        deep = slot in ("weekly", "all")
        for iid in instruments:
            symbol = local_code(iid).upper()
            one = Pull()
            steps = [self._grades, self._earnings_dates, self._targets]
            if deep:
                steps += [self._statements, self._estimates, self._transcript]
            for step in steps:
                try:
                    step(one, iid, symbol, since, key)
                except PlanExcluded as e:
                    one.notes.append(str(e))
                except SourceError as e:
                    failures.append(f"{iid} {step.__name__}: {e}")
            pull.extend(one)
        if instruments and failures and len(failures) >= 3 * len(instruments):
            raise SourceError("every endpoint failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    # -- transport with FMP's error convention -------------------------------------

    def _get(self, path: str, params: dict) -> list | dict:
        payload = self.get_json(f"{BASE}/{path}", params)
        if isinstance(payload, dict) and payload.get("Error Message"):
            msg = str(payload["Error Message"])
            low = msg.lower()
            if any(w in low for w in ("plan", "premium", "subscription", "upgrade", "exclusive")):
                raise PlanExcluded(f"fmp {path}: {msg[:160]}")
            raise SourceError(f"fmp {path}: {msg[:160]}")
        return payload

    # -- daily pieces ----------------------------------------------------------------

    def _grades(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        rows = self._get("grades", {"symbol": symbol, "limit": 25, "apikey": key})
        if not isinstance(rows, list):
            raise SourceError(f"fmp grades for {symbol}: expected a list")
        for r in rows:
            if not isinstance(r, dict):
                continue
            when = parse_date(r.get("date"))
            if when is None:
                continue
            house = str(r.get("gradingCompany") or "an analyst")
            action = str(r.get("action") or "").lower()
            new, old = r.get("newGrade"), r.get("previousGrade")
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"{symbol}:grade:{when}:{house}",
                    instrument_id=iid,
                    kind="rating_change",
                    announced_at=datetime(when.year, when.month, when.day, tzinfo=UTC),
                    title=f"{house} {action or 'rates'} {new or ''}"
                    + (f" (from {old})" if old and old != new else ""),
                    payload={"house": house, "action": action, "new": new, "previous": old},
                )
            )

    def _earnings_dates(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        rows = self._get("earnings", {"symbol": symbol, "limit": 12, "apikey": key})
        if not isinstance(rows, list):
            raise SourceError(f"fmp earnings for {symbol}: expected a list")
        now = self._clock()
        for r in rows:
            if not isinstance(r, dict):
                continue
            when = parse_date(r.get("date"))
            if when is None or when < now.date():
                continue  # the past is Finnhub's surprise table; here only what is ahead
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"{symbol}:earnings:{when}",
                    instrument_id=iid,
                    kind="earnings_result",
                    announced_at=now,
                    effective_at=datetime(when.year, when.month, when.day, tzinfo=UTC),
                    title="results",
                    payload={
                        "eps_estimate": r.get("epsEstimated"),
                        "revenue_estimate": r.get("revenueEstimated"),
                    },
                )
            )

    def _targets(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        rows = self._get("price-target-consensus", {"symbol": symbol, "apikey": key})
        row = (
            rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else None
        )
        if not isinstance(row, dict):
            raise SourceError(f"fmp price-target-consensus for {symbol}: unexpected shape")
        today = self.today()
        for key_, concept in (
            ("targetConsensus", "price_target_consensus"),
            ("targetMedian", "price_target_median"),
            ("targetHigh", "price_target_high"),
            ("targetLow", "price_target_low"),
        ):
            value = as_decimal(row.get(key_))
            if value is not None:
                pull.observations.append(
                    Observation(
                        self.name, iid, concept, known_at=today, value=value, currency="USD"
                    )
                )

    # -- weekly pieces ---------------------------------------------------------------

    def _statements(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        for path, mapping in (
            ("income-statement", INCOME),
            ("cash-flow-statement", CASHFLOW),
            ("balance-sheet-statement", BALANCE),
        ):
            rows = self._get(
                path, {"symbol": symbol, "period": "quarter", "limit": self.QUARTERS, "apikey": key}
            )
            if not isinstance(rows, list):
                raise SourceError(f"fmp {path} for {symbol}: expected a list")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                period = parse_date(r.get("date"))
                filed = parse_date(r.get("filingDate")) or parse_date(r.get("acceptedDate"))
                if period is None:
                    continue
                known = filed if (filed and filed >= period) else max(self.today(), period)
                currency = str(r.get("reportedCurrency") or "USD")
                for key_, concept in mapping.items():
                    value = as_decimal(r.get(key_))
                    if value is None:
                        continue
                    pull.observations.append(
                        Observation(
                            self.name,
                            iid,
                            concept,
                            known_at=known,
                            value=value,
                            period_end=period,
                            currency=currency,
                            payload={"fiscal_year": r.get("fiscalYear"), "period": r.get("period")},
                        )
                    )

    def _estimates(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        rows = self._get(
            "analyst-estimates", {"symbol": symbol, "period": "annual", "limit": 4, "apikey": key}
        )
        if not isinstance(rows, list):
            raise SourceError(f"fmp analyst-estimates for {symbol}: expected a list")
        today = self.today()
        for r in rows:
            if not isinstance(r, dict):
                continue
            period = parse_date(r.get("date"))
            if period is None:
                continue
            for key_, concept in (("revenueAvg", "est_revenue"), ("epsAvg", "est_eps")):
                value = as_decimal(r.get(key_))
                if value is None:
                    continue
                pull.observations.append(
                    Observation(
                        self.name,
                        iid,
                        concept,
                        known_at=max(today, period) if period > today else today,
                        value=value,
                        period_end=period,
                        currency="USD",
                        payload={
                            "analysts": r.get("numAnalystsEps") or r.get("numAnalystsRevenue")
                        },
                    )
                )

    def _transcript(self, pull: Pull, iid: str, symbol: str, since: datetime, key: str) -> None:
        """The latest call. Which quarter is 'latest' comes from the statements
        already pulled this run, so no extra request is spent finding out."""
        latest = None
        for o in pull.observations:
            if o.instrument_id == iid and o.period_end and o.payload.get("fiscal_year"):
                if latest is None or o.period_end > latest[0]:
                    latest = (o.period_end, o.payload.get("fiscal_year"), o.payload.get("period"))
        if latest is None:
            return
        _, year, period = latest
        quarter = str(period or "").upper().lstrip("Q")
        if not quarter.isdigit():
            return
        rows = self._get(
            "earning-call-transcript",
            {"symbol": symbol, "year": year, "quarter": int(quarter), "apikey": key},
        )
        if not isinstance(rows, list):
            raise SourceError(f"fmp earning-call-transcript for {symbol}: expected a list")
        for r in rows:
            if not isinstance(r, dict) or not r.get("content"):
                continue
            when = parse_datetime(r.get("date")) or self._clock()
            pull.documents.append(
                Document(
                    source=self.name,
                    doc_id=f"{symbol}:transcript:{r.get('year')}Q{r.get('quarter')}",
                    instrument_id=iid,
                    kind="transcript",
                    title=f"{symbol} Q{r.get('quarter')} {r.get('year')} earnings call",
                    body=str(r["content"]),
                    published_at=when,
                    payload={"year": r.get("year"), "quarter": r.get("quarter")},
                )
            )


def _decimal_or_zero(raw) -> Decimal:
    return as_decimal(raw) or Decimal(0)
