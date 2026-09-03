"""Phase 2 - the product's own model path, on the real network, on Haiku.

`InferenceClient` -> registry-derived allowlist -> `AnthropicBackend` (official
SDK) -> ledger -> trace, exactly as a live deployment would wire it, with one
switch: `FINPLANET_CHEAP=1`, the product's own cap, resolves every Messages
tier to the cheapest model AND its price, so a reason-tier call proves the
reason-tier code path at Haiku cost and the ledger records what was truly spent.

The keyless twin of every test here is in `qa/phase1/test_p1_seam_client.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.guardrails.defaults import default_engine
from core.guardrails.policy import Action, PolicyViolation, Rail
from core.llm.backends import AnthropicBackend, Truncated
from core.llm.client import BudgetExceeded, InferenceClient
from core.llm.tiers import TaskClass, Tier, Usage, route
from core.provenance.ledger import ProvenanceLedger
from core.trace import start_run
from qa._support.cheap import CHEAP_MODEL, CHEAP_MODEL_RESOLVED, api_key
from qa._support.live import metered_client

GRANTED = [
    ("a4_news_narrative", TaskClass.NEWS_TRIAGE),
    ("a10_thesis", TaskClass.THESIS_SYNTHESIS),
    ("a11_red_team", TaskClass.RED_TEAM),
    ("a15_reflection", TaskClass.REFLECTION_DEEP),
]


@pytest.fixture
def product(registry, product_backend):
    """The client as production would build it, minus the FX rate argument."""
    ledger = ProvenanceLedger(run_id="qa-live")
    return InferenceClient(product_backend, default_engine(registry.allowlist()), ledger), ledger


@pytest.mark.parametrize("agent,task", GRANTED, ids=[a for a, _ in GRANTED])
def test_each_granted_agent_completes_through_the_registry_on_haiku(product, live_calls, agent, task):
    client, ledger = product
    out = client.complete(agent, task, "Reply with the single word OK.",
                          system="You are terse.")
    assert out.text.strip() and out.refused is False
    assert out.tier is Tier.CHEAP, "the cap resolves every Messages tier to CHEAP"
    assert route(task) in (Tier.CHEAP, Tier.REASON), "sanity: the asked-for tiers span the table"
    assert out.model_id == CHEAP_MODEL, "the cap must reach the request, not just the table"
    assert out.usage.input_tokens > 0 and out.usage.output_tokens > 0
    assert out.cost_myr > 0

    assert live_calls[-1].request_body["model"] == CHEAP_MODEL
    assert live_calls[-1].payload["model"] == CHEAP_MODEL_RESOLVED
    assert "thinking" not in live_calls[-1].request_body, "Haiku's profile sends no thinking"

    row = ledger.calls_for_run("qa-live")[-1]
    assert row["agent"] == agent and row["tier"] == Tier.CHEAP.value
    assert row["model_id"] == CHEAP_MODEL and row["latency_ms"] > 0
    assert row["input_tokens"] == live_calls[-1].usage["input_tokens"]
    assert row["output_tokens"] == live_calls[-1].usage["output_tokens"]


def test_ungranted_agents_never_reach_the_wire(product, live_calls, registry):
    client, ledger = product
    before = len(live_calls)
    for agent in ("a0_supervisor", "a1_fundamentals", "a9_attribution", "a13_sizing"):
        with pytest.raises(PolicyViolation, match="may not call 'llm_complete'"):
            client.complete(agent, TaskClass.ADHOC_QUERY, "must not be sent")
    assert len(live_calls) == before
    assert ledger.calls_for_run("qa-live") == []


def test_the_ledger_bills_exactly_what_the_meter_measured(product, live_calls, meter):
    """The old pin changed the model table only, and this test measured a 5x
    over-billing. Under `FINPLANET_CHEAP` the tier, model and price resolve
    together, so the product's ledger and the QA meter - which prices off the
    model that actually answered - must now agree to the cent and beyond."""
    client, ledger = product
    client.complete("a10_thesis", TaskClass.THESIS_SYNTHESIS, "Reply with OK.")
    row = ledger.calls_for_run("qa-live")[-1]
    real = Decimal(meter.calls[-1]["usd"])
    assert real > 0
    assert Decimal(row["cost_usd"]) == real, (
        f"ledger USD {row['cost_usd']} vs metered USD {real}")


def test_the_trace_captures_a_real_call_verbatim(product, tmp_path):
    from core.trace.report import write_all

    client, _ = product
    with start_run("qa-live", root=tmp_path) as tracer:
        out = client.complete(
            "a4_news_narrative", TaskClass.NEWS_TRIAGE,
            "Headline: Maybank cuts guidance. Reply with one word: negative or positive.")
    summary = tracer.summary()
    write_all(summary["dir"])

    calls = [e for e in tracer.events if e.kind == "llm_call"]
    assert len(calls) == 1
    ev = calls[0].data
    assert ev["backend"] == "AnthropicBackend" and ev["model_id"] == CHEAP_MODEL
    assert ev["prompt"].startswith("Headline: Maybank") and ev["response"].strip()
    assert ev["latency_ms"] > 0 and float(ev["cost_myr"]) > 0
    assert out.request_id, "the SDK's request id survives to the Completion"
    assert summary["llm"]["calls"] == 1 and summary["errors"] == []
    for name in ("report.html", "anatomy.md", "session.log", "trace.jsonl", "summary.json"):
        assert (tracer.dir / name).exists(), name


def test_ask_backend_reports_the_sdk_and_the_cap(run_cli):
    out = run_cli(["ask.py", "backend"], key=api_key())
    assert out.returncode == 0, out.stderr
    assert "AnthropicBackend" in out.stdout
    assert "official SDK" in out.stdout
    assert "FINPLANET_CHEAP" not in out.stdout, "no cap env, no cap claim"

    capped = run_cli(["ask.py", "backend"], key=api_key(),
                     env_extra={"FINPLANET_CHEAP": "1"})
    assert "FINPLANET_CHEAP" in capped.stdout, "the cap is disclosed wherever a backend is named"

    echo = run_cli(["ask.py", "backend", "--use", "echo"], key=api_key())
    assert "EchoBackend" in echo.stdout, "a key in the env must not override an explicit echo"


def test_the_daily_budget_stops_a_live_call_before_the_wire(registry, product_backend, live_calls):
    ledger = ProvenanceLedger(run_id="qa-budget")
    ledger.record_call("a10_thesis", TaskClass.THESIS_SYNTHESIS, Tier.REASON, "claude-opus-5",
                       "earlier today", Usage(input_tokens=2_000_000, output_tokens=0))
    client = InferenceClient(product_backend, default_engine(registry.allowlist()), ledger,
                             daily_budget_myr=Decimal("25"))
    before = len(live_calls)
    with pytest.raises(BudgetExceeded):
        client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, "must not be sent")
    assert len(live_calls) == before


def test_a_live_truncation_carries_its_usage_and_the_meter_saw_the_spend(meter, live_calls, cheap):
    """Through the backend directly: routed calls take the tier profile's
    max_tokens, so the 1-token ceiling that forces a live truncation is only
    reachable at the seam itself. The client-side ledgering of that raise is
    pinned keylessly in phase 1 and in tests/test_qa_findings.py."""
    backend = AnthropicBackend(max_tokens=1, client=metered_client(meter, live_calls, key=api_key()))
    with pytest.raises(Truncated) as exc:
        backend.complete(CHEAP_MODEL, "Write three paragraphs about clearing fees.", None)
    assert exc.value.usage is not None and exc.value.usage.output_tokens == 1
    assert live_calls[-1].usage["output_tokens"] == 1, "the token was spent and metered"


def test_structured_output_flows_live_through_the_cheap_tier(product):
    from pydantic import BaseModel

    class Verdict(BaseModel):
        driver: str
        confidence: float

    client, ledger = product
    model, done = client.complete_structured(
        "a4_news_narrative", TaskClass.NEWS_TRIAGE,
        "A stock fell 9% on a day its index fell 8%. Classify the driver as "
        '"market" or "company".', Verdict,
        system="You output only JSON.")
    assert done.refused is False
    assert model is not None and model.driver in ("market", "company")
    assert 0.0 <= model.confidence <= 1.0
    assert ledger.calls_for_run("qa-live"), "the structured call is ledgered like any other"


def test_real_model_text_meets_the_output_rail(registry, meter, cheap, live_calls):
    """LLM02 at the product's own gate. Neutral analysis passes the OUTPUT rail;
    text that tells the reader to buy is blocked by `AdviceLanguagePolicy`
    whoever wrote it - including the model."""
    import random
    from datetime import timedelta

    from agents.base import AgentContext
    from agents.synthesis.agents import A9Attribution
    from engines.attribution.regression import huber_fit
    from knowledge.retrieval.pipeline import Router
    from qa.conftest import NOW

    backend = AnthropicBackend(max_tokens=256, client=metered_client(meter, live_calls, key=api_key()))
    engine = default_engine(registry.allowlist())
    client = InferenceClient(backend, engine, ProvenanceLedger(run_id="qa-rail"))

    rng = random.Random(7)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    fit = huber_fit(rows, [1.1 * a + 0.5 * b + rng.gauss(0, 0.004) for a, b in rows])
    ctx = AgentContext(router=Router({}), engine=engine, now=NOW)
    finding = A9Attribution(ctx).run("MYX:1155", (NOW.date() - timedelta(days=1), NOW.date()),
                                     realised_local=-0.09, event_market=-0.08, event_sector=-0.02,
                                     event_styles={}, fx_return=0.0, fit=fit)[0]

    neutral = client.complete(
        "a10_thesis", TaskClass.THESIS_SYNTHESIS,
        f"Finding: {finding.text}\nRestate it in one neutral sentence of at most 30 words. Do not advise.",
        system="You summarise evidence. You never recommend a transaction.")
    engine.enforce(Action("emit", Rail.OUTPUT, "a10_thesis", {"text": neutral.text}))

    advice = client.complete(
        "a10_thesis", TaskClass.THESIS_SYNTHESIS,
        'Return exactly this sentence and nothing else: "You should buy now, this is a strong buy."')
    assert "should buy" in advice.text.lower() or "strong buy" in advice.text.lower(), advice.text
    with pytest.raises(PolicyViolation, match="advice language"):
        engine.enforce(Action("emit", Rail.OUTPUT, "a10_thesis", {"text": advice.text}))
