"""Vendor backends. The ONLY place a vendor API may be reached.

`core/llm/client.py` states the rule: no agent imports a vendor SDK, everything
goes through `InferenceClient.complete()`, and the `Backend` protocol is the one
seam a provider is allowed to sit behind. `EchoBackend` lives in that file so
everything is testable with no keys.

Two vendor backends live here. `AnthropicBackend` sits on the official
`anthropic` SDK (lazily imported, so the echo path never loads it) - typed
errors and response parsing come from the vendor instead of being re-derived
from raw JSON. `OpenAICompatibleBackend` reaches any free-tier provider in
`core/llm/providers.py` over stdlib urllib, because those endpoints share one
wire format and a second SDK would be a second dependency for the same JSON.
`SplitBackend` puts a different one of them behind each Messages tier, which is
how "reasoning on one provider, triage on another" is expressed (docs/21).

Three decisions survive from the urllib version of the Anthropic backend, on
purpose, hold for the free-provider one too, and each is pinned by tests:

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

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from core.llm import providers as _providers
from core.llm.providers import Provider
from core.llm.tiers import MESSAGES_TIERS, MODEL_IDS, RequestProfile, Tier, Usage
from core.net.retry import retry_after_seconds

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


#: A 400 whose message says one of these is the prompt outrunning the window,
#: not a malformed request. Provider wordings differ; these are the ones seen.
_CONTEXT_MARKERS: tuple[str, ...] = (
    "prompt is too long",
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "exceeds the model",
)

#: Retry-worthy statuses, the same set the keyless feeds use (core/net/retry.py).
_TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})

#: What this process calls itself on the wire, matching knowledge/sources/base.py
#: and the price feeds. A bare "Python-urllib" is refused by some edges.
USER_AGENT = "finplanet-analyst-mind/0.1 (personal research)"

#: Reasoning models on the open-weight side (DeepSeek-R1, Qwen3, GPT-OSS
#: through some hosts) put their chain of thought in the reply text between
#: these tags. It is not the answer, and a JSON parser downstream would find
#: braces inside it. A block cut off before its close tag is a model that was
#: truncated while thinking, and nothing after the opening tag is an answer.
#: A bearer token as it appears in an Authorization header. Some gateways echo
#: the offending header back in an error body; the product relays a provider's
#: words, and the key must not be among them.
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.DOTALL)


def strip_thinking(text: str) -> str:
    """The reply with any `<think>...</think>` passage removed."""
    if "<think>" not in text:
        return text.strip()
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


class OpenAICompatibleBackend:
    """A free-tier provider's `chat/completions` endpoint behind the `Backend` protocol.

    One class serves every provider in `core/llm/providers.py` because they
    share the OpenAI wire format: the request is `model`, `messages` and
    `max_tokens`; the reply is `choices[0].message.content`, `finish_reason`
    and `usage`. Nothing provider-specific is sent - no effort level, no
    thinking budget, no cache markers - because no free endpoint takes them by
    Anthropic's names and an unknown parameter is a 400 on most. The output
    cap is the one dial from the request profile that reaches the wire.

    Same three rules as the Anthropic backend, same shapes: the retry loop is
    ours and its sleep is injectable; a truncated or filtered reply raises
    carrying its `Usage`; a missing key raises at construction. A prebuilt
    `opener` (the urllib seam every keyless feed uses) skips the key check -
    that is the test seam.

    Calls are paced to the provider's free-tier requests-per-minute. Triage
    arrives in bursts; a burst that trips the limit spends its retries on 429s.
    """

    DEFAULT_MAX_TOKENS = 4096
    #: Longer than the Anthropic backend's: free hosts queue under load, and
    #: open-weight reasoning models are slow to first token.
    TIMEOUT = 180.0
    #: `ask.py backend` reads this to say the effort dial does not reach here.
    takes_effort = False

    def __init__(
        self,
        provider: Provider,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_attempts: int = 3,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[float, float], float] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import random
        import time

        if max_tokens < 1:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        if not provider.base_url:
            raise ValueError(
                f"provider {provider.name!r} has no base URL; build it with "
                "providers.from_env() so LLM_BASE_URL is read"
            )

        self.provider = provider
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self._sleep = sleep if sleep is not None else time.sleep
        self._jitter = jitter if jitter is not None else random.uniform
        self._clock = clock if clock is not None else time.monotonic
        self._last_started: float | None = None
        self.last_request_id: str | None = None

        key = api_key if api_key is not None else provider.key()
        if opener is None and provider.key_env is not None and not (key or "").strip():
            raise AuthError(
                f"{provider.key_env} is empty or unset. Put it in .env (every entrypoint "
                "loads that file; an exported variable wins over it), pick another "
                "provider with LLM_BACKEND, or pass EchoBackend explicitly if you "
                "meant to run without a model."
            )
        self._key = (key or "").strip() or None
        self._opener = opener if opener is not None else urllib.request.urlopen

    # -- what the client asks a backend ----------------------------------------

    @property
    def name(self) -> str:
        return self.provider.name

    def model_for(self, tier: Tier) -> str | None:
        """The provider's model for a chat tier; None for EMBED and LOCAL, which
        the client resolves from `MODEL_IDS` as before."""
        return self.provider.models.get(tier)

    def pricing_for(self, tier: Tier) -> tuple[Decimal, Decimal] | None:
        """Zero on every tier this backend serves. The free tier bills nothing,
        and a ledger row priced at Claude's rate for a call that cost nothing
        would trip the budget rail on spend that never happened."""
        if tier in self.provider.models:
            return (Decimal(0), Decimal(0))
        return None

    # -- the seam ---------------------------------------------------------------

    def complete(
        self,
        model_id: str,
        prompt: str,
        system: str | None,
        profile: RequestProfile | None = None,
    ) -> tuple[str, Usage]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": profile.max_tokens if profile else self.max_tokens,
        }
        data = self._request(body)
        return self._parse(data)

    # -- transport --------------------------------------------------------------

    def _pace(self) -> None:
        """Wait until the provider's per-minute allowance has room for one more."""
        now = self._clock()
        if self.provider.rpm and self._last_started is not None:
            wait = self._last_started + 60.0 / self.provider.rpm - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_started = now

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        """Bounded retry around one POST. The last failure is raised."""
        payload = json.dumps(body).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The same identity every other fetcher here sends. Without it urllib
            # announces itself as "Python-urllib", and at least one provider's
            # edge (Groq, behind Cloudflare) answers that signature with a 403
            # error 1010 before the key is ever looked at.
            "User-Agent": USER_AGENT,
            **self.provider.extra_headers,
        }
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        url = f"{self.provider.base_url}/chat/completions"
        who = self.provider.name

        last: BackendError | None = None
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            self._pace()
            try:
                with self._opener(req, timeout=self.TIMEOUT) as resp:
                    raw = resp.read()
                    hdrs = getattr(resp, "headers", None)
                    rid = None
                    if hdrs is not None:
                        rid = hdrs.get("x-request-id") or hdrs.get("X-Request-Id")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    raise BackendError(
                        f"{who} returned a body that is not JSON: {raw[:200]!r}"
                    ) from e
                if not isinstance(data, dict):
                    raise BackendError(f"{who} returned {type(data).__name__}, not an object")
                self.last_request_id = rid or data.get("id")
                return data
            except urllib.error.HTTPError as e:
                status = e.code
                message = self._scrub(self._error_message(e))
                if status in (401, 403):
                    raise AuthError(f"{who} rejected the key ({status}): {message}") from e
                if status in _TRANSIENT_STATUSES:
                    last = TransientError(f"{who} returned {status}: {message}")
                    retry_after = retry_after_seconds(e)
                elif status == 404:
                    raise BackendError(
                        f"{who} has no model {body['model']!r} (404): {message}. Free "
                        "lineups rotate; set LLM_MODEL_REASON, LLM_MODEL_BALANCED or "
                        "LLM_MODEL_CHEAP to a model this provider serves today "
                        "(`python ask.py backend --list` shows the defaults)"
                    ) from e
                elif status == 400 and any(m in message.lower() for m in _CONTEXT_MARKERS):
                    raise ContextOverflow(
                        f"prompt exceeds the model's context window: {message}. "
                        "Shrink the input - trim evidence or split the question; "
                        "retrying the same prompt hits the same wall."
                    ) from e
                else:
                    raise BackendError(f"{who} returned {status}: {message}") from e
            except OSError as e:
                # URLError, a socket timeout, a reset mid-body, a TLS failure:
                # every one of them is the network, not the request.
                last = TransientError(f"{who} unreachable: {e}")

            if attempt == self.max_attempts:
                raise last
            # Same policy as the Anthropic loop: the server's own wait is obeyed
            # exactly; absent one, exponential backoff with jitter.
            if retry_after is not None:
                self._sleep(retry_after)
            else:
                self._sleep(self._jitter(0.5, 1.0) * 2.0 ** (attempt - 1))
        raise last if last is not None else BackendError("retry loop exited without a result")

    def _scrub(self, text: str) -> str:
        """The provider's words with our own key taken out.

        A gateway that quotes the Authorization header back in its error body
        exists, and the message built from it reaches logs and the trace's
        error field. The exact key goes first, then any bearer token, in case
        the echo is not byte-identical to what was sent.
        """
        if self._key and self._key in text:
            text = text.replace(self._key, "<redacted key>")
        return _BEARER.sub("Bearer <redacted>", text)

    @staticmethod
    def _error_message(err: urllib.error.HTTPError) -> str:
        """The provider's own words for what went wrong, or the status line."""
        try:
            raw = err.read()
        except Exception:  # pragma: no cover - a body that cannot be read
            raw = b""
        text = raw.decode("utf-8", "replace").strip() if raw else ""
        if text:
            try:
                data = json.loads(text)
            except ValueError:
                return text[:300]
            if isinstance(data, dict):
                inner = data.get("error")
                if isinstance(inner, dict) and inner.get("message"):
                    return str(inner["message"])[:300]
                if isinstance(inner, str) and inner:
                    return inner[:300]
                if data.get("message"):
                    return str(data["message"])[:300]
            return text[:300]
        return str(getattr(err, "reason", "") or f"http {err.code}")

    # -- response ---------------------------------------------------------------

    def _parse(self, data: dict[str, Any]) -> tuple[str, Usage]:
        who = self.provider.name
        # A provider that answers 200 with an error object exists (some
        # gateways do). It carries no choices, and it is a failure, not silence.
        choices = data.get("choices")
        if not choices:
            detail = data.get("error") or data
            raise BackendError(f"{who} returned no choices: {json.dumps(detail)[:200]}")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type", "text") == "text"
            )
        elif content is None:
            text = ""
        else:
            text = str(content)
        text = strip_thinking(text)

        raw_usage = data.get("usage")
        if not isinstance(raw_usage, dict):
            raise BackendError(f"{who} response carries no usage; tokens cannot be ledgered")
        try:
            prompt_tokens = int(raw_usage.get("prompt_tokens") or 0)
            completion_tokens = int(raw_usage.get("completion_tokens") or 0)
            details = raw_usage.get("prompt_tokens_details") or {}
            cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
        except (TypeError, ValueError) as e:
            raise BackendError(f"{who} usage is not numeric: {raw_usage!r}") from e
        # OpenAI-style `prompt_tokens` INCLUDES the cached part; `Usage` holds
        # the uncached remainder, as the Anthropic API reports it. Subtract, so
        # one ledger column means one thing whichever backend wrote the row.
        usage = Usage(
            input_tokens=max(prompt_tokens - cached, 0),
            output_tokens=completion_tokens,
            cached_input_tokens=cached,
        )

        finish = choice.get("finish_reason")
        if finish == "length":
            raise Truncated(
                f"model hit max_tokens after {usage.output_tokens} output tokens; "
                "raise max_tokens or narrow the question - a truncated answer is "
                "never returned as a whole one",
                usage=usage,
            )
        if finish == "content_filter":
            raise Declined(
                "model declined to answer (finish_reason=content_filter)",
                usage=usage,
                category="content_filter",
                explanation=text or None,
            )
        if not text.strip():
            raise BackendError(f"{who} returned no text (finish_reason={finish!r})")
        return text, usage


# --- reading a backend without knowing its class ---------------------------------


def model_of(backend: Any, tier: Tier) -> str:
    """The model id this backend will call for a tier.

    A backend may implement `model_for(tier)`; one that does not (Echo,
    Anthropic) is on the Claude table. The client resolves every model through
    here so the ledger records what was actually called, never the table's
    idea of it.
    """
    hook: Any = getattr(backend, "model_for", None)
    if callable(hook):
        chosen = hook(tier)
        if chosen:
            return str(chosen)
    return MODEL_IDS[tier]


def pricing_of(backend: Any, tier: Tier) -> tuple[Decimal, Decimal] | None:
    """The (input, output) USD-per-million rates a backend declares for a tier,
    or None to bill at the tier's first-party rate."""
    hook: Any = getattr(backend, "pricing_for", None)
    if callable(hook):
        rates = hook(tier)
        if rates is not None:
            return cast("tuple[Decimal, Decimal]", rates)
    return None


def models_by_tier(backend: Any) -> dict[Tier, str]:
    """Every tier's model on this backend: what `ask.py backend` and the web
    surface must show instead of the routing table."""
    return {tier: model_of(backend, tier) for tier in MODEL_IDS}


def backend_name(backend: Any, tier: Tier | None = None) -> str:
    """The class answering, per tier when the backend is a split."""
    if tier is not None:
        hook: Any = getattr(backend, "name_for", None)
        if callable(hook):
            return str(hook(tier))
    return type(backend).__name__


def effort_reaches(backend: Any, tier: Tier) -> bool:
    """Whether FINPLANET_EFFORT is sent to the model this tier lands on."""
    if isinstance(backend, SplitBackend):
        backend = backend.by_tier.get(tier, backend)
    return bool(getattr(backend, "takes_effort", True))


class SplitBackend:
    """One backend per Messages tier, so reasoning and triage can sit on
    different providers - Claude for a thesis, a free model for tagging.

    `complete()` receives a model id, not a tier, so dispatch is by model id:
    every tier's model is resolved at construction and mapped to the backend
    that serves it. Two backends serving the same id is refused, because the
    split could not tell which one a call was for.
    """

    def __init__(self, by_tier: dict[Tier, Any]) -> None:
        missing = [t.value for t in MESSAGES_TIERS if t not in by_tier]
        if missing:
            raise ValueError(f"split needs a backend for every Messages tier; missing {missing}")
        self.by_tier: dict[Tier, Any] = dict(by_tier)
        self._owner: dict[str, Any] = {}
        for tier, sub in self.by_tier.items():
            model = model_of(sub, tier)
            prior = self._owner.get(model)
            if prior is not None and prior is not sub:
                raise ValueError(
                    f"model {model!r} is served by two different backends in the split "
                    f"({type(prior).__name__} and {type(sub).__name__}); the split could "
                    "not tell which one a call was for"
                )
            self._owner[model] = sub
        self._last: Any = None

    def model_for(self, tier: Tier) -> str | None:
        sub = self.by_tier.get(tier)
        return model_of(sub, tier) if sub is not None else None

    def pricing_for(self, tier: Tier) -> tuple[Decimal, Decimal] | None:
        sub = self.by_tier.get(tier)
        return pricing_of(sub, tier) if sub is not None else None

    def name_for(self, tier: Tier) -> str:
        sub = self.by_tier.get(tier)
        return type(sub).__name__ if sub is not None else type(self).__name__

    @property
    def last_request_id(self) -> str | None:
        return getattr(self._last, "last_request_id", None)

    def complete(
        self,
        model_id: str,
        prompt: str,
        system: str | None,
        profile: RequestProfile | None = None,
    ) -> tuple[str, Usage]:
        sub = self._owner.get(model_id)
        if sub is None:
            raise BackendError(
                f"no backend in the split serves model {model_id!r}; it serves "
                f"{sorted(self._owner)}"
            )
        self._last = sub
        return sub.complete(model_id, prompt, system, profile=profile)


# --- selection --------------------------------------------------------------------

_SPLIT_ENV: dict[Tier, str] = {
    Tier.REASON: "LLM_BACKEND_REASON",
    Tier.BALANCED: "LLM_BACKEND_BALANCED",
    Tier.CHEAP: "LLM_BACKEND_CHEAP",
}


def _provider_backend(name: str, how: str) -> tuple[OpenAICompatibleBackend, str]:
    """Build a free-provider backend and the sentence that discloses it.

    Colon discipline: `ask.py` labels a narrative with `reason.split(":")[0]`,
    so everything before the first colon is the label and the URL (which has
    one of its own) comes after it.
    """
    provider = _providers.from_env(name)  # ValueError names the variable to set
    backend = OpenAICompatibleBackend(provider)  # AuthError names the key
    m = provider.models
    reason = (
        f"{provider.name} ({how}, free tier): "
        f"reason={m[Tier.REASON]} balanced={m[Tier.BALANCED]} cheap={m[Tier.CHEAP]}; "
        f"OpenAI-compatible at {provider.base_url}; priced at zero in the ledger; "
        f"{provider.note}"
    )
    return backend, reason


def _select(choice: str) -> tuple[Any, str]:
    """One backend for one name, or for no name at all."""
    from core.llm.client import EchoBackend

    if choice in ("echo", "none", "offline"):
        return EchoBackend(), "echo (explicitly selected): deterministic stub, not a model"
    if choice in ("anthropic", "claude"):
        return AnthropicBackend(), "anthropic (explicitly selected, official SDK)"
    if choice == "free":
        provider = _providers.first_configured()
        if provider is None:
            keys = ", ".join(
                p.key_env
                for p in _providers.PROVIDERS
                if p.name in _providers.AUTO_SELECTABLE and p.key_env
            )
            raise AuthError(
                f"LLM_BACKEND=free, but no free provider has a key: set one of {keys} "
                "in .env, or LLM_BACKEND=ollama for a local server"
            )
        return _provider_backend(
            provider.name, f"{provider.key_env} is set, chosen by LLM_BACKEND=free"
        )
    if choice and _providers.is_provider_name(choice):
        return _provider_backend(choice, "explicitly selected")
    if choice:
        raise ValueError(
            f"unknown LLM_BACKEND {choice!r}; expected 'anthropic', 'echo', 'free' "
            f"or a provider: {', '.join(_providers.names())}"
        )

    # Nothing named: the keys decide, and the reason says which one did.
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        where = f", workspace {workspace}" if workspace else ""
        also = _providers.configured_names()
        tail = (
            f"; a free-provider key is also set ({', '.join(also)}) - "
            f"LLM_BACKEND={also[0]} selects it instead"
            if also
            else ""
        )
        return (
            AnthropicBackend(),
            f"anthropic (ANTHROPIC_API_KEY is set, official SDK{where}){tail}",
        )
    provider = _providers.first_configured()
    if provider is not None:
        return _provider_backend(
            provider.name, f"{provider.key_env} is set and ANTHROPIC_API_KEY is not"
        )
    return EchoBackend(), (
        "echo (no ANTHROPIC_API_KEY and no free-provider key): deterministic stub, "
        "NOT a model - narrative output is placeholder text"
    )


def _canonical(backend: Any) -> str:
    """The name two tiers would have to agree on to share one instance."""
    if isinstance(backend, OpenAICompatibleBackend):
        return backend.name
    if isinstance(backend, AnthropicBackend):
        return "anthropic"
    return "echo"


def _split_from_env(base: Any, base_choice: str) -> tuple[SplitBackend, str] | None:
    """Per-tier overrides, when any is set: LLM_BACKEND_REASON, _BALANCED, _CHEAP.

    Tiers that name the same provider share one instance, so the pacing clock
    (one per backend) sees every call to that provider - two instances would
    each believe they had the whole per-minute allowance.
    """
    wanted = {tier: os.environ.get(var, "").strip().lower() for tier, var in _SPLIT_ENV.items()}
    if not any(wanted.values()):
        return None
    built: dict[str, Any] = {_canonical(base): base}
    by_tier: dict[Tier, Any] = {}
    for tier, name in wanted.items():
        if not name:
            by_tier[tier] = base
            continue
        resolved = _providers.lookup(name)
        key = resolved.name if resolved is not None else name
        if key == "claude":
            key = "anthropic"
        if key in ("none", "offline"):
            key = "echo"
        if key not in built:
            built[key], _ = _select(name)
            # `free` resolves to whichever provider had a key; file it under
            # that name too so a later tier saying the name shares it.
            built.setdefault(_canonical(built[key]), built[key])
        by_tier[tier] = built[key]
    split = SplitBackend(by_tier)
    parts = [
        f"{tier.value}={backend_name(by_tier[tier])} {model_of(by_tier[tier], tier)}"
        for tier in MESSAGES_TIERS
    ]
    return split, "split by tier (LLM_BACKEND_<TIER>) " + ", ".join(parts)


def backend_from_env(explicit: str | None = None):
    """Pick a backend the way an operator expects, and SAY which was picked.

    Precedence: an explicit choice, else `LLM_BACKEND`, else a real backend when
    a key exists - Anthropic's first, then the first free provider's - else the
    echo stand-in. With nothing explicit, `LLM_BACKEND_REASON`, `_BALANCED` and
    `_CHEAP` may each put a different backend behind one tier, which is how the
    thesis stays on Claude while triage runs on a free model.

    The returned reason is not decoration - the difference between a real
    answer and a deterministic stub is the single most important thing to show
    on screen, and a system that quietly ran on EchoBackend for a week would be
    indistinguishable from one that worked.

    Returns (backend, reason).
    """
    from core.llm.tiers import cheap_capped, selection_note

    note = selection_note()
    cap = f" | {note}" if note else ""
    if cheap_capped():
        cap += " (every Messages tier resolves to the cheapest model)"
    choice = (explicit or os.environ.get("LLM_BACKEND", "")).strip().lower()

    backend, reason = _select(choice)
    if explicit is None:
        split = _split_from_env(backend, choice)
        if split is not None:
            backend, extra = split
            reason = f"{reason} | {extra}"
    return backend, reason + cap
