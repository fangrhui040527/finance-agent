"""Transport doubles for the one network seam in this repository.

`AnthropicBackend` takes an `opener` callable in its constructor. That parameter
is the whole interception story: nothing in this file patches `urllib`,
monkey-patches a builtin, or installs a global handler. The seam already exists,
so a test drives it by passing a callable, which is the Python equivalent of the
reference guide's "intercept at the network layer, do not patch native fetch"
requirement.

Three doubles live here:

`FakeResponse`
    A context manager with `.read()`, matching what `urllib.request.urlopen`
    returns. Constructed from bytes so a test can hand the backend a body that is
    not valid UTF-8, or not valid JSON, or truncated mid-character.

`ScriptedOpener`
    Replays a fixed list of outcomes, one per call, and records every request it
    was given. A `TransportError` entry raises rather than returns, which is how
    an HTTP status or a socket failure is expressed. Calling it more times than
    the script allows is itself an assertion failure: an unbounded retry loop
    shows up as `ScriptExhausted` rather than as a hang.

`http_error`
    Builds a real `urllib.error.HTTPError` with a readable body and headers, so a
    test can assert on behaviour the production code reaches through `e.code`,
    `e.read()` and `e.headers` exactly as it would against the live API.
"""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import dataclass, field
from typing import Any


class ScriptExhausted(AssertionError):
    """The code under test made more requests than the script allowed.

    Raised as an AssertionError on purpose. An unbounded retry loop should fail
    the test immediately rather than spin until the suite times out.
    """


class FakeResponse:
    """What `urlopen` returns: a context manager whose `read()` gives bytes."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self.body = body
        self.status = status
        self.code = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


@dataclass
class Request:
    """One captured outbound request, decoded for assertions."""

    url: str
    method: str
    headers: dict
    raw_body: bytes
    timeout: Any = None

    @property
    def body(self) -> dict:
        return json.loads(self.raw_body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        """Header lookup that does not care about case.

        `urllib.request.Request` title-cases the keys it is given, so a test that
        asks for `x-api-key` and a production file that sets `x-api-key` would
        otherwise disagree about a header that is present.
        """
        lower = {k.lower(): v for k, v in self.headers.items()}
        return lower.get(name.lower())


@dataclass
class ScriptedOpener:
    """A drop-in for `urllib.request.urlopen` that replays a fixed script.

    Each entry is either a `FakeResponse` (returned) or an exception instance
    (raised). Requests are recorded in order.
    """

    script: list = field(default_factory=list)
    calls: list[Request] = field(default_factory=list)

    def __call__(self, req, timeout=None):
        self.calls.append(
            Request(
                url=req.full_url,
                method=req.get_method(),
                headers=dict(req.headers),
                raw_body=req.data or b"",
                timeout=timeout,
            )
        )
        if len(self.calls) > len(self.script):
            raise ScriptExhausted(
                f"request {len(self.calls)} was made but the script has only "
                f"{len(self.script)} entries; the code under test is retrying "
                "more than the script permits"
            )
        outcome = self.script[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def attempts(self) -> int:
        return len(self.calls)


def http_error(status: int, body: str = "", headers: dict | None = None) -> urllib.error.HTTPError:
    """A real HTTPError, with a body that survives being read once."""
    return urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=status,
        msg=f"status {status}",
        hdrs=headers or {},
        fp=io.BytesIO(body.encode("utf-8")),
    )


def ok_body(
    text: str = "hello",
    input_tokens: int = 10,
    output_tokens: int = 5,
    stop_reason: str = "end_turn",
    **extra_usage,
) -> bytes:
    """A well-formed Messages API success payload, encoded.

    `extra_usage` lets a test add the fields the live API really sends -
    `cache_read_input_tokens`, `cache_creation_input_tokens`, `service_tier` -
    so a parser can be checked against the real shape rather than a tidy one.
    """
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    usage.update(extra_usage)
    return json.dumps(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5-20251001",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage,
        }
    ).encode("utf-8")


def sleepless() -> tuple:
    """A `sleep` double plus the list it records into.

    Backoff behaviour is asserted on the recorded delays, and the suite never
    actually waits. Returns `(sleep_fn, delays_list)`.
    """
    delays: list[float] = []
    return (lambda s: delays.append(s)), delays
