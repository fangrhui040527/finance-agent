"""X1-X2: extended thinking, signatures, and what context this system keeps.

The blueprint's Extensibility pillar is one specific, unrecoverable bug: an
assistant turn containing a `thinking` block carries an Anthropic
cryptographic `signature`, and sending that turn back with the thinking text
summarised, trimmed or re-serialised returns HTTP 400 permanently. Any system
that compacts its own conversation history is one refactor away from it.

The honest finding here is architectural: this system never sends an assistant
turn back at all. Every call is one user turn against a cached system prompt,
so there is no context to compact and no signature to preserve. That is worth
checking rather than assuming, because it is the property that makes the bug
impossible - and the day a multi-turn path is added, it stops being true.
"""

from __future__ import annotations

import re
from pathlib import Path

from audit._support.scorecard import Check

ROOT = Path(__file__).resolve().parents[2]


def x1_no_assistant_turn_is_ever_sent_back() -> Check:
    c = Check(
        "X1",
        "Extensibility",
        1,
        "No conversation history is replayed to the API",
        "context compaction cannot corrupt a thinking signature that is never resent",
    )
    src = (ROOT / "core" / "llm" / "backends.py").read_text(encoding="utf-8")
    if re.search(r"role[\"']?\s*[:=]\s*[\"']assistant", src):
        return c.failed(
            "an assistant turn is constructed and sent; if it can carry a thinking "
            "block, its signature must be preserved byte-for-byte and this audit "
            "does not yet check that"
        )
    if "messages" not in src:
        return c.failed("could not locate the message assembly to inspect")
    c.evidence = (
        "every request is one user turn against a cached system prompt; there is "
        "no history to compact"
    )
    return c.ok()


def x2_thinking_is_only_asked_for_where_it_is_supported() -> Check:
    """A 400 that only appears on the cheap tier is a 400 you meet in production."""
    c = Check(
        "X2",
        "Extensibility",
        1,
        "Thinking and effort are sent only to models that accept them",
        "Haiku rejects thinking with a 400; the tier table has to know that",
    )
    from core.llm.tiers import REQUEST_PROFILES, Tier

    cheap = REQUEST_PROFILES.get(Tier.CHEAP)
    if cheap is None:
        return c.failed("the cheap tier has no request profile")
    if getattr(cheap, "adaptive_thinking", False) or getattr(cheap, "effort", ""):
        return c.failed(
            "the cheap profile asks for thinking or effort, which Haiku rejects "
            "with an HTTP 400 - every cheap call would fail"
        )
    c.evidence = (
        f"cheap profile: thinking={getattr(cheap, 'adaptive_thinking', False)}, "
        f"effort={getattr(cheap, 'effort', '') or 'none'}"
    )
    return c.ok()


CHECKS = (
    x1_no_assistant_turn_is_ever_sent_back,
    x2_thinking_is_only_asked_for_where_it_is_supported,
)
