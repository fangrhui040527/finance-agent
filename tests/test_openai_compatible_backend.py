"""The free-provider backend, tested without a network and without a key.

Same property `test_anthropic_backend.py` holds the Anthropic backend to: a
BROKEN backend must never look like a QUIET one. Every failure path raises,
nothing returns empty text, nothing degrades a tier it was handed.

The seam is the urllib `opener` every keyless feed already takes, so the
doubles come from conftest and nothing is monkeypatched.
"""

from __future__ import annotations

import json
import urllib.error
from decimal import Decimal

import pytest

from core.llm import providers
from core.llm.backends import (
    AuthError,
    BackendError,
    ContextOverflow,
    Declined,
    OpenAICompatibleBackend,
    TransientError,
    Truncated,
    strip_thinking,
)
from core.llm.tiers import RequestProfile, Tier, Usage
from tests.conftest import http_error, scripted_opener

GROQ = providers.lookup("groq")
OLLAMA = providers.lookup("ollama")
OPENROUTER = providers.lookup("openrouter")
assert GROQ is not None and OLLAMA is not None and OPENROUTER is not None


def reply(
    text: str | list | None = "Margins compressed on funding cost.",
    *,
    finish: str = "stop",
    usage: dict | None = None,
    with_usage: bool = True,
    request_id: str = "chatcmpl-test",
) -> str:
    body: dict = {
        "id": request_id,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish,
            }
        ],
    }
    if with_usage:
        body["usage"] = (
            usage if usage is not None else {"prompt_tokens": 120, "completion_tokens": 45}
        )
    return json.dumps(body)


def _backend(script=None, provider=GROQ, capture=None, **kw) -> OpenAICompatibleBackend:
    kw.setdefault("sleep", lambda _s: None)  # no real waiting in tests
    kw.setdefault("opener", scripted_opener(script if script is not None else [reply()], capture))
    return OpenAICompatibleBackend(provider, **kw)


def _sent(capture: list, i: int = 0) -> dict:
    return json.loads(capture[i].data.decode())


# --- construction -------------------------------------------------------------


def test_missing_key_raises_at_construction_not_at_first_call(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(AuthError, match="GROQ_API_KEY is empty or unset"):
        OpenAICompatibleBackend(GROQ)


def test_blank_key_is_treated_as_missing():
    with pytest.raises(AuthError):
        OpenAICompatibleBackend(GROQ, api_key="   ")


def test_a_keyless_provider_needs_no_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    b = OpenAICompatibleBackend(OLLAMA, opener=scripted_opener([reply("hi")]))
    text, _ = b.complete("qwen3:8b", "q", None)
    assert text == "hi"


def test_a_prebuilt_opener_skips_the_key_check():
    b = _backend([reply("hi")])
    text, _ = b.complete("llama-3.1-8b-instant", "q", None)
    assert text == "hi"


def test_nonsense_bounds_are_refused():
    with pytest.raises(ValueError):
        _backend(max_tokens=0)
    with pytest.raises(ValueError):
        _backend(max_attempts=0)


def test_a_provider_without_a_base_url_cannot_be_built():
    generic = providers.lookup("openai-compatible")
    assert generic is not None and generic.base_url == ""
    with pytest.raises(ValueError, match="no base URL"):
        OpenAICompatibleBackend(generic, opener=scripted_opener([]))


# --- the request on the wire --------------------------------------------------


def test_request_goes_to_chat_completions_with_a_bearer_key():
    calls: list = []
    _backend([reply()], capture=calls, api_key="gsk_test").complete(
        "llama-3.1-8b-instant", "q", None
    )
    req = calls[0]
    assert req.full_url == "https://api.groq.com/openai/v1/chat/completions"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer gsk_test"
    assert req.get_header("Content-type") == "application/json"


def test_the_request_announces_this_process_not_python_urllib():
    """Groq's edge (Cloudflare) answers urllib's default User-Agent with a 403
    error 1010 before the key is looked at; the live probe found it. The
    backend sends the same identity every other fetcher in the repository
    sends."""
    calls: list = []
    _backend([reply()], capture=calls, api_key="gsk_test").complete("m", "q", None)
    ua = calls[0].get_header("User-agent")
    assert ua is not None and ua.startswith("finplanet-analyst-mind/")
    assert "urllib" not in ua.lower()


def test_request_body_is_model_messages_and_the_output_cap_only():
    calls: list = []
    profile = RequestProfile(max_tokens=777, adaptive_thinking=True, effort="high", stream=True)
    _backend([reply()], capture=calls).complete(
        "llama-3.3-70b-versatile", "the question", "the rules", profile=profile
    )
    body = _sent(calls)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [
        {"role": "system", "content": "the rules"},
        {"role": "user", "content": "the question"},
    ]
    assert body["max_tokens"] == 777
    # Anthropic's dials are not sent: no free endpoint takes them by that name
    # and an unknown parameter is a 400 on most.
    for absent in ("thinking", "output_config", "effort", "stream", "reasoning_effort"):
        assert absent not in body


def test_no_system_prompt_means_no_system_message():
    calls: list = []
    _backend([reply()], capture=calls).complete("m", "q", None)
    assert [m["role"] for m in _sent(calls)["messages"]] == ["user"]


def test_without_a_profile_the_backend_default_cap_is_sent():
    calls: list = []
    _backend([reply()], capture=calls, max_tokens=999).complete("m", "q", None)
    assert _sent(calls)["max_tokens"] == 999


def test_a_keyless_provider_sends_no_authorization_header():
    calls: list = []
    OpenAICompatibleBackend(OLLAMA, opener=scripted_opener([reply()], calls)).complete(
        "qwen3:8b", "q", None
    )
    assert calls[0].get_header("Authorization") is None
    assert calls[0].full_url == "http://localhost:11434/v1/chat/completions"


def test_a_provider_may_ask_for_extra_headers():
    calls: list = []
    _backend([reply()], provider=OPENROUTER, capture=calls).complete("m", "q", None)
    assert calls[0].get_header("Http-referer")


# --- parsing ------------------------------------------------------------------


def test_text_and_usage_come_back_off_the_wire():
    text, usage = _backend(
        [reply("answer", usage={"prompt_tokens": 120, "completion_tokens": 45})]
    ).complete("m", "q", None)
    assert text == "answer"
    assert usage == Usage(120, 45)


def test_cached_prompt_tokens_are_split_out_of_the_prompt_count():
    """OpenAI-style prompt_tokens INCLUDES the cached part; the ledger column
    holds the uncached remainder, as the Anthropic API reports it."""
    msg = reply(
        usage={
            "prompt_tokens": 120,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 100},
        }
    )
    _, usage = _backend([msg]).complete("m", "q", None)
    assert usage.cached_input_tokens == 100
    assert usage.input_tokens == 20
    assert usage.cache_write_tokens == 0


def test_content_as_a_list_of_parts_is_joined():
    parts = [
        {"type": "text", "text": "a"},
        {"type": "image_url", "url": "x"},
        {"type": "text", "text": "b"},
    ]
    text, _ = _backend([reply(parts)]).complete("m", "q", None)
    assert text == "ab"


def test_a_thinking_block_is_stripped_from_the_answer():
    text, _ = _backend([reply('<think>\nlet me see\n</think>\n{"ok": true}')]).complete(
        "m", "q", None
    )
    assert text == '{"ok": true}'


def test_a_reply_that_is_all_thinking_is_no_answer():
    with pytest.raises(BackendError, match="no text"):
        _backend([reply("<think>still going")]).complete("m", "q", None)


def test_a_response_with_no_usage_raises_rather_than_ledgering_zero():
    with pytest.raises(BackendError, match="no usage"):
        _backend([reply("x", with_usage=False)]).complete("m", "q", None)


def test_non_numeric_usage_raises():
    with pytest.raises(BackendError, match="not numeric"):
        _backend([reply("x", usage={"prompt_tokens": "many", "completion_tokens": 1})]).complete(
            "m", "q", None
        )


def test_empty_text_raises_because_silence_is_not_an_answer():
    with pytest.raises(BackendError, match="no text"):
        _backend([reply("")]).complete("m", "q", None)
    with pytest.raises(BackendError, match="no text"):
        _backend([reply(None)]).complete("m", "q", None)


def test_a_200_carrying_an_error_object_is_a_failure_not_silence():
    body = json.dumps({"error": {"message": "model is loading", "code": 503}})
    with pytest.raises(BackendError, match="no choices"):
        _backend([body]).complete("m", "q", None)


def test_a_body_that_is_not_json_is_a_failure():
    with pytest.raises(BackendError, match="not JSON"):
        _backend(["<html>rate limited</html>"]).complete("m", "q", None)
    with pytest.raises(BackendError, match="not an object"):
        _backend(["[1, 2]"]).complete("m", "q", None)


def test_request_id_comes_from_the_body_when_no_header_names_one():
    b = _backend([reply(request_id="chatcmpl-abc")])
    b.complete("m", "q", None)
    assert b.last_request_id == "chatcmpl-abc"


# --- finish reasons carry their spend -----------------------------------------


def test_length_finish_raises_rather_than_returning_half_a_thesis():
    msg = reply("half", finish="length", usage={"prompt_tokens": 900, "completion_tokens": 1024})
    with pytest.raises(Truncated) as exc:
        _backend([msg]).complete("m", "q", None)
    assert exc.value.usage == Usage(900, 1024)


def test_content_filter_is_surfaced_as_a_refusal_not_an_answer():
    msg = reply("", finish="content_filter", usage={"prompt_tokens": 30, "completion_tokens": 2})
    with pytest.raises(Declined, match="declined") as exc:
        _backend([msg]).complete("m", "q", None)
    assert exc.value.usage == Usage(30, 2)
    assert exc.value.category == "content_filter"


# --- HTTP failures ---------------------------------------------------------------


def test_a_rejected_key_is_an_auth_error_and_is_not_retried():
    calls: list = []
    with pytest.raises(AuthError, match="rejected the key"):
        _backend(
            [http_error(401, '{"error":{"message":"Invalid API Key"}}')], capture=calls
        ).complete("m", "q", None)
    assert len(calls) == 1


def test_an_unknown_model_names_the_variable_that_fixes_it():
    with pytest.raises(BackendError, match="LLM_MODEL_CHEAP") as exc:
        _backend([http_error(404, '{"error":{"message":"model not found"}}')]).complete(
            "llama-old:free", "q", None
        )
    assert "llama-old:free" in str(exc.value)


def test_a_400_about_context_length_is_a_context_overflow():
    err = http_error(400, '{"error":{"message":"this model\'s maximum context length is 8192"}}')
    with pytest.raises(ContextOverflow, match="Shrink the input"):
        _backend([err]).complete("m", "q", None)


def test_any_other_400_is_a_plain_backend_error_with_the_providers_words():
    calls: list = []
    with pytest.raises(BackendError, match="unsupported parameter") as exc:
        _backend([http_error(400, '{"error":"unsupported parameter"}')], capture=calls).complete(
            "m", "q", None
        )
    assert not isinstance(exc.value, TransientError)
    assert len(calls) == 1


def test_a_non_json_error_body_is_quoted_as_is():
    with pytest.raises(BackendError, match="Bad Gateway page"):
        _backend([http_error(418, "Bad Gateway page")], max_attempts=1).complete("m", "q", None)


def test_a_provider_that_echoes_the_key_does_not_get_it_into_the_error():
    """Some gateways quote the offending Authorization header back in the
    error body. The provider's words are relayed; the key is not, because the
    message reaches logs and the trace's error field (QA finding, 2026-09-04)."""
    key = "gsk_test_not_a_real_key_0123456789"
    echoed = json.dumps({"error": {"message": f"invalid header Authorization: Bearer {key}"}})
    with pytest.raises(BackendError) as exc:
        _backend([http_error(400, echoed)], api_key=key).complete("m", "q", None)
    text = str(exc.value)
    assert key not in text
    assert "invalid header Authorization: Bearer <redacted key>" in text


def test_a_bearer_token_that_is_not_ours_is_scrubbed_too():
    """A proxy may re-encode or rotate what it echoes; any bearer token goes."""
    echoed = json.dumps({"error": {"message": "upstream saw Bearer sk-or-v1-abcdef0123456789"}})
    with pytest.raises(BackendError, match=r"upstream saw Bearer <redacted>") as exc:
        _backend([http_error(400, echoed)]).complete("m", "q", None)
    assert "sk-or-v1" not in str(exc.value)


def test_429_is_retried_and_retry_after_is_obeyed_exactly():
    sleeps: list = []
    b = _backend(
        [http_error(429, headers={"Retry-After": "7"}), reply("second try")],
        sleep=sleeps.append,
        jitter=lambda a, b: 1.0,
        clock=lambda: 0.0,  # pacing sees no time passing and adds nothing
    )
    # pacing is off for this test: rpm-based waits would mix into `sleeps`
    b.provider = providers.Provider(**{**GROQ.__dict__, "rpm": None})
    text, _ = b.complete("m", "q", None)
    assert text == "second try"
    assert sleeps == [7.0]


def test_retry_after_is_capped_at_a_minute():
    sleeps: list = []
    b = _backend(
        [http_error(429, headers={"Retry-After": "3600"}), reply()],
        sleep=sleeps.append,
        provider=providers.Provider(**{**GROQ.__dict__, "rpm": None}),
    )
    b.complete("m", "q", None)
    assert sleeps == [60.0]


def test_5xx_backs_off_exponentially_then_raises_the_last_error():
    sleeps: list = []
    calls: list = []
    with pytest.raises(TransientError, match="503"):
        _backend(
            [http_error(503), http_error(503), http_error(503)],
            capture=calls,
            sleep=sleeps.append,
            jitter=lambda a, b: 1.0,
            provider=providers.Provider(**{**GROQ.__dict__, "rpm": None}),
        ).complete("m", "q", None)
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_transport_failure_is_transient_and_retried():
    calls: list = []
    text, _ = _backend(
        [urllib.error.URLError("connection reset"), reply("after retry")],
        capture=calls,
        provider=providers.Provider(**{**GROQ.__dict__, "rpm": None}),
    ).complete("m", "q", None)
    assert text == "after retry" and len(calls) == 2


def test_a_timeout_is_transient_too():
    with pytest.raises(TransientError, match="unreachable"):
        _backend([TimeoutError("timed out")], max_attempts=1).complete("m", "q", None)


# --- pacing to the free tier's allowance -----------------------------------------


def test_calls_are_spaced_to_the_providers_requests_per_minute():
    sleeps: list = []
    now = [100.0]
    b = _backend(
        [reply(), reply(), reply()], sleep=sleeps.append, clock=lambda: now[0]
    )  # groq: 30/min -> one every 2s
    b.complete("m", "q", None)
    assert sleeps == []  # the first call never waits
    b.complete("m", "q", None)
    assert sleeps == [2.0]
    now[0] += 5.0  # enough time has passed: no wait
    b.complete("m", "q", None)
    assert sleeps == [2.0]


def test_an_unpaced_provider_never_sleeps_between_calls():
    sleeps: list = []
    b = OpenAICompatibleBackend(
        OLLAMA, opener=scripted_opener([reply(), reply()]), sleep=sleeps.append, clock=lambda: 0.0
    )
    b.complete("m", "q", None)
    b.complete("m", "q", None)
    assert sleeps == []


# --- what the client reads off the backend -----------------------------------------


def test_model_for_serves_the_chat_tiers_and_nothing_else():
    b = _backend()
    assert b.model_for(Tier.REASON) == "openai/gpt-oss-120b"
    assert b.model_for(Tier.CHEAP) == "openai/gpt-oss-20b"
    assert b.model_for(Tier.EMBED) is None
    assert b.model_for(Tier.LOCAL) is None


def test_pricing_is_zero_on_every_chat_tier_and_undeclared_elsewhere():
    b = _backend()
    assert b.pricing_for(Tier.CHEAP) == (Decimal(0), Decimal(0))
    assert b.pricing_for(Tier.REASON) == (Decimal(0), Decimal(0))
    assert b.pricing_for(Tier.EMBED) is None


def test_the_effort_dial_is_declared_not_to_reach_this_backend():
    assert OpenAICompatibleBackend.takes_effort is False
    assert _backend().name == "groq"


# --- strip_thinking ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain", "plain"),
        ("<think>a</think>answer", "answer"),
        ("<think>a\nb</think>\n\nanswer", "answer"),
        ("<think>one</think>x<think>two</think>y", "xy"),
        ("<think>never closed", ""),
        ("  padded  ", "padded"),
    ],
)
def test_strip_thinking(raw, expected):
    assert strip_thinking(raw) == expected
