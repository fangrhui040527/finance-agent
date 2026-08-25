"""A1-A8, the evidence layer.

These eight agents are where every claim originates. What each may NOT do is
tested here alongside what it does, because the prohibitions in docs/02 section 5
are the part that decays silently.
"""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agents.base import Agent, AgentContext
from agents.evidence.agents import (
    A1Fundamentals, A2Valuation, A3PriceTechnical, A4NewsNarrative,
    A5CatalystEvents, A6MacroRegime, A7SectorTechnology, A8OwnershipFlow,
)
from agents.synthesis.agents import A10Thesis
from core.guardrails.defaults import default_engine
from core.market.pointintime import Fact, FactStore
from core.market.prices import Bar, PriceSeries
from core.registry.loader import load
from engines.events.taxonomy import BaseRateTable, CapBand, Event, EventType, Observation
from knowledge.graph.entity_graph import (
    Edge, EdgeKind, EntityGraph, Node, NodeKind, PathRequired,
)
from knowledge.retrieval.pipeline import Router
from markets.contract import AccountingStandard

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
ASOF = date(2026, 8, 25)

ALLOW = {
    "a1_fundamentals": {"get_statement", "dupont", "accrual_ratio", "restatement_diff"},
    "a2_valuation": {"multiple_vs_history", "reverse_dcf", "peer_multiples"},
    "a3_price_technical": {"ohlcv", "atr", "drawdown", "base_rate"},
    "a4_news_narrative": {"retrieve", "search_news", "extract_features"},
    "a5_catalyst_events": {"events_in_window", "base_rate", "blackout_check"},
    "a6_macro_regime": {"series", "regime_label", "country_stress"},
    "a7_sector_technology": {"traverse", "peers", "sector_primer"},
    "a8_ownership_flow": {"insider_activity", "ownership_change", "short_interest_trend"},
}


def ctx():
    return AgentContext(router=Router({}), engine=default_engine(ALLOW), now=NOW)


# -- the contract that binds all sixteen -------------------------------------

def test_every_agent_id_matches_the_registry():
    """The registry is the single source of truth for agent identity.

    This test exists because six ids drifted from it: the classes said
    a5_events while the registry said a5_catalyst_events. Nothing crashed.
    A10 simply reported two evidence gaps that were in fact covered, docked
    its own confidence by 0.24 on every thesis, and the red team raised a
    coverage challenge on every thesis forever - which is the same as never
    raising one at all.
    """
    import agents.evidence.agents as ev, agents.learning.reflection as rf
    import agents.learning.teacher as te, agents.portfolio.agents as po
    import agents.supervisor as su, agents.synthesis.agents as sy

    reg = load("agents/registry.yaml")
    seen = {}
    for mod in (ev, sy, po, rf, te, su):
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, Agent) and obj is not Agent:
                seen[obj.__name__] = obj.agent_id

    unregistered = {n: a for n, a in seen.items() if a not in reg.agents}
    assert not unregistered, f"agent ids not in the registry: {unregistered}"
    assert len(seen) == len(reg.agents) == 16


def test_every_agent_only_declares_tools_the_registry_grants_it():
    reg = load("agents/registry.yaml")
    import agents.evidence.agents as ev, agents.learning.reflection as rf
    import agents.learning.teacher as te, agents.portfolio.agents as po
    import agents.supervisor as su, agents.synthesis.agents as sy

    for mod in (ev, sy, po, rf, te, su):
        for name in dir(mod):
            obj = getattr(mod, name)
            if not (isinstance(obj, type) and issubclass(obj, Agent) and obj is not Agent):
                continue
            extra = set(obj.tools) - set(reg.agent(obj.agent_id).tools)
            assert not extra, f"{obj.agent_id} claims unregistered tools {sorted(extra)}"


def test_the_thesis_agents_required_evidence_is_all_real_agents():
    reg = load("agents/registry.yaml")
    for required in A10Thesis.REQUIRED_EVIDENCE:
        assert required in reg.agents, (
            f"A10 waits for {required!r}, which no agent emits: a permanent phantom gap"
        )


# -- A1 fundamentals ---------------------------------------------------------

def fact(concept, value, period_end=date(2026, 6, 30), known_at=date(2026, 8, 14),
         restated=False):
    return Fact("MYX:1155", concept, period_end, known_at, Decimal(str(value)), "MYR",
                AccountingStandard.IFRS, "doc:q2", is_restatement=restated)


def store(*facts):
    s = FactStore()
    for f in facts:
        s.add(f)
    return s


def test_a_figure_not_yet_public_is_reported_as_unavailable_not_estimated():
    a1 = A1Fundamentals(ctx(), store(fact("net_income", 500, known_at=date(2026, 9, 30))))
    out = a1.run("MYX:1155", ["net_income"], ASOF)
    assert out[0].kind == "unavailable"
    assert "not yet public" in out[0].text


def test_a_known_figure_reports_when_it_became_knowable():
    a1 = A1Fundamentals(ctx(), store(fact("net_income", 500)))
    out = a1.run("MYX:1155", ["net_income"], ASOF)
    assert out[0].kind == "line_item"
    assert "first knowable 2026-08-14" in out[0].text


def test_a_restated_figure_carries_the_restatement_caveat():
    a1 = A1Fundamentals(ctx(), store(fact("revenue", 900, restated=True)))
    out = a1.run("MYX:1155", ["revenue"], ASOF)
    assert any("restated" in c for c in out[0].caveats)


def test_earnings_far_above_cash_flow_is_flagged():
    a1 = A1Fundamentals(ctx(), store(fact("net_income", 500),
                                     fact("cash_from_operations", 120)))
    out = a1.earnings_quality("MYX:1155", ASOF)
    assert "exceeds operating cash flow" in out[0].text
    assert out[0].numbers["accrual_gap"] == pytest.approx(380.0)


def test_healthy_cash_conversion_is_not_flagged():
    a1 = A1Fundamentals(ctx(), store(fact("net_income", 500),
                                     fact("cash_from_operations", 610)))
    out = a1.earnings_quality("MYX:1155", ASOF)
    assert "broadly supported" in out[0].text
    assert not out[0].caveats


def test_the_quality_check_says_so_when_it_cannot_run():
    a1 = A1Fundamentals(ctx(), store(fact("net_income", 500)))
    out = a1.earnings_quality("MYX:1155", ASOF)
    assert "unavailable" in out[0].text


# -- A2 valuation ------------------------------------------------------------

def test_a_bank_is_valued_on_price_to_book_and_dcf_is_ruled_out():
    a2 = A2Valuation(ctx())
    out = a2.run("MYX:1155", "bank", 1.15, [0.9, 1.0, 1.1, 1.2, 1.4])
    assert "P/B vs ROE" in out[0].text
    assert any("DCF is not applicable" in c for c in out[0].caveats)


def test_a_pre_profit_company_gets_reverse_dcf_only():
    a2 = A2Valuation(ctx())
    out = a2.run("X", "pre_profit", 8.0, [5.0, 8.0, 12.0])
    assert "reverse-DCF only" in out[0].text
    assert any("forward multiple" in c for c in out[0].caveats)


def test_the_current_multiple_is_placed_in_its_own_history():
    a2 = A2Valuation(ctx())
    out = a2.run("X", "cyclical", 14.0, [8.0, 10.0, 12.0, 16.0, 20.0])
    assert out[0].numbers["percentile"] == pytest.approx(0.6)


def test_no_history_means_no_context_rather_than_a_guess():
    a2 = A2Valuation(ctx())
    out = a2.run("X", "bank", 1.15, [])
    assert "no multiple history" in out[0].text


def test_reverse_dcf_states_what_the_price_requires_not_what_it_is_worth():
    a2 = A2Valuation(ctx())
    out = a2.reverse_dcf(price=100.0, current_earnings=5.0, discount=0.10)
    assert "already requiring" in out[0].text
    assert any("not an estimate of value" in c for c in out[0].caveats)


def test_a_higher_price_implies_a_higher_required_growth_rate():
    a2 = A2Valuation(ctx())
    cheap = a2.reverse_dcf(60.0, 5.0, 0.10)[0].numbers["implied_growth"]
    dear = a2.reverse_dcf(140.0, 5.0, 0.10)[0].numbers["implied_growth"]
    assert dear > cheap


# -- A3 price ----------------------------------------------------------------

def series(n=60, start=10.0, seed=4):
    rng = random.Random(seed)
    bars, px, d = [], start, date(2026, 5, 1)
    for i in range(n):
        px *= 1 + rng.gauss(0, 0.01)
        bars.append(Bar(d + timedelta(days=i), px, px * 1.01, px * 0.99, px, 100_000))
    return PriceSeries("MYX:1155", bars)


def test_too_little_history_produces_no_trend_claim():
    assert "insufficient price history" in A3PriceTechnical(ctx()).run(series(10))[0].text


def test_price_context_is_descriptive_and_carries_its_numbers():
    out = A3PriceTechnical(ctx()).run(series())
    assert out[0].kind == "price_context"
    for key in ("close", "atr20", "drawdown", "adv20"):
        assert key in out[0].numbers


def test_short_horizon_signals_are_suppressed_near_earnings():
    out = A3PriceTechnical(ctx()).run(series(), sessions_to_earnings=1)
    blackout = next(f for f in out if f.kind == "blackout")
    assert "suppressed" in blackout.text
    assert any("event risk dominates" in c for c in blackout.caveats)


def test_the_blackout_is_symmetric_around_the_announcement():
    a3 = A3PriceTechnical(ctx())
    assert any(f.kind == "blackout" for f in a3.run(series(), sessions_to_earnings=-2))
    assert not any(f.kind == "blackout" for f in a3.run(series(), sessions_to_earnings=9))


# -- A5 events ---------------------------------------------------------------

def event(eid="e1", etype=EventType.EARNINGS_RESULT, day=15, doc="d1"):
    return Event(eid, "MYX:1155", etype, datetime(2026, 8, day, tzinfo=timezone.utc),
                 market="XKLS", cap_band=CapBand.LARGE, source_doc_id=doc)


def table_with(n):
    t = BaseRateTable()
    rng = random.Random(2)
    for i in range(n):
        t.observe(Observation(event(f"h{i}"), rng.gauss(0.004, 0.01),
                              rng.gauss(0.02, 0.02), rng.gauss(0.01, 0.03)))
    return t


def test_events_outside_the_window_are_not_returned():
    a5 = A5CatalystEvents(ctx(), [event(day=1)], BaseRateTable())
    out = a5.run("MYX:1155", datetime(2026, 8, 10, tzinfo=timezone.utc),
                 datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert out == []


def test_an_event_is_reported_with_its_historical_base_rate():
    a5 = A5CatalystEvents(ctx(), [event()], table_with(60))
    out = a5.run("MYX:1155", datetime(2026, 8, 1, tzinfo=timezone.utc),
                 datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert "historically worth a median" in out[0].text
    assert "n=60" in out[0].text


def test_an_unconfirmed_event_cannot_be_cited_as_a_cause():
    a5 = A5CatalystEvents(ctx(), [event(doc=None)], BaseRateTable())
    out = a5.run("MYX:1155", datetime(2026, 8, 1, tzinfo=timezone.utc),
                 datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert any("cannot be cited" in c for c in out[0].caveats)


def test_a_thin_base_rate_admits_it_is_thin():
    a5 = A5CatalystEvents(ctx(), [event()], table_with(6))
    out = a5.run("MYX:1155", datetime(2026, 8, 1, tzinfo=timezone.utc),
                 datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert any("thin sample" in c for c in out[0].caveats)


# -- A6 macro ----------------------------------------------------------------

def test_a_regime_needs_enough_history_to_measure():
    out = A6MacroRegime(ctx()).run([0.001] * 10)
    assert "insufficient history" in out[0].text


def test_a_falling_volatile_market_reads_as_risk_off():
    rng = random.Random(9)
    returns = [rng.gauss(-0.002, 0.020) for _ in range(120)]
    out = A6MacroRegime(ctx()).run(returns)
    assert out[0].numbers["drift"] < 0
    assert "risk_off" in out[0].text


def test_the_regime_label_is_described_as_a_rule_not_a_forecast():
    rng = random.Random(9)
    out = A6MacroRegime(ctx()).run([rng.gauss(0.0006, 0.006) for _ in range(120)])
    assert any("not a forecast" in c for c in out[0].caveats)


# -- A7 sector ---------------------------------------------------------------

def graph():
    g = EntityGraph()
    for nid, kind, lbl in [("EV:x", NodeKind.EVENT, "Shock"),
                           ("SEC:s", NodeKind.SECTOR, "Shipping"),
                           ("CO:a", NodeKind.COMPANY, "Alpha")]:
        g.add_node(Node(nid, kind, lbl))
    g.add_edge(Edge("EV:x", "SEC:s", EdgeKind.AFFECTS, 1.0, "doc:1"))
    g.add_edge(Edge("SEC:s", "CO:a", EdgeKind.CLASSIFIED_IN, 1.0, "doc:2"))
    return g


def test_without_a_graph_the_agent_says_so_rather_than_inferring():
    out = A7SectorTechnology(ctx()).run("EV:x", {"CO:a"})
    assert "no graph is loaded" in out[0].text


def test_exposure_is_reported_with_its_path_and_its_strength():
    out = A7SectorTechnology(ctx(), graph()).run("EV:x", {"CO:a"})
    assert "--affects-->" in out[0].text
    assert out[0].numbers["hops"] == 2.0
    assert any("indirect" in c for c in out[0].caveats)


def test_an_unexposed_holding_produces_no_finding_at_all():
    assert A7SectorTechnology(ctx(), graph()).run("EV:x", {"CO:zzz"}) == []


# -- A8 flow -----------------------------------------------------------------

def test_scheduled_insider_selling_is_explicitly_not_a_signal():
    out = A8OwnershipFlow(ctx()).run(0, 4, 4, 0.03, 2.0)
    insider = next(f for f in out if f.kind == "insider")
    assert "all under scheduled plans" in insider.text
    assert any("not treated as a signal" in c for c in insider.caveats)


def test_non_plan_selling_is_separated_from_plan_selling():
    out = A8OwnershipFlow(ctx()).run(0, 5, 2, 0.03, 2.0)
    assert any(f.numbers.get("non_plan_sells") == 3 for f in out)


def test_cluster_buying_is_reported():
    out = A8OwnershipFlow(ctx()).run(4, 0, 0, 0.03, 2.0)
    assert any("cluster buying" in f.text for f in out)


def test_institutional_holdings_always_carry_their_reporting_lag():
    out = A8OwnershipFlow(ctx()).run(0, 0, 0, 0.03, 2.0,
                                     institutional_asof=date(2026, 6, 30))
    own = next(f for f in out if f.kind == "ownership")
    assert "lag by 45+ days" in own.caveats[0]


# -- the shared prohibition --------------------------------------------------

@pytest.mark.parametrize("cls", [A1Fundamentals, A2Valuation, A3PriceTechnical,
                                 A4NewsNarrative, A5CatalystEvents, A6MacroRegime,
                                 A7SectorTechnology, A8OwnershipFlow])
def test_no_evidence_agent_can_reach_an_execution_tool(cls):
    agent = object.__new__(cls)
    agent.ctx = ctx()
    with pytest.raises(Exception):
        agent._guard_tool("place_order")
