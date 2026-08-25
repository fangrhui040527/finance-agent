#!/usr/bin/env python3
"""Full P0 pipeline on mock data. No network, no API keys, under a second.

Pattern from devpulse_ai/verify.py at awesome-llm-apps 11a4bc33 (Apache-2.0),
per docs/12 section 2.5. Without this, CI ends up depending on live vendor feeds
and starts failing for reasons unrelated to the code.

    python verify.py
"""

from __future__ import annotations

import sys
import time
from datetime import date as _date, datetime, timezone
from decimal import Decimal

from core.contracts.answer import Citation, Claim, TrustTier, verify_answer
from core.contracts.money import Money
from core.guardrails.chain import GuardrailChain
from core.guardrails.defaults import default_engine
from core.guardrails.policy import Action, PolicyViolation, Rail
from core.llm.client import EchoBackend, InferenceClient
from core.llm.tiers import TaskClass, Tier
from core.provenance.ledger import ProvenanceLedger

NOW = datetime.now(timezone.utc)
CHUNKS = {("1155.KL-Q2-2026", "c7"): "Net interest margin improved to 2.31% from 2.18%."}
OK, FAIL = "[OK]", "[FAIL]"
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {OK if cond else FAIL} {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        failures.append(label)


def main() -> int:
    t0 = time.time()
    print("\nFinPlanet P0 verification (mock data only)\n")

    print("1. Tier routing")
    engine = default_engine({"a4": {"llm_complete"}, "a10": {"llm_complete", "emit"}})
    ledger = ProvenanceLedger()
    client = InferenceClient(EchoBackend(), engine, ledger, daily_budget_myr=Decimal("25"))
    check("news triage -> cheap", client.complete("a4", TaskClass.NEWS_TRIAGE, "headline").tier is Tier.CHEAP)
    check("thesis -> reason", client.complete("a10", TaskClass.THESIS_SYNTHESIS, "memo").tier is Tier.REASON)

    print("\n2. Guardrail chain")
    chain = GuardrailChain(engine)
    check("all five rails covered", len(chain.rails_covered()) == 5)
    denied = False
    try:
        engine.enforce(Action("place_order", Rail.TOOL, "a10", {}))
    except PolicyViolation:
        denied = True
    check("execution denied", denied)
    blocked = False
    try:
        engine.enforce(Action("emit", Rail.OUTPUT, "a10", {"text": "you should buy now"}))
    except PolicyViolation:
        blocked = True
    check("advice language blocked", blocked)

    print("\n3. Citation verification")
    ans = verify_answer(
        [
            Claim(text="NIM improved", citations=[Citation(
                source="1155.KL-Q2-2026", chunk_id="c7",
                quoted_span="Net interest margin improved to 2.31%",
                trust=TrustTier.FILINGS, as_of=NOW)]),
            Claim(text="loan growth 9%", citations=[Citation(
                source="1155.KL-Q2-2026", chunk_id="c7",
                quoted_span="Loans grew 9 percent",
                trust=TrustTier.FILINGS, as_of=NOW)]),
        ],
        lambda s, c: CHUNKS.get((s, c)), NOW, confidence=0.62,
    )
    check("supported claim kept", len(ans.claims) == 1)
    check("fabricated claim dropped", len(ans.dropped) == 1)
    for c in ans.dropped:
        ledger.record_claim("a10", c.text, [], survived=False, dropped_reason=c.dropped_reason)

    print("\n4. Provenance ledger")
    calls = list(ledger.calls())
    check("every call logged", len(calls) == 2, f"{len(calls)} rows")
    check("cost in MYR", ledger.total_cost_myr() > 0, f"RM {ledger.total_cost_myr():.6f}")
    tamper = False
    try:
        ledger.conn.execute("DELETE FROM llm_calls")
    except Exception:
        tamper = True
    check("append-only enforced", tamper)

    print("\n5. Money contract")
    usd = Money(amount=Decimal("49"), currency="USD", fx_asof=NOW)
    myr = usd.convert("MYR", Decimal("4.15"), NOW)
    check("USD -> MYR carries fx_asof", myr.fx_asof is not None, str(myr))

    print("\n6. Attribution engine")
    import random
    from datetime import date
    from engines.attribution.decompose import Verdict, decompose
    from engines.attribution.regression import huber_fit
    rng = random.Random(11)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    ys = [1.1 * a + 0.6 * b + rng.gauss(0, 0.005) for a, b in rows]
    fit = huber_fit(rows, ys)
    w = (date(2026, 8, 1), date(2026, 8, 12))
    mkt = decompose("X", w, -0.068, -0.015, {}, -0.094, 0.0, fit)
    check("market selloff gets no company story",
          mkt.verdict in (Verdict.MARKET_DRIVEN, Verdict.NOT_SIGNIFICANT)
          and not mkt.needs_cause_hunt(), mkt.verdict.value)
    idio = decompose("X", w, -0.002, 0.001, {}, 0.072, 0.0, fit)
    check("idiosyncratic move triggers a hunt", idio.needs_cause_hunt(),
          f"{idio.unexplained_share:.0%} unexplained")

    print("\n7. Risk and sizing")
    from decimal import Decimal as DD
    from engines.risk.concentration import Limits, Position, check as ccheck, effective_number_of_bets
    from engines.sizing.caps import cost_floor_value
    from markets.registry import get as mget
    banks = [Position(f"B{i}", 0.10, "Financials", "MY", "MYR", 0.006) for i in range(10)]
    corr = [[1.0 if i == j else 0.85 for j in range(10)] for i in range(10)]
    eb = effective_number_of_bets([p.weight for p in banks], corr)
    check("10 correlated names read as ~1 bet", eb < 1.5, f"{eb:.2f} effective bets")
    check("breaches reported", len(ccheck(banks, corr, Limits())) >= 4)
    floor = cost_floor_value(mget("XKLS").fee_schedule.round_trip, "XKLS")
    check("Bursa minimum economic position", DD("3000") < floor < DD("8000"), f"RM {floor:,.0f}")

    print("\n8. News features and catalyst matching")
    from datetime import datetime as _dt, timezone as _tz
    from knowledge.news.features import LexiconExtractor, near_duplicate_hash
    from engines.events.taxonomy import (
        BaseRateTable, CapBand, Event, EventType, Observation, SurpriseBucket)
    from engines.events.catalyst import attach, score_candidates
    ex = LexiconExtractor()
    probe = ex.extract("Maybank may face a probe, the outcome is uncertain", ["Maybank"])
    plunge = ex.extract("Maybank shares plunged sharply after the group cut guidance", ["Maybank"])
    check("intensity separates what polarity collapses",
          probe.polarity < 0 and plunge.polarity < 0 and plunge.intensity > probe.intensity)
    body = "The group cut guidance for the coming year amid weaker demand across segments"
    check("wire duplicates collapse", near_duplicate_hash(body) == near_duplicate_hash("(Reuters) " + body))

    ts = _dt(2026, 8, 12, tzinfo=_tz.utc)
    tbl = BaseRateTable()
    import random as _r
    _r.seed(3)
    for i in range(60):
        e = Event(f"h{i}", "X", EventType.EARNINGS_RESULT, ts, market="XKLS",
                  cap_band=CapBand.LARGE, surprise=SurpriseBucket.BIG_BEAT, source_doc_id="d")
        tbl.observe(Observation(e, _r.gauss(0.004, 0.01), _r.gauss(0.029, 0.02), _r.gauss(0.005, 0.03)))
    br = tbl.lookup(EventType.EARNINGS_RESULT, "XKLS", CapBand.LARGE, SurpriseBucket.BIG_BEAT)
    check("base rate carries n and IQR", br.n == 60, br.describe()[:70])

    cands = score_candidates(mkt, [Event("e1", "X", EventType.EARNINGS_RESULT, ts,
                                         market="XKLS", cap_band=CapBand.LARGE,
                                         surprise=SurpriseBucket.BIG_MISS, source_doc_id="d")],
                             tbl, {"e1": 1}, "XKLS")
    check("market-driven move gets no candidates scored", cands == [])
    idio2 = attach(idio, score_candidates(
        idio, [Event("e2", "X", EventType.DIVIDEND_CHANGE, ts, market="XKLS",
                     cap_band=CapBand.LARGE, source_doc_id="d")],
        tbl, {"e2": 1}, "XKLS"))
    check("routine dividend cannot explain a 7% move",
          idio2.verdict.value == "no_identified_catalyst")

    # ---------------------------------------------------------------- 9
    print("\n9. Agents: the seam and the refusals")
    from agents.base import AgentContext, Finding
    from agents.portfolio.agents import A12PortfolioRisk, A13Sizing
    from agents.supervisor import A0Supervisor, Intent
    from agents.synthesis.agents import A9Attribution, A10Thesis, A11RedTeam, Breaker, Stance
    from knowledge.retrieval.pipeline import Router

    allow = {
        "a0_supervisor": {"plan", "budget", "route", "refuse"},
        "a9_attribution": {"decompose", "candidate_causes", "long_horizon_decompose"},
        "a10_thesis": {"compose", "check_coverage"},
        "a11_red_team": {"find_disconfirming"},
        "a12_portfolio_risk": {"concentration_check", "drawdown_state"},
        "a13_sizing": {"investable_capital", "risk_budget_cap", "kelly_cap", "lot_round"},
        "a14_teacher": {"explain", "next_concept", "quiz"},
        "a15_reflection": {"grade_queue", "propose_lesson", "calibrate", "curate"},
    }
    ctx = AgentContext(router=Router({}), engine=default_engine(allow), now=NOW)

    a0 = A0Supervisor(ctx)
    check("supervisor refuses to place an order",
          not a0.plan("buy 1000 shares of tenaga for me").allowed)
    check("supervisor refuses a point price forecast",
          not a0.plan("what will nvidia be worth in december").allowed)
    plan = a0.plan("why did maybank fall today", instrument_ids=("MYX:1155",))
    check("routing picks the documented playbook",
          plan.intent is Intent.WHY_IT_MOVED and "a9_attribution" in plan.agents,
          f"RM {plan.estimated_cost.amount:.2f} estimated")
    try:
        a0.retrieve("kb_filings", "x")
        check("supervisor cannot retrieve", False)
    except PermissionError:
        check("supervisor cannot retrieve evidence itself", True)

    a10 = A10Thesis(ctx)
    ev = [Finding(a, "fact", "x") for a in
          ("a1_fundamentals", "a2_valuation", "a5_catalyst_events", "a6_macro_regime")]
    a10.run("MYX:1155", ev, proposed_stance=Stance.ACCUMULATE, breakers=[])
    check("no breakers means no stance", a10.last.stance is Stance.NO_VIEW)
    brk = [Breaker("NIM below 2.0%", "nim < 0.020", "kb_filings", _date(2027, 2, 1)),
           Breaker("credit cost above 60bps", "credit_cost > 0.006", "kb_filings")]
    a10.run("MYX:1155", ev, proposed_stance=Stance.ACCUMULATE, breakers=brk)
    check("two checkable breakers make a thesis actionable", a10.last.is_actionable())

    a11 = A11RedTeam(ctx)
    check("red team is never silent on a live thesis", bool(a11.run(a10.last)))

    # ---------------------------------------------------------------- 10
    print("\n10. Graph: no path, no claim")
    from knowledge.graph.entity_graph import (
        Edge, EdgeKind, EntityGraph, Node, NodeKind, PathRequired, require_path)

    g = EntityGraph()
    for nid, kind, lbl in [("EV:redsea", NodeKind.EVENT, "Red Sea disruption"),
                           ("SEC:shipping", NodeKind.SECTOR, "Shipping"),
                           ("CO:MISC", NodeKind.COMPANY, "MISC Berhad")]:
        g.add_node(Node(nid, kind, lbl))
    g.add_edge(Edge("EV:redsea", "SEC:shipping", EdgeKind.AFFECTS, 1.0, "doc:1"))
    g.add_edge(Edge("SEC:shipping", "CO:MISC", EdgeKind.CLASSIFIED_IN, 1.0, "doc:2"))
    hit = dict(g.impact_of("EV:redsea", {"CO:MISC"}))["CO:MISC"]
    check("multi-hop impact ships with its path and decay",
          hit.strength == "indirect" and hit.citable, hit.describe()[:60])
    try:
        require_path("shipping hits Maybank", None)
        check("unpathed impact claim is blocked", False)
    except PathRequired:
        check("unpathed impact claim cannot be emitted", True)

    # ---------------------------------------------------------------- 11
    print("\n11. Reflection: the loop that mostly declines to learn")
    from agents.learning.reflection import (
        A15Reflection, LessonStore, Outcome, OutcomeQueue, Status)

    a15 = A15Reflection(ctx, OutcomeQueue(), LessonStore())
    one = [Outcome("p0", NOW.date(), 0.05, 0.01, True)]
    check("one vivid trade writes nothing",
          a15.propose("ceo sounded confident", one, NOW.date(), {"MYX:1155"})[0].kind == "no_lesson")
    many_one_name = [Outcome(f"p{i}", NOW.date(), 0.05, 0.01, i < 10) for i in range(12)]
    check("twelve repeats on one instrument still write nothing",
          a15.propose("gap down", many_one_name, NOW.date(), {"MYX:1155"})[0].kind == "no_lesson")
    spread = [Outcome(f"q{i}", NOW.date(), 0.05, 0.01, i < 6) for i in range(8)]
    check("a genuinely repeated pattern is allowed through",
          a15.propose("unexplained gap reverses", spread, NOW.date(),
                      {f"S{i}" for i in range(5)})[0].kind == "lesson_written")

    # ---------------------------------------------------------------- 12
    print("\n12. Registry: the ratchet")
    from core.registry.loader import load as load_registry
    reg = load_registry("agents/registry.yaml")
    check("all 16 agents register with an eval suite carrying negatives",
          len(reg.agents) == 16, f"{len(reg.knowledge)} knowledge stores")
    check("no agent may write to human-authored knowledge",
          not any(reg.may_write(a, "kb_craft") for a in reg.agents))
    check("the lessons store is the agent-writable one",
          reg.may_write("a15_reflection", "kb_lessons"))

    # ---------------------------------------------------------------- 13
    print("\n13. Teacher: order is enforced, not suggested")
    from agents.learning.teacher import A14Teacher, Learner, CURRICULUM, validate_graph
    validate_graph()
    t = A14Teacher(ctx)
    check(f"curriculum graph is acyclic across {len(CURRICULUM)} concepts", True)
    check("kelly cannot be taught before expected value",
          t.run("kelly", Learner(known={"share"}))[0].kind == "prerequisite")

    # ---------------------------------------------------------------- 14
    print("\n14. Surface: the honest answer is as easy to show")
    from ui.render import daily_brief, decomposition_bars
    bars_out = decomposition_bars(idio2)
    check("unexplained share is always on screen", "unexplained" in bars_out)
    check("no-catalyst gets a layout, not an empty slot",
          "no catalyst cleared the evidence threshold" in bars_out)
    brief = daily_brief(NOW, [mkt], [], [])
    check("a quiet day is rendered as a quiet day",
          "Nothing needs a decision today" in brief)

    dt = time.time() - t0
    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}  ({dt:.2f}s)\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
