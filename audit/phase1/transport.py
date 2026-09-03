"""R1-R4: what the retry loop does when the API says no.

The blueprint separates two failures that look alike and are not:

  * **HTTP 429** is this account's token bucket, and the server says exactly
    how long it needs. Retrying sooner extends the wait for everyone holding
    the bucket. The `retry-after` header is an instruction, not a hint.
  * **HTTP 529** is Anthropic's own saturation. Its retry headers are
    unreliable, and every client backing off on the same deterministic curve
    reconverges on the same instant - the thundering herd the jitter exists to
    break up.

These run against the product's real `_request` loop with a scripted client, so
what is measured is the shipped behaviour rather than a description of it.
"""

from __future__ import annotations

from audit._support.scorecard import Check


def _anthropic():
    import anthropic

    return anthropic


def _err(status: int, retry_after: str | None = None):
    """A real SDK exception with a real response attached."""
    import httpx

    anthropic = _anthropic()
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, headers=headers, request=request, json={"error": {}})
    body = {"error": {"type": "error", "message": f"scripted {status}"}}
    if status == 429:
        return anthropic.RateLimitError("scripted 429", response=response, body=body)
    return anthropic.APIStatusError(f"scripted {status}", response=response, body=body)


class _Scripted:
    """A client that raises a scripted sequence, then answers."""

    def __init__(self, errors: list):
        self._errors = list(errors)
        self.calls = 0

        class _Messages:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                return self._outer._next()

            def stream(self, **kwargs):
                raise AssertionError("this audit scripts the non-streaming path")

        self.messages = _Messages(self)

    def _next(self):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return _Answer()


class _Answer:
    class _Usage:
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    content = [type("B", (), {"type": "text", "text": "ok"})()]
    usage = _Usage()
    model = "claude-haiku-4-5"
    stop_reason = "end_turn"
    _request_id = "req_scripted"


def _backend(errors, sleeps):
    """The product's own backend, with its documented sleep seam."""
    from core.llm.backends import AnthropicBackend

    return AnthropicBackend(client=_Scripted(errors), api_key="unused", sleep=sleeps.append)


def _call(backend):
    return backend._request(
        {"model": "claude-haiku-4-5", "max_tokens": 8, "messages": []}, stream=False
    )


def r1_429_obeys_retry_after() -> Check:
    c = Check(
        "R1",
        "Reliability",
        1,
        "A 429 waits exactly as long as the server asked",
        "read retry-after and pause that long; do not hammer",
    )
    sleeps: list[float] = []
    backend = _backend([_err(429, "7")], sleeps)
    try:
        _call(backend)
    except Exception as e:  # noqa: BLE001
        return c.failed(f"a single 429 was not recovered from: {type(e).__name__}: {e}")
    if sleeps != [7.0]:
        return c.failed(f"slept {sleeps} rather than the 7s the server asked for")
    c.evidence = "retry-after: 7 honoured exactly, then the call succeeded"
    return c.ok()


def r2_retry_after_is_capped() -> Check:
    c = Check(
        "R2",
        "Reliability",
        1,
        "An absurd retry-after is capped, not obeyed",
        "a server instruction is followed, but not off a cliff",
    )
    sleeps: list[float] = []
    try:
        _call(_backend([_err(429, "86400")], sleeps))
    except Exception as e:  # noqa: BLE001
        return c.failed(f"raised rather than retrying: {e}")
    if not sleeps or sleeps[0] > 300:
        return c.failed(f"slept {sleeps[0] if sleeps else 'nothing'}s on a 24h retry-after")
    c.evidence = f"86400s instruction capped to {sleeps[0]:g}s"
    return c.ok()


def r3_529_backs_off_exponentially() -> Check:
    c = Check(
        "R3",
        "Reliability",
        1,
        "A 529 is transient and backs off, rather than failing the call",
        "overload is retried on a growing curve rather than treated as a client error",
    )
    sleeps: list[float] = []
    backend = _backend([_err(529), _err(529)], sleeps)
    try:
        _call(backend)
    except Exception as e:  # noqa: BLE001
        return c.failed(f"two 529s were not survived: {type(e).__name__}: {e}")
    if len(sleeps) != 2:
        return c.failed(f"expected two waits, saw {sleeps}")
    # With jitter the second wait is drawn from [1.0, 2.0] and the first from
    # [0.5, 1.0], so it can never be smaller - but it can tie at exactly 1.0.
    if sleeps[1] < sleeps[0]:
        return c.failed(f"the curve does not grow: {sleeps}")
    c.evidence = f"waits {sleeps[0]:g}s then {sleeps[1]:g}s"
    return c.ok()


def r4_529_backoff_is_jittered() -> Check:
    """The blueprint's thundering-herd requirement, stated as a check.

    Non-blocking: this system is one user's toolkit, so the herd is small. It
    is still a real weakness - the CLI, the MCP server and the web app can be
    retrying the same overloaded endpoint at the same instant, and a fixed
    curve guarantees they collide on every attempt.
    """
    c = Check(
        "R4",
        "Reliability",
        1,
        "529 backoff is randomised so clients do not reconverge",
        "exponential backoff WITH jitter, per the blueprint's 529 branch",
        blocking=False,
    )
    runs = []
    for _ in range(6):
        sleeps: list[float] = []
        try:
            _call(_backend([_err(529), _err(529)], sleeps))
        except Exception as e:  # noqa: BLE001
            return c.failed(f"could not measure: {e}")
        runs.append(tuple(sleeps))
    if len({r for r in runs}) == 1:
        return c.failed(
            f"every run backs off on the identical curve {runs[0]} - no jitter. Concurrent "
            f"clients retry in lockstep and re-collide on each attempt (core/llm/backends.py, "
            f"the _sleep call at the end of _request)"
        )
    c.evidence = f"{len({r for r in runs})} distinct curves over 6 runs"
    return c.ok()


def r5_client_errors_are_not_retried() -> Check:
    c = Check(
        "R5",
        "Reliability",
        1,
        "A 400 costs one request, not five",
        "a permanent error is permanent; retrying it burns budget for the same answer",
    )
    sleeps: list[float] = []
    client = _Scripted([_err(400), _err(400), _err(400)])
    from core.llm.backends import AnthropicBackend

    backend = AnthropicBackend(client=client, api_key="unused", sleep=sleeps.append)
    try:
        _call(backend)
    except Exception:  # noqa: BLE001, S110
        pass
    if client.calls != 1:
        return c.failed(f"a 400 was sent {client.calls} times")
    if sleeps:
        return c.failed(f"slept {sleeps} before giving up on a permanent error")
    c.evidence = "one request, no sleep, raised to the caller"
    return c.ok()


CHECKS = (
    r1_429_obeys_retry_after,
    r2_retry_after_is_capped,
    r3_529_backs_off_exponentially,
    r4_529_backoff_is_jittered,
    r5_client_errors_are_not_retried,
)
