"""Financial Modeling Prep: statements, estimates, targets, rating changes, transcripts.

The research review singles FMP out for depth of history and for the
earnings-call transcript endpoint - management's own words, dated. On the
free plan (250 calls a day) the statements and estimates are there; some of
the rest answers with a JSON "Error Message" naming the plan, or with HTTP
402 outright (`/stable/earnings` did, every week to 2026-09-13). Either is
recorded as ONE note per endpoint - "outside the plan", with the names it was
not collected for - and the pull continues, because a plan boundary on one
endpoint says nothing about the eight others. An endpoint refused once is not
asked again for the next name in the same run: the boundary is the plan's,
not the company's, and the second request would spend a call to learn it
twice.

Point-in-time is handled with care here because this is where it matters
most: a quarter's revenue is stamped `known_at = filingDate` (the day the
10-Q went in), never the period end. `core.market.pointintime` refuses a
reported Fact whose known_at precedes its period end, and this adapter never
builds one.

Estimates are the other case. Consensus for next fiscal year is knowable
today and describes a period that ends in a year, so it is stamped
`known_at` = the day it was fetched, `period_end` = the period it targets,
and `forward=True`. Each revision then lands as its own vintage. Until
2026-09-18 this adapter clamped known_at up to the target period instead,
which gave every revision one date years out and left all 46 rows in the
fact book invisible to every as-of read.

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


class EndpointExcluded(PlanExcluded):
    """One FMP endpoint the plan does not include, named by its path.

    Carries the path so `collect` can say it once - "/earnings is outside the
    plan (HTTP 402); not collected for NVDA, AAPL, MSFT" - rather than once
    per name with a redacted URL each. The per-name form is what the pulls
    table held to 2026-09-17: three names, several endpoints, and a line that
    ran past the column before it named the second company.
    """

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"fmp /{path}: outside the plan ({why})")
        self.path = path
        self.why = why


class FmpCollector(Collector):
    name = "fmp"
    key_env = "FMP_API_KEY"
    QUARTERS = 8

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        #: path -> why, for the endpoints this run has been refused on, and the
        #: symbols each refusal stood in for. One run's boundary is one run's:
        #: `collect` clears both, so a plan bought tomorrow is asked tomorrow.
        self._excluded: dict[str, str] = {}
        self._excluded_for: dict[str, list[str]] = {}

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self.key
        pull = Pull()
        failures: list[str] = []
        self._excluded.clear()
        self._excluded_for.clear()
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
                except EndpointExcluded:
                    pass  # recorded by _get; said once per endpoint below
                except PlanExcluded as e:
                    one.notes.append(str(e))
                except SourceError as e:
                    failures.append(f"{iid} {step.__name__}: {e}")
            pull.extend(one)
        for path, why in self._excluded.items():
            names = ", ".join(dict.fromkeys(self._excluded_for.get(path, ())))
            pull.notes.append(f"fmp /{path} is outside the plan ({why}); not collected for {names}")
        if instruments and failures and len(failures) >= 3 * len(instruments):
            raise SourceError("every endpoint failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    # -- transport with FMP's error convention -------------------------------------

    def _get(self, path: str, params: dict) -> list | dict:
        symbol = str(params.get("symbol") or "")
        if path in self._excluded:
            # Refused earlier this run, so not asked for another name: the
            # plan is per endpoint, not per company.
            self._excluded_for.setdefault(path, []).append(symbol)
            raise EndpointExcluded(path, self._excluded[path])
        try:
            payload = self.get_json(f"{BASE}/{path}", params)
        except PlanExcluded as e:  # get_json's 402/403, with the redacted URL
            code = getattr(e.__cause__, "code", None)
            raise self._exclude(path, symbol, f"HTTP {code}" if code else str(e)) from e
        if isinstance(payload, dict) and payload.get("Error Message"):
            msg = str(payload["Error Message"])
            low = msg.lower()
            if any(w in low for w in ("plan", "premium", "subscription", "upgrade", "exclusive")):
                raise self._exclude(path, symbol, msg[:160])
            raise SourceError(f"fmp {path}: {msg[:160]}")
        return payload

    def _exclude(self, path: str, symbol: str, why: str) -> EndpointExcluded:
        self._excluded[path] = why
        self._excluded_for.setdefault(path, []).append(symbol)
        return EndpointExcluded(path, why)

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
            # A REITERATION IS NOT A CHANGE. This endpoint returns every time a
            # broker republishes the opinion it already held, and those are the
            # overwhelming majority: of the 3,917 rows in the fact book on
            # 2026-09-06, NVIDIA's last 30 days were 28 "maintain Buy" against
            # 2 things the company itself did. Both are kept - the store never
            # drops what a source said - but under kinds that mean different
            # things, so a reader asking what happened is not handed a list of
            # people restating their position.
            changed = action in ("upgrade", "downgrade", "initialise", "initialize", "initiate")
            changed = changed or bool(new and old and str(new) != str(old))
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"{symbol}:grade:{when}:{house}",
                    instrument_id=iid,
                    kind="rating_change" if changed else "rating_reiteration",
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
            try:
                rows = self._get(
                    path,
                    {"symbol": symbol, "period": "quarter", "limit": self.QUARTERS, "apikey": key},
                )
            except EndpointExcluded:
                continue  # one statement outside the plan says nothing about the other two
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
                        known_at=today,
                        value=value,
                        period_end=period,
                        currency="USD",
                        payload={
                            "analysts": r.get("numAnalystsEps") or r.get("numAnalystsRevenue")
                        },
                        forward=True,
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
