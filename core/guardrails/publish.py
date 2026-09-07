"""The publication rail's one door.

Four of the five rails had something in production constructing an `Action`
for them. This one did not: `Rail.PUBLICATION` appeared in the rail order, in
`DisclaimerPolicy`, and in two test files, and nowhere else. The rule was
enforceable and never enforced, so the guarantee it stands for - *nothing
leaves this system without saying what it is* - was a convention held up by
whoever last edited a renderer.

The gap that made this worth closing is `journal/digest/`. The collector
writes a digest three times a day, commits it and pushes it to a public
repository. A reader who arrives at `latest.md` from a search engine finds a
list of company names with tone scores and starred escalations, and nothing
at all telling them the file is not a signal service. That is the single most
literally *published* thing this system produces and it was the one output
with no notice on it.

The split here is deliberate and it is what keeps the check honest:

  * **Renderers compose.** `to_markdown`, `thesis_memo`, `daily_brief` each
    end their own output with the notice, the same way they already own every
    other line they emit.
  * **`publish` checks.** It reads the finished text and refuses to let it
    out unsigned.

Composing and checking in the same expression - `publish(sign(text))` - would
assert only that two adjacent lines of code agree, which is worth nothing.
The check earns its place by sitting at a different boundary from the writer,
so the edit that drops the last line of a renderer six months from now fails
loudly at the door instead of shipping.
"""

from __future__ import annotations

from core.guardrails.policy import Action, PolicyEngine, PolicyResult, Rail

#: The standing notice. Every user-facing surface ends with this exact
#: sentence, and the publication rail reads the text for it.
PUBLICATION_NOTICE = "This is analysis, not advice, and this system cannot place orders."


def carries_notice(text: str) -> bool:
    """Does this text say what it is? Substring, so a renderer may indent it."""
    return PUBLICATION_NOTICE in (text or "")


def sign(text: str) -> str:
    """Append the notice, unless it is already there.

    For the callers that assemble a block of prose and have nowhere else to
    put it - the model narrative, chiefly. Idempotent, so a text that came
    through a renderer that already signs it is returned unchanged rather than
    ending with the sentence twice.
    """
    if carries_notice(text):
        return text
    body = text.rstrip()
    return f"{body}\n\n{PUBLICATION_NOTICE}" if body else PUBLICATION_NOTICE


def publish(engine: PolicyEngine, agent: str, name: str, text: str) -> PolicyResult:
    """Run the publication rail over finished text. Raises if it is unsigned.

    `PolicyViolation` is not caught here on purpose. A caller that wants to
    degrade - print a warning, write nothing, return an error envelope - can
    catch it; one that does not gets a traceback rather than a quietly
    unsigned file on a public branch.
    """
    return engine.enforce(
        Action(name=name, rail=Rail.PUBLICATION, agent=agent, payload={"text": text})
    )
