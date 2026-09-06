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
discrete Q4; the fourth quarter is derived as FY minus the three quarters and
carries `derived` in its payload. Where two tags describe the same line
(revenue has three), the first tag with a figure for a period wins.

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
                        if start is None:
                            key = concept
                        else:
                            span = end - start
                            if QUARTER[0] <= span <= QUARTER[1]:
                                key = concept
                            elif YEAR[0] <= span <= YEAR[1]:
                                key = f"{concept}_fy"
                            else:
                                continue  # six- and nine-month year-to-date figures
                            flows.add(concept)
                        pk = (key, end)
                        if pk in seen_periods:
                            continue  # an earlier tag already carries this period
                        periods_this_tag.add(pk)
                        meta = {
                            "tag": tag,
                            "fy": it.get("fy"),
                            "fp": it.get("fp"),
                            "form": it.get("form"),
                            "accn": it.get("accn"),
                            "frame": it.get("frame"),
                            "unit": unit,
                        }
                        bucket = claimed.setdefault(pk, {})
                        if val not in bucket or filed < bucket[val][0]:
                            bucket[val] = (filed, meta)
                seen_periods |= periods_this_tag
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
    def _derive_fourth_quarters(claimed: dict, flows: set[str]) -> None:
        """FY minus Q1..Q3 for every flow whose fourth quarter is not reported."""
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
                fy_val, (fy_filed, fy_meta) = max(fy_vals.items(), key=lambda kv: kv[1][0])
                q_sum = Decimal(0)
                for _, vals in qs:
                    v, _ = max(vals.items(), key=lambda kv: kv[1][0])
                    q_sum += v
                claimed[(concept, fy_end)] = {
                    fy_val - q_sum: (fy_filed, {**fy_meta, "derived": "FY - Q1..Q3", "fp": "Q4"})
                }
