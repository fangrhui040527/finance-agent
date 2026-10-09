"""Every ratio reads its flows on one period, and balance-sheet lines where the collectors put them.

Each test fails on the code before 2026-10-08:

  * `Statements.ttm` fell back to a line's latest fiscal year one line at a
    time, so a ratio divided four quarters of one flow by a fiscal year of
    another. NVDA on 2026-10-07: net income to 2026-07-26 against operating
    cash flow to 2026-01-25 (10-Q cash flows are year-to-date, so only Q1 is
    stored) made a 47% accruals flag where the matching year shows 14%;
    AAPL's fcf_margin and capex_to_revenue divided FY2025 cash flows by
    revenue to June 2026;
  * Beneish, Piotroski and the Sloan ratio's opening assets read
    balance-sheet lines as `total_assets_fy` and the like, keys no collector
    writes, so Beneish computed for no stored name, Piotroski stopped at about
    3 of 9 and both blamed collectors that had stored every line. The quality
    tests fed `_fy` copies of the instants, which is why they passed.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from core.market.pointintime import FactStore
from engines.fundamentals.quality import accruals, beneish_m_score, piotroski_f_score
from engines.fundamentals.ratios import (
    Statements,
    accrual_ratio,
    capex_to_depreciation,
    capex_to_revenue,
    fcf,
    fcf_margin,
    interest_cover,
    ratio_sheet,
)
from tests.statement_fixtures import (
    ASOF,
    BALANCE_T,
    BALANCE_T1,
    FILED_T,
    FILED_T1,
    IID,
    T1,
    T,
    us_store,
)
from tests.statement_fixtures import fact as _fact

ON = date(2026, 10, 7)


def near(a: Decimal | None, b: str, tol: str = "0.0005") -> bool:
    return a is not None and abs(a - Decimal(b)) <= Decimal(tol)


def fact(concept: str, end: date, value, filed: date | None = None):
    return _fact(concept, end, filed or end + timedelta(days=30), value)


def nvda_like() -> FactStore:
    """NVDA's lines as stored on 2026-10-07, in USD millions.

    Six quarters of net income; operating cash flow only as Q1s and fiscal
    years, because sec_xbrl drops the six- and nine-month 10-Q cash flows.
    """
    store = FactStore()
    quarters = (
        (date(2025, 4, 27), 18775),
        (date(2025, 7, 27), 26422),
        (date(2025, 10, 26), 31910),
        (date(2026, 1, 25), 42960),
        (date(2026, 4, 26), 52000),
        (date(2026, 7, 26), 66009),
    )
    for end, v in quarters:
        store.add(fact("net_income", end, v))
    for end, v in ((date(2025, 4, 27), 27414), (date(2026, 4, 26), 41000)):
        store.add(fact("cash_from_operations", end, v))
    for end, ni, cfo, ta in (
        (date(2025, 1, 26), 72880, 64089, 111601),
        (date(2026, 1, 25), 120067, 102718, 206803),
    ):
        store.add(fact("net_income_fy", end, ni))
        store.add(fact("cash_from_operations_fy", end, cfo))
        store.add(fact("total_assets", end, ta))
    store.add(fact("total_assets", date(2026, 7, 26), 320272))
    return store


def test_quarterly_net_income_with_annual_only_cash_flow_is_read_on_the_shared_fiscal_year():
    s = Statements.from_store(nvda_like(), IID, ON)
    sc = accruals(s)
    assert sc.verdict == "gap 14% of net income, inside the 30% rule", (
        "120,067 - 102,718 on the year to 2026-01-25, not 192,879 TTM against the year's cash flow"
    )
    assert near(sc.value, str(Decimal(17349) / Decimal(206803))), "assets at that year's end"
    assert {"net_income_fy@2026-01-25", "cash_from_operations_fy@2026-01-25"} <= set(sc.inputs)
    r = accrual_ratio(s)
    assert near(r.value, str(Decimal(17349) / Decimal(159202))), "assets averaged over that year"
    assert "fiscal year to 2026-01-25" in r.formula and "TTM" not in r.formula
    assert not any(k.startswith("net_income@") for k in r.inputs)


def test_inputs_that_share_no_period_are_refused_and_say_which_periods_they_had():
    store = FactStore()
    for end in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)):
        store.add(fact("operating_income", end, 100))
        store.add(fact("net_income", end, 80))
    # four quarters each, but a year apart: AAPL stopped tagging interest expense in 2023
    for end in (date(2022, 12, 31), date(2023, 4, 1), date(2023, 7, 1), date(2023, 9, 30)):
        store.add(fact("interest_expense", end, 5))
    for end in (date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)):
        store.add(fact("cash_from_operations", end, 90))
    store.add(fact("capex_fy", date(2023, 12, 31), 40))
    store.add(fact("depreciation_fy", date(2025, 12, 31), 30))
    s = Statements.from_store(store, IID, ON)

    r = interest_cover(s)
    assert r.value is None and r.missing == (
        "a shared period (operating_income TTM to 2026-06-30, interest_expense TTM to 2023-09-30)",
    )
    assert r.text().startswith("interest_cover: not computable, missing a shared period (")
    sc = accruals(s)
    assert sc.verdict == "not computable" and len(sc.missing) == 1
    assert "net_income TTM to 2026-06-30" in sc.missing[0]
    assert "cash_from_operations TTM to 2025-12-31" in sc.missing[0]
    d = capex_to_depreciation(s)
    assert d.value is None and "capex fiscal year to 2023-12-31" in d.missing[0]
    assert "depreciation fiscal year to 2025-12-31" in d.missing[0]

    # one input stands on a fiscal year the other has no figure for
    lone = FactStore()
    for end in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)):
        lone.add(fact("net_income", end, 80))
    lone.add(fact("cash_from_operations_fy", date(2025, 12, 31), 300))
    a = accrual_ratio(Statements.from_store(lone, IID, ON))
    assert a.value is None
    assert "no net_income_fy for the fiscal year to 2025-12-31" in a.missing[0]
    # four quarters that end on the fiscal year end are that year
    lone.add(fact("net_income", date(2025, 3, 31), 70))
    lone.add(fact("net_income", date(2025, 6, 30), 80))
    lone.add(fact("total_assets", date(2025, 12, 31), 1000))
    asof = date(2026, 4, 15)  # before the June quarter: the last four end on 31 December
    a = accrual_ratio(Statements.from_store(lone, IID, asof))
    assert a.value == Decimal("0.01"), "(310 - 300) over 1,000"
    assert "fiscal year to 2025-12-31" in a.formula


def test_free_cash_flow_capex_and_tax_ratios_are_read_on_the_fiscal_year_their_lines_share():
    store = FactStore()
    # AAPL in USD millions: revenue and income tax by quarter, cash flows by fiscal year only
    for end, rev, tax in (
        (date(2025, 9, 27), 102466, 4000),
        (date(2025, 12, 27), 143756, 6000),
        (date(2026, 3, 28), 111184, 5000),
        (date(2026, 6, 27), 109417, 7000),
    ):
        store.add(fact("revenue", end, rev))
        store.add(fact("income_tax", end, tax))
    fy = date(2025, 9, 27)
    for concept, v in (
        ("revenue_fy", 416161),
        ("cash_from_operations_fy", 111482),
        ("capex_fy", 12715),
        ("income_tax_fy", 20719),
        ("pretax_income_fy", 132729),
    ):
        store.add(fact(concept, fy, v))
    store.add(fact("cash_from_operations", date(2025, 12, 27), 39000))  # a Q1, not four quarters
    s = Statements.from_store(store, IID, ON)

    assert fcf(s).value == Decimal(98767) and "fiscal year to 2025-09-27" in fcf(s).formula
    m = fcf_margin(s)
    assert near(m.value, "0.237330"), "98,767 over FY2025 revenue 416,161, not TTM 466,823"
    assert "revenue_fy@2025-09-27" in m.inputs and "revenue@2026-06-27" not in m.inputs
    assert near(capex_to_revenue(s).value, "0.030553"), "12,715 over 416,161"
    rate, inputs = s.effective_tax_rate()
    assert near(rate, "0.156100"), "FY2025 tax 20,719 over pretax 132,729, not TTM tax 22,000"
    assert set(inputs) == {"income_tax_fy@2025-09-27", "pretax_income_fy@2025-09-27"}


def test_beneish_and_piotroski_read_balance_sheet_lines_where_the_collectors_store_them():
    store = us_store()
    assert not any(
        c.endswith("_fy") and c.removesuffix("_fy") in BALANCE_T for _, c in store._facts
    ), "collector-shaped: sec_xbrl and eodhd store instants only under the plain key"
    s = Statements.from_store(store, IID, ASOF)
    m = beneish_m_score(s)
    assert m.computable == m.needed == 8 and near(m.value, "-2.327", "0.01")
    assert {"total_assets@2025-12-31", "total_assets@2024-12-31"} <= set(m.inputs)
    f = piotroski_f_score(s)
    assert f.computable == 9 and f.value == Decimal(8) and not f.missing

    # 52/53-week years: a balance sheet stamped days off the fiscal year end is that year's
    drift = us_store(balance=False)
    for c, v in BALANCE_T.items():
        drift.add(_fact(c, T - timedelta(days=4), FILED_T, v))
    for c, v in BALANCE_T1.items():
        drift.add(_fact(c, T1 - timedelta(days=3), FILED_T1, v))
    assert near(beneish_m_score(Statements.from_store(drift, IID, ASOF)).value, "-2.327", "0.01")

    # a quarter-end figure is never borrowed for a year end: the score stays partial
    quarter = FactStore()
    for series in store._facts.values():
        for x in series:
            if x.concept != "receivables":
                quarter.add(x)
    quarter.add(_fact("receivables", date(2025, 9, 30), date(2025, 11, 1), 140))
    quarter.add(_fact("receivables", date(2024, 9, 30), date(2024, 11, 1), 110))
    q = beneish_m_score(Statements.from_store(quarter, IID, ASOF))
    assert q.value is None and q.computable == 7 and q.missing == ("receivables",)

    # a store that does hold `_fy` copies of the instants is still read
    copies = us_store(balance=False)
    for c, v in BALANCE_T.items():
        copies.add(_fact(f"{c}_fy", T, FILED_T, v))
    for c, v in BALANCE_T1.items():
        copies.add(_fact(f"{c}_fy", T1, FILED_T1, v))
    assert near(beneish_m_score(Statements.from_store(copies, IID, ASOF)).value, "-2.327", "0.01")


def test_accrual_ratio_averages_total_assets_from_plain_key_instants_over_its_own_period():
    sheet = ratio_sheet(us_store(), IID, ASOF)
    r = sheet.ratios["accrual_ratio"]
    assert near(r.value, "-0.015789"), "(150 - 180) over the average of 2,000 and 1,800"
    assert {"total_assets@2025-12-31", "total_assets@2024-12-31"} <= set(r.inputs)

    # four quarters to 30 June: the balance sheets a year apart, not the fiscal year end's
    store = FactStore()
    for end in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)):
        store.add(fact("net_income", end, 10))
        store.add(fact("cash_from_operations", end, 5))
    store.add(fact("total_assets", date(2026, 6, 30), 1100))
    store.add(fact("total_assets", date(2025, 6, 28), 900))  # a 52-week year ends two days early
    r = accrual_ratio(Statements.from_store(store, IID, ON))
    assert r.value == Decimal("0.02"), "(40 - 20) over the average of 1,100 and 900"
    assert {"total_assets@2026-06-30", "total_assets@2025-06-28"} <= set(r.inputs)
