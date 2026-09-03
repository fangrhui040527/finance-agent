"""Vendor backends. The ONLY place a vendor API may be reached.

`core/llm/client.py` states the rule: no agent imports a vendor SDK, everything
goes through `InferenceClient.complete()`, and the `Backend` protocol is the one
seam a provider is allowed to sit behind. `EchoBackend` lives in that file so
everything is testable with no keys.

This file now sits on the official `anthropic` SDK (lazily imported, so the
echo path never loads it) - typed errors and response parsing come from the
vendor instead of being re-derived from raw JSON. Three decisions survive from
the urllib version, on purpose, and each is pinned by tests:

  * **The retry loop is OURS, not the SDK's** (`max_retries=0` on the client).
    A 429's `retry-after` is obeyed and capped at 60s - beyond that the server
    is asking the caller to park a plan the supervisor already budgeted for -
    and the sleep is injectable, so the loop is testable without waiting.
  * **A broken backend never looks like a quiet one.** Every failure raises;
    no path returns empty text or silently downgrades a tier. A truncated or
    refused answer raises `Truncated`/`Declined` CARRYING its `Usage`, because
    the spend is real and must reach the ledger.
  * **No server-side fallbacks.** Commitment 6: refusal beats invention. A
    model refusal must surface as a refusal, never be silently re-routed to a
    different model that might answer.

The system prompt is sent as a `cache_control` block: it is the part that
repeats call after call, and a cache read bills at a tenth of fresh input.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from core.llm.tiers import RequestProfile, Usage

if TYPE_CHECKING:  # pragma: no cover - typing only
    import anthropic


class BackendError(RuntimeError):
    """A model call failed. Never swallowed, never rendered as an empty answer."""


class TransientError(BackendError):
    """Rate limit, overload, or transport. Worth retrying; the caller is told."""


class AuthError(BackendError):
    """Missing, malformed or rejected credentials. Retrying cannot help."""


class ContextOverflow(BackendError):
    """The prompt exceeds the model's context window.

    docs/13's taxonomy: this is neither transient (retrying the same prompt
    hits the same wall) nor a generic bad request (the caller's next move is
    to SHRINK - trim evidence, split the question - not to fix a field). A
    flat 400 hid that distinction.
    """


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
    thesis that reads as a whole one is the most expensive failure in the
    system, so it is an exception rather than a shorter string.
    """


class Declined(Billed):
    """The model refused to answer (stop_reason=refusal). Not an empty answer.

    Carries the refusal category and explanation when the API names them
    (`stop_details`), so the refusal card can say WHY without guessing.
    """

    def __init__(
        self,
        message: str,
        usage: Usage | None = None,
        category: str | None = None,
        explanation: str | None = None,
    ) -> None:
        super().__init__(message, usage=usage)
        self.category = category
        self.explanation = explanation


#: A `retry-after` header is obeyed up to this many seconds. Beyond it the
#: server is asking the caller to park a plan the supervisor already budgeted
#: for, and a bounded failure beats an unbounded wait.
RETRY_AFTER_CAP = 60.0


class AnthropicBackend:
    """Anthropic Messages API behind the `Backend` protocol, via the official SDK.

    The key is read once, at construction. A backend built without one raises
    immediately rather than at the first call, because the first call is halfway
    through a plan the supervisor has already budgeted for. A prebuilt `client`
    (any object with `.messages.create/.messages.stream`) skips the key check -
    that is the test seam, replacing the urllib `opener`.
    """

    #: Generous enough for a thesis, bounded enough that a runaway costs cents.
    DEFAULT_MAX_TOKENS = 4096
    TIMEOUT = 120.0

    def __init__(
        self,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_attempts: int = 3,
        client: Any | None = None,
        sleep=None,
        jitter=None,
    ) -> None:
        import random
        import time

        if max_tokens < 1:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self._sleep = sleep if sleep is not None else time.sleep
        # Injectable so the backoff curve is testable without waiting for it,
        # and so a test can pin the randomness it is asserting about.
        self._jitter = jitter if jitter is not None else random.uniform
        self.last_request_id: str | None = None

        if client is not None:
            self._client = client
            return
        key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        if not key.strip():
            raise AuthError(
                "ANTHROPIC_API_KEY is empty or unset. Put it in .env (every "
                "entrypoint loads that file; an exported variable wins over it), "
                "or pass EchoBackend explicitly if you meant to run without a model."
            )
        import anthropic  # the ONE import site; lazy so echo never loads it

        # An identity-linked key is scoped to a workspace and the API refuses
        # every request that does not name one - a 400 before the model is
        # reached, on all five effort levels, for the same reason. The header is
        # sent when ANTHROPIC_WORKSPACE_ID is set and omitted when it is not,
        # because a plain key rejects a workspace it does not have.
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        self.workspace_id = workspace or None
        extra: dict[str, Any] = {}
        if workspace:
            extra["default_headers"] = {"anthropic-workspace-id": workspace}

        # max_retries=0: the retry loop below owns backoff, so retry-after
        # capping and sleep injection stay testable and disclosed.
        self._client = anthropic.Anthropic(
            api_key=key, max_retries=0, timeout=self.TIMEOUT, **extra
        )

    # -- the seam -----------------------------------------------------------

    def complete(
        self,
        model_id: str,
        prompt: str,
        system: str | None,
        profile: RequestProfile | None = None,
    ) -> tuple[str, Usage]:
        params: dict[str, Any] = {
            "model": model_id,
            "max_tokens": profile.max_tokens if profile else self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            # The system prompt is the part that repeats call after call, so it
            # carries the cache breakpoint. A prompt under the model's minimum
            # cacheable length is simply not cached; the marker costs nothing.
            params["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if profile is not None:
            # Haiku 4.5 rejects adaptive thinking and `effort` with a 400, and
            # takes the older budgeted form instead; the Opus/Sonnet 5 family is
            # the other way round. `tiers.profile_for` knows which model takes
            # what and the two forms are mutually exclusive by construction.
            # Nothing here guesses.
            if profile.thinking_budget is not None:
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": profile.thinking_budget,
                }
            elif profile.adaptive_thinking:
                params["thinking"] = {"type": "adaptive"}
            if profile.effort:
                params["output_config"] = {"effort": profile.effort}

        message = self._request(params, stream=bool(profile and profile.stream))
        return self._parse(message)

    # -- transport ----------------------------------------------------------

    def _request(self, params: dict, stream: bool):
        """Bounded retry around the SDK call. The last failure is raised."""
        import anthropic

        last: BackendError | None = None
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                if stream:
                    # Streaming keeps long generations under HTTP idle timeouts;
                    # the caller still receives one final message.
                    with self._client.messages.stream(**params) as s:
                        message = s.get_final_message()
                else:
                    message = self._client.messages.create(**params)
                self.last_request_id = getattr(message, "_request_id", None)
                return message
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
                raise AuthError(f"Anthropic rejected the key: {e.message}") from e
            except anthropic.RateLimitError as e:
                last = TransientError(f"Anthropic returned 429: {e.message}")
                retry_after = self._retry_after(e)
            except anthropic.APIStatusError as e:
                if e.status_code in (500, 502, 503, 529):
                    last = TransientError(f"Anthropic returned {e.status_code}: {e.message}")
                    retry_after = self._retry_after(e)
                elif e.status_code == 400 and "anthropic-workspace-id" in str(e.message).lower():
                    # Neither transient nor a prompt problem: the credential is
                    # incomplete. Left as a generic 400 it reads as "the request
                    # was malformed" and sends the operator to look at the
                    # request, which is the one place the fault is not.
                    raise AuthError(
                        "this API key is identity-linked and every request must name "
                        "the workspace it acts in. Set ANTHROPIC_WORKSPACE_ID in .env "
                        "to the workspace id from the Console URL "
                        "(console.anthropic.com/settings/workspaces). "
                        f"Anthropic said: {e.message}"
                    ) from e
                elif e.status_code == 400 and any(
                    marker in str(e.message).lower()
                    for marker in (
                        "prompt is too long",
                        "context length",
                        "maximum context",
                        "too many tokens",
                    )
                ):
                    raise ContextOverflow(
                        f"prompt exceeds the model's context window: {e.message}. "
                        "Shrink the input - trim evidence or split the question; "
                        "retrying the same prompt hits the same wall."
                    ) from e
                else:
                    raise BackendError(f"Anthropic returned {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:  # includes APITimeoutError
                last = TransientError(f"Anthropic unreachable: {e}")

            if attempt == self.max_attempts:
                raise last
            # The server's own instruction outranks the client's guess: a 429
            # says exactly how long the token bucket needs, and retrying sooner
            # only extends the wait. That one is obeyed EXACTLY - jittering an
            # instruction is just disobeying it by a random amount.
            #
            # Absent a header, the wait is exponential with jitter. Without the
            # jitter every client that hit the same 529 backs off on the same
            # curve and returns at the same instant, which is the collision the
            # backoff existed to avoid - and this system has three surfaces (CLI,
            # MCP, web) that can be talking to the same overloaded endpoint.
            if retry_after is not None:
                self._sleep(retry_after)
            else:
                self._sleep(self._jitter(0.5, 1.0) * 2.0 ** (attempt - 1))
        raise last if last is not None else BackendError("retry loop exited without a result")

    @staticmethod
    def _retry_after(err: anthropic.APIStatusError) -> float | None:
        """Seconds the server asked for, capped; None if absent or not numeric.

        HTTP-date forms are ignored rather than parsed: a wrong clock on either
        side turns a date into a multi-hour sleep, and the exponential fallback
        is the safer failure.
        """
        response = getattr(err, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            seconds = float(str(value).strip())
        except ValueError:
            return None
        if seconds <= 0:
            return None
        return min(seconds, RETRY_AFTER_CAP)

    # -- response -----------------------------------------------------------

    def _parse(self, message) -> tuple[str, Usage]:
        text = "".join(
            getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text"
        )

        raw_usage = getattr(message, "usage", None)
        if raw_usage is None:
            raise BackendError("Anthropic response carries no usage; cost cannot be ledgered")
        try:
            usage = Usage(
                input_tokens=int(raw_usage.input_tokens or 0),
                output_tokens=int(raw_usage.output_tokens or 0),
                # Priced at 10% of base input in tiers.cost_usd. Dropping it does
                # not under-report cost, it OVER-reports it, and an inflated spend
                # trips the budget rail early - a wrong refusal, not a wrong bill.
                cached_input_tokens=int(getattr(raw_usage, "cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(getattr(raw_usage, "cache_creation_input_tokens", 0) or 0),
            )
        except (TypeError, ValueError) as e:
            raise BackendError(f"Anthropic usage is not numeric: {raw_usage!r}") from e

        stop = getattr(message, "stop_reason", None)
        if stop == "max_tokens":
            raise Truncated(
                f"model hit max_tokens after {usage.output_tokens} output tokens; "
                "raise max_tokens or narrow the question - a truncated answer is "
                "never returned as a whole one",
                usage=usage,
            )
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            raise Declined(
                "model declined to answer (stop_reason=refusal)",
                usage=usage,
                category=getattr(details, "category", None),
                explanation=getattr(details, "explanation", None),
            )

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
    from core.llm.tiers import cheap_capped, selection_note

    note = selection_note()
    cap = f" | {note}" if note else ""
    if cheap_capped():
        cap += " (every Messages tier resolves to the cheapest model)"
    choice = (explicit or os.environ.get("LLM_BACKEND", "")).strip().lower()

    if choice in ("echo", "none", "offline"):
        return EchoBackend(), "echo (explicitly selected): deterministic stub, not a model" + cap
    if choice in ("anthropic", "claude"):
        return AnthropicBackend(), "anthropic (explicitly selected, official SDK)" + cap
    if choice:
        raise ValueError(f"unknown LLM_BACKEND {choice!r}; expected 'anthropic' or 'echo'")

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        where = f", workspace {workspace}" if workspace else ""
        return (
            AnthropicBackend(),
            f"anthropic (ANTHROPIC_API_KEY is set, official SDK{where})" + cap,
        )
    return EchoBackend(), (
        "echo (no ANTHROPIC_API_KEY): deterministic stub, NOT a model - "
        "narrative output is placeholder text" + cap
    )
