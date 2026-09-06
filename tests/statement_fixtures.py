"""A two-year, four-quarter statement fixture with hand-checkable ratios.

Fiscal years end 31 December; the annual lines are filed mid-February, the
quarters six weeks after they end. Every expected figure in the engine tests
is worked from these numbers by hand.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.market.pointintime import Fact, FactStore
from markets.contract import AccountingStandard

IID = "XNAS:TEST"
ASOF = date(2026, 3, 1)

T = date(2025, 12, 31)
T1 = date(2024, 12, 31)
FILED_T = date(2026, 2, 15)
FILED_T1 = date(2025, 2, 15)

ANNUAL_T = {
    "revenue": 1000,
    "cost_of_revenue": 600,
    "gross_profit": 400,
    "operating_income": 200,
    "net_income": 150,
    "cash_from_operations": 180,
    "capex": 50,
    "depreciation": 40,
    "sga": 120,
    "interest_expense": 10,
    "income_tax": 40,
    "pretax_income": 190,
}
ANNUAL_T1 = {
    "revenue": 900,
    "cost_of_revenue": 560,
    "gross_profit": 340,
    "operating_income": 170,
    "net_income": 130,
    "cash_from_operations": 160,
    "capex": 45,
    "depreciation": 38,
    "sga": 110,
    "interest_expense": 12,
    "income_tax": 35,
    "pretax_income": 165,
}
BALANCE_T = {
    "total_assets": 2000,
    "current_assets": 800,
    "current_liabilities": 400,
    "total_liabilities": 1000,
    "receivables": 150,
    "ppe_net": 700,
    "long_term_debt": 500,
    "short_term_debt": 50,
    "equity": 1000,
    "retained_earnings": 600,
    "cash": 300,
    "shares_outstanding": 100,
}
BALANCE_T1 = {
    "total_assets": 1800,
    "current_assets": 700,
    "current_liabilities": 380,
    "total_liabilities": 950,
    "receivables": 120,
    "ppe_net": 650,
    "long_term_debt": 520,
    "short_term_debt": 40,
    "equity": 850,
    "retained_earnings": 500,
    "cash": 250,
    "shares_outstanding": 102,
}
QUARTERS = (
    (date(2025, 3, 31), date(2025, 5, 1)),
    (date(2025, 6, 30), date(2025, 8, 1)),
    (date(2025, 9, 30), date(2025, 11, 1)),
    (date(2025, 12, 31), date(2026, 2, 15)),
)
QUARTERLY = {
    "revenue": (240, 250, 255, 255),
    "net_income": (35, 37, 39, 39),
    "cash_from_operations": (40, 45, 45, 50),
    "operating_income": (48, 50, 51, 51),
    "capex": (12, 12, 13, 13),
}
PRIOR_Q4 = {"revenue": 230, "net_income": 33}  # the quarter ending 2024-12-31, for the year-on-year


def fact(concept: str, period_end: date, known_at: date, value, iid: str = IID) -> Fact:
    return Fact(
        iid,
        concept,
        period_end,
        known_at,
        Decimal(value),
        "USD",
        AccountingStandard.US_GAAP,
        f"fixture:{concept}:{period_end}",
    )


def us_store(
    iid: str = IID, *, annual: bool = True, quarterly: bool = True, balance: bool = True
) -> FactStore:
    store = FactStore()
    if annual:
        for c, v in ANNUAL_T.items():
            store.add(fact(f"{c}_fy", T, FILED_T, v, iid))
        for c, v in ANNUAL_T1.items():
            store.add(fact(f"{c}_fy", T1, FILED_T1, v, iid))
    if balance:
        for c, v in BALANCE_T.items():
            store.add(fact(c, T, FILED_T, v, iid))
        for c, v in BALANCE_T1.items():
            store.add(fact(c, T1, FILED_T1, v, iid))
        # annual copies of the instants the year-over-year models read
        for c, v in BALANCE_T.items():
            store.add(fact(f"{c}_fy", T, FILED_T, v, iid))
        for c, v in BALANCE_T1.items():
            store.add(fact(f"{c}_fy", T1, FILED_T1, v, iid))
    if quarterly:
        for c, values in QUARTERLY.items():
            for (end, filed), v in zip(QUARTERS, values):
                store.add(fact(c, end, filed, v, iid))
        for c, v in PRIOR_Q4.items():
            store.add(fact(c, T1, FILED_T1, v, iid))
        store.add(fact("shares_outstanding", date(2025, 9, 30), date(2025, 11, 1), 100, iid))
    return store
