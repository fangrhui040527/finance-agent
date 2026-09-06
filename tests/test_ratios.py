"""Ratios from stored lines: hand-checked arithmetic, point-in-time, honest partials."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.market.pointintime import FactStore
from engines.fundamentals.ratios import (
    Statements,
    accrual_ratio,
    cagr,
    capex_to_depreciation,
    dilution,
    fillers,
    gross_margin,
    interest_cover,
    net_debt_to_ebitda,
    ratio_sheet,
    ratio_sheet_text,
    receivable_days,
    roic,
    yoy,
)
from tests.statement_fixtures import ASOF, IID, fact, us_store


def close(a: Decimal | None, b: str, tol: str = "0.0005") -> bool:
    return a is not None and abs(a - Decimal(b)) <= Decimal(tol)


def test_the_sheet_reproduces_the_hand_arithmetic():
    sheet = ratio_sheet(us_store(), IID, ASOF)
    g = sheet.ratios
    assert close(g["gross_margin"].value, "0.40") and close(g["operating_margin"].value, "0.20")
    assert close(g["net_margin"].value, "0.15") and g["free_cash_flow"].value == Decimal(130)
    assert close(g["fcf_margin"].value, "0.13") and close(g["capex_to_revenue"].value, "0.05")
    assert close(g["capex_to_depreciation"].value, "1.25")
    assert close(g["accrual_ratio"].value, "-0.015789"), "(150 - 180) / average assets 1900"
    assert close(g["roe"].value, "0.15") and close(g["roa"].value, "0.075")
    assert close(g["roic"].value, "0.12632") and "effective tax rate" in g["roic"].note
    assert g["net_debt"].value == Decimal(250)
    assert close(g["net_debt_to_ebitda"].value, "1.04167"), (
        "EBITDA derived as operating income + depreciation"
    )
    assert close(g["debt_to_equity"].value, "0.55") and g["interest_cover"].value == Decimal(20)
    assert g["current_ratio"].value == Decimal(2) and close(g["asset_turnover"].value, "0.5")
    assert close(g["receivable_days"].value, "54.75")
    assert close(g["revenue_yoy"].value, "0.108696"), (
        "255 against the 230 of the year-earlier quarter"
    )
    assert close(g["revenue_cagr"].value, "0.11111")
    assert close(g["dilution"].value, "-0.019608"), "100 shares against 102 a year earlier"
    assert sheet.computable == sheet.total == 22


def test_a_ratio_carries_its_inputs_known_at_and_sources():
    r = gross_margin(Statements.from_store(us_store(), IID, ASOF))
    assert r.known_at == date(2026, 2, 15) and any("gross_profit" in k for k in r.inputs)
    assert all(src.startswith("fixture:") for src in r.sources)
    assert "40.0%" in r.text() and "known 2026-02-15" in r.text()


def test_a_missing_line_is_named_with_the_collector_that_would_fill_it_never_zero():
    store = us_store(annual=False, balance=False)  # quarterly flows only
    s = Statements.from_store(store, IID, ASOF)
    r = interest_cover(s)
    assert (
        r.value is None
        and "interest_expense" in r.missing
        and "sec_xbrl" in fillers("interest_expense")
    )
    assert "not computable" in r.text() and "filled by" in r.text()
    d = receivable_days(s)
    assert d.value is None and "receivables" in d.missing
    sheet = ratio_sheet(store, IID, ASOF)
    assert sheet.computable < sheet.total and "receivables" in sheet.fillers
    assert "would fill the gaps" in ratio_sheet_text(sheet)
    empty = ratio_sheet(FactStore(), "MYX:1155", ASOF)
    assert empty.computable == 0 and "0 of 22" in ratio_sheet_text(empty)


def test_point_in_time_hides_a_filing_after_the_asof_date():
    store = us_store()
    early = Statements.from_store(store, IID, date(2026, 1, 31))  # before the FY2025 10-K
    assert early.latest_annual("revenue").period_end == date(2024, 12, 31)
    assert cagr(early, "revenue").value is None, "one annual figure is not a growth rate"
    late = Statements.from_store(store, IID, ASOF)
    assert late.latest_annual("revenue").period_end == date(2025, 12, 31)


def test_ttm_prefers_four_quarters_and_falls_back_to_the_year():
    s = Statements.from_store(us_store(), IID, ASOF)
    v, inputs = s.ttm("revenue")
    assert v == Decimal(1000) and len(inputs) == 4
    v, inputs = s.ttm("sga")  # no quarterly SGA in the fixture
    assert v == Decimal(120) and list(inputs)[0].startswith("sga_fy@")


def test_capex_sign_is_normalised_once_and_roic_uses_the_supplied_rate_when_no_tax_lines():
    store = us_store(annual=False)
    store.add(fact("capex_fy", date(2025, 12, 31), date(2026, 2, 15), -50))
    store.add(fact("depreciation_fy", date(2025, 12, 31), date(2026, 2, 15), 40))
    s = Statements.from_store(store, IID, ASOF)
    assert capex_to_depreciation(s).value == Decimal("1.25"), (
        "FMP stores capex negative; the ratio layer takes the absolute value"
    )
    store2 = us_store(annual=False)  # quarterly operating income, no tax lines
    r = roic(Statements.from_store(store2, IID, ASOF), Decimal("0.24"))
    assert r.value is not None and "statutory" in r.note
    assert roic(Statements.from_store(store2, IID, ASOF), None).value is None


def test_growth_refuses_a_non_positive_base_and_yoy_needs_the_year_earlier_quarter():
    store = us_store(quarterly=False)
    assert yoy(Statements.from_store(store, IID, ASOF), "revenue").value is None
    store = FactStore()
    store.add(fact("revenue_fy", date(2024, 12, 31), date(2025, 2, 1), -10))
    store.add(fact("revenue_fy", date(2025, 12, 31), date(2026, 2, 1), 50))
    r = cagr(Statements.from_store(store, IID, ASOF), "revenue")
    assert r.value is None and "non-positive" in r.formula
    assert accrual_ratio(Statements.from_store(FactStore(), IID, ASOF)).value is None
    assert net_debt_to_ebitda(Statements.from_store(FactStore(), IID, ASOF)).missing
    assert dilution(Statements.from_store(FactStore(), IID, ASOF)).value is None
