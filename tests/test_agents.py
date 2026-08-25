"""P7/P8: the agent seam. What an agent may NOT do is the interesting half."""
import random
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agents.base import AgentContext, Finding
from agents.portfolio.agents import A12PortfolioRisk, A13Sizing, DRAWDOWN_TIERS
from agents.supervisor import A0Supervisor, Intent, PLAYBOOK
from agents.synthesis.agents import (
    A9Attribution, A10Thesis, A11RedTeam, Breaker, Stance, Thesis,
)
from core.guardrails.defaults import default_engine
from core.llm.tiers import ROUTING, Tier
from engines.attribution.regression import huber_fit
from engines.risk.concentration import Limits, Position
from engines.sizing.waterfall import Goal, Liability
from knowledge.retrieval.pipeline import Router

WINDOW = (date(2026, 8, 3), date(2026, 8, 4))
NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)

ALLOW = {
    "a0_supervisor": {"plan", "budget", "route", "refuse"},
    "a9_attribution": {"decompose", "candidate_causes", "long_horizon_decompose"},
    "a10_thesis": {"compose", "check_coverage"},
    "a11_red_team": {"find_disconfirming", "retrieve", "check_crowding"},
    "a12_portfolio_risk": {"concentration_check", "drawdown_state", "stress"},
    "a13_sizing": {"investable_capital", "risk_budget_cap", "kelly_cap", "lot_round"},
}


def ctx():
    return AgentContext(router=Router({}), engine=default_engine(ALLOW), now=NOW)


def fit(beta_mkt=1.1, beta_sec=0.5, n=250, seed=7):
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(n)]
    y = [beta_mkt * a + beta_sec * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


# -- A9 ---------------------------------------------------------------------

def test_market_wide_selloff_produces_no_cause_hunt():
    """A 9% fall that is 93% market beta. Statistically significant, and still
    nothing to explain about the company."""
    a9 = A9Attribution(ctx())
    out = a9.run("MYX:1155", WINDOW, realised_local=-0.090, event_market=-0.080,
                 event_sector=-0.020, event_styles={}, fx_return=0.0, fit=fit())
    head = out[0]
    assert head.kind == "decomposition"
    assert "market, not the company" in head.text
    assert not [f for f in out if f.kind == "candidate_cause"]


def test_an_ordinary_day_is_reported_as_ordinary():
    a9 = A9Attribution(ctx())
    out = a9.run("XNAS:NVDA", WINDOW, realised_local=-0.055, event_market=-0.050,
                 event_sector=-0.010, event_styles={}, fx_return=0.0, fit=fit())
    assert "no explanation is required" in out[0].text


def test_a_significant_idiosyncratic_move_reports_its_unexplained_share():
    a9 = A9Attribution(ctx())
    out = a9.run("XNAS:NVDA", WINDOW, realised_local=0.072, event_market=0.004,
                 event_sector=0.002, event_styles={}, fx_return=0.0, fit=fit())
    head = out[0]
    assert head.numbers["unexplained_share"] > 0.8
    assert "unexplained" in head.text


def test_narration_leads_with_the_number_not_the_verb():
    a9 = A9Attribution(ctx())
    out = a9.run("XNAS:NVDA", WINDOW, realised_local=-0.090, event_market=-0.080,
                 event_sector=-0.020, event_styles={}, fx_return=0.0, fit=fit())
    text = out[0].text
    # every component and its size is stated before any causal sentence begins
    assert text.index("market") < text.index("This was")
    assert text.startswith("XNAS:NVDA returned -9.0%")


def test_long_horizon_flags_a_return_that_came_from_re_rating():
    a9 = A9Attribution(ctx())
    out = a9.since_purchase("XNAS:NVDA", eps_start=1.0, eps_end=1.1,
                            multiple_start=15.0, multiple_end=40.0,
                            cumulative_shareholder_yield=0.02,
                            fx_start=4.0, fx_end=4.2, years=3.0)
    assert "multiple change" in out[0].text
    assert any("not repeatable" in c for c in out[0].caveats)


def test_attribution_agent_cannot_use_an_unlisted_tool():
    a9 = A9Attribution(ctx())
    with pytest.raises(Exception):
        a9._guard_tool("place_order")


# -- A10 --------------------------------------------------------------------

def evidence(*agents):
    return [Finding(a, "fact", f"{a} said something", numbers={}) for a in agents]


FULL = ("a1_fundamentals", "a2_valuation", "a5_catalyst_events", "a6_macro_regime")


def two_breakers():
    return [
        Breaker("gross margin recovers above 34%", "margin_pct > 0.34", "kb_filings",
                date(2027, 2, 1)),
        Breaker("net debt to EBITDA stays below 2.5x", "net_debt_ebitda < 2.5", "kb_filings",
                date(2027, 2, 1)),
    ]


def test_thesis_agent_may_never_retrieve():
    a10 = A10Thesis(ctx())
    with pytest.raises(PermissionError, match="may not retrieve"):
        a10.retrieve("kb_filings", "anything")


def test_no_breakers_means_no_stance_however_good_the_evidence():
    a10 = A10Thesis(ctx())
    a10.run("MYX:1155", evidence(*FULL), proposed_stance=Stance.ACCUMULATE, breakers=[])
    assert a10.last.stance is Stance.NO_VIEW
    assert not a10.last.is_actionable()


def test_one_breaker_is_not_two():
    a10 = A10Thesis(ctx())
    a10.run("MYX:1155", evidence(*FULL), proposed_stance=Stance.ACCUMULATE,
            breakers=two_breakers()[:1])
    assert a10.last.stance is Stance.NO_VIEW


def test_a_breaker_without_a_query_cannot_be_constructed():
    with pytest.raises(ValueError, match="no executable query"):
        Breaker("management seems confident", "  ", "kb_filings")


def test_missing_fundamentals_blocks_accumulate_but_not_a_hold():
    a10 = A10Thesis(ctx())
    partial = evidence("a2_valuation", "a5_catalyst_events", "a6_macro_regime")
    a10.run("MYX:1155", partial, proposed_stance=Stance.ACCUMULATE, breakers=two_breakers())
    assert a10.last.stance is Stance.NO_VIEW
    assert "a1_fundamentals" in a10.last.gaps


def test_confidence_falls_with_gaps_and_never_rises_with_findings():
    a10 = A10Thesis(ctx())
    a10.run("MYX:1155", evidence(*FULL), breakers=two_breakers())
    complete = a10.last.confidence
    a10.run("MYX:1155", evidence("a2_valuation"), breakers=two_breakers())
    assert a10.last.confidence < complete


def test_unexplained_recent_move_stages_the_entry():
    a10 = A10Thesis(ctx())
    findings = evidence(*FULL) + [
        Finding("a9_attribution", "decomposition", "moved for reasons unknown",
                numbers={"unexplained_share": 0.92})
    ]
    out = a10.run("MYX:1155", findings, proposed_stance=Stance.ACCUMULATE,
                  breakers=two_breakers())
    assert any("staged" in c for c in out[0].caveats)


# -- A11 --------------------------------------------------------------------

def thesis(stance=Stance.ACCUMULATE, gaps=(), breakers=None, valuation=(Decimal(4), Decimal(6)),
           confidence=0.6, supporting=None):
    return Thesis(
        instrument_id="MYX:1155", stance=stance, horizon_months=12,
        in_one_sentence="x", what_must_be_true=[],
        breakers=list(breakers if breakers is not None else two_breakers()),
        valuation_range=valuation, key_uncertainties=[], gaps=list(gaps),
        supporting=list(supporting or evidence(*FULL)), confidence=confidence,
    )


def test_accumulating_with_no_valuation_range_is_fatal():
    a11 = A11RedTeam(ctx())
    out = a11.run(thesis(valuation=None))
    assert a11.verdict(out) == "thesis_rejected"


def test_three_gaps_is_fatal():
    a11 = A11RedTeam(ctx())
    out = a11.run(thesis(gaps=("a1_fundamentals", "a2_valuation", "a6_macro_regime")))
    assert a11.verdict(out) == "thesis_rejected"


def test_red_team_excludes_the_sources_the_bull_case_used():
    a11 = A11RedTeam(ctx())
    from core.contracts.answer import Citation, TrustTier
    supporting = [Finding("a1_fundamentals", "fact", "x", citations=[
        Citation(source="annual_report_2025", chunk_id="c1", quoted_span="s",
                 trust=TrustTier.FILINGS, as_of=NOW)])]
    a11.run(thesis(supporting=supporting))
    assert "annual_report_2025" in a11.excluded


def test_unearned_confidence_is_challenged():
    a11 = A11RedTeam(ctx())
    out = a11.run(thesis(gaps=("a6_macro_regime",), confidence=0.85))
    assert any("not earned" in f.text for f in out)


def test_a_complete_thesis_still_draws_the_standing_challenges():
    a11 = A11RedTeam(ctx())
    out = a11.run(thesis())
    assert out, "the red team is never silent"


# -- A0 ---------------------------------------------------------------------

def test_supervisor_refuses_to_place_an_order():
    a0 = A0Supervisor(ctx())
    p = a0.plan("buy 1000 shares of tenaga for me")
    assert not p.allowed
    assert "cannot place orders" in p.refusal.reason


def test_supervisor_refuses_a_guarantee():
    a0 = A0Supervisor(ctx())
    assert not a0.plan("find me a guaranteed stock that can't lose").allowed


def test_supervisor_refuses_a_point_price_forecast():
    a0 = A0Supervisor(ctx())
    p = a0.plan("what will nvidia be worth in december")
    assert not p.allowed
    assert "false precision" in p.refusal.reason


def test_why_it_moved_without_an_instrument_is_a_refusal_not_a_guess():
    a0 = A0Supervisor(ctx())
    p = a0.plan("why did it drop")
    assert not p.allowed
    assert "instrument" in p.refusal.reason


def test_routing_picks_the_documented_playbook():
    a0 = A0Supervisor(ctx())
    p = a0.plan("why did maybank fall today", instrument_ids=("MYX:1155",))
    assert p.intent is Intent.WHY_IT_MOVED
    assert p.agents == PLAYBOOK[Intent.WHY_IT_MOVED]


def test_supervisor_may_never_retrieve():
    a0 = A0Supervisor(ctx())
    with pytest.raises(PermissionError, match="may not retrieve"):
        a0.retrieve("kb_filings", "q")


def test_a_budget_too_small_for_an_honest_answer_is_refused_not_cheapened():
    a0 = A0Supervisor(ctx())
    p = a0.plan("should i buy nvidia", budget_myr=Decimal("0.05"),
                instrument_ids=("XNAS:NVDA",))
    assert not p.allowed
    assert "narrower question" in p.refusal.what_would_help


def test_a_workable_budget_trims_rather_than_refusing():
    a0 = A0Supervisor(ctx())
    full = a0.plan("should i buy nvidia", instrument_ids=("XNAS:NVDA",))
    p = a0.plan("should i buy nvidia",
                budget_myr=full.estimated_cost.amount * Decimal("0.7"),
                instrument_ids=("XNAS:NVDA",))
    assert p.allowed
    assert len(p.agents) < len(full.agents)
    # the floor survives the trim: no thesis without a red team, ever
    for essential in ("a1_fundamentals", "a2_valuation", "a10_thesis", "a11_red_team"):
        assert essential in p.agents


def test_the_floor_itself_is_never_trimmed_away():
    """Trimming stops at the point where the answer would stop being honest,
    and refuses instead. docs/01 section 4.3."""
    a0 = A0Supervisor(ctx())
    floor_only = a0.plan("should i buy nvidia", budget_myr=Decimal("1.48"),
                         instrument_ids=("XNAS:NVDA",))
    assert floor_only.allowed
    assert set(floor_only.agents) == {"a1_fundamentals", "a2_valuation",
                                      "a10_thesis", "a11_red_team"}
    assert not a0.plan("should i buy nvidia", budget_myr=Decimal("1.40"),
                       instrument_ids=("XNAS:NVDA",)).allowed


def test_cost_estimate_is_in_ringgit():
    a0 = A0Supervisor(ctx())
    p = a0.plan("why did maybank fall", instrument_ids=("MYX:1155",))
    assert p.estimated_cost.currency == "MYR"
    assert p.estimated_cost.amount > 0


# -- A12 --------------------------------------------------------------------

def banks(n=10, weight=0.09):
    return [Position(f"BANK{i}", weight, "financials", "MY", "MYR", risk_to_stop=0.004)
            for i in range(n)]


def test_ten_correlated_banks_pass_hhi_and_fail_effective_bets():
    a12 = A12PortfolioRisk(ctx())
    corr = [[1.0 if i == j else 0.85 for j in range(10)] for i in range(10)]
    out = a12.run(banks(), corr=corr, limits=Limits())
    head = next(f for f in out if f.kind == "concentration")
    assert head.numbers["hhi"] < Limits().hhi
    assert head.numbers["effective_bets"] < Limits().min_effective_bets
    assert any("collapse into" in c for c in head.caveats)


def test_every_breach_is_reported_not_just_the_first():
    a12 = A12PortfolioRisk(ctx())
    positions = [Position("BIG", 0.30, "financials", "MY", "MYR", risk_to_stop=0.05),
                 Position("B", 0.30, "financials", "MY", "MYR", risk_to_stop=0.05),
                 Position("C", 0.40, "financials", "MY", "MYR", risk_to_stop=0.05)]
    out = a12.run(positions, corr=None, limits=Limits())
    assert len([f for f in out if f.kind == "breach"]) >= 3


def test_drawdown_tiers_are_mechanical_and_monotonic():
    a12 = A12PortfolioRisk(ctx())
    prev = 1.01
    for dd, scalar, _ in DRAWDOWN_TIERS:
        out = a12.drawdown_state(Decimal(str(1 - dd)) * 100, Decimal("100"))
        got = out[0].numbers["risk_scalar"]
        assert got <= prev
        prev = got
    assert prev == 0.0


def test_deep_drawdown_is_not_overridable_by_conviction():
    a12 = A12PortfolioRisk(ctx())
    out = a12.drawdown_state(Decimal("70"), Decimal("100"))
    assert out[0].numbers["risk_scalar"] == 0.0
    assert any("not overridable" in c for c in out[0].caveats)


def test_stress_admits_it_is_optimistic():
    a12 = A12PortfolioRisk(ctx())
    out = a12.stress(banks(), {"2008 financials": -0.45})
    assert any("correlations rise towards 1" in c for c in out[0].caveats)


def test_empty_book_is_not_a_breach():
    a12 = A12PortfolioRisk(ctx())
    out = a12.run([], corr=None)
    assert out[0].kind == "empty_book"


# -- A13 --------------------------------------------------------------------

def test_emergency_floor_can_leave_nothing_investable():
    a13 = A13Sizing(ctx())
    w, out = a13.investable_capital(Decimal("30000"), Decimal("5000"))
    assert w.investable == 0
    assert any("zero" in c for c in out[0].caveats)


def test_near_term_goals_are_locked_before_investing():
    a13 = A13Sizing(ctx())
    w, _ = a13.investable_capital(
        Decimal("100000"), Decimal("5000"),
        goals=[Goal(name="deposit", amount=Decimal("40000"), months_away=12)])
    assert w.investable == Decimal("30000")


def test_expensive_debt_is_repaid_before_equities():
    a13 = A13Sizing(ctx())
    w, _ = a13.investable_capital(
        Decimal("100000"), Decimal("2000"),
        liabilities=[Liability(name="card", balance=Decimal("20000"),
                               annual_rate=Decimal("0.17"))])
    assert w.investable == Decimal("68000")


def test_an_implausible_edge_is_refused_and_named():
    a13 = A13Sizing(ctx())
    caps, out = a13.caps(
        portfolio_value=Decimal("100000"), stop_distance_frac=Decimal("0.12"),
        adv_20d=Decimal("2000000"), round_trip_cost_at=lambda v: v * Decimal("0.005"),
        win_rate=0.75, payoff=3.0, n_trades=200)
    assert caps.kelly is None
    assert any("Kelly cap refused" in c for c in out[0].caveats)


def test_kelly_needs_a_track_record_before_it_says_anything():
    a13 = A13Sizing(ctx())
    caps, out = a13.caps(
        portfolio_value=Decimal("100000"), stop_distance_frac=Decimal("0.12"),
        adv_20d=Decimal("2000000"), round_trip_cost_at=lambda v: v * Decimal("0.005"),
        win_rate=0.54, payoff=1.5, n_trades=12)
    assert caps.kelly is None


def test_the_binding_cap_is_named_because_which_one_bound_is_the_lesson():
    a13 = A13Sizing(ctx())
    _, out = a13.caps(
        portfolio_value=Decimal("100000"), stop_distance_frac=Decimal("0.12"),
        adv_20d=Decimal("2000000"), round_trip_cost_at=lambda v: v * Decimal("0.005"))
    assert "binding cap is" in out[0].text
