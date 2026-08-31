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

    Haiku 4.5 rejects `thinking`/`effort` with a 400; the Opus/Sonnet 5 family
    wants adaptive thinking and an effort level, and long generations should
    stream so they cannot die on an HTTP idle timeout. The table below is the
    single place that knowledge lives - the backend never guesses.
    """

    max_tokens: int
    adaptive_thinking: bool = False
    effort: str | None = None
    stream: bool = False


REQUEST_PROFILES: dict[Tier, RequestProfile] = {
    Tier.REASON: RequestProfile(
        max_tokens=16000, adaptive_thinking=True, effort="high", stream=True
    ),
    Tier.BALANCED: RequestProfile(max_tokens=8000, adaptive_thinking=True, effort="medium"),
    Tier.CHEAP: RequestProfile(max_tokens=1024),
    Tier.EMBED: RequestProfile(max_tokens=1),
    Tier.LOCAL: RequestProfile(max_tokens=1),
}


def cheap_capped() -> bool:
    """FINPLANET_CHEAP=1 forces every Messages tier onto the cheapest model.

    The development budget rule: live testing runs on Haiku, always, and the
    cap is DISCLOSED everywhere a backend is named - never silent. Promoted
    from qa/_support/cheap.py so a dev run cannot accidentally bill Opus.
    """
    import os

    return os.environ.get("FINPLANET_CHEAP", "").strip() in ("1", "true", "yes")


def effective_tier(tier: Tier) -> Tier:
    """The tier that will actually be called AND billed.

    Under the cheap cap, REASON and BALANCED resolve to CHEAP - model and
    price move together, so the ledger records what was truly spent rather
    than what the task class would normally cost. EMBED/LOCAL are untouched.
    """
    if cheap_capped() and tier in (Tier.REASON, Tier.BALANCED):
        return Tier.CHEAP
    return tier


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
