"""Phase 1 - all sixteen agents, on the allowlist the registry actually derives.

The unit tests build agents with hand-written allowlists. `details/04-AGENTS.md`
records the bug that let through: a grant missing from the registry denied every
real call while every test passed. So this file has one rule - the `PolicyEngine`
comes from `registry.allowlist()` and nothing else - and asks two questions of
each agent: does its documented happy path run, and is every tool it guards
actually granted to it.
"""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agents.base import Agent
from agents.evidence.agents import (
    A1Fundamentals, A2Valuation, A3PriceTechnical, A4NewsNarrative, A5CatalystEvents,
    A6MacroRegime, A7SectorTechnology, A8OwnershipFlow,
)
from agents.learning.reflection import (
    A15Reflection, Horizon, LessonStore, Outcome, OutcomeQueue, Prediction,
)
from agents.learning.teacher import CURRICULUM, A14Teacher, Learner
from agents.portfolio.agents import A12PortfolioRisk, A13Sizing
from agents.supervisor import A0Supervisor, Intent
from agents.synthesis.agents import A9Attribution, A10Thesis, A11RedTeam, Breaker, Stance
from core.guardrails.policy import Action, PolicyViolation, Rail
from core.market.pointintime import Fact, FactStore
from core.market.prices import Bar, PriceSeries
from engines.attribution.regression import huber_fit
from engines.events.taxonomy import BaseRateTable, CapBand, Event, EventType, Observation, SurpriseBucket
from engines.risk.concentration import Limits, Position
from engines.sizing.caps import Band, CapSet, CurrencyMismatch
from engines.sizing.waterfall import Goal, Liability
from knowledge.chunking.parent_child import Chunk
from knowledge.retrieval.hybrid import Collection
from markets.contract import AccountingStandard
from markets.registry import get as market_get
from qa.conftest import NOW, ROOT

TODAY = NOW.date()
AGENT_CLASSES: list[type[Agent]] = [
    A0Supervisor, A1Fundamentals, A2Valuation, A3PriceTechnical, A4NewsNarrative,
    A5CatalystEvents, A6MacroRegime, A7SectorTechnology, A8OwnershipFlow, A9Attribution,
    A10Thesis, A11RedTeam, A12PortfolioRisk, A13Sizing, A14Teacher, A15Reflection,
]


def _fit(seed=7):
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [1.1 * a + 0.5 * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


# -- code and registry must agree -----------------------------------------------

def test_the_sixteen_agent_classes_are_exactly_the_registered_ids(registry):
    assert {c.agent_id for c in AGENT_CLASSES} == set(registry.agents)


@pytest.mark.parametrize("cls", AGENT_CLASSES, ids=lambda c: c.agent_id)
def test_each_agents_declared_tools_match_its_registry_entry(cls, registry):
    """`Agent.tools` on the class is documentation; the registry is the control.
    They drifted once (`ohlcv` registered as `trend_state`). Never again."""
    assert set(cls.tools) == set(registry.agents[cls.agent_id].tools)


def test_every_tool_name_guarded_in_source_is_granted_to_an_agent_in_that_file(registry):
    guarded = re.compile(r'_guard_tool\("([a-z_]+)"')
    files = {
        "agents/supervisor.py": {"a0_supervisor"},
        "agents/evidence/agents.py": {f"a{i}_" for i in range(1, 9)},
        "agents/synthesis/agents.py": {"a9_", "a10_", "a11_"},
        "agents/portfolio/agents.py": {"a12_", "a13_"},
        "agents/learning/teacher.py": {"a14_"},
        "agents/learning/reflection.py": {"a15_"},
        "agents/base.py": {""},                                  # retrieve: any agent
    }
    for rel, prefixes in files.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        granted = {t for aid, spec in registry.agents.items()
                   if any(aid.startswith(p) for p in prefixes) for t in spec.tools}
        for name in guarded.findall(src):
            assert name in granted, f"{rel} guards {name!r} but no agent in that file is granted it"


# -- the same seam, refusing ---------------------------------------------------------

@pytest.mark.parametrize("agent_id", sorted(a.agent_id for a in AGENT_CLASSES))
def test_no_agent_may_place_an_order_and_no_agent_may_borrow_a_tool(agent_id, real_ctx, registry):
    from core.guardrails.policy import NoExecutionPolicy

    engine = real_ctx.engine
    # Taken from the policy, never spelled out here: tests/test_no_execution_anywhere.py
    # greps every file for these names, and this file has no business being exempt.
    for forbidden in sorted(NoExecutionPolicy.FORBIDDEN):
        with pytest.raises(PolicyViolation, match="no_execution"):
            engine.enforce(Action(forbidden, Rail.TOOL, agent_id, {}))
    mine = set(registry.agents[agent_id].tools)
    others = {t for aid, s in registry.agents.items() if aid != agent_id for t in s.tools} - mine
    assert others, "every agent has at least one tool it does not hold"
    with pytest.raises(PolicyViolation, match="may not call"):
        engine.enforce(Action(sorted(others)[0], Rail.TOOL, agent_id, {}))


def test_the_supervisor_and_the_thesis_writer_cannot_retrieve(real_ctx):
    with pytest.raises(PermissionError, match="may not retrieve"):
        A0Supervisor(real_ctx).retrieve("kb_filings", "x")
    with pytest.raises(PermissionError, match="may not retrieve"):
        A10Thesis(real_ctx).retrieve("kb_filings", "x")


def test_an_agent_may_only_read_the_collections_it_owns(real_ctx):
    from knowledge.retrieval.pipeline import CollectionScopeError

    with pytest.raises(CollectionScopeError):
        real_ctx.router.get("a1_fundamentals", "kb_news")


# -- orchestration ----------------------------------------------------------------

def test_a0_plans_refuses_and_trims_on_the_real_allowlist(real_ctx):
    a0 = A0Supervisor(real_ctx)
    plan = a0.plan("why did maybank fall today", instrument_ids=("MYX:1155",))
    assert plan.allowed and plan.intent is Intent.WHY_IT_MOVED
    assert "a9_attribution" in plan.agents and plan.estimated_cost.currency == "MYR"

    for q in ("buy 1000 shares of tenaga for me", "what will nvidia be worth in december",
              "give me a price target by next quarter", "give me a risk-free sure thing",
              "any insider info on the deal"):
        assert not a0.plan(q, instrument_ids=("MYX:1155",)).allowed, q
    assert not a0.plan("why did it move").allowed, "no instrument resolvable"

    trimmed = a0.plan("why did maybank fall today", budget_myr=Decimal("0.70"),
                      instrument_ids=("MYX:1155",))
    assert trimmed.allowed and "a9_attribution" in trimmed.agents
    assert trimmed.estimated_cost.amount <= Decimal("0.70")
    too_small = a0.plan("why did maybank fall today", budget_myr=Decimal("0.10"),
                        instrument_ids=("MYX:1155",))
    assert not too_small.allowed and "budget" in too_small.refusal.reason.lower()

    findings = a0.run("should i buy maybank", instrument_ids=("MYX:1155",))
    assert findings[0].kind == "plan" and "not a recommendation" in " ".join(findings[0].caveats)


# -- evidence layer -------------------------------------------------------------------

def test_a1_reads_facts_point_in_time_and_flags_earnings_quality(real_ctx):
    facts = FactStore()
    common = dict(instrument_id="MYX:1155", currency="MYR",
                  accounting_standard=AccountingStandard.IFRS, source_doc_id="q2")
    facts.add(Fact(concept="net_income", period_end=date(2026, 6, 30), known_at=date(2026, 8, 20),
                   value=Decimal("2500"), **common))
    facts.add(Fact(concept="cash_from_operations", period_end=date(2026, 6, 30),
                   known_at=date(2026, 8, 20), value=Decimal("1200"), **common))
    a1 = A1Fundamentals(real_ctx, facts)

    early = a1.run("MYX:1155", ["net_income"], asof=date(2026, 8, 1))
    assert early[0].kind == "unavailable"
    late = a1.run("MYX:1155", ["net_income", "revenue"], asof=date(2026, 9, 1))
    assert [f.kind for f in late] == ["line_item", "unavailable"]
    assert late[0].numbers["net_income"] == 2500.0

    q = a1.earnings_quality("MYX:1155", date(2026, 9, 1))
    assert q[0].kind == "quality_flag" and "exceeds operating cash flow" in q[0].text


def test_a2_contextualises_a_multiple_and_states_what_the_price_requires(real_ctx):
    a2 = A2Valuation(real_ctx)
    v = a2.run("MYX:1155", "bank", 1.2, [0.8, 0.9, 1.0, 1.1, 1.5])
    assert v[0].kind == "valuation" and v[0].numbers["percentile"] == 0.8
    assert "DCF is not applicable" in v[0].caveats[0]
    r = a2.reverse_dcf(price=100.0, current_earnings=5.0, discount=0.09)
    assert r[0].kind == "reverse_dcf" and 0 < r[0].numbers["implied_growth"] < 0.6
    assert a2.run("X", "bank", 1.0, [])[0].caveats == ["cannot contextualise"]


def test_a3_describes_price_and_suppresses_signals_near_earnings(real_ctx):
    rng = random.Random(3)
    bars, px = [], 6.0
    for i in range(40):
        px *= 1 + rng.gauss(0, 0.01)
        bars.append(Bar(date(2026, 6, 1) + timedelta(days=i), px, px * 1.01, px * 0.99, px, 900_000))
    a3 = A3PriceTechnical(real_ctx)
    out = a3.run(PriceSeries("MYX:1155", bars), sessions_to_earnings=2)
    assert [f.kind for f in out] == ["price_context", "blackout"]
    assert out[0].numbers["atr20"] > 0 and out[0].numbers["adv20"] > 0
    assert a3.run(PriceSeries("X", bars[:10]))[0].caveats == ["fewer than 21 bars"]


def test_a4_retrieves_only_from_its_own_collection_and_cites_what_it_returns(real_ctx):
    kb = Collection("kb_news")
    stories = [
        "Maybank cut guidance after weak loan growth in the second quarter",
        "Maybank guidance cut: analysts see NIM pressure into next year",
        "Petronas Chemicals opens a new plant",
    ]
    for i, text in enumerate(stories):
        kb.add(Chunk(chunk_id=f"n{i}", text=text, corpus="kb_news", as_of=NOW,
                     metadata={"instruments": ["MYX:1155"]}))
    real_ctx.router.register(kb)
    a4 = A4NewsNarrative(real_ctx)

    findings = a4.run("MYX:1155", "Maybank guidance cut")
    assert findings and all(f.kind == "news" for f in findings)
    assert all(f.citations and f.citations[0].source == "kb_news" for f in findings)
    answer = a4.emit(findings, kb.find_chunk, confidence=0.5)
    assert answer.answered and len(answer.claims) == len(findings)

    refused = a4.run("MYX:1155", "quantum lithography tariffs")
    assert len(refused) == 1 and "no news cleared" in refused[0].text


def test_a5_attaches_base_rates_to_events_in_the_window(real_ctx):
    ts = datetime(2026, 8, 12, tzinfo=timezone.utc)
    table = BaseRateTable()
    rng = random.Random(5)
    for i in range(40):
        e = Event(f"h{i}", "MYX:1155", EventType.EARNINGS_RESULT, ts, market="XKLS",
                  cap_band=CapBand.LARGE, surprise=SurpriseBucket.BEAT, source_doc_id="d")
        table.observe(Observation(e, rng.gauss(0, 0.01), rng.gauss(0.02, 0.02), rng.gauss(0, 0.03)))
    events = [
        Event("e1", "MYX:1155", EventType.EARNINGS_RESULT, ts, market="XKLS",
              cap_band=CapBand.LARGE, surprise=SurpriseBucket.BEAT, source_doc_id="ann"),
        Event("e2", "MYX:1155", EventType.EXECUTIVE_CHANGE, ts + timedelta(days=1), market="XKLS",
              confirmed=False),
        Event("e3", "XNAS:NVDA", EventType.EARNINGS_RESULT, ts, market="XNAS"),
    ]
    out = A5CatalystEvents(real_ctx, events, table).run("MYX:1155", ts - timedelta(days=1), ts + timedelta(days=2))
    assert [f.kind for f in out] == ["event", "event"]
    assert "median_car" in out[0].numbers and "n=40" in out[0].text
    assert "unconfirmed: cannot be cited as a cause" in out[1].caveats


def test_a6_labels_a_regime_without_forecasting(real_ctx):
    rng = random.Random(6)
    calm = [rng.gauss(0.0006, 0.006) for _ in range(120)]
    panic = [rng.gauss(-0.004, 0.025) for _ in range(120)]
    a6 = A6MacroRegime(real_ctx)
    assert "risk_on" in a6.run(calm)[0].text
    assert "risk_off" in a6.run(panic)[0].text
    assert a6.run(calm[:10])[0].caveats[0].startswith("fewer observations")


def test_a7_walks_the_real_extracted_graph_and_the_output_gate_accepts_the_chain(real_ctx):
    from knowledge.graph.build import build as build_graph
    from knowledge.graph.evidence import CuratedCorpus
    from knowledge.graph.store import GraphStore

    store = GraphStore()
    report = build_graph(store)
    assert report.edges > 0 and report.citable == report.edges
    graph = store.load()
    corpus = CuratedCorpus()

    a7 = A7SectorTechnology(real_ctx, graph, lambda doc: corpus.citation(doc, NOW))
    findings = a7.run("CM:aluminium", {"CO:XKLS:8869"}, asof=TODAY)
    assert findings and findings[0].kind == "exposure"
    assert findings[0].all_citations_required and findings[0].citations
    answer = a7.emit(findings, corpus.chunk, confidence=0.5)
    assert answer.answered, [c.dropped_reason for c in answer.dropped]

    blind = A7SectorTechnology(real_ctx, graph)               # no corpus: uncited, dropped
    refused = blind.emit(blind.run("CM:aluminium", {"CO:XKLS:8869"}, asof=TODAY), corpus.chunk, 0.5)
    assert not refused.answered
    assert A7SectorTechnology(real_ctx).run("x", set())[0].caveats == ["multi-hop exposure unavailable"]
    store.close()


def test_a8_treats_routine_selling_as_noise_and_cluster_buying_as_signal(real_ctx):
    a8 = A8OwnershipFlow(real_ctx)
    out = a8.run(insider_buys=3, insider_sells=2, scheduled_sells=2,
                 short_interest_pct=0.05, days_to_cover=2.0, institutional_asof=date(2026, 6, 30))
    kinds = [f.kind for f in out]
    assert kinds == ["insider", "insider", "short_interest", "ownership"]
    assert "cluster buying" in out[0].text and "not treated as a signal" in out[1].caveats[0]
    assert "lag by 45+ days" in out[3].caveats[0]


# -- synthesis --------------------------------------------------------------------------

def test_a9_decomposes_before_it_narrates(real_ctx):
    a9 = A9Attribution(real_ctx)
    window = (TODAY - timedelta(days=1), TODAY)
    fit = _fit()
    mkt = a9.run("MYX:1155", window, realised_local=-0.09, event_market=-0.08, event_sector=-0.02,
                 event_styles={}, fx_return=0.0, fit=fit)
    assert mkt[0].kind == "decomposition" and "the market, not the company" in mkt[0].text
    idio = a9.run("XNAS:NVDA", window, realised_local=0.072, event_market=0.004, event_sector=0.002,
                  event_styles={}, fx_return=0.0, fit=fit, base_currency="USD")
    assert "unexplained" in idio[0].text and idio[0].numbers["unexplained_share"] > 0.7
    nan = a9.run("X", window, realised_local=float("nan"), event_market=0.0, event_sector=0.0,
                 event_styles={}, fx_return=0.0, fit=fit)
    assert "Attribution unavailable" in nan[0].text
    lh = a9.since_purchase("MYX:1155", 1.0, 1.3, 12.0, 15.0, 0.08, 4.1, 4.3, 3.0)
    assert lh[0].kind == "long_horizon" and "driven mainly by" in lh[0].text


def test_a10_and_a11_compose_then_attack_on_the_real_allowlist(real_ctx):
    from agents.base import Finding

    evidence = [Finding(a, "supplied", t) for a, t in (
        ("a1_fundamentals", "CASA fell to 24% from 31%"),
        ("a2_valuation", "P/B at the 12th percentile"),
        ("a5_catalyst_events", "results due in three weeks"),
        ("a6_macro_regime", "OPR on hold"))]
    breakers = [Breaker("NIM falls below 2.0%", "nim < 0.020", "kb_filings", date(2027, 2, 1)),
                Breaker("CASA below 22%", "casa < 0.22", "kb_filings")]
    a10, a11 = A10Thesis(real_ctx), A11RedTeam(real_ctx)

    a10.run("MYX:1155", evidence, proposed_stance=Stance.ACCUMULATE, breakers=breakers,
            valuation_range=(Decimal("8.50"), Decimal("11.00")))
    thesis = a10.last
    assert thesis.stance is Stance.ACCUMULATE and thesis.is_actionable()
    challenges = a11.run(thesis)
    kinds = {c.caveats[0].split(": ")[-1] for c in challenges}
    assert {"consensus", "accounting", "liquidity", "breaker_timing"} <= kinds
    assert a11.verdict(challenges) in ("thesis_survives", "thesis_weakened")

    a10.run("MYX:1155", evidence, proposed_stance=Stance.ACCUMULATE, breakers=breakers)
    unpriced = a11.run(a10.last)
    assert a11.verdict(unpriced) == "thesis_rejected", "accumulating with no valuation range is fatal"

    a10.run("MYX:1155", evidence[:1], proposed_stance=Stance.ACCUMULATE, breakers=breakers)
    assert a10.last.stance is Stance.NO_VIEW and len(a10.last.gaps) == 3
    assert a11.verdict(a11.run(a10.last)) == "thesis_rejected"
    with pytest.raises(ValueError, match="no executable query"):
        Breaker("a wish", "   ", "kb_filings")


# -- portfolio ------------------------------------------------------------------------------

def test_a12_reports_the_whole_book_and_the_drawdown_ladder(real_ctx):
    book = [Position("MYX:1155", 0.22, "bank", "MY", "MYR", 0.01),
            Position("MYX:1023", 0.20, "bank", "MY", "MYR", 0.01),
            Position("XNAS:NVDA", 0.18, "tech", "US", "USD", 0.02)]
    corr = [[1.0, 0.85, 0.1], [0.85, 1.0, 0.1], [0.1, 0.1, 1.0]]
    a12 = A12PortfolioRisk(real_ctx)
    out = a12.run(book, corr=corr, limits=Limits(), peak_equity=Decimal(100_000), equity=Decimal(78_000))
    kinds = [f.kind for f in out]
    assert kinds[0] == "concentration" and "breach" in kinds and "cluster" in kinds and kinds[-1] == "drawdown"
    assert out[-1].numbers["risk_scalar"] == 0.25 and "post-mortem" in out[-1].text
    stress = a12.stress(book, {"2008": -0.45, "covid": -0.30})
    assert [f.kind for f in stress] == ["stress", "stress"] and stress[0].numbers["portfolio_impact"] < 0
    assert a12.run([])[0].kind == "empty_book"


def test_a13_caps_sizes_and_refuses_across_the_currency_boundary(real_ctx):
    a13 = A13Sizing(real_ctx)
    myx, nas = market_get("MYX"), market_get("XNAS")

    caps, fs = a13.caps(Decimal(200_000), Decimal("0.0968"), Decimal(900_000),
                        myx.fee_schedule.round_trip, mic="MYX")
    assert caps.currency == "MYR" and fs[0].kind == "caps" and "60 bps" in fs[0].text
    binding, value = caps.binding()
    assert value <= min(caps.risk, caps.concentration, caps.liquidity)

    with pytest.raises(CurrencyMismatch):
        a13.caps(Decimal(200_000), Decimal("0.0833"), Decimal(30_000_000_000),
                 nas.fee_schedule.round_trip, mic="XNAS")
    caps_usd, fs = a13.caps(Decimal(200_000), Decimal("0.0833"), Decimal(30_000_000_000),
                            nas.fee_schedule.round_trip, mic="XNAS", fx_base_per_quote=Decimal("4.20"))
    assert caps_usd.currency == "USD" and "= MYR" in fs[0].text

    sized = a13.run(instrument_id="MYX:1155", band=Band.ACCUMULATE, investable=Decimal(200_000),
                    caps=caps, price=Decimal("6.20"), lot_size=100, stop_price=Decimal("5.60"),
                    breakers=("nim", "casa"), time_stop=date(2027, 1, 1),
                    round_trip_cost_at=myx.fee_schedule.round_trip, mic="MYX")
    assert sized[0].kind == "size" and sized[0].numbers["units"] % 100 == 0
    tiny = a13.run(instrument_id="MYX:1155", band=Band.ACCUMULATE, investable=Decimal(2_000),
                   caps=CapSet(Decimal(100), None, Decimal(160), Decimal(45_000), Decimal(4_700)),
                   price=Decimal("6.20"), lot_size=100, stop_price=Decimal("5.60"),
                   breakers=("nim", "casa"), time_stop=date(2027, 1, 1),
                   round_trip_cost_at=myx.fee_schedule.round_trip, mic="MYX")
    assert tiny[0].kind == "no_position"

    w, wf = a13.investable_capital(Decimal(100_000), Decimal(4_000),
                                   goals=[Goal("car", Decimal(20_000), 12)],
                                   liabilities=[Liability("card", Decimal(10_000), Decimal("0.18"))])
    assert w.investable == Decimal(46_000) and wf[0].kind == "investable_capital"
    assert a13.investable_capital(Decimal(10_000), Decimal(4_000))[0].investable == 0


# -- learning ---------------------------------------------------------------------------------

def test_a14_teaches_in_order_and_knows_what_comes_next(real_ctx):
    a14 = A14Teacher(real_ctx)
    learner = Learner()
    assert a14.run("kelly", learner)[0].kind == "prerequisite"
    assert a14.run("astrology", learner)[0].kind == "unknown_concept"
    first = a14.run("share", learner)
    assert first[0].kind == "explain" and {"misconception", "check"} <= {f.kind for f in first}
    assert a14.next_concept(learner).text.startswith("next: What a share actually is")
    for c in CURRICULUM:
        learner.mastered(c.key)
    assert "complete" in a14.next_concept(learner).text
    assert len(a14.syllabus(learner)) == 8
    with pytest.raises(KeyError, match="not in the curriculum"):
        learner.mastered("astrology")


def test_a15_mostly_declines_to_learn_and_grades_nothing_early(real_ctx):
    queue, store = OutcomeQueue(), LessonStore()
    a15 = A15Reflection(real_ctx, queue, store)
    p = Prediction("p1", "MYX:1155", "human", NOW, Horizon.D21, "NIM recovers", 1, 0.6,
                   grade_on=TODAY + timedelta(days=21))
    queue.enqueue(p)
    with pytest.raises(ValueError, match="score noise"):
        queue.grade("p1", TODAY, 0.05, 0.01)

    spread = [Outcome(f"q{i}", TODAY, 0.05, 0.01, i < 6) for i in range(8)]
    out = a15.run(TODAY, cohort={"gap reverses": spread, "one name": spread},
                  instruments={"gap reverses": {f"S{i}" for i in range(5)}, "one name": {"MYX:1155"}})
    kinds = [f.kind for f in out]
    assert kinds[0] == "queue" and "lesson_written" in kinds and "no_lesson" in kinds
    assert len(store.active()) == 1
    assert a15.propose("unverifiable", spread, TODAY)[0].kind == "no_lesson"
    again = a15.propose("gap reverses", spread, TODAY, {f"S{i}" for i in range(5)})
    assert again[0].kind == "lesson_confirmed"

    cal = a15.calibration([(0.9, False)] * 6 + [(0.6, True)] * 4)
    assert cal[0].kind == "calibration" and cal[0].caveats, "an overconfident band is named"
    assert a15.calibration([])[0].text == "no graded predictions yet"
