"""P0 DoD: tier routing unit-tested; classification never hits the reasoning tier."""

from decimal import Decimal

import pytest

from core.llm.tiers import (
    MODEL_IDS,
    PRICING_USD,
    TaskClass,
    Tier,
    TierRoutingError,
    Usage,
    cost_usd,
    route,
)


def test_every_task_class_routes():
    for task in TaskClass:
        assert route(task) in Tier


def test_classification_never_reaches_reasoning_tier():
    cheap_work = {
        TaskClass.INTENT_ROUTING,
        TaskClass.NEWS_TRIAGE,
        TaskClass.ENTITY_TAG,
        TaskClass.DEDUP_ADJUDICATE,
        TaskClass.CATEGORY_CLASSIFY,
    }
    for task in cheap_work:
        assert route(task) is Tier.CHEAP, f"{task} must not escalate"


def test_local_tier_is_free():
    assert PRICING_USD[Tier.LOCAL] == (Decimal(0), Decimal(0))


def test_unknown_task_raises():
    class Fake(str):
        pass

    with pytest.raises(TierRoutingError):
        route(Fake("not_a_task"))


def test_every_tier_has_a_model_id():
    assert set(MODEL_IDS) == set(Tier)


def test_cost_arithmetic():
    # 1M input + 1M output on the reason tier = $5 + $25
    assert cost_usd(Tier.REASON, Usage(1_000_000, 1_000_000)) == Decimal("30.00")


def test_cached_input_bills_at_ten_percent():
    full = cost_usd(Tier.BALANCED, Usage(1_000_000, 0))
    cached = cost_usd(Tier.BALANCED, Usage(0, 0, cached_input_tokens=1_000_000))
    assert cached == full * Decimal("0.1")


def test_misrouting_everything_to_reason_is_measurably_worse():
    """docs/08 section 4.6: the 3.4x claim, asserted rather than asserted-in-prose."""
    cheap_work = Usage(11_200_000, 2_300_000)
    tiered = cost_usd(Tier.CHEAP, cheap_work)
    misrouted = cost_usd(Tier.REASON, cheap_work)
    assert misrouted > tiered * 4
