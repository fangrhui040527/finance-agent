"""The bear case never out-grows the base, and the reverse DCF values the years after ten and says when its answer is off the scale.

Each test fails on the code before 2026-10-08:

- facts-valuation-6: default_scenarios set bear growth to half the revenue CAGR with
  no cap, while base and bull were capped at 25% and 30%. Above a 50% CAGR the bear
  grew faster than the base, and above 60% faster than the bull. At NVDA's 67.1% the
  bear grew 33.6% against 25% and 30% and became the top of the range.
- facts-valuation-7: implied_growth summed ten years of earnings with nothing after.
  NVDA at 30.21x and a 15.33% discount read 37.8% where a 4% terminal value gives
  22.2%. It also returned its own 60% (or -20%) bound with no signal whenever the
  answer lay outside it, and comps and the valuation agent printed that bound as the
  growth the price requires.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agents.base import AgentContext
from agents.evidence.agents import A2Valuation
from core.guardrails.defaults import default_engine
from core.market.pointintime import FactStore
from engines.fundamentals.ratios import Statements, cagr
from engines.valuation import comps, dcf
from engines.valuation.cost_of_capital import load_table
from knowledge.facts import FactBook
from knowledge.retrieval.pipeline import Router
from tests.statement_fixtures import (
    ANNUAL_T,
    ANNUAL_T1,
    ASOF,
    FILED_T,
    FILED_T1,
    IID,
    T1,
    T,
    fact,
    us_store,
)
from tests.test_dcf import TABLE_YAML, _coc

ONE = Decimal(1)
NVDA_PE = Decimal("30.21")
NVDA_DISCOUNT = Decimal("0.1533")
US_TERMINAL = Decimal("0.04")


@pytest.fixture
def table(tmp_path):
    p = tmp_path / "coc.yaml"
    p.write_text(TABLE_YAML, encoding="utf-8")
    return load_table(p)


def store_growing_at(g: Decimal) -> FactStore:
    """The statement fixture with the prior year's revenue set so the annual revenue CAGR
    is exactly `g` (the two year-ends are 365 days apart) and the prior operating margin
    kept at the fixture's 170/900, so only growth moves between cases."""
    store = us_store(annual=False)
    for c, v in ANNUAL_T.items():
        store.add(fact(f"{c}_fy", T, FILED_T, v))
    prior_revenue = Decimal(ANNUAL_T["revenue"]) / (ONE + g)
    prior = {
        **ANNUAL_T1,
        "revenue": prior_revenue,
        "operating_income": prior_revenue * Decimal(170) / Decimal(900),
    }
    for c, v in prior.items():
        store.add(fact(f"{c}_fy", T1, FILED_T1, v))
    return store


def closed_form_price(g: float, discount: float, terminal: float, years: int = 10) -> float:
    """Price per unit of earnings: a geometric sum for the explicit years plus a Gordon
    value on the year after, written out independently of the engine."""
    q = (1 + g) / (1 + discount)
    return q * (1 - q**years) / (1 - q) + q**years * (1 + terminal) / (discount - terminal)


@pytest.mark.parametrize(
    "growth",
    ["-0.30", "0", "0.05", "0.11", "0.40", "0.50", "0.55", "0.60", "0.671", "0.90", "1.50"],
)
def test_bear_grows_no_faster_than_base_and_base_no_faster_than_bull_at_any_cagr(table, growth):
    g = Decimal(growth)
    s = Statements.from_store(store_growing_at(g), IID, ASOF)
    assert cagr(s, "revenue").value == pytest.approx(g, abs=Decimal("1e-12"))
    coc = _coc()
    scenarios, reasons = dcf.default_scenarios(s, coc, table, "US")
    assert not reasons
    bear, base, bull = (sc.assumptions for sc in scenarios)
    assert bear.growth_first_year <= base.growth_first_year <= bull.growth_first_year, (
        f"CAGR {g}: bear {bear.growth_first_year:.3f}, base {base.growth_first_year:.3f}, "
        f"bull {bull.growth_first_year:.3f}"
    )
    assert bear.growth_first_year >= bear.terminal_growth, "no scenario fades upward"
    assert bear.margin <= base.margin <= bull.margin
    vr = dcf.scenario_range(s, coc, scenarios, table, "US", "USD")
    assert vr.refused is None and set(vr.results) == {"bear", "base", "bull"}
    value = {n: (r.equity if r.equity is not None else r.ev) for n, r in vr.results.items()}
    assert value["bear"] <= value["base"] <= value["bull"]
    assert (vr.low, vr.high) == (value["bear"], value["bull"]), "the range runs bear to bull"


def test_reverse_dcf_counts_the_years_after_ten_at_the_scenario_dcfs_terminal_growth(table):
    s = Statements.from_store(us_store(), IID, ASOF)
    coc = _coc()
    terminal = dcf.terminal_growth_for(coc, table, "US")
    scenarios, _ = dcf.default_scenarios(s, coc, table, "US")
    assert terminal == US_TERMINAL == scenarios[1].assumptions.terminal_growth
    g = dcf.implied_growth(NVDA_PE, ONE, NVDA_DISCOUNT, US_TERMINAL)
    assert g is not None and f"{g:.1%}" == "22.2%", "not the 37.8% of ten years and nothing after"
    assert closed_form_price(float(g), 0.1533, 0.04) == pytest.approx(30.21, abs=1e-6)
    pe_120 = dcf.implied_growth(Decimal(120), ONE, NVDA_DISCOUNT, US_TERMINAL)
    assert pe_120 is not None and dcf.IMPLIED_GROWTH_FLOOR < pe_120 < dcf.IMPLIED_GROWTH_CEILING
    assert closed_form_price(float(pe_120), 0.1533, 0.04) == pytest.approx(120, abs=1e-6)
    with pytest.raises(ValueError, match="perpetuity"):
        dcf.implied_growth(NVDA_PE, ONE, US_TERMINAL, US_TERMINAL)


def test_a_price_beyond_the_solvers_range_is_none_and_the_text_names_the_end(tmp_path, registry):
    assert dcf.implied_growth(Decimal(400), ONE, NVDA_DISCOUNT, US_TERMINAL) is None
    assert dcf.implied_growth(Decimal(2), ONE, NVDA_DISCOUNT, US_TERMINAL) is None
    above = dcf.beyond_solver_range(Decimal(400), ONE, NVDA_DISCOUNT, US_TERMINAL)
    below = dcf.beyond_solver_range(Decimal(2), ONE, NVDA_DISCOUNT, US_TERMINAL)
    assert (above, below) == ("above 60% a year", "below -20% a year")

    with FactBook(tmp_path / "facts.db") as book:
        dear = comps.three_contexts(
            book, IID, set(), "pe_ttm", ASOF, Decimal(400), ONE, NVDA_DISCOUNT, US_TERMINAL
        )
        fair = comps.three_contexts(
            book, IID, set(), "pe_ttm", ASOF, NVDA_PE, ONE, NVDA_DISCOUNT, US_TERMINAL
        )
        a2 = A2Valuation(
            AgentContext(
                router=Router({}),
                engine=default_engine(registry.allowlist()),
                now=datetime(2026, 10, 8, tzinfo=UTC),
            )
        )
        tool = a2.peer_multiples(
            book, IID, set(), "pe_ttm", ASOF, Decimal(400), ONE, NVDA_DISCOUNT, US_TERMINAL
        )[0]
    assert dear.implied_growth is None
    assert (
        "growth the price requires (reverse DCF, 10 years, then 4.0% a year in perpetuity): "
        "beyond the solver's range (above 60% a year)"
    ) in dear.text()
    assert "60.0%" not in dear.text()
    assert (
        "growth the price requires (reverse DCF, 10 years, then 4.0% a year in perpetuity): "
        "22.2% a year"
    ) in fair.text()
    assert "beyond the solver's range (above 60% a year)" in tool.text
    assert "implied_growth" not in tool.numbers

    agent = a2.reverse_dcf(400.0, 1.0, 0.1533, 0.04)[0]
    assert "beyond the solver's range (above 60% a year)" in agent.text
    assert "implied_growth" not in agent.numbers and "60.0%" not in agent.text
