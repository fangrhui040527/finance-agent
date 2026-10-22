"""P0 DoD: tier routing unit-tested; classification never hits the reasoning tier."""

from decimal import Decimal

import pytest

from core.llm.tiers import (
    MODEL_IDS,
    PRICING_USD,
    WEB_SEARCH_USD,
    TaskClass,
    Tier,
    TierRoutingError,
    Usage,
    cost_usd,
    route,
    worst_case_usd,
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
    # 1M input + 1M output on the reason tier (Opus 5.5) = $4 + $20
    assert cost_usd(Tier.REASON, Usage(1_000_000, 1_000_000)) == Decimal("24.00")


#: Each priced tier's first-party rate, USD per million tokens, paired with the
#: model ID it is the rate FOR. Changing a model without its price, or a price
#: without its model, fails here.
EXPECTED_RATES: dict[Tier, tuple[str, Decimal, Decimal]] = {
    Tier.REASON: ("claude-opus-5-5", Decimal("4.00"), Decimal("20.00")),
    Tier.BALANCED: ("claude-sonnet-5-5", Decimal("2.00"), Decimal("10.00")),
    Tier.CHEAP: ("claude-haiku-5-5", Decimal("0.10"), Decimal("0.50")),
}


def test_every_priced_tier_is_billed_at_its_own_models_rate():
    """The bug this exists to catch, because it already happened once.

    BALANCED was billed at $3/$15 - Sonnet 4.6's rate - while MODEL_IDS had
    already moved to Sonnet 5 at $2/$10. Nothing failed. Every cost, every
    budget check and every ledger row was simply 50% high, and the daily budget
    refused questions it could afford.

    A rate on its own looks like a number someone chose. Pinned to the model it
    prices, a stale row becomes a failing test instead of a quiet overcharge.
    """
    for tier, (model, inp, out) in EXPECTED_RATES.items():
        assert MODEL_IDS[tier] == model, (
            f"{tier.value} now routes to {MODEL_IDS[tier]}, not {model}. If that "
            f"is deliberate, update its rate in EXPECTED_RATES too - the pair "
            f"moves together or not at all."
        )
        assert PRICING_USD[tier] == (inp, out), (
            f"{tier.value} bills {model} at {PRICING_USD[tier]}, expected ({inp}, {out}) per MTok."
        )


def test_a_cheaper_tier_actually_costs_less():
    """The shape every mispricing takes, whichever direction it drifts.

    Checked on input and output separately: a tier can be correct on one and
    stale on the other, and averaging the two would hide exactly that.
    """
    ladder = (Tier.CHEAP, Tier.BALANCED, Tier.REASON)
    for cheaper, dearer in zip(ladder[:-1], ladder[1:], strict=True):
        lo_in, lo_out = PRICING_USD[cheaper]
        hi_in, hi_out = PRICING_USD[dearer]
        assert lo_in < hi_in, (
            f"{cheaper.value} input costs {lo_in} against {dearer.value}'s "
            f"{hi_in}: the routing table sends cheap work to the cheaper tier, "
            f"so a cheaper tier that bills more inverts every budget decision."
        )
        assert lo_out < hi_out, (
            f"{cheaper.value} output costs {lo_out} against {dearer.value}'s {hi_out}."
        )


def test_the_rate_guard_covers_every_tier_that_bills():
    """A tier added later with no entry above would be silently unguarded -
    which is how the first stale row survived as long as it did."""
    billed = {t for t in Tier if PRICING_USD[t] != (Decimal(0), Decimal(0))}
    unguarded = billed - set(EXPECTED_RATES) - {Tier.EMBED}
    assert not unguarded, (
        f"{sorted(t.value for t in unguarded)} bill for tokens but no test pins "
        f"their rate. Add them to EXPECTED_RATES."
    )


def test_cached_input_bills_at_ten_percent():
    full = cost_usd(Tier.BALANCED, Usage(1_000_000, 0))
    cached = cost_usd(Tier.BALANCED, Usage(0, 0, cached_input_tokens=1_000_000))
    assert cached == full * Decimal("0.1")


def test_misrouting_everything_to_reason_is_measurably_worse():
    """docs/08 section 4.6: the misrouting claim, asserted rather than asserted-in-prose.
    On the cheap-tier work alone it is far worse than the 5x of the whole bill:
    Haiku 5.5 is a fortieth of Opus 5.5's rate. Priced as one request, this
    month-sized aggregate crosses Haiku's long-prompt step and is still 8x."""
    cheap_work = Usage(11_200_000, 2_300_000)
    tiered = cost_usd(Tier.CHEAP, cheap_work)
    misrouted = cost_usd(Tier.REASON, cheap_work)
    assert misrouted > tiered * 4


def test_a_web_search_bills_ten_dollars_a_thousand_on_top_of_the_tokens():
    """The server-side search fee is per search, not per token, so a call that
    searched costs more than its tokens say - by exactly this much."""
    assert WEB_SEARCH_USD * 1000 == Decimal("10")
    plain = cost_usd(Tier.CHEAP, Usage(1_000, 100))
    searched = cost_usd(Tier.CHEAP, Usage(1_000, 100, web_search_requests=4))
    assert searched - plain == Decimal("0.04")


def test_the_reservation_is_the_most_a_call_can_cost():
    """What the monthly cap reserves before a call: every input token fresh and
    every output token used, at the model's own rate. An Opus 5.5 call of 3,000
    input tokens with a 16,000-token cap reserves $0.015 + $0.32 (input priced
    at the dearer cache-write rate, the worst way an input token can bill)."""
    reserve = worst_case_usd("claude-opus-5-5", 3_000, 16_000)
    assert reserve == Decimal("3000") / 1_000_000 * Decimal("5.00") + Decimal("0.32")
    spent = cost_usd(Tier.REASON, Usage(3_000, 16_000), model_id="claude-opus-5-5")
    assert spent <= reserve
