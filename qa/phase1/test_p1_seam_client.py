"""Phase 1 - `InferenceClient` on the REAL registry, with a scripted SDK client.

The seam every model call passes through, exercised the way production would:
the allowlist derived from `agents/registry.yaml`, the append-only ledger, the
trace, the daily budget, and the per-tier request profiles - with the only fake
being the SDK client. This is the keyless twin of
`qa/phase2/test_p2_product_seam_live.py`; the two assert the same things and
differ only in whether a real model answered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.guardrails.defaults import default_engine
from core.guardrails.policy import PolicyViolation
from core.llm.backends import AnthropicBackend, Truncated
from core.llm.client import BudgetExceeded, InferenceClient
from core.llm.tiers import (
    MODEL_IDS, REQUEST_PROFILES, TaskClass, Tier, Usage, cost_usd, route,
)
from core.provenance.ledger import ProvenanceLedger
from core.trace import start_run
from tests._anthropic_double import FakeAnthropic, reply

#: The four agents `agents/registry.yaml` grants `llm_complete`, with the task
#: class each documents. Everything else must be refused before the wire.
GRANTED = {
    "a4_news_narrative": TaskClass.NEWS_TRIAGE,
    "a10_thesis": TaskClass.THESIS_SYNTHESIS,
    "a11_red_team": TaskClass.RED_TEAM,
    "a15_reflection": TaskClass.REFLECTION_DEEP,
}


def make_client(registry, script, ledger=None, **client_kw):
    fake = FakeAnthropic(list(script))
    backend = AnthropicBackend(client=fake, sleep=lambda _s: None)
    ledger = ledger or ProvenanceLedger()
    client = InferenceClient(backend, default_engine(registry.allowlist()), ledger, **client_kw)
    return client, fake, ledger


def test_the_registry_grants_llm_complete_to_exactly_four_agents(registry):
    holders = {aid for aid, spec in registry.agents.items() if "llm_complete" in spec.tools}
    assert holders == set(GRANTED)


def test_only_granted_agents_reach_the_wire_each_with_its_tiers_model_and_profile(registry, monkeypatch):
    monkeypatch.delenv("FINPLANET_CHEAP", raising=False)
    client, fake, ledger = make_client(
        registry, [reply(f"answer {i}") for i in range(len(GRANTED))])

    for agent, task in GRANTED.items():
        before = len(fake.calls)
        out = client.complete(agent, task, f"prompt for {agent}")
        assert len(fake.calls) == before + 1
        tier = route(task)
        call = fake.calls[-1]
        assert call["model"] == MODEL_IDS[tier] == out.model_id
        assert call["max_tokens"] == REQUEST_PROFILES[tier].max_tokens, (
            "the profile, not the backend default, sizes a routed call")
        assert call["method"] == ("stream" if REQUEST_PROFILES[tier].stream else "create")
        assert out.tier is tier and out.refused is False

    for agent in sorted(set(registry.agents) - set(GRANTED)):
        before = len(fake.calls)
        with pytest.raises(PolicyViolation, match="may not call 'llm_complete'"):
            client.complete(agent, TaskClass.ADHOC_QUERY, "should never be sent")
        assert len(fake.calls) == before, f"{agent} reached the wire without a grant"

    assert len(list(ledger.calls())) == len(GRANTED), "denied calls must not be ledgered"


def test_an_unregistered_agent_is_refused_by_name(registry):
    client, fake, _ = make_client(registry, [reply()])
    with pytest.raises(PolicyViolation, match="no registered toolset"):
        client.complete("a99_intruder", TaskClass.NEWS_TRIAGE, "x")
    assert fake.calls == []


def test_the_ledger_row_matches_what_crossed_the_wire(registry):
    client, fake, ledger = make_client(registry, [reply(
        "text", usage={"input_tokens": 123, "output_tokens": 45,
                       "cache_read_input_tokens": 23, "cache_creation_input_tokens": 7})])
    out = client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, "triage this")

    rows = list(ledger.calls())
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "a4_news_narrative"
    assert row["task_class"] == "news_triage"
    assert row["tier"] == Tier.CHEAP.value
    assert row["model_id"] == fake.calls[0]["model"]
    assert (row["input_tokens"], row["output_tokens"]) == (123, 45)
    assert (row["cached_tokens"], row["cache_write_tokens"]) == (23, 7)
    assert Decimal(row["cost_usd"]) == cost_usd(Tier.CHEAP, Usage(123, 45, 23, 7))
    assert Decimal(row["cost_myr"]) == out.cost_myr
    assert row["latency_ms"] > 0, "latency is recorded on every call, not only when tracing"
    assert row["prompt_hash"] and len(row["prompt_hash"]) == 16, "the ledger keeps a hash, not text"


def test_the_cheap_cap_calls_haiku_and_bills_haiku(registry, monkeypatch):
    """The old QA pin swapped the model table only, and the ledger over-billed
    by exactly the tier ratio. `FINPLANET_CHEAP=1` resolves tier, model AND
    price together: the ledger records what was truly spent."""
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    client, fake, ledger = make_client(registry, [reply(
        "thesis", usage={"input_tokens": 1_000_000, "output_tokens": 0})])
    out = client.complete("a10_thesis", TaskClass.THESIS_SYNTHESIS, "compose")

    call = fake.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["method"] == "create" and "thinking" not in call, (
        "the resolved tier's profile applies: Haiku takes no thinking/effort")
    assert out.tier is Tier.CHEAP
    row = list(ledger.calls())[0]
    assert row["tier"] == "cheap"
    assert Decimal(row["cost_usd"]) == Decimal("1.00"), "a million input tokens at Haiku's rate"


def test_the_daily_budget_is_checked_before_the_wire_and_is_windowed(registry):
    ledger = ProvenanceLedger()
    spent = Usage(input_tokens=2_000_000, output_tokens=0)          # USD 10 at the reason rate
    ledger.record_call("a10_thesis", TaskClass.THESIS_SYNTHESIS, Tier.REASON,
                       "claude-opus-5", "old", spent,
                       at=datetime.now(UTC) - timedelta(days=2))
    client, fake, _ = make_client(registry, [reply()], ledger=ledger,
                                  daily_budget_myr=Decimal("25"))
    client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, "fine: the spend is old")
    assert len(fake.calls) == 1

    ledger.record_call("a10_thesis", TaskClass.THESIS_SYNTHESIS, Tier.REASON,
                       "claude-opus-5", "today", spent)                # RM 41.50 now
    with pytest.raises(BudgetExceeded, match="exhausted"):
        client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, "refused before send")
    assert len(fake.calls) == 1, "a budget refusal must not cost a request"


def test_a_truncated_answer_is_raised_and_its_spend_is_ledgered(registry):
    """`stop_reason=max_tokens` still raises - a half-written thesis is never
    returned as a whole one - and the spend is ledgered before the raise."""
    client, fake, ledger = make_client(registry, [reply(
        "half a thesis", stop_reason="max_tokens",
        usage={"input_tokens": 900, "output_tokens": 4096})])
    with pytest.raises(Truncated, match="max_tokens"):
        client.complete("a10_thesis", TaskClass.THESIS_SYNTHESIS, "write a long memo")
    assert len(fake.calls) == 1
    rows = list(ledger.calls())
    assert len(rows) == 1, "the spend must not vanish with the exception"
    assert rows[0]["output_tokens"] == 4096 and rows[0]["agent"] == "a10_thesis"


def test_a_refusal_is_ledgered_and_returned_as_content_on_the_real_registry(registry):
    """Commitment 6 through the whole stack: a model refusal comes back as a
    `Completion(refused=True)` the caller renders - never an exception, never a
    silent re-route to a model that might comply."""
    client, fake, ledger = make_client(registry, [reply(
        "", stop_reason="refusal", usage={"input_tokens": 30, "output_tokens": 2})])
    out = client.complete("a11_red_team", TaskClass.RED_TEAM, "argue against this thesis")
    assert out.refused is True and out.text == ""
    assert "declined" in (out.refusal_reason or "")
    assert len(fake.calls) == 1
    rows = list(ledger.calls())
    assert len(rows) == 1 and rows[0]["output_tokens"] == 2, "a refusal still cost tokens"


def test_the_trace_keeps_the_verbatim_prompt_and_names_the_backend(registry, tmp_path):
    client, _, _ = make_client(registry, [reply("short"), reply("long")])
    long_prompt = "evidence line\n" * 300                           # > INLINE_LIMIT chars
    with start_run("qa", root=tmp_path) as tracer:
        client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, "short prompt",
                        system="triage")
        client.complete("a11_red_team", TaskClass.RED_TEAM, long_prompt)

    calls = [e for e in tracer.events if e.kind == "llm_call"]
    assert len(calls) == 2
    short, long = calls
    assert short.data["backend"] == "AnthropicBackend"
    assert short.data["model_id"] == MODEL_IDS[Tier.CHEAP]
    assert short.data["prompt"] == "short prompt" and short.data["system"] == "triage"
    assert short.data["response"] == "short"
    assert short.data["latency_ms"] > 0
    blob = long.data["prompt"]
    assert isinstance(blob, dict) and blob["_blob"].startswith("prompts/")
    assert (tracer.dir / blob["_blob"]).read_text(encoding="utf-8") == long_prompt

    allowed = [e for e in tracer.events if e.kind == "allowed" and e.name == "llm_complete"]
    assert [e.data["agent"] for e in allowed] == ["a4_news_narrative", "a11_red_team"]
    assert (tracer.dir / "trace.jsonl").exists()


def test_complete_structured_validates_and_treats_a_refusal_as_a_refusal(registry):
    from pydantic import BaseModel

    class Verdict(BaseModel):
        driver: str
        confidence: float

    client, fake, _ = make_client(registry, [
        reply('{"driver": "market", "confidence": 0.8}'),
        reply("not json at all"),
        reply("", stop_reason="refusal", usage={"input_tokens": 5, "output_tokens": 1}),
    ])
    model, done = client.complete_structured(
        "a4_news_narrative", TaskClass.NEWS_TRIAGE, "classify", Verdict)
    assert model.driver == "market" and 0 <= model.confidence <= 1
    assert "JSON object matching this schema" in fake.calls[0]["messages"][0]["content"], (
        "the schema instruction is appended for the model to follow")

    from core.llm.backends import BackendError
    with pytest.raises(BackendError):
        client.complete_structured("a4_news_narrative", TaskClass.NEWS_TRIAGE, "classify", Verdict)

    model, done = client.complete_structured(
        "a4_news_narrative", TaskClass.NEWS_TRIAGE, "classify", Verdict)
    assert model is None and done.refused is True, "a refusal is not a parse failure"
