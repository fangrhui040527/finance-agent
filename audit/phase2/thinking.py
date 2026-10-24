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
        "Thinking and effort are sent in the one form every model accepts",
        "the 5.5 models reject `budget_tokens` and sampling parameters with a 400; "
        "the tier table and the backend have to know that",
    )
    import os

    from core.llm.tiers import EFFORT_ORDER, MESSAGES_TIERS, REQUEST_PROFILES, Tier, profile_for

    missing = [t.value for t in MESSAGES_TIERS if REQUEST_PROFILES.get(t) is None]
    if missing:
        return c.failed(f"no request profile for {missing}")

    # The wire form, read from the backend itself: the budgeted form and the
    # sampling parameters are a 400 on every model this system calls, so the
    # code that builds the request must not be able to produce them at all.
    src = (ROOT / "core" / "llm" / "backends.py").read_text(encoding="utf-8")
    for banned in ("budget_tokens", '"temperature"', '"top_p"', '"top_k"'):
        if re.search(rf"params\[{re.escape(banned)}\]|{re.escape(banned)}\s*:", src):
            return c.failed(
                f"core/llm/backends.py can build {banned.strip(chr(34))}, which every "
                "5.5 model rejects with an HTTP 400"
            )

    # The default table is not enough: FINPLANET_EFFORT reshapes the request,
    # and the shape that reaches the wire is `profile_for`. An audit that reads
    # the table would pass while every call under a selected effort returned a
    # 400 - checking the wrong object is how the six drifted agent ids survived
    # a green suite.
    before = os.environ.get("FINPLANET_EFFORT")
    seen = []
    try:
        for effort in (None, *EFFORT_ORDER):
            if effort is None:
                os.environ.pop("FINPLANET_EFFORT", None)
            else:
                os.environ["FINPLANET_EFFORT"] = effort.value
            label = effort.value if effort else "unset"
            for tier in MESSAGES_TIERS:
                shape = profile_for(tier)
                if hasattr(shape, "thinking_budget"):
                    return c.failed(
                        "the request profile still carries a thinking budget, the "
                        "`budget_tokens` form every 5.5 model rejects with an HTTP 400"
                    )
                if not (shape.adaptive_thinking and shape.effort):
                    return c.failed(
                        f"at effort={label} the {tier.value} profile sends "
                        f"{'no effort level' if shape.adaptive_thinking else 'no adaptive thinking'}; "
                        "the 5.5 models are steered by effort, and a profile without it "
                        "runs at whatever the model's own default happens to be"
                    )
            cheap = profile_for(Tier.CHEAP)
            seen.append(f"{label}={cheap.effort}/{cheap.max_tokens}")
    finally:
        if before is None:
            os.environ.pop("FINPLANET_EFFORT", None)
        else:
            os.environ["FINPLANET_EFFORT"] = before

    c.evidence = "cheap tier effort/cap by selection: " + ", ".join(seen)
    return c.ok()


CHECKS = (
    x1_no_assistant_turn_is_ever_sent_back,
    x2_thinking_is_only_asked_for_where_it_is_supported,
)
