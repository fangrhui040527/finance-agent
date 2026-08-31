"""A scripted stand-in for `anthropic.Anthropic`, shared by the LLM test files.

Replaces the urllib `opener` seam the old backend exposed: the SDK backend
takes `client=`, and this double plays a script of SDK `Message` objects and
SDK exceptions while recording every request's kwargs.
"""

from __future__ import annotations

import anthropic
import httpx2
from anthropic.types import Message, TextBlock
from anthropic.types import Usage as AUsage

_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def reply(
    text: str = "Margins compressed on funding cost.",
    *,
    model: str = "claude-sonnet-5",
    stop_reason: str = "end_turn",
    usage: dict | None = None,
    content: list | None = None,
    request_id: str = "req_test",
) -> Message:
    u = usage or {"input_tokens": 120, "output_tokens": 45}
    msg = Message.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model=model,
        content=(
            [TextBlock(type="text", text=text)]
            if content is None
            else [
                TextBlock(type="text", text=b["text"])
                if isinstance(b, dict) and b.get("type") == "text"
                else b
                for b in content
            ]
        ),
        stop_reason=stop_reason,
        usage=AUsage.model_construct(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cache_read_input_tokens=u.get("cache_read_input_tokens"),
            cache_creation_input_tokens=u.get("cache_creation_input_tokens"),
        ),
    )
    msg._request_id = request_id
    return msg


def http_status_error(status: int, headers: dict | None = None) -> anthropic.APIStatusError:
    resp = httpx2.Response(status, request=_REQ, headers=headers or {})
    cls = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }.get(status)
    if cls is None:
        return anthropic.APIStatusError(f"http {status}", response=resp, body=None)
    return cls(f"http {status}", response=resp, body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_REQ)


class _Stream:
    """Just enough of the SDK's MessageStreamManager for get_final_message()."""

    def __init__(self, outcome):
        self._outcome = outcome

    def __enter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._outcome


class _Messages:
    def __init__(self, script: list, calls: list) -> None:
        self._script = script
        self.calls = calls

    def _next(self):
        if not self._script:
            raise AssertionError("FakeAnthropic script exhausted: more requests than steps")
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def create(self, **kwargs):
        self.calls.append({"method": "create", **kwargs})
        return self._next()

    def stream(self, **kwargs):
        self.calls.append({"method": "stream", **kwargs})
        if self._script and isinstance(self._script[0], BaseException):
            return _Stream(self._script.pop(0))
        return _Stream(self._next())


class FakeAnthropic:
    """`FakeAnthropic([reply(), http_status_error(429), ...])`; `.calls` records kwargs."""

    def __init__(self, script: list | None = None) -> None:
        self.calls: list[dict] = []
        self.messages = _Messages(list(script or [reply()]), self.calls)
