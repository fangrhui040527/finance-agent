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
PRICING_USD: dict[Tier, tuple[Decimal, Decimal]] = {
    Tier.REASON: (Decimal("5.00"), Decimal("25.00")),
    Tier.BALANCED: (Decimal("3.00"), Decimal("15.00")),
    Tier.CHEAP: (Decimal("1.00"), Decimal("5.00")),
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
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


def cost_usd(tier: Tier, usage: Usage) -> Decimal:
    """Cached input reads bill at ~10% of base input."""
    in_rate, out_rate = PRICING_USD[tier]
    million = Decimal(1_000_000)
    fresh = Decimal(usage.input_tokens - usage.cached_input_tokens) / million * in_rate
    cached = Decimal(usage.cached_input_tokens) / million * in_rate * Decimal("0.1")
    out = Decimal(usage.output_tokens) / million * out_rate
    return fresh + cached + out
