"""Earnings-quality scores: each index by hand, and n of N when inputs are missing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.market.pointintime import FactStore
from engines.fundamentals.quality import (
    accruals,
    altman_z,
    beneish_m_score,
    piotroski_f_score,
    quality_report,
    quality_text,
)
from engines.fundamentals.ratios import Statements
from tests.statement_fixtures import ASOF, IID, fact, us_store


def near(a, b, tol="0.005"):
    return a is not None and abs(a - Decimal(b)) <= Decimal(tol)


def test_beneish_indices_and_score_by_hand():
    sc = beneish_m_score(Statements.from_store(us_store(), IID, ASOF))
    c = sc.components
    assert near(c["DSRI"], "1.125") and near(c["GMI"], "0.9444") and near(c["AQI"], "1.0")
    assert near(c["SGI"], "1.1111") and near(c["DEPI"], "1.0218") and near(c["SGAI"], "0.9818")
    assert near(c["LVGI"], "0.9") and near(c["TATA"], "-0.015")
    assert sc.computable == sc.needed == 8 and near(sc.value, "-2.327", "0.01")
    assert sc.verdict.startswith("below") and "-1.78" in sc.threshold


def test_beneish_is_refused_below_eight_indices_and_names_what_is_missing():
    store = us_store()
    # take away the receivables history: DSRI cannot compute
    s = Statements.from_store(FactStore(), IID, ASOF)
    sc = beneish_m_score(s)
    assert sc.value is None and sc.computable == 0 and "revenue_fy" in sc.missing
    partial = FactStore()
    for f in store._facts.values():  # every fact except receivables
        for x in f:
            if not x.concept.startswith("receivables"):
                partial.add(x)
    sc = beneish_m_score(Statements.from_store(partial, IID, ASOF))
    assert sc.value is None and sc.computable == 7 and sc.missing == ("receivables_fy",)
    assert "7 of 8" in sc.verdict and "uncalibrated" in sc.caveats[0]
    assert "7 of 8 inputs computable" in sc.text() and "sec_xbrl" in sc.text()


def test_piotroski_counts_eight_of_nine_and_reports_over_computable_signals():
    sc = piotroski_f_score(Statements.from_store(us_store(), IID, ASOF))
    assert sc.value == Decimal(8) and sc.computable == 9
    assert sc.components["asset_turnover_improving"] == Decimal(0), (
        "0.5 against 0.5 is not an improvement"
    )
    assert sc.verdict == "8 of 9 computable signals"
    thin = FactStore()
    for c, v in (
        ("net_income_fy", 150),
        ("total_assets_fy", 2000),
        ("cash_from_operations_fy", 180),
    ):
        thin.add(fact(c, date(2025, 12, 31), date(2026, 2, 15), v))
    for c, v in (("net_income_fy", 130), ("total_assets_fy", 1800)):
        thin.add(fact(c, date(2024, 12, 31), date(2025, 2, 15), v))
    sc = piotroski_f_score(Statements.from_store(thin, IID, ASOF))
    assert sc.computable == 4 and sc.value == Decimal(4) and "not comparable" in sc.caveats[0]
    assert "4 of 4 computable signals (5 not computable)" == sc.verdict


def test_altman_zones_and_the_bank_refusal():
    s = Statements.from_store(us_store(), IID, ASOF)
    sc = altman_z(s, Decimal(3000))
    assert near(sc.value, "3.29", "0.01") and sc.verdict == "safe zone"
    assert altman_z(s, Decimal(100)).verdict in ("grey zone", "distress zone")
    no_cap = altman_z(s, None)
    assert no_cap.value is None and "market_cap" in no_cap.missing and "4 of 5" in no_cap.verdict
    bank = altman_z(s, Decimal(3000), archetype="bank")
    assert bank.value is None and "not applicable to a bank" in bank.verdict


def test_accruals_reproduce_the_thirty_percent_rule():
    sc = accruals(Statements.from_store(us_store(), IID, ASOF))
    assert sc.verdict == "cash from operations covers net income" and near(sc.value, "-0.015")
    flagged = FactStore()
    flagged.add(fact("net_income_fy", date(2025, 12, 31), date(2026, 2, 15), 100))
    flagged.add(fact("cash_from_operations_fy", date(2025, 12, 31), date(2026, 2, 15), 50))
    flagged.add(fact("total_assets", date(2025, 12, 31), date(2026, 2, 15), 1000))
    sc = accruals(Statements.from_store(flagged, IID, ASOF))
    assert sc.verdict.startswith("flag") and "50%" in sc.verdict
    assert accruals(Statements.from_store(FactStore(), IID, ASOF)).verdict == "not computable"


def test_the_report_runs_on_an_empty_store_and_says_so():
    scores = quality_report(Statements.from_store(FactStore(), "MYX:1155", ASOF), None, None)
    assert [sc.name for sc in scores] == ["accruals", "beneish_m", "piotroski_f", "altman_z"]
    assert all(sc.value is None for sc in scores)
    text = quality_text(scores)
    assert "0 of 8 indices" in text and "0 of 9 signals" in text
