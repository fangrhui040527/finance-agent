"""Phase 1 - the model seam, driven through the SDK `client=` test seam.

The product moved off urllib onto the official `anthropic` SDK; the injection
point moved with it, from `opener=` to `client=` (any object exposing
`.messages.create/.stream`). `tests/test_anthropic_backend.py` and the ported
LLM sections of `tests/test_qa_findings.py` own the core behaviours now -
retry-after, auth mapping, truncation and refusal usage, the cacheable system
block. This file keeps what those do not: the REQUEST PROFILES a tier shapes a
call with, the cheap cap's model-and-billing resolution, block handling, and
the hard attempt ceiling.

Nothing here touches the network. Every test scripts the SDK double.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.llm.backends import AnthropicBackend, BackendError, TransientError, backend_from_env
from core.llm.tiers import (
    MODEL_IDS, PRICING_USD, REQUEST_PROFILES, Tier, Usage, cheap_capped, cost_usd, effective_tier,
)
from tests._anthropic_double import FakeAnthropic, connection_error, http_status_error, reply


def backend(script=None, **kw) -> tuple[AnthropicBackend, FakeAnthropic]:
    fake = FakeAnthropic(script)
    kw.setdefault("sleep", lambda _s: None)
    # The fallback backoff is jittered in production (uniform(0.5, 1.0) x 2^n);
    # pinning it to 1.0 keeps the exact-curve assertions meaningful. A numeric
    # retry-after is never jittered, and tests/test_qa_findings.py holds both.
    kw.setdefault("jitter", lambda lo, hi: 1.0)
    return AnthropicBackend(client=fake, **kw), fake


# -- what the request looks like, per profile -----------------------------------

def test_a_profileless_call_is_one_user_turn_with_the_backends_own_ceiling():
    be, fake = backend([reply()], max_tokens=77)
    be.complete("claude-haiku-4-5", "hello", "be terse")

    call = fake.calls[0]
    assert call["method"] == "create"
    assert call["model"] == "claude-haiku-4-5"
    assert call["max_tokens"] == 77
    assert call["messages"] == [{"role": "user", "content": "hello"}], (
        "single-turn by construction: no assistant history, no thinking block can ever be resent")
    assert call["system"] == [{"type": "text", "text": "be terse",
                               "cache_control": {"type": "ephemeral"}}]
    for absent in ("thinking", "output_config", "tools", "tool_choice", "metadata"):
        assert absent not in call, f"a profileless call must not send {absent}"


def test_the_cheap_profile_never_sends_thinking_or_effort():
    """Haiku rejects `thinking`/`effort` with a 400. The profile table is the
    one place that knowledge lives; the request must obey it."""
    be, fake = backend([reply()])
    be.complete("claude-haiku-4-5", "q", None, profile=REQUEST_PROFILES[Tier.CHEAP])
    call = fake.calls[0]
    assert call["method"] == "create"
    assert call["max_tokens"] == REQUEST_PROFILES[Tier.CHEAP].max_tokens == 1024
    assert "thinking" not in call and "output_config" not in call
    assert "system" not in call, "no system prompt was given; none may be invented"


def test_the_reason_profile_streams_with_adaptive_thinking_and_high_effort():
    be, fake = backend([reply("long thesis")])
    text, _ = be.complete("claude-opus-5", "q", "system", profile=REQUEST_PROFILES[Tier.REASON])
    assert text == "long thesis"
    call = fake.calls[0]
    assert call["method"] == "stream", "long generations stream so they cannot die on an idle timeout"
    assert call["max_tokens"] == 16000
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}


# -- response handling ----------------------------------------------------------

def test_non_ascii_text_survives_intact():
    sample = "馬來亞銀行 NIM ↑ 2.31% — 📈 naïve façade"
    be, _ = backend([reply(sample)])
    text, _ = be.complete("m", "q", None)
    assert text == sample


def test_thinking_and_tool_use_blocks_are_dropped_not_stringified():
    blocks = [
        SimpleNamespace(type="thinking", thinking="let me think", signature="EqX..."),
        SimpleNamespace(type="tool_use", id="toolu_1", name="x", input={}),
        {"type": "text", "text": "the answer"},
    ]
    be, _ = backend([reply(content=blocks)])
    text, _ = be.complete("m", "q", None)
    assert text == "the answer"


def test_a_response_with_no_usage_raises_rather_than_ledgering_zero():
    naked = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")],
                            usage=None, stop_reason="end_turn")
    be, _ = backend([naked])
    with pytest.raises(BackendError, match="no usage"):
        be.complete("m", "q", None)


def test_empty_text_raises_because_silence_is_not_an_answer():
    be, _ = backend([reply("   ")])
    with pytest.raises(BackendError, match="no text block"):
        be.complete("m", "q", None)


# -- the attempt ceiling ---------------------------------------------------------

def test_the_attempt_budget_is_a_hard_ceiling_not_a_suggestion():
    """Script FIVE failures but allow two attempts: reaching TransientError with
    exactly two recorded calls proves the loop cannot spin."""
    delays: list[float] = []
    be, fake = backend([connection_error() for _ in range(5)], max_attempts=2,
                       sleep=delays.append)
    with pytest.raises(TransientError, match="unreachable"):
        be.complete("m", "q", None)
    assert len(fake.calls) == 2
    assert delays == [1.0], "one sleep fewer than attempts, exponential base"


@pytest.mark.parametrize("status", [500, 502, 503, 529])
def test_server_side_noise_is_transient(status):
    be, fake = backend([http_status_error(status)] * 2, max_attempts=2)
    with pytest.raises(TransientError, match=str(status)):
        be.complete("m", "q", None)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("status", [400, 404, 413])
def test_client_errors_are_permanent_and_cost_one_request(status):
    be, fake = backend([http_status_error(status)] * 3, max_attempts=3)
    with pytest.raises(BackendError) as exc:
        be.complete("m", "q", None)
    assert not isinstance(exc.value, TransientError)
    assert len(fake.calls) == 1


# -- the cheap cap ---------------------------------------------------------------

def test_finplanet_cheap_resolves_model_and_billing_together(monkeypatch):
    """The QA pin used to change the MODEL table only, so the ledger over-billed
    a pinned reason call by 5x. `FINPLANET_CHEAP=1` moves model and price as one:
    what is called is what is billed."""
    monkeypatch.delenv("FINPLANET_CHEAP", raising=False)
    assert not cheap_capped()
    assert effective_tier(Tier.REASON) is Tier.REASON

    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    assert cheap_capped()
    assert effective_tier(Tier.REASON) is Tier.CHEAP
    assert effective_tier(Tier.BALANCED) is Tier.CHEAP
    assert effective_tier(Tier.EMBED) is Tier.EMBED, "not a Messages tier; never capped"
    assert effective_tier(Tier.LOCAL) is Tier.LOCAL
    assert MODEL_IDS[effective_tier(Tier.REASON)] == "claude-haiku-4-5"

    million = Usage(1_000_000, 0)
    assert cost_usd(effective_tier(Tier.REASON), million) == PRICING_USD[Tier.CHEAP][0], (
        "billing follows the resolved tier, not the asked-for one")


def test_the_cheap_cap_is_disclosed_wherever_a_backend_is_named(monkeypatch, no_key_env):
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    _, reason = backend_from_env()
    assert "FINPLANET_CHEAP" in reason, "a silent cap is how a dev run lies about what ran"
    monkeypatch.delenv("FINPLANET_CHEAP")
    _, reason = backend_from_env()
    assert "FINPLANET_CHEAP" not in reason


def test_the_deprecated_qa_pin_still_restores_the_model_table():
    """`qa/_support/cheap.py::cheap_models` predates the cap and a few tools may
    still reach for it; it must keep cleaning up after itself."""
    from qa._support.cheap import cheap_models

    original = dict(MODEL_IDS)
    with cheap_models():
        assert MODEL_IDS[Tier.REASON] == "claude-haiku-4-5"
    assert dict(MODEL_IDS) == original
