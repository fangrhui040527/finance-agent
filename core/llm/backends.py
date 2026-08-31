"""Vendor backends. The ONLY place a vendor API may be reached.

`core/llm/client.py` states the rule: no agent imports a vendor SDK, everything
goes through `InferenceClient.complete()`, and the `Backend` protocol is the one
seam a provider is allowed to sit behind. `EchoBackend` lives in that file so P0
is testable with no keys. This file is the other half - the backend that talks to
a real model - kept separate so importing the client never drags a network path
in with it.

No SDK dependency. urllib, like `knowledge/feeds/adapter.py`, for the same
reason: two runtime dependencies is a feature, and the Messages API is one POST.

The property that matters here is the one the feed adapter already establishes -
**a broken backend must never look like a quiet one**. Every failure raises. No
path returns empty text, and no path silently downgrades the tier it was handed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from core.llm.tiers import Usage

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class BackendError(RuntimeError):
    """A model call failed. Never swallowed, never rendered as an empty answer."""


class TransientError(BackendError):
    """Rate limit or overload. Worth retrying; the caller is told which it was."""


class AuthError(BackendError):
    """Missing, malformed or rejected credentials. Retrying cannot help."""


class Billed(BackendError):
    """The model answered - and billed - but the answer cannot be returned.

    Carries the `Usage` the API reported, so the caller can ledger a spend that
    produced no usable text. Before this, the tokens of every truncated or
    refused call vanished from the ledger: under-reporting by exactly the calls
    that went wrong, which is the worst possible selection.
    """

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class Truncated(Billed):
    """The model hit max_tokens.

    docs/08 section 8: truncation is disclosed, never silent. A half-written
    thesis that reads as a whole one is the most expensive failure in the system,
    so it is an exception rather than a shorter string.
    """


#: Retried. 429 is a rate limit, 529 is Anthropic's overloaded signal, and the
#: 5xx pair are ordinary gateway noise.
RETRY_STATUS = frozenset({429, 500, 502, 503, 529})


class Declined(Billed):
    """The model refused to answer (stop_reason=refusal). Not an empty answer."""


#: A `retry-after` header is obeyed up to this many seconds. Beyond it the
#: server is asking the caller to park a plan the supervisor already budgeted
#: for, and a bounded failure beats an unbounded wait.
RETRY_AFTER_CAP = 60.0


class AnthropicBackend:
    """Anthropic Messages API behind the `Backend` protocol.

    The key is read once, at construction. A backend built without one raises
    immediately rather than at the first call, because the first call is halfway
    through a plan the supervisor has already budgeted for.
    """

    #: Generous enough for a thesis, bounded enough that a runaway costs cents.
    DEFAULT_MAX_TOKENS = 4096
    TIMEOUT = 120

    def __init__(
        self,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_attempts: int = 3,
        opener: Callable | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        import time

        key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        if not key.strip():
            raise AuthError(
                "ANTHROPIC_API_KEY is empty or unset. Set it in .env, or pass "
                "EchoBackend explicitly if you meant to run without a model."
            )
        if max_tokens < 1:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

        self.api_key = key
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self._opener = opener
        self._sleep = sleep if sleep is not None else time.sleep

    # -- the seam -----------------------------------------------------------

    def complete(self, model_id: str, prompt: str, system: str | None) -> tuple[str, Usage]:
        body = {
            "model": model_id,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            # The system prompt is the part that repeats call after call, so it
            # carries the cache breakpoint. A prompt under the model's minimum
            # cacheable length is simply not cached; the marker costs nothing.
            body["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        payload = self._post(json.dumps(body).encode())
        return self._parse(payload)

    # -- transport ----------------------------------------------------------

    def _post(self, data: bytes) -> dict:
        """POST with bounded retry. The last failure is raised, not masked."""
        import urllib.error
        import urllib.request

        opener = self._opener or urllib.request.urlopen
        last: BackendError | None = None

        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            req = urllib.request.Request(
                ANTHROPIC_URL,
                data=data,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                method="POST",
            )
            try:
                with opener(req, timeout=self.TIMEOUT) as resp:
                    raw = resp.read()
                break
            except urllib.error.HTTPError as e:
                status = e.code
                detail = self._detail(e)
                if status in (401, 403):
                    raise AuthError(f"Anthropic rejected the key ({status}): {detail}") from e
                if status not in RETRY_STATUS:
                    raise BackendError(f"Anthropic returned {status}: {detail}") from e
                last = TransientError(f"Anthropic returned {status}: {detail}")
                retry_after = self._retry_after(e)
            except urllib.error.URLError as e:
                last = TransientError(f"Anthropic unreachable: {e.reason}")
            except OSError as e:
                last = TransientError(f"Anthropic unreachable: {e}")

            if attempt == self.max_attempts:
                assert last is not None
                raise last
            # The server's own instruction outranks the client's guess: a 429
            # says exactly how long the token bucket needs, and retrying sooner
            # only extends the wait. Absent or unreadable, exponential as before.
            self._sleep(retry_after if retry_after is not None else 2.0 ** (attempt - 1))
        else:  # pragma: no cover - loop breaks or raises
            assert last is not None
            raise last

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BackendError(f"Anthropic returned non-JSON: {raw[:200]!r}") from e
        if not isinstance(payload, dict):
            raise BackendError(f"Anthropic returned {type(payload).__name__}, expected an object")
        return payload

    @staticmethod
    def _retry_after(err) -> float | None:
        """Seconds the server asked for, capped; None if absent or not numeric.

        HTTP-date forms are ignored rather than parsed: a wrong clock on either
        side turns a date into a multi-hour sleep, and the exponential fallback
        is the safer failure.
        """
        headers = getattr(err, "headers", None) or {}
        try:
            items = list(headers.items())
        except AttributeError:  # pragma: no cover - defensive
            return None
        for name, value in items:
            if str(name).lower() != "retry-after":
                continue
            try:
                seconds = float(str(value).strip())
            except ValueError:
                return None
            if seconds <= 0:
                return None
            return min(seconds, RETRY_AFTER_CAP)
        return None

    @staticmethod
    def _detail(err) -> str:
        """The API puts the useful part in the body, not the status line."""
        try:
            body = err.read()
        except Exception:  # pragma: no cover - defensive
            return err.reason if hasattr(err, "reason") else ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return body[:200]

    # -- response -----------------------------------------------------------

    @staticmethod
    def _parse(payload: dict) -> tuple[str, Usage]:
        if payload.get("type") == "error":
            err = payload.get("error", {})
            raise BackendError(f"Anthropic error: {err.get('type')}: {err.get('message')}")

        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise BackendError(f"Anthropic response has no content list: {str(payload)[:200]!r}")

        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )

        raw_usage = payload.get("usage")
        if not isinstance(raw_usage, dict):
            raise BackendError("Anthropic response carries no usage; cost cannot be ledgered")
        try:
            usage = Usage(
                input_tokens=int(raw_usage.get("input_tokens", 0)),
                output_tokens=int(raw_usage.get("output_tokens", 0)),
                # Priced at 10% of base input in tiers.cost_usd. Dropping it does
                # not under-report cost, it OVER-reports it, and an inflated spend
                # trips the budget rail early - a wrong refusal, not a wrong bill.
                cached_input_tokens=int(raw_usage.get("cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(raw_usage.get("cache_creation_input_tokens", 0) or 0),
            )
        except (TypeError, ValueError) as e:
            raise BackendError(f"Anthropic usage is not numeric: {raw_usage!r}") from e

        stop = payload.get("stop_reason")
        if stop == "max_tokens":
            raise Truncated(
                f"model hit max_tokens after {usage.output_tokens} output tokens; "
                "raise max_tokens or narrow the question - a truncated answer is "
                "never returned as a whole one",
                usage=usage,
            )
        if stop == "refusal":
            raise Declined("model declined to answer (stop_reason=refusal)", usage=usage)

        if not text.strip():
            raise BackendError(f"Anthropic returned no text block (stop_reason={stop!r})")
        return text, usage


def backend_from_env(explicit: str | None = None):
    """Pick a backend the way an operator expects, and SAY which was picked.

    Precedence: an explicit choice, else a real backend when a key exists, else
    the echo stand-in. The returned reason is not decoration - the difference
    between a real answer and a deterministic stub is the single most important
    thing to show on screen, and a system that quietly ran on EchoBackend for a
    week would be indistinguishable from one that worked.

    Returns (backend, reason).
    """
    from core.llm.client import EchoBackend

    choice = (explicit or os.environ.get("LLM_BACKEND", "")).strip().lower()

    if choice in ("echo", "none", "offline"):
        return EchoBackend(), "echo (explicitly selected): deterministic stub, not a model"
    if choice in ("anthropic", "claude"):
        return AnthropicBackend(), "anthropic (explicitly selected)"
    if choice:
        raise ValueError(f"unknown LLM_BACKEND {choice!r}; expected 'anthropic' or 'echo'")

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return AnthropicBackend(), "anthropic (ANTHROPIC_API_KEY is set)"
    return EchoBackend(), (
        "echo (no ANTHROPIC_API_KEY): deterministic stub, NOT a model - "
        "narrative output is placeholder text"
    )
