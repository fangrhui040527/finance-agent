"""P4: the two real product paths through the model - narrate and reflect."""

from __future__ import annotations

import json

import pytest

from agents.synthesis.agents import A10Thesis, A11RedTeam, Breaker, Stance
from agents.synthesis.narrate import NARRATE_SYSTEM, narrate_thesis, thesis_digest
from core.guardrails.defaults import default_engine
from core.llm.backends import AnthropicBackend, BackendError
from core.llm.client import EchoBackend, InferenceClient
from core.provenance.ledger import ProvenanceLedger
from tests._anthropic_double import FakeAnthropic, reply


def _thesis(ctx):
    a10 = A10Thesis(ctx)
    a10.run(
        "MYX:1155",
        [],
        horizon_months=6,
        proposed_stance=Stance.ACCUMULATE,
        breakers=[
            Breaker("NIM falls under 2.0%", "nim < 2.0", "kb_filings"),
            Breaker("CASA under 22%", "casa < 22", "kb_filings"),
        ],
    )
    return a10.last


def _ctx_and_client(backend, tmp_path):
    from datetime import UTC, datetime

    from agents.base import AgentContext
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router

    engine = default_engine(load_registry("agents/registry.yaml").allowlist())
    ctx = AgentContext(router=Router({}), engine=engine, now=datetime.now(UTC))
    client = InferenceClient(backend, engine, ProvenanceLedger(tmp_path / "l.db"))
    return ctx, client


def test_the_system_prompt_is_byte_stable():
    """It carries the cache breakpoint; a volatile byte invalidates the cache."""
    from agents.synthesis import narrate

    assert narrate.NARRATE_SYSTEM == NARRATE_SYSTEM
    assert "{" not in NARRATE_SYSTEM  # no interpolation slots survive


def test_the_digest_carries_only_engine_output(tmp_path):
    ctx, _ = _ctx_and_client(EchoBackend(), tmp_path)
    thesis = _thesis(ctx)
    challenges = A11RedTeam(ctx).run(thesis)
    digest = thesis_digest(thesis, challenges)
    assert "MYX:1155" in digest
    assert f"confidence: {thesis.confidence:.2f}" in digest
    assert "NIM falls under 2.0%" in digest


def test_narrate_on_echo_is_ledgered_and_labelled_text_comes_back(tmp_path):
    ctx, client = _ctx_and_client(EchoBackend(), tmp_path)
    thesis = _thesis(ctx)
    done = narrate_thesis(client, thesis, [])
    assert done.text.startswith("[claude-")  # the echo stub names the model, visibly
    assert not done.refused
    assert len(list(client.ledger.calls())) == 1


def test_a_model_refusal_is_content_not_an_exception(tmp_path):
    msg = reply("", stop_reason="refusal", usage={"input_tokens": 5, "output_tokens": 1})
    backend = AnthropicBackend(client=FakeAnthropic([msg]), sleep=lambda _s: None)
    ctx, client = _ctx_and_client(backend, tmp_path)
    thesis = _thesis(ctx)
    done = narrate_thesis(client, thesis, [])
    assert done.refused and done.text == ""
    assert len(list(client.ledger.calls())) == 1  # billed, so ledgered


def test_advice_verbs_from_the_model_are_blocked_by_the_output_rail(tmp_path):
    from core.guardrails.policy import Action, PolicyViolation, Rail

    ctx, _ = _ctx_and_client(EchoBackend(), tmp_path)
    with pytest.raises(PolicyViolation):
        ctx.engine.enforce(
            Action(
                name="narrate",
                rail=Rail.OUTPUT,
                agent="a10_thesis",
                payload={"text": "Strong buy, you should buy this now."},
            )
        )


# --- A15 structured second opinion ---------------------------------------------


def _a15(tmp_path, backend):
    from datetime import UTC, datetime

    from agents.base import AgentContext
    from agents.learning.reflection import A15Reflection, LessonStore, OutcomeQueue
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router

    engine = default_engine(load_registry("agents/registry.yaml").allowlist())
    ctx = AgentContext(router=Router({}), engine=engine, now=datetime.now(UTC))
    agent = A15Reflection(ctx, OutcomeQueue(), LessonStore())
    client = InferenceClient(backend, engine, ProvenanceLedger(tmp_path / "l.db"))
    return agent, client


def _outcomes(n=5):
    from datetime import date

    from agents.learning.reflection import Outcome

    return [Outcome(f"p{i}", date(2026, 3, 1), 0.01, 0.0, i % 2 == 0, "") for i in range(n)]


def test_a_valid_no_lesson_reply_validates(tmp_path):
    body = json.dumps({"root": {"kind": "no_lesson", "reason": "only 5 instances on 2 names"}})
    backend = AnthropicBackend(client=FakeAnthropic([reply(body)]), sleep=lambda _s: None)
    agent, client = _a15(tmp_path, backend)
    parsed, done = agent.second_opinion(client, "earnings_beat_fade", _outcomes())
    assert parsed.root.kind == "no_lesson"
    assert not done.refused


def test_a_valid_lesson_proposal_validates(tmp_path):
    body = json.dumps(
        {
            "root": {
                "kind": "lesson",
                "text": "fade day-two moves on thin volume",
                "pattern": "earnings_beat_fade",
                "counter_example_search": "checked 2019-2026, 3 counter-cases",
            }
        }
    )
    backend = AnthropicBackend(client=FakeAnthropic([reply(body)]), sleep=lambda _s: None)
    agent, client = _a15(tmp_path, backend)
    parsed, _ = agent.second_opinion(client, "earnings_beat_fade", _outcomes())
    assert parsed.root.kind == "lesson"
    assert parsed.root.counter_example_search


def test_a_malformed_reply_is_an_error_never_a_guessed_no_lesson(tmp_path):
    backend = AnthropicBackend(
        client=FakeAnthropic([reply("I think probably no lesson here?")]), sleep=lambda _s: None
    )
    agent, client = _a15(tmp_path, backend)
    with pytest.raises(BackendError, match="does not validate"):
        agent.second_opinion(client, "p", _outcomes())


def test_a_refusal_reaches_the_caller_as_refused_not_as_a_lesson(tmp_path):
    msg = reply("", stop_reason="refusal", usage={"input_tokens": 5, "output_tokens": 1})
    backend = AnthropicBackend(client=FakeAnthropic([msg]), sleep=lambda _s: None)
    agent, client = _a15(tmp_path, backend)
    parsed, done = agent.second_opinion(client, "p", _outcomes())
    assert parsed is None and done.refused


def test_the_schema_hint_reaches_the_model(tmp_path):
    fake = FakeAnthropic([reply(json.dumps({"root": {"kind": "no_lesson", "reason": "x"}}))])
    backend = AnthropicBackend(client=fake, sleep=lambda _s: None)
    agent, client = _a15(tmp_path, backend)
    agent.second_opinion(client, "p", _outcomes())
    sent = fake.calls[0]["messages"][0]["content"]
    assert "single JSON object" in sent and "no_lesson" in sent
