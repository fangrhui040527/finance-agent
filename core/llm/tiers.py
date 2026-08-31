"""The single inference choke point.

docs/01 section 9: four tiers routed by task class, never by habit. No agent
imports a vendor SDK directly - everything goes through complete() here.

docs/08 section 4.6: routing everything to the reasoning tier costs 3.4x for no
quality gain (RM 6,300/year). That is why the routing table lives here and is not
left to each agent's discretion.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Tier(str, Enum):
    REASON = "reason"
    BALANCED = "balanced"
    CHEAP = "cheap"
    EMBED = "embed"
    LOCAL = "local"


class Effort(str, Enum):
    """How hard the model is asked to think, in Claude's own five levels.

    This is `output_config.effort` on the Messages API, not a local invention:
    the same low/medium/high/xhigh/max ladder every current Claude surface
    exposes. It is a SEPARATE dial from the tier - the tier decides WHICH model
    answers, the effort decides how much thinking that model spends getting
    there - and the two move independently on purpose. Raising effort on Haiku
    is a great deal cheaper than routing the same question to Opus, and the
    cost table cannot tell you which is the better trade for your question.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


#: Ascending, so a surface can print the ladder in the order a person reads it.
EFFORT_ORDER: tuple[Effort, ...] = (
    Effort.LOW,
    Effort.MEDIUM,
    Effort.HIGH,
    Effort.XHIGH,
    Effort.MAX,
)

_EFFORT_VALUES: tuple[str, ...] = tuple(e.value for e in EFFORT_ORDER)


class TaskClass(str, Enum):
    """What the call is FOR. Tier is derived from this, never passed directly."""

    # reason
    THESIS_SYNTHESIS = "thesis_synthesis"
    RED_TEAM = "red_team"
    ATTRIBUTION_HARD = "attribution_hard"
    REFLECTION_DEEP = "reflection_deep"
    # balanced
    FUNDAMENTALS_READ = "fundamentals_read"
    VALUATION_COMMENT = "valuation_comment"
    CATALYST_MATCH = "catalyst_match"
    MACRO_READ = "macro_read"
    SECTOR_READ = "sector_read"
    FLOW_READ = "flow_read"
    RISK_COMMENT = "risk_comment"
    ADHOC_QUERY = "adhoc_query"
    DAILY_BRIEF = "daily_brief"
    BREAKER_SWEEP = "breaker_sweep"
    # cheap
    INTENT_ROUTING = "intent_routing"
    NEWS_TRIAGE = "news_triage"
    ENTITY_TAG = "entity_tag"
    DEDUP_ADJUDICATE = "dedup_adjudicate"
    CATEGORY_CLASSIFY = "category_classify"
    # local / embed
    SENTIMENT_SCORE = "sentiment_score"
    RERANK = "rerank"
    EMBED_TEXT = "embed_text"


ROUTING: dict[TaskClass, Tier] = {
    TaskClass.THESIS_SYNTHESIS: Tier.REASON,
    TaskClass.RED_TEAM: Tier.REASON,
    TaskClass.ATTRIBUTION_HARD: Tier.REASON,
    TaskClass.REFLECTION_DEEP: Tier.REASON,
    TaskClass.FUNDAMENTALS_READ: Tier.BALANCED,
    TaskClass.VALUATION_COMMENT: Tier.BALANCED,
    TaskClass.CATALYST_MATCH: Tier.BALANCED,
    TaskClass.MACRO_READ: Tier.BALANCED,
    TaskClass.SECTOR_READ: Tier.BALANCED,
    TaskClass.FLOW_READ: Tier.BALANCED,
    TaskClass.RISK_COMMENT: Tier.BALANCED,
    TaskClass.ADHOC_QUERY: Tier.BALANCED,
    TaskClass.DAILY_BRIEF: Tier.BALANCED,
    TaskClass.BREAKER_SWEEP: Tier.BALANCED,
    TaskClass.INTENT_ROUTING: Tier.CHEAP,
    TaskClass.NEWS_TRIAGE: Tier.CHEAP,
    TaskClass.ENTITY_TAG: Tier.CHEAP,
    TaskClass.DEDUP_ADJUDICATE: Tier.CHEAP,
    TaskClass.CATEGORY_CLASSIFY: Tier.CHEAP,
    TaskClass.SENTIMENT_SCORE: Tier.LOCAL,
    TaskClass.RERANK: Tier.LOCAL,
    TaskClass.EMBED_TEXT: Tier.EMBED,
}

# USD per million tokens, first-party API rates (docs/08 section 4).
#
# These are paired with MODEL_IDS below and must be changed together. BALANCED
# was billed at $3/$15 - Sonnet 4.6's rate - while MODEL_IDS already pointed at
# Sonnet 5, which is $2/$10. Nothing failed: every cost, every budget check and
# every ledger row was simply 50% too high, and the daily budget refused
# questions it could afford.
PRICING_USD: dict[Tier, tuple[Decimal, Decimal]] = {
    Tier.REASON: (Decimal("5.00"), Decimal("25.00")),  # claude-opus-5
    Tier.BALANCED: (Decimal("2.00"), Decimal("10.00")),  # claude-sonnet-5
    Tier.CHEAP: (Decimal("1.00"), Decimal("5.00")),  # claude-haiku-4-5
    Tier.EMBED: (Decimal("0.05"), Decimal("0")),
    Tier.LOCAL: (Decimal("0"), Decimal("0")),
}

MODEL_IDS: dict[Tier, str] = {
    Tier.REASON: "claude-opus-5",
    Tier.BALANCED: "claude-sonnet-5",
    Tier.CHEAP: "claude-haiku-4-5",
    Tier.EMBED: "text-embedding-3-small",
    Tier.LOCAL: "finbert-local",
}


class TierRoutingError(ValueError):
    """Raised when a caller tries to pick a tier directly."""


def route(task: TaskClass) -> Tier:
    try:
        return ROUTING[task]
    except KeyError as exc:
        raise TierRoutingError(f"no tier registered for task class {task!r}") from exc


@dataclass(frozen=True)
class Usage:
    """Token counts exactly as the Messages API reports them.

    `input_tokens` is the UNCACHED remainder. The API reports cache reads and
    cache writes in fields of their own and does not fold them into it. The
    first `cost_usd` subtracted the reads from `input_tokens` a second time -
    invisible while nothing was ever cached, and a NEGATIVE bill on the first
    day something was. Found by the live QA pass, because no offline test had
    ever seen a non-zero cache read.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0  # cache_read_input_tokens, billed at 0.1x
    cache_write_tokens: int = 0  # cache_creation_input_tokens, billed at 1.25x


@dataclass(frozen=True)
class RequestProfile:
    """How a request is SHAPED for the model a tier resolves to.

    Haiku 4.5 rejects `output_config.effort` and `thinking: {type: "adaptive"}`
    with a 400; the Opus/Sonnet 5 family wants adaptive thinking and an effort
    level, and long generations should stream so they cannot die on an HTTP idle
    timeout. The table below is the single place that knowledge lives - the
    backend never guesses.

    `thinking_budget` is the Haiku-shaped dial: that model takes the older
    `{type: "enabled", budget_tokens: N}` form instead of an effort level, so
    the same five-level control reaches all three models without either one
    being sent a parameter it rejects. The two thinking forms are mutually
    exclusive by construction - sending both is a 400, and a profile that could
    express it would be a bug waiting for a live call to find.
    """

    max_tokens: int
    adaptive_thinking: bool = False
    effort: str | None = None
    stream: bool = False
    thinking_budget: int | None = None

    def __post_init__(self) -> None:
        if self.thinking_budget is not None:
            if self.adaptive_thinking or self.effort:
                raise ValueError(
                    "thinking_budget is the pre-4.6 form and cannot be combined with "
                    "adaptive thinking or an effort level; one model, one dial"
                )
            if self.thinking_budget < 1024:
                raise ValueError(
                    f"thinking budget must be at least 1024 tokens, got {self.thinking_budget}"
                )
            if self.thinking_budget >= self.max_tokens:
                raise ValueError(
                    f"thinking budget {self.thinking_budget} must be under max_tokens "
                    f"{self.max_tokens}; the budget is spent INSIDE the output cap"
                )
        if self.effort is not None and self.effort not in _EFFORT_VALUES:
            raise ValueError(
                f"unknown effort {self.effort!r}; expected one of {', '.join(_EFFORT_VALUES)}"
            )


REQUEST_PROFILES: dict[Tier, RequestProfile] = {
    Tier.REASON: RequestProfile(
        max_tokens=16000, adaptive_thinking=True, effort="high", stream=True
    ),
    Tier.BALANCED: RequestProfile(max_tokens=8000, adaptive_thinking=True, effort="medium"),
    Tier.CHEAP: RequestProfile(max_tokens=1024),
    Tier.EMBED: RequestProfile(max_tokens=1),
    Tier.LOCAL: RequestProfile(max_tokens=1),
}


#: What an operator may write in FINPLANET_MODEL. Both the friendly name and
#: the exact model id resolve to the same tier, because the two get used
#: interchangeably in practice and a system that accepts one spelling and
#: silently ignores the other is the `MYX`/`XKLS` defect again.
MODEL_ALIASES: dict[str, Tier] = {
    "haiku": Tier.CHEAP,
    "haiku-4-5": Tier.CHEAP,
    "claude-haiku-4-5": Tier.CHEAP,
    "cheap": Tier.CHEAP,
    "sonnet": Tier.BALANCED,
    "sonnet-5": Tier.BALANCED,
    "claude-sonnet-5": Tier.BALANCED,
    "balanced": Tier.BALANCED,
    "opus": Tier.REASON,
    "opus-5": Tier.REASON,
    "claude-opus-5": Tier.REASON,
    "reason": Tier.REASON,
}

#: The Messages tiers. EMBED and LOCAL are not Claude models and are never
#: pinned, capped or given an effort level.
MESSAGES_TIERS: tuple[Tier, ...] = (Tier.REASON, Tier.BALANCED, Tier.CHEAP)

#: Tiers whose model accepts `output_config.effort`. Haiku 4.5 does not - it
#: returns a 400 - which is why CHEAP is absent here and gets a budget instead.
EFFORT_TIERS: tuple[Tier, ...] = (Tier.REASON, Tier.BALANCED)

#: Effort -> (thinking budget, output cap) for Haiku 4.5, the one model here
#: with no effort parameter. The budget is spent INSIDE the cap, so the two
#: move together; a table is the only honest way to hold that.
CHEAP_EFFORT_BUDGET: dict[Effort, tuple[int, int]] = {
    Effort.LOW: (1024, 3072),
    Effort.MEDIUM: (2048, 4608),
    Effort.HIGH: (4096, 8192),
    Effort.XHIGH: (8192, 12288),
    Effort.MAX: (16000, 20000),
}

#: The output cap an effort level needs to land in on an effort-taking model.
#: Raising effort without raising the cap buys thinking that is then truncated:
#: `Truncated` raises, and the spend was real.
EFFORT_MAX_TOKENS: dict[Effort, int] = {
    Effort.LOW: 4096,
    Effort.MEDIUM: 8000,
    Effort.HIGH: 16000,
    Effort.XHIGH: 32000,
    Effort.MAX: 64000,
}

#: At or above this cap the request streams. Not a preference: a large
#: max_tokens on a non-streaming call can die on an HTTP idle timeout, and a
#: plan that dies there has still been billed.
STREAM_ABOVE_MAX_TOKENS = 16000


class ModelSelectionError(ValueError):
    """An unreadable FINPLANET_MODEL or FINPLANET_EFFORT. Never guessed at.

    The same rule as `LLM_BACKEND`: a junk value fails at startup rather than
    falling back to a default, because a typo that silently selects Opus is the
    expensive direction of a mistake nobody would otherwise notice.
    """


def pinned_tier() -> Tier | None:
    """The one model every Messages tier is pinned to, or None for the router.

    `FINPLANET_MODEL=haiku|sonnet|opus` (or an exact model id) is the operator's
    model selection. `FINPLANET_CHEAP=1` still means exactly
    `FINPLANET_MODEL=haiku`; when both are set the explicit name wins, because
    it is the more specific instruction.
    """
    import os

    raw = os.environ.get("FINPLANET_MODEL", "").strip().lower()
    if raw:
        try:
            return MODEL_ALIASES[raw]
        except KeyError:
            raise ModelSelectionError(
                f"unknown FINPLANET_MODEL {raw!r}; expected one of "
                f"{', '.join(sorted(set(MODEL_ALIASES)))}"
            ) from None
    if os.environ.get("FINPLANET_CHEAP", "").strip() in ("1", "true", "yes"):
        return Tier.CHEAP
    return None


def cheap_capped() -> bool:
    """True when every Messages tier resolves to the cheapest model.

    The development budget rule: live testing runs on Haiku, always, and the
    cap is DISCLOSED everywhere a backend is named - never silent. Promoted
    from qa/_support/cheap.py so a dev run cannot accidentally bill Opus. Now
    also true for `FINPLANET_MODEL=haiku`, which is the same instruction said
    the other way; a disclosure that knew only one spelling would be telling
    the truth about the variable and lying about the run.
    """
    return pinned_tier() is Tier.CHEAP


def selected_effort() -> Effort | None:
    """`FINPLANET_EFFORT`, or None to leave each tier on its own default."""
    import os

    raw = os.environ.get("FINPLANET_EFFORT", "").strip().lower()
    if not raw:
        return None
    try:
        return Effort(raw)
    except ValueError:
        raise ModelSelectionError(
            f"unknown FINPLANET_EFFORT {raw!r}; expected one of {', '.join(_EFFORT_VALUES)}"
        ) from None


def effective_tier(tier: Tier) -> Tier:
    """The tier that will actually be called AND billed.

    Under a pin every Messages tier resolves to the pinned one - model and
    price move together, so the ledger records what was truly spent rather
    than what the task class would normally cost. EMBED/LOCAL are untouched:
    they are not Claude models and there is nothing to pin them to.
    """
    pin = pinned_tier()
    if pin is not None and tier in MESSAGES_TIERS:
        return pin
    return tier


def profile_for(tier: Tier) -> RequestProfile:
    """The request shape for a tier, after the operator's effort selection.

    `REQUEST_PROFILES` stays the untouched default table - what runs when
    nothing is selected, and what the tests pin. This is the resolved view,
    and it is the only shape the client should send.

    Three model-shaped facts live here and nowhere else:

      * Opus 5 and Sonnet 5 take `output_config.effort` at all five levels.
      * Haiku 4.5 takes no effort parameter at all. Its dial is the older
        `thinking.budget_tokens`, so the five levels reach it in the one form
        it accepts rather than being quietly dropped on the floor.
      * A higher effort needs a bigger output cap to land in, and a big cap
        needs to stream.
    """
    base = REQUEST_PROFILES[tier]
    effort = selected_effort()
    if effort is None or tier not in MESSAGES_TIERS:
        return base

    if tier is Tier.CHEAP:
        budget, cap = CHEAP_EFFORT_BUDGET[effort]
        return RequestProfile(
            max_tokens=cap,
            thinking_budget=budget,
            stream=cap >= STREAM_ABOVE_MAX_TOKENS,
        )

    cap = max(base.max_tokens, EFFORT_MAX_TOKENS[effort])
    return RequestProfile(
        max_tokens=cap,
        adaptive_thinking=True,
        effort=effort.value,
        stream=base.stream or cap >= STREAM_ABOVE_MAX_TOKENS,
    )


def selection_note() -> str:
    """One line naming the model pin and the effort; "" when neither is set.

    Every surface that names a backend appends this. A run that silently thinks
    at `low` because a shell still has FINPLANET_EFFORT exported is
    indistinguishable from one that thought hard - the same class of failure as
    EchoBackend answering while looking like a model.
    """
    pin, effort = pinned_tier(), selected_effort()
    parts: list[str] = []
    if pin is not None:
        parts.append(f"every Messages tier pinned to {MODEL_IDS[pin]} (FINPLANET_MODEL)")
    if effort is not None:
        parts.append(f"effort {effort.value} (FINPLANET_EFFORT)")
        if pin is None or pin is Tier.CHEAP:
            budget = CHEAP_EFFORT_BUDGET[effort][0]
            parts.append(
                f"on Haiku 4.5 that is a {budget}-token thinking budget, "
                "because it takes no effort parameter"
            )
    # No colons. `ask.py` labels the narrative with `reason.split(":")[0]` to cut
    # the explanatory tail off the echo reason, so a colon in here truncates the
    # disclosure mid-sentence - the label stops exactly where it starts saying
    # something. Found by reading a live run's own output.
    return " | ".join(parts)


#: First-party rates: a cache read costs a tenth of fresh input, a five-minute
#: cache write a quarter more. Both scale the tier's own input rate.
CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")


def cost_usd(tier: Tier, usage: Usage) -> Decimal:
    """Fresh input at the base rate, reads at a tenth, writes at a quarter over.

    Every term is clamped at zero, so the sum can never be negative whatever a
    malformed usage block says.
    """
    in_rate, out_rate = PRICING_USD[tier]
    million = Decimal(1_000_000)
    fresh = Decimal(max(usage.input_tokens, 0)) / million * in_rate
    cached = Decimal(max(usage.cached_input_tokens, 0)) / million * in_rate * CACHE_READ_MULTIPLIER
    written = Decimal(max(usage.cache_write_tokens, 0)) / million * in_rate * CACHE_WRITE_MULTIPLIER
    out = Decimal(max(usage.output_tokens, 0)) / million * out_rate
    return fresh + cached + written + out
