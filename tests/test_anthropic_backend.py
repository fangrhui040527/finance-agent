"""The real model backend, tested without a network and without a key.

Same property the GDELT tests hold the feed to: a BROKEN backend must never look
like a QUIET one. Every failure path raises. Nothing here returns empty text, and
nothing degrades a tier it was handed.

The backend sits on the official `anthropic` SDK now; the test seam moved from
`opener=` (raw urllib) to `client=` (any object with `.messages.create/stream`).
The behaviours pinned here are unchanged from the urllib version - the retry
loop is still OURS (max_retries=0 on the SDK client), so retry-after capping
and backoff stay observable through an injected sleep.
"""

from types import SimpleNamespace

import pytest

from core.llm.backends import (
    AnthropicBackend,
    AuthError,
    BackendError,
    Declined,
    TransientError,
    Truncated,
    backend_from_env,
)
from core.llm.tiers import Tier, Usage
from tests._anthropic_double import (
    FakeAnthropic,
    connection_error,
    http_status_error,
    reply,
)

KEY = "sk-ant-test-not-a-real-key"


def _backend(script=None, **kw) -> AnthropicBackend:
    kw.setdefault("sleep", lambda _s: None)  # no real waiting in tests
    kw.setdefault("client", FakeAnthropic(script))
    return AnthropicBackend(**kw)


# --- construction -------------------------------------------------------------


def test_missing_key_raises_at_construction_not_at_first_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError, match="empty or unset"):
        AnthropicBackend()


def test_blank_key_is_treated_as_missing():
    with pytest.raises(AuthError):
        AnthropicBackend(api_key="   ")


def test_a_prebuilt_client_skips_the_key_check():
    b = _backend([reply("hi")])
    text, _ = b.complete("claude-sonnet-5", "q", None)
    assert text == "hi"


def test_nonsense_bounds_are_refused():
    with pytest.raises(ValueError):
        _backend(max_tokens=0)
    with pytest.raises(ValueError):
        _backend(max_attempts=0)


# --- parsing ------------------------------------------------------------------


def test_text_and_usage_come_back_off_the_wire():
    text, usage = _backend(
        [reply("answer", usage={"input_tokens": 120, "output_tokens": 45})]
    ).complete("claude-sonnet-5", "q", None)
    assert text == "answer"
    assert usage == Usage(120, 45)


def test_multiple_text_blocks_are_joined():
    msg = reply(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    text, _ = _backend([msg]).complete("claude-sonnet-5", "q", None)
    assert text == "ab"


def test_non_text_blocks_are_skipped_not_stringified():
    thinking = SimpleNamespace(type="thinking", thinking="hmm", text=None)
    msg = reply(content=[thinking, {"type": "text", "text": "real"}])
    text, _ = _backend([msg]).complete("claude-sonnet-5", "q", None)
    assert text == "real"


def test_cache_reads_are_reported_so_cost_is_not_overstated():
    msg = reply(
        usage={
            "input_tokens": 20,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 7,
        }
    )
    _, usage = _backend([msg]).complete("claude-sonnet-5", "q", None)
    assert usage.cached_input_tokens == 100
    assert usage.cache_write_tokens == 7
    assert usage.input_tokens == 20  # the UNCACHED remainder, never re-derived


def test_a_response_with_no_usage_raises_rather_than_ledgering_zero():
    msg = SimpleNamespace(content=[], usage=None, stop_reason="end_turn")
    with pytest.raises(BackendError, match="no usage"):
        _backend([msg]).complete("claude-sonnet-5", "q", None)


def test_non_numeric_usage_raises():
    bad = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="x")],
        usage=SimpleNamespace(
            input_tokens="many",
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )
    with pytest.raises(BackendError, match="not numeric"):
        _backend([bad]).complete("claude-sonnet-5", "q", None)


def test_empty_text_raises_because_silence_is_not_an_answer():
    with pytest.raises(BackendError, match="no text block"):
        _backend([reply("")]).complete("claude-sonnet-5", "q", None)


# --- stop reasons carry their spend -------------------------------------------


def test_max_tokens_stop_reason_raises_rather_than_returning_half_a_thesis():
    msg = reply(
        "half", stop_reason="max_tokens", usage={"input_tokens": 900, "output_tokens": 4096}
    )
    with pytest.raises(Truncated) as exc:
        _backend([msg]).complete("claude-opus-5", "q", None)
    assert exc.value.usage == Usage(900, 4096)


def test_refusal_is_surfaced_not_returned_as_an_answer():
    msg = reply("", stop_reason="refusal", usage={"input_tokens": 30, "output_tokens": 2})
    with pytest.raises(Declined, match="declined") as exc:
        _backend([msg]).complete("claude-opus-5", "q", None)
    assert isinstance(exc.value, BackendError) and exc.value.usage == Usage(30, 2)


def test_a_refusal_names_its_category_when_the_api_does():
    msg = reply("", stop_reason="refusal", usage={"input_tokens": 3, "output_tokens": 1})
    msg.stop_details = SimpleNamespace(type="refusal", category="cyber", explanation="no")
    with pytest.raises(Declined) as exc:
        _backend([msg]).complete("claude-opus-5", "q", None)
    assert exc.value.category == "cyber" and exc.value.explanation == "no"


# --- request shape ------------------------------------------------------------


def test_request_carries_the_system_prompt_as_a_cacheable_block():
    fake = FakeAnthropic([reply()])
    _backend(client=fake).complete("claude-sonnet-5", "q", "you are terse")
    sent = fake.calls[0]
    assert sent["system"] == [
        {"type": "text", "text": "you are terse", "cache_control": {"type": "ephemeral"}}
    ]
    assert sent["messages"] == [{"role": "user", "content": "q"}]


def test_system_is_omitted_entirely_when_absent():
    fake = FakeAnthropic([reply()])
    _backend(client=fake).complete("claude-sonnet-5", "q", None)
    assert "system" not in fake.calls[0]


def test_max_tokens_is_sent_and_configurable():
    fake = FakeAnthropic([reply()])
    _backend(client=fake, max_tokens=777).complete("claude-sonnet-5", "q", None)
    assert fake.calls[0]["max_tokens"] == 777


def test_the_profile_shapes_the_request_per_tier():
    from core.llm.tiers import REQUEST_PROFILES

    fake = FakeAnthropic([reply()])
    _backend(client=fake).complete(
        "claude-sonnet-5", "q", None, profile=REQUEST_PROFILES[Tier.BALANCED]
    )
    sent = fake.calls[0]
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["max_tokens"] == 8000
    assert sent["method"] == "create"


def test_the_cheap_profile_sends_no_thinking_or_effort():
    """Haiku 4.5 rejects thinking/effort with a 400; the profile must not guess."""
    from core.llm.tiers import REQUEST_PROFILES

    fake = FakeAnthropic([reply()])
    _backend(client=fake).complete(
        "claude-haiku-4-5", "q", None, profile=REQUEST_PROFILES[Tier.CHEAP]
    )
    sent = fake.calls[0]
    assert "thinking" not in sent and "output_config" not in sent
    assert sent["max_tokens"] == 1024


def test_the_reason_profile_streams():
    from core.llm.tiers import REQUEST_PROFILES

    fake = FakeAnthropic([reply("long answer")])
    text, _ = _backend(client=fake).complete(
        "claude-opus-5", "q", None, profile=REQUEST_PROFILES[Tier.REASON]
    )
    assert text == "long answer"
    assert fake.calls[0]["method"] == "stream"


def test_request_id_is_kept_for_the_trace():
    b = _backend([reply(request_id="req_abc123")])
    b.complete("claude-sonnet-5", "q", None)
    assert b.last_request_id == "req_abc123"


# --- retries: the loop is ours ------------------------------------------------


def test_bad_key_raises_auth_and_is_not_retried():
    fake = FakeAnthropic([http_status_error(401)])
    with pytest.raises(AuthError):
        _backend(client=fake).complete("claude-sonnet-5", "q", None)
    assert len(fake.calls) == 1


def test_a_400_is_permanent_and_not_retried():
    fake = FakeAnthropic([http_status_error(400)])
    with pytest.raises(BackendError):
        _backend(client=fake).complete("claude-sonnet-5", "q", None)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_transient_statuses_are_retried_then_raised(status):
    fake = FakeAnthropic([http_status_error(status)] * 3)
    with pytest.raises(TransientError):
        _backend(client=fake, max_attempts=3).complete("claude-sonnet-5", "q", None)
    assert len(fake.calls) == 3


def test_a_retry_that_succeeds_returns_the_answer():
    fake = FakeAnthropic([http_status_error(529), reply("recovered")])
    text, _ = _backend(client=fake).complete("claude-sonnet-5", "q", None)
    assert text == "recovered"
    assert len(fake.calls) == 2


def test_backoff_is_exponential_and_bounded():
    """The curve doubles, and `jitter` is injected so it is still exact."""
    waits: list[float] = []
    fake = FakeAnthropic([http_status_error(500)] * 3)
    with pytest.raises(TransientError):
        _backend(
            client=fake,
            max_attempts=3,
            sleep=waits.append,
            jitter=lambda lo, hi: 1.0,
        ).complete("claude-sonnet-5", "q", None)
    assert waits == [1.0, 2.0]


def test_backoff_is_jittered_so_clients_do_not_reconverge():
    """Two clients that hit the same 529 must not return at the same instant.

    Without jitter every caller backs off on the identical curve and collides
    again on each attempt - the thundering herd the backoff exists to break up.
    This system has three surfaces that can be talking to the same endpoint.
    """
    curves = []
    for _ in range(6):
        waits: list[float] = []
        fake = FakeAnthropic([http_status_error(529)] * 3)
        with pytest.raises(TransientError):
            _backend(client=fake, max_attempts=3, sleep=waits.append).complete(
                "claude-sonnet-5", "q", None
            )
        curves.append(tuple(waits))
    assert len(set(curves)) > 1, f"identical curve every time: {curves[0]}"
    # Still bounded, and still growing: draw n is in [0.5, 1.0] x 2^(n-1).
    for first, second in curves:
        assert 0.5 <= first <= 1.0
        assert 1.0 <= second <= 2.0


def test_an_unreachable_host_is_transient():
    fake = FakeAnthropic([connection_error()] * 2)
    with pytest.raises(TransientError, match="unreachable"):
        _backend(client=fake, max_attempts=2).complete("claude-sonnet-5", "q", None)


# --- backend_from_env ---------------------------------------------------------


def test_no_key_falls_back_to_echo_and_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    backend, reason = backend_from_env()
    assert type(backend).__name__ == "EchoBackend"
    assert "NOT a model" in reason


def test_a_key_selects_the_real_backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    backend, reason = backend_from_env()
    assert isinstance(backend, AnthropicBackend)
    assert "official SDK" in reason


def test_echo_can_be_forced_even_with_a_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("LLM_BACKEND", "echo")
    backend, reason = backend_from_env()
    assert type(backend).__name__ == "EchoBackend"
    assert "explicitly selected" in reason


def test_asking_for_anthropic_without_a_key_raises_rather_than_falling_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError):
        backend_from_env("anthropic")


def test_an_unknown_backend_name_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="unknown LLM_BACKEND"):
        backend_from_env("gemini")


def test_the_cheap_cap_is_disclosed_in_the_reason(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    _, reason = backend_from_env()
    assert "cheapest model" in reason


# --- the cheap cap itself -----------------------------------------------------


def test_the_cap_forces_reason_and_balanced_onto_the_cheap_tier(monkeypatch):
    from core.llm.tiers import Tier, effective_tier

    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    assert effective_tier(Tier.REASON) is Tier.CHEAP
    assert effective_tier(Tier.BALANCED) is Tier.CHEAP
    assert effective_tier(Tier.EMBED) is Tier.EMBED
    assert effective_tier(Tier.LOCAL) is Tier.LOCAL


def test_without_the_cap_tiers_pass_through(monkeypatch):
    from core.llm.tiers import Tier, effective_tier

    monkeypatch.delenv("FINPLANET_CHEAP", raising=False)
    assert effective_tier(Tier.REASON) is Tier.REASON


def test_the_capped_call_bills_at_haiku_rates_and_names_haiku(monkeypatch, tmp_path):
    from decimal import Decimal

    from core.guardrails.defaults import default_engine
    from core.llm.client import EchoBackend, InferenceClient
    from core.llm.tiers import TaskClass
    from core.provenance.ledger import ProvenanceLedger

    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    led = ProvenanceLedger(tmp_path / "l.db")
    client = InferenceClient(EchoBackend(), default_engine({"a1": {"llm_complete"}}), led)
    done = client.complete("a1", TaskClass.THESIS_SYNTHESIS, "x" * 400)
    assert done.model_id == "claude-haiku-4-5"
    assert done.tier.value == "cheap"
    row = list(led.calls())[0]
    assert row["model_id"] == "claude-haiku-4-5" and row["tier"] == "cheap"
    # Haiku's input rate is $1/MTok; 100 tokens of fresh input ~ $0.0001.
    assert Decimal(row["cost_usd"]) < Decimal("0.001")
