"""C1-C2: prompt-caching economics, reconciled against the wire.

The blueprint's Efficiency pillar is caching ROI: an ephemeral cache breakpoint
cuts repeated input cost by up to 90%, and the thing that silently destroys it
is a "cache buster" - anything varying at the top of the system prompt, a
timestamp being the classic.

Two checks. C1 is free and runs always: the system prompt is inspected for
anything that changes between two builds, because a cache that never hits costs
more than no cache at all (a write bills at 1.25x). C2 is live: send the same
cached system prompt twice and confirm the second call reports
`cache_read_input_tokens`, then reconcile the product's own cost arithmetic
against what the API says it billed.
"""

from __future__ import annotations

from decimal import Decimal

from audit._support.budget import (
    CACHE_READ_MULT,
    CACHE_WRITE_MULT,
    IN_RATE,
    MILLION,
    OUT_RATE,
    BudgetExceeded,
    ask,
    live_enabled,
)
from audit._support.scorecard import Check

#: A cache entry is only created above the model's minimum prompt length -
#: 2048 tokens for Haiku. A shorter probe reports zero cached tokens and looks
#: exactly like a broken breakpoint, which is what the first run of this check
#: reported. Roughly 3,000 tokens, comfortably clear of the floor.
FILLER = (
    "You are an equity research analyst working to a written method. "
    "You state the evidence, you name the constraint that bound each number, "
    "and you refuse rather than guess. You never recommend a transaction, "
    "never promise a return, and never describe a position you have not sized. "
    "Every claim you make must be checkable against the material you were given. "
) * 120


def c1_the_system_prompt_has_no_cache_buster() -> Check:
    c = Check(
        "C1",
        "Efficiency",
        1,
        "Nothing varying sits above the cache breakpoint",
        "a timestamp at the top of a system prompt makes every call a cache miss",
    )
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "core" / "llm" / "backends.py").read_text(encoding="utf-8")

    # Find where the cached block is built, and look at what goes into it.
    i = src.find('cache_control": {"type": "ephemeral"}')
    if i < 0:
        return c.failed("no ephemeral cache breakpoint is set on the system prompt")
    window = src[max(0, i - 1200) : i]
    busters = [
        pat
        for pat in ("datetime.now", "time.time", "uuid", "random", "utcnow")
        if re.search(rf"\b{pat}", window)
    ]
    if busters:
        return c.failed(
            f"the cached block is assembled near {', '.join(busters)}; anything varying "
            f"above the breakpoint turns every call into a cache write at 1.25x"
        )
    c.evidence = "the cached block is the caller's system prompt with nothing dynamic prepended"
    return c.ok()


def c2_the_cache_reads_on_the_second_call() -> Check:
    c = Check(
        "C2",
        "Efficiency",
        2,
        "A repeated system prompt reports a cache READ",
        "the breakpoint works on the wire, not just in the request body",
    )
    if not live_enabled():
        return c.skipped("live phase off: set EVAL_LIVE=1 with a key present")
    system = FILLER
    try:
        _, first = ask("c2-write", system, "Reply with the single word: one.", max_tokens=16)
        _, second = ask("c2-read", system, "Reply with the single word: two.", max_tokens=16)
    except BudgetExceeded as e:
        return c.skipped(str(e))
    if second.cache_read <= 0:
        return c.failed(
            f"the second call read {second.cache_read} cached tokens (first call wrote "
            f"{first.cache_write}). Either the prompt is below the cacheable minimum or "
            f"the breakpoint is not taking"
        )
    saved = Decimal(second.cache_read) * IN_RATE * (1 - CACHE_READ_MULT) / MILLION
    c.evidence = (
        f"wrote {first.cache_write} tokens, read {second.cache_read} back, "
        f"saving USD {saved:.6f} on one repeat"
    )
    return c.ok()


def c3_the_product_prices_a_call_the_way_the_api_bills_it() -> Check:
    """Reconciliation, not arithmetic-in-a-vacuum.

    The product prices its own calls from a rate table. If that table forgets
    that a cache WRITE bills at 1.25x, every cached run under-reports its cost -
    quietly, and in the direction that flatters the system.
    """
    c = Check(
        "C3",
        "Efficiency",
        1,
        "The product's cost arithmetic matches the billing rules",
        "cache reads at 0.1x, cache writes at 1.25x, both accounted",
    )
    from core.llm.tiers import Tier, Usage, cost_usd

    usage = Usage(
        input_tokens=1000,
        output_tokens=500,
        cached_input_tokens=4000,
        cache_write_tokens=2000,
    )
    product = Decimal(str(cost_usd(Tier.CHEAP, usage)))
    expected = (
        Decimal(1000) * IN_RATE
        + Decimal(4000) * IN_RATE * CACHE_READ_MULT
        + Decimal(2000) * IN_RATE * CACHE_WRITE_MULT
        + Decimal(500) * OUT_RATE
    ) / MILLION
    gap = abs(product - expected)
    if gap > Decimal(str(0.0000005)):
        return c.failed(
            f"the product prices this call at USD {product:.6f}; the billing rules "
            f"give USD {expected:.6f} (gap USD {gap:.6f}). A cache write bills at "
            f"1.25x input and a read at 0.1x"
        )
    c.evidence = f"USD {product:.6f} on both sides for a 4k-read, 2k-write call"
    return c.ok()


CHECKS = (
    c1_the_system_prompt_has_no_cache_buster,
    c2_the_cache_reads_on_the_second_call,
    c3_the_product_prices_a_call_the_way_the_api_bills_it,
)
