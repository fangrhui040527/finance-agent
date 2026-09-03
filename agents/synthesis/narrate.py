"""The first real product path through the model: narrating a finished thesis.

The rule from mcp_server/tools.py holds with no exceptions: **the model
reasons, the engines decide.** Everything numeric in the narrative's input -
stance, confidence, breakers, challenges - was computed by tested code before
the model is invoked, and the rendered output shows the ENGINE numbers, with
the model's prose labelled by backend name. On the Echo backend the label is
what makes a placeholder unmistakable for analysis.

The system prompt is a module constant and must stay byte-stable: it is sent
as a cache_control block, and a prompt that drifts per call (a timestamp, an
f-string over volatile state) silently invalidates the cache on every request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from core.llm.client import Completion, InferenceClient

#: Byte-stable. Interpolate NOTHING volatile here.
NARRATE_SYSTEM = (
    "You turn a finished equity thesis into three short plain-English paragraphs "
    "for its own author to re-read later.\n"
    "Rules, all hard:\n"
    "  - Use ONLY facts present in the input. Never add a number, a price, a "
    "date, or a name that is not there.\n"
    "  - Never use advice verbs. Bands and conditions, not instructions.\n"
    "  - The case against gets its own paragraph, stated as strongly as the "
    "evidence in the input allows.\n"
    "  - If the input marks the thesis not actionable, say so in the first "
    "sentence and say why.\n"
    "  - No preamble, no headers, no bullet lists. Three paragraphs."
)


def thesis_digest(thesis, challenges) -> str:
    """The model's data: engine output, verbatim, nothing else."""
    lines = [
        f"instrument: {thesis.instrument_id}",
        f"stance: {thesis.stance.value}",
        f"confidence: {thesis.confidence:.2f}",
        f"actionable: {thesis.is_actionable()}",
        f"summary: {thesis.in_one_sentence}",
        "breakers:",
    ]
    for b in thesis.breakers:
        lines.append(f"  - {b.statement} [{b.query} against {b.store}]")
    lines.append("challenges:")
    for c in challenges:
        lines.append(f"  - {c.text}")
    if thesis.gaps:
        lines.append("evidence gaps:")
        for g in thesis.gaps:
            lines.append(f"  - {g}")
    return "\n".join(lines)


def narrate_thesis(client: InferenceClient, thesis, challenges) -> Completion:
    """One model call, tier-routed as THESIS_SYNTHESIS, refusal-as-content."""
    from core.llm.tiers import TaskClass

    return client.complete(
        "a10_thesis",
        TaskClass.THESIS_SYNTHESIS,
        thesis_digest(thesis, challenges),
        system=NARRATE_SYSTEM,
    )


#: Numbers that carry no claim on their own - ordinals, small counts, years in
#: prose. Flagging "three paragraphs" as an invented figure would make the
#: check noise, and a noisy check gets switched off.
_IGNORE = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "100"}


def unsupported_numbers(narrative: str, source: str) -> list[str]:
    """Numbers in the model's prose that do not appear in what we gave it.

    Commitment: never invent a number a tool can give you. This is the
    deterministic half of checking that - no model judges it. A number here is
    not proof of invention (the model may have rounded 2.31 to 2.3), which is
    why the result is a LIST to look at rather than a pass/fail verdict.
    """
    import re

    def numbers(text: str) -> list[str]:
        return re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))

    have = set(numbers(source))
    # A rounded restatement is not an invention: accept a prefix match against
    # anything we supplied, so 2.3 passes when the source says 2.31.
    out = []
    for n in numbers(narrative):
        if n in _IGNORE or n in have:
            continue
        if any(h.startswith(n) or n.startswith(h) for h in have):
            continue
        out.append(n)
    return sorted(set(out), key=lambda x: (len(x), x))
