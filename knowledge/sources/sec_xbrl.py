"""SEC XBRL company facts: every reported line item, with the day it was filed.

The free statement sources this book has had so far stop short of what the
earnings-quality models need: FMP's free plan answers 402 on the income
statement, and Finnhub's metrics are a vendor's ratios, not lines. The SEC
publishes the lines themselves, keyless, for every US filer:

    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

one document per company holding every us-gaap fact it has ever tagged, each
with the period it covers, the form that carried it, and the date that form
was filed. That `filed` date is the point-in-time stamp this book is built
around: a figure is knowable the day it is filed, never the day its quarter
ended. Requests carry the same User-Agent as the EDGAR collector, because
data.sec.gov answers 403 without a contact address.

Concept keys follow the conventions engines/fundamentals/ratios.py reads:
quarterly flows under the plain key (`revenue`), annual flows under
`<key>_fy`, balance-sheet instants under the plain key with the statement
date as `period_end`. Filers report Q1-Q3 and the full year but usually no
discrete Q4, and a 10-Q tags its cash-flow statement only year to date (3, 6
and 9 months), so a quarter nobody reports is derived from two figures with
the same start: Q2 = 6M - 3M, Q3 = 9M - 6M, Q4 = FY - 9M, else FY minus the
three quarters. A reported quarter always wins over a derived one. A derived
row carries `derived` and its `inputs` in its payload and is knowable on the
day its last input was filed, never earlier. Where two tags describe the same
line (revenue has three), the first tag with a figure for a period wins.

Dedup: the document repeats old quarters in later 10-Ks; the earliest filing
of each (concept, period, value) is kept, because that is when the figure
became public. A different value for the same period is a second row - a
restatement - and the fact book keeps both. One request per name per weekly
slot; the document is the whole history, so `since` is not sent.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from knowledge.facts import Observation, as_decimal
from knowledge.sources.base import Collector, PlanExcluded, Pull, SourceError, parse_date
from knowledge.sources.edgar import CIK, sec_headers

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

#: System concept -> us-gaap tags, in order of preference.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "cash_from_operations": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
    "interest_expense": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "dividends_paid": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock",),
    "total_assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "total_liabilities": ("Liabilities",),
    "receivables": ("AccountsReceivableNetCurrent",),
    "ppe_net": ("PropertyPlantAndEquipmentNet",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"),
    "equity": ("StockholdersEquity",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "shares_outstanding": ("CommonStockSharesOutstanding",),
}
#: Share counts also live in the dei taxonomy, as of the cover page date.
DEI: dict[str, tuple[str, ...]] = {"shares_outstanding": ("EntityCommonStockSharesOutstanding",)}
UNITS = {"USD", "USD/shares", "shares"}
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
QUARTER = (timedelta(days=80), timedelta(days=100))
YEAR = (timedelta(days=350), timedelta(days=380))
#: Periods that run from a fiscal year's start, as 10-Qs and 10-Ks report them. The
#: 6M and 9M spans are only ever inputs to a derived quarter, never rows of their own.
SPANS = {
    "3M": QUARTER,
    "6M": (timedelta(days=170), timedelta(days=200)),
    "9M": (timedelta(days=260), timedelta(days=290)),
    "FY": YEAR,
}
#: The span a quarter is subtracted from -> the shorter span before it, and the quarter.
PRIOR = {"6M": ("3M", "Q2"), "9M": ("6M", "Q3"), "FY": ("9M", "Q4")}
#: A period's distinct values, each with the day it was first filed and its meta.
Versions = dict[Decimal, tuple[date, dict]]
#: Periods older than this are not stored; the models read two to ten years.
LOOKBACK = timedelta(days=12 * 365)


class SecCompanyFacts(Collector):
    name = "sec_xbrl"
    key_env = None
    TIMEOUT = 60  # the document runs to several megabytes for a large filer

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        today = self.today()
        names: list[str] = []
        for iid in instruments:
            if iid in CIK:
                names.append(iid)
            elif iid.upper().startswith(("XNAS:", "XNYS:")):
                pull.notes.append(f"{iid}: no CIK on file (knowledge/sources/edgar.py); skipped")
        failures: list[str] = []
        for iid in names:
            try:
                payload = self.get_json(URL.format(cik=CIK[iid]), headers=sec_headers())
            except PlanExcluded as e:
                raise SourceError(
                    f"SEC answered {e} - data.sec.gov requires a descriptive User-Agent with a "
                    "contact address: set SEC_USER_AGENT to '<name> <email>'"
                ) from None
            except SourceError as e:
                failures.append(f"{iid}: {e}")
                continue
            if not isinstance(payload, dict) or "facts" not in payload:
                failures.append(f"{iid}: no facts object in the reply")
                continue
            rows = self._observations(iid, payload, today)
            if not rows:
                failures.append(f"{iid}: no us-gaap facts matched the concept map")
                continue
            pull.observations.extend(rows)
        if names and failures and len(failures) == len(names):
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    # -- the document -> observations ---------------------------------------------------

    def _observations(self, iid: str, payload: dict, today: date) -> list[Observation]:
        facts = payload.get("facts") or {}
        gaap = facts.get("us-gaap") or {}
        dei = facts.get("dei") or {}
        floor = today - LOOKBACK
        # (concept_key, period_end) -> {value: (filed, meta)}; the first tag with a
        # figure for a period claims it, so a later tag never overwrites an earlier one.
        claimed: dict[tuple[str, date], dict[Decimal, tuple[date, dict]]] = {}
        # (concept_key, tag, start) -> {end: {value: (filed, meta)}}: every flow figure by
        # the day its period starts, so 6M - 3M and FY - 9M subtract within one tag.
        cumulative: dict[tuple[str, str, date], dict[date, Versions]] = {}
        flows: set[str] = set()
        for concept, tags in list(CONCEPTS.items()) + list(DEI.items()):
            taxonomy = dei if concept in DEI and tags is DEI[concept] else gaap
            seen_periods: set[tuple[str, date]] = set()
            for tag in tags:
                entry = taxonomy.get(tag)
                if not isinstance(entry, dict):
                    continue
                periods_this_tag: set[tuple[str, date]] = set()
                for unit, items in (entry.get("units") or {}).items():
                    if unit not in UNITS or not isinstance(items, list):
                        continue
                    for it in items:
                        if not isinstance(it, dict) or it.get("form") not in FORMS:
                            continue
                        end = parse_date(it.get("end"))
                        filed = parse_date(it.get("filed"))
                        val = as_decimal(it.get("val"))
                        if (
                            end is None
                            or filed is None
                            or val is None
                            or end < floor
                            or filed < end
                        ):
                            continue
                        start = parse_date(it.get("start"))
                        span = None if start is None else _span(start, end)
                        if start is None:
                            key = concept
                        elif span is None:
                            continue  # neither a quarter, a year nor a year to date
                        else:
                            # 6M and 9M have no key: the quarter after them is the row.
                            key = {"3M": concept, "FY": f"{concept}_fy"}.get(span)
                            flows.add(concept)
                        meta = {
                            "tag": tag,
                            "fy": it.get("fy"),
                            "fp": it.get("fp"),
                            "form": it.get("form"),
                            "accn": it.get("accn"),
                            "frame": it.get("frame"),
                            "unit": unit,
                        }
                        if start is not None:
                            by_end = cumulative.setdefault((concept, tag, start), {})
                            _keep(by_end.setdefault(end, {}), val, filed, meta)
                        if key is None:
                            continue
                        pk = (key, end)
                        if pk in seen_periods:
                            continue  # an earlier tag already carries this period
                        periods_this_tag.add(pk)
                        _keep(claimed.setdefault(pk, {}), val, filed, meta)
                seen_periods |= periods_this_tag
        self._derive_from_year_to_date(claimed, cumulative)
        self._derive_fourth_quarters(claimed, flows)
        out: list[Observation] = []
        for (key, end), values in sorted(claimed.items()):
            for val, (filed, meta) in values.items():
                unit = meta.get("unit", "")
                out.append(
                    Observation(
                        self.name,
                        iid,
                        key,
                        known_at=filed,
                        value=val,
                        period_end=end,
                        unit="USD" if unit == "USD" else unit,
                        currency="USD" if str(unit).startswith("USD") else "",
                        payload=meta,
                    )
                )
        return out

    @staticmethod
    def _derive_from_year_to_date(claimed: dict, cumulative: dict) -> None:
        """Q2 = 6M - 3M, Q3 = 9M - 6M, Q4 = FY - 9M for every quarter not reported.

        Dropping the year-to-date figures left NVDA's cash from operations with one
        Q1 a year, so its TTM fell back to the year to 2026-01-25 while net income
        ran to 2026-07-26, and accruals flagged a 47% gap that was only the mismatch.
        """
        # Keys arrive tag by tag in order of preference, so the first tag that can
        # derive a quarter claims it, as with reported figures.
        for (concept, _tag, start), by_end in cumulative.items():
            for end, vals in by_end.items():
                span = _span(start, end)
                if span not in PRIOR or (concept, end) in claimed:
                    continue
                before, quarter = PRIOR[span]
                for prior, prior_vals in by_end.items():
                    if _span(start, prior) == before and QUARTER[0] <= end - prior <= QUARTER[1]:
                        claimed[(concept, end)] = _derived(
                            [(1, span, end, vals), (-1, before, prior, prior_vals)],
                            f"{span} - {before}",
                            quarter,
                        )
                        break

    @staticmethod
    def _derive_fourth_quarters(claimed: dict, flows: set[str]) -> None:
        """FY minus Q1..Q3 for every flow whose fourth quarter is neither reported nor derived."""
        for concept in flows:
            annual = {end: vals for (key, end), vals in claimed.items() if key == f"{concept}_fy"}
            quarterly = {end: vals for (key, end), vals in claimed.items() if key == concept}
            for fy_end, fy_vals in annual.items():
                if fy_end in quarterly:
                    continue
                qs = [
                    (end, vals)
                    for end, vals in quarterly.items()
                    if fy_end - timedelta(days=300) <= end < fy_end
                ]
                if len(qs) != 3:
                    continue
                parts = [(1, "FY", fy_end, fy_vals)] + [(-1, "3M", end, vals) for end, vals in qs]
                claimed[(concept, fy_end)] = _derived(parts, "FY - Q1..Q3", "Q4")


def _span(start: date, end: date) -> str | None:
    """3M, 6M, 9M or FY for a period from `start` to `end`; None for any other length."""
    return next((name for name, (lo, hi) in SPANS.items() if lo <= end - start <= hi), None)


def _keep(versions: Versions, val: Decimal, filed: date, meta: dict) -> None:
    """The earliest filing of a value is the day it became public."""
    if val not in versions or filed < versions[val][0]:
        versions[val] = (filed, meta)


def _derived(parts: list[tuple[int, str, date, Versions]], how: str, fp: str) -> Versions:
    """Every value the signed `parts` sum to as their filings land, each dated by its last input.

    On each day an input was filed, the newest version of every input filed by then
    is combined - what a collection run that day would have stored - so a late or
    restated input makes a later row, never an earlier one. Stamping the year's 10-K
    date alone backdated MSFT's FY25 Q4 depreciation (6.3B, known_at 2025-07-30) on
    quarters first filed between 2025-10-29 and 2026-04-29.
    """
    days = sorted({filed for *_, versions in parts for filed, _ in versions.values()})
    out: Versions = {}
    for day in days:
        used = []
        for sign, span, end, versions in parts:
            known = [(filed, v, meta) for v, (filed, meta) in versions.items() if filed <= day]
            if not known:
                break  # an input not filed yet: the quarter is not knowable on this day
            filed, v, meta = max(known, key=lambda k: (k[0], k[1]))
            used.append((sign, span, end, v, filed, meta))
        else:
            value = sum((sign * v for sign, _, _, v, _, _ in used), Decimal(0))
            if value in out:
                continue
            base = next(meta for *_, filed, meta in used if filed == day)
            inputs = [
                {
                    "span": span,
                    "end": end.isoformat(),
                    "value": str(v),
                    "filed": filed.isoformat(),
                    "accn": meta.get("accn"),
                }
                for _, span, end, v, filed, meta in used
            ]
            # The row takes the meta of the filing that completed it; the frame named
            # the input's period, not the quarter's.
            out[value] = (day, {**base, "frame": None, "derived": how, "fp": fp, "inputs": inputs})
    return out
