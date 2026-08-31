"""The real model backend, tested without a network and without a key.

Same property the GDELT tests hold the feed to: a BROKEN backend must never look
like a QUIET one. Every failure path raises. Nothing here returns empty text, and
nothing degrades a tier it was handed.
"""

import io
import json
import urllib.error

import pytest

from core.llm.backends import (
    AnthropicBackend,
    AuthError,
    BackendError,
    TransientError,
    Truncated,
)
from core.llm.tiers import Usage

KEY = "sk-ant-test-not-a-real-key"


class _Response:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reply(text: str = "Margins compressed on funding cost.", **kw) -> str:
    payload = {
        "type": "message",
        "role": "assistant",
        "model": kw.get("model", "claude-sonnet-5"),
        "content": kw.get("content", [{"type": "text", "text": text}]),
        "stop_reason": kw.get("stop_reason", "end_turn"),
        "usage": kw.get("usage", {"input_tokens": 120, "output_tokens": 45}),
    }
    for k, v in kw.items():
        if k not in payload and k not in ("content", "usage", "stop_reason", "model"):
            payload[k] = v
    return json.dumps(payload)


def _opener(body: str, capture: list | None = None):
    def open_(req, timeout=None):
        if capture is not None:
            capture.append(req)
        return _Response(body)

    return open_


def _http_error(status: int, body: str = '{"error":{"message":"nope"}}'):
    def open_(req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            status,
            "err",
            {},
            io.BytesIO(body.encode()),
        )

    return open_


def _backend(opener, **kw) -> AnthropicBackend:
    kw.setdefault("sleep", lambda _s: None)  # no real waiting in tests
    return AnthropicBackend(api_key=KEY, opener=opener, **kw)


# --- the key is required before anything is spent -------------------------
def test_missing_key_raises_at_construction_not_at_first_call(monkeypatch):
    """The first call is halfway through a plan the supervisor already budgeted."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
        AnthropicBackend()


def test_blank_key_is_treated_as_missing(monkeypatch):
    """A key set to whitespace reads as configured. It is not."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(AuthError):
        AnthropicBackend()


def test_key_is_read_from_the_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert AnthropicBackend().api_key == KEY


def test_nonsense_bounds_are_refused():
    with pytest.raises(ValueError, match="max_tokens"):
        AnthropicBackend(api_key=KEY, max_tokens=0)
    with pytest.raises(ValueError, match="max_attempts"):
        AnthropicBackend(api_key=KEY, max_attempts=0)


# --- the happy path -------------------------------------------------------
def test_text_and_usage_come_back_off_the_wire():
    text, usage = _backend(_opener(_reply("Funding cost."))).complete(
        "claude-sonnet-5", "why did it move", None
    )
    assert text == "Funding cost."
    assert usage == Usage(input_tokens=120, output_tokens=45, cached_input_tokens=0)


def test_multiple_text_blocks_are_joined():
    body = _reply(
        content=[
            {"type": "text", "text": "one. "},
            {"type": "text", "text": "two."},
        ]
    )
    text, _ = _backend(_opener(body)).complete("claude-opus-5", "q", None)
    assert text == "one. two."


def test_non_text_blocks_are_skipped_not_stringified():
    body = _reply(
        content=[
            {"type": "thinking", "thinking": "internal"},
            {"type": "text", "text": "answer"},
        ]
    )
    text, _ = _backend(_opener(body)).complete("claude-opus-5", "q", None)
    assert text == "answer"


def test_request_carries_the_key_version_and_system_prompt():
    seen: list = []
    _backend(_opener(_reply(), seen)).complete("claude-opus-5", "q", "you are terse")
    req = seen[0]
    assert req.get_header("X-api-key") == KEY
    assert req.get_header("Anthropic-version") == "2023-06-01"
    body = json.loads(req.data)
    assert body["system"][0]["text"] == "you are terse"
    assert body["model"] == "claude-opus-5"
    assert body["messages"] == [{"role": "user", "content": "q"}]


def test_system_is_omitted_entirely_when_absent():
    seen: list = []
    _backend(_opener(_reply(), seen)).complete("claude-haiku-4-5", "q", None)
    assert "system" not in json.loads(seen[0].data)


def test_max_tokens_is_sent_and_configurable():
    seen: list = []
    _backend(_opener(_reply(), seen), max_tokens=512).complete("claude-sonnet-5", "q", None)
    assert json.loads(seen[0].data)["max_tokens"] == 512


# --- cost is ledgered from what was actually billed -----------------------
def test_cache_reads_are_reported_so_cost_is_not_overstated():
    """Priced at 10% of base input. Dropping it inflates spend and trips the
    budget rail early - a wrong refusal, which is worse than a wrong bill."""
    body = _reply(
        usage={
            "input_tokens": 500,
            "output_tokens": 100,
            "cache_read_input_tokens": 7500,
            "cache_creation_input_tokens": 0,
        }
    )
    _, usage = _backend(_opener(body)).complete("claude-opus-5", "q", None)
    assert usage.cached_input_tokens == 7500

    # `input_tokens` is the UNCACHED remainder. 500 fresh + 7500 read at a tenth
    # is far less than the 8000 fresh tokens the same prompt costs uncached.
    from core.llm.tiers import Tier, cost_usd

    billed = cost_usd(Tier.REASON, usage)
    uncached = cost_usd(Tier.REASON, Usage(8000, 100))
    assert billed == cost_usd(Tier.REASON, Usage(500, 100)) + cost_usd(
        Tier.REASON, Usage(0, 0, 7500)
    )
    assert billed < uncached


def test_a_response_with_no_usage_raises_rather_than_ledgering_zero():
    body = json.dumps(
        {"type": "message", "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    )
    with pytest.raises(BackendError, match="no usage"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


def test_non_numeric_usage_raises():
    body = _reply(usage={"input_tokens": "lots", "output_tokens": 4})
    with pytest.raises(BackendError, match="not numeric"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


# --- truncation is disclosed, never silent --------------------------------
def test_max_tokens_stop_reason_raises_rather_than_returning_half_a_thesis():
    body = _reply("The thesis rests on three legs. First", stop_reason="max_tokens")
    with pytest.raises(Truncated, match="max_tokens"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


def test_refusal_is_surfaced_not_returned_as_an_answer():
    body = _reply("", stop_reason="refusal", content=[])
    with pytest.raises(BackendError, match="refusal"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


def test_empty_text_raises_because_silence_is_not_an_answer():
    body = _reply(content=[{"type": "text", "text": "   "}])
    with pytest.raises(BackendError, match="no text block"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


# --- failure classification ----------------------------------------------
def test_bad_key_raises_auth_and_is_not_retried():
    calls: list = []

    def open_(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(
            "u", 401, "unauthorized", {}, io.BytesIO(b'{"error":{"message":"bad key"}}')
        )

    with pytest.raises(AuthError, match="401"):
        _backend(open_, max_attempts=3).complete("claude-opus-5", "q", None)
    assert len(calls) == 1, "retrying a rejected key just burns the rate limit"


def test_a_400_is_permanent_and_not_retried():
    calls: list = []

    def open_(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError("u", 400, "bad request", {}, io.BytesIO(b"{}"))

    with pytest.raises(BackendError) as e:
        _backend(open_, max_attempts=3).complete("claude-opus-5", "q", None)
    assert not isinstance(e.value, TransientError)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_transient_statuses_are_retried_then_raised(status):
    calls: list = []

    def open_(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError("u", status, "busy", {}, io.BytesIO(b"{}"))

    with pytest.raises(TransientError, match=str(status)):
        _backend(open_, max_attempts=3).complete("claude-opus-5", "q", None)
    assert len(calls) == 3


def test_a_retry_that_succeeds_returns_the_answer():
    calls: list = []

    def open_(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 529, "overloaded", {}, io.BytesIO(b"{}"))
        return _Response(_reply("recovered"))

    text, _ = _backend(open_, max_attempts=3).complete("claude-opus-5", "q", None)
    assert text == "recovered"
    assert len(calls) == 2


def test_backoff_is_exponential_and_bounded():
    waits: list[float] = []

    def open_(req, timeout=None):
        raise urllib.error.HTTPError("u", 529, "overloaded", {}, io.BytesIO(b"{}"))

    with pytest.raises(TransientError):
        AnthropicBackend(api_key=KEY, opener=open_, max_attempts=4, sleep=waits.append).complete(
            "claude-opus-5", "q", None
        )
    assert waits == [1.0, 2.0, 4.0], "one sleep fewer than attempts, doubling each time"


def test_an_unreachable_host_is_transient():
    def open_(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(TransientError, match="unreachable"):
        _backend(open_, max_attempts=2).complete("claude-opus-5", "q", None)


def test_non_json_body_raises():
    with pytest.raises(BackendError, match="non-JSON"):
        _backend(_opener("<html>502 Bad Gateway</html>")).complete("claude-opus-5", "q", None)


def test_api_level_error_object_raises():
    body = json.dumps(
        {"type": "error", "error": {"type": "invalid_request_error", "message": "too long"}}
    )
    with pytest.raises(BackendError, match="too long"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


def test_missing_content_list_raises():
    body = json.dumps({"type": "message", "usage": {"input_tokens": 1, "output_tokens": 1}})
    with pytest.raises(BackendError, match="no content list"):
        _backend(_opener(body)).complete("claude-opus-5", "q", None)


# --- choosing a backend ---------------------------------------------------
def test_no_key_falls_back_to_echo_and_says_so(monkeypatch):
    """Silently stubbing the model is the failure this string exists to prevent."""
    from core.llm.backends import backend_from_env
    from core.llm.client import EchoBackend

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    backend, reason = backend_from_env()
    assert isinstance(backend, EchoBackend)
    assert "NOT a model" in reason


def test_a_key_selects_the_real_backend(monkeypatch):
    from core.llm.backends import backend_from_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    backend, reason = backend_from_env()
    assert isinstance(backend, AnthropicBackend)
    assert "anthropic" in reason


def test_echo_can_be_forced_even_with_a_key_present(monkeypatch):
    """Working offline with a key in the environment must stay possible."""
    from core.llm.backends import backend_from_env
    from core.llm.client import EchoBackend

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    backend, reason = backend_from_env("echo")
    assert isinstance(backend, EchoBackend)
    assert "explicitly selected" in reason


def test_asking_for_anthropic_without_a_key_raises_rather_than_falling_back(monkeypatch):
    from core.llm.backends import backend_from_env

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError):
        backend_from_env("anthropic")


def test_an_unknown_backend_name_is_refused(monkeypatch):
    from core.llm.backends import backend_from_env

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="unknown LLM_BACKEND"):
        backend_from_env("gpt")
