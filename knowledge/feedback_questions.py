"""Open questions across the nightly feedback pages, and how long each has stood.

WHY THIS EXISTS. Every feedback page carries `open_questions` per name and an
`open_questions_carried` list at the top, each stamped with the date it was
first asked - and until now nothing in the code read either. They were prose,
copied forward by hand from the previous night's page, and a question that had
stood unanswered for a fortnight looked exactly like one asked yesterday.

A question the collection cannot answer is a finding ABOUT THE COLLECTION. "The
six Bursa names have no fact-book coverage; which of bursa_announcements or a
keyed Bursa source is the shortest path?" is not idle curiosity - it is the
system reporting, every night, that half the book is dark. Ageing makes that
audible: one night is a question, three weeks is a gap somebody decided not to
close.

CLOSING IS BY ABSENCE, deliberately. A question is open while the writer keeps
carrying it and closed the first night they do not - which is exactly what the
page contract already means by dropping it. Nothing here edits a page: the
pages are the record, this only reads them.

ONE LEDGER, NOT TWO. `open_questions_carried` at the top of a page is the
running ledger the contract asks for; the per-name `open_questions` are that
night's asking. Counting both gave every question twice, and reworded - "does
the fact book pick up a filing for the Hugging Face transaction" one night,
"...and does a 5-session decomposition keep the idiosyncratic share above 50%"
the next - four times over two pages. So the carried list is the ledger here,
and the per-name questions are read only to check the latest page CARRIED what
it asked, which is a hygiene finding about the page rather than a question in
its own right.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

FEEDBACK_DIR = "knowledge/feedback"

#: "(since 2026-09-04)" as the page contract writes it. The date inside is the
#: writer's own claim about when the question was first asked, and it is
#: believed over the file it appears in: a question carried forward keeps its
#: original date, which is the entire point of writing it there.
SINCE = re.compile(r"\(since (\d{4}-\d{2}-\d{2})\)")


@dataclass(frozen=True)
class OpenQuestion:
    text: str
    first_asked: date
    last_carried: date
    nights: int
    instruments: tuple[str, ...] = ()

    @property
    def age_days(self) -> int:
        return (self.last_carried - self.first_asked).days

    def line(self) -> str:
        who = f"  [{', '.join(self.instruments)}]" if self.instruments else ""
        return (
            f"  {self.age_days:>4}d  {self.first_asked} -> {self.last_carried}  "
            f"({self.nights} night{'s' if self.nights != 1 else ''}){who}\n"
            f"        {self.text}"
        )


def _key(text: str) -> str:
    """A question minus its date stamp and spacing, so the same question asked
    on four nights is one row rather than four."""
    return " ".join(SINCE.sub("", text).split()).rstrip("?").lower()


def _pages(directory: str | Path) -> list[tuple[date, dict]]:
    return _read(directory)[0]


def malformed(directory: str | Path = FEEDBACK_DIR) -> list[tuple[str, str]]:
    """(file name, what is wrong) for every page the ledger had to skip.

    A skipped page is not a crash, but it is not nothing either: its questions
    are missing from the ledger until the page is fixed. On 2026-09-24 a page
    carried the COUNT of its questions (23) where the list belongs, and the
    monitor died on it with "'int' object is not iterable".
    """
    return _read(directory)[1]


def _read(directory: str | Path) -> tuple[list[tuple[date, dict]], list[tuple[str, str]]]:
    out: list[tuple[date, dict]] = []
    faults: list[tuple[str, str]] = []
    for path in sorted(Path(directory).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")):
        try:
            day = date.fromisoformat(path.stem)
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError) as e:
            faults.append((path.name, f"does not parse: {type(e).__name__}"))
            continue  # a page that will not parse is a page, not a crash
        fault = _shape_fault(data)
        if fault:
            faults.append((path.name, fault))
            continue  # nor is a page whose ledger fields have the wrong shape
        out.append((day, data))
    return out, faults


def _shape_fault(data) -> str:
    """Why the ledger cannot read this page, or "" if it can.

    The whole page is skipped rather than the one bad field: a carried list
    read as empty would close every question the page meant to carry.
    """
    if not isinstance(data, dict):
        return f"the page is a {type(data).__name__}, not an object"
    carried = data.get("open_questions_carried")
    if carried is not None and not isinstance(carried, list):
        return f"open_questions_carried is a {type(carried).__name__}, not a list of questions"
    names = data.get("names")
    if names is not None and not isinstance(names, list):
        return f"names is a {type(names).__name__}, not a list"
    for name in names or []:
        asked = name.get("open_questions") if isinstance(name, dict) else None
        if asked is not None and not isinstance(asked, list):
            iid = name.get("instrument_id") or "a name"
            return f"open_questions under {iid} is a {type(asked).__name__}, not a list"
    return ""


def open_questions(directory: str | Path = FEEDBACK_DIR) -> list[OpenQuestion]:
    """Every question still carried on the most recent page, oldest first.

    A question that appears on page N but not on page N+1 has been closed by
    the writer and is not returned; `answered` reports those.
    """
    pages = _pages(directory)
    if not pages:
        return []
    seen = _walk(pages)
    latest = pages[-1][0]
    return sorted(
        (q for q in seen.values() if q.last_carried == latest),
        key=lambda q: (-q.age_days, q.first_asked),
    )


def uncarried(directory: str | Path = FEEDBACK_DIR) -> list[tuple[str, str]]:
    """(instrument, question) asked on the latest page but not carried on it.

    A page-hygiene check, not a question ledger: the contract asks the writer
    to carry every open question forward with the date first asked, and one
    left only under a name is one the next night will not see.
    """
    pages = _pages(directory)
    if not pages:
        return []
    _, data = pages[-1]
    carried = [_tokens(q) for q in (data.get("open_questions_carried") or []) if isinstance(q, str)]
    out: list[tuple[str, str]] = []
    for text, iid in _named_questions(data):
        mine = _tokens(text)
        if any(_overlap(mine, c) >= MATCH for c in carried):
            continue
        out.append((iid, " ".join(text.split())))
    return out


def answered(directory: str | Path = FEEDBACK_DIR) -> list[OpenQuestion]:
    """Questions the writer stopped carrying, newest closure first."""
    pages = _pages(directory)
    if not pages:
        return []
    latest = pages[-1][0]
    return sorted(
        (q for q in _walk(pages).values() if q.last_carried != latest),
        key=lambda q: q.last_carried,
        reverse=True,
    )


def _walk(pages: list[tuple[date, dict]]) -> dict[str, OpenQuestion]:
    seen: dict[str, OpenQuestion] = {}
    for day, data in pages:
        for text, iid in _carried_questions(data):
            key = _key(text)
            if not key:
                continue
            stamped = SINCE.search(text)
            asked = date.fromisoformat(stamped.group(1)) if stamped else day
            prior = seen.get(key)
            if prior is None:
                seen[key] = OpenQuestion(
                    text=" ".join(text.split()),
                    first_asked=min(asked, day),
                    last_carried=day,
                    nights=1,
                    instruments=(iid,) if iid else (),
                )
                continue
            names = tuple(dict.fromkeys(prior.instruments + ((iid,) if iid else ())))
            seen[key] = OpenQuestion(
                text=prior.text,
                first_asked=min(prior.first_asked, asked),
                last_carried=day,
                nights=prior.nights + 1,
                instruments=names,
            )
    return seen


def _carried_questions(data: dict):
    """(question, "") from the page's running ledger. The ledger is the record."""
    for q in data.get("open_questions_carried") or []:
        if isinstance(q, str) and q.strip():
            yield q, ""


def _named_questions(data: dict):
    """(question, instrument) from the per-name sections: tonight's asking."""
    for name in data.get("names") or []:
        if not isinstance(name, dict):
            continue
        iid = str(name.get("instrument_id") or "")
        for q in name.get("open_questions") or []:
            if isinstance(q, str) and q.strip():
                yield q, iid


#: Token overlap above which two wordings are treated as the same question.
#: Used ONLY by `uncarried`, where a wrong call costs a hygiene note and never
#: a ledger row - the ledger matches on the writer's own text, not on a guess.
MATCH = 0.5


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9:.%-]+", SINCE.sub("", text).lower()) if len(w) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def render(directory: str | Path = FEEDBACK_DIR) -> str:
    """The ledger as a page reads it: open questions by age, then closures."""
    pages = _pages(directory)
    if not pages:
        return (
            f"NO FEEDBACK PAGES in {directory}. The nightly routine writes them; "
            "docs/20-FEEDBACK-ROUTINE.md is the contract."
        )
    still = open_questions(directory)
    closed = answered(directory)
    lines = [
        f"open questions across {len(pages)} feedback page(s), {pages[0][0]} to {pages[-1][0]}",
        "",
    ]
    if still:
        lines.append(f"STILL OPEN on {pages[-1][0]}, oldest first: {len(still)}")
        lines += [q.line() for q in still]
    else:
        lines.append(f"nothing carried on {pages[-1][0]}.")
    if closed:
        lines += ["", f"NO LONGER CARRIED: {len(closed)}"]
        lines += [q.line() for q in closed[:10]]
    skipped = malformed(directory)
    if skipped:
        lines += ["", f"SKIPPED, NOT READ: {len(skipped)} page(s)"]
        lines += [f"  {name}: {why}" for name, why in skipped]
        lines += ["  Their questions are missing from the ledger until the page is fixed."]
    loose = uncarried(directory)
    if loose:
        lines += ["", f"ASKED ON {pages[-1][0]} BUT NOT CARRIED: {len(loose)}"]
        lines += [f"  {iid or '-':<12} {q}" for iid, q in loose]
        lines += [
            "  The contract asks for every open question to be carried forward with "
            "the date first asked; one left only under a name is one tomorrow will "
            "not see."
        ]
    lines += [
        "",
        "A question is open while the writer keeps carrying it and closed the first "
        "night they do not. An age in weeks is not a stale page - it is a question "
        "the collection has never been able to answer, which is a fact about the "
        "collection.",
    ]
    return "\n".join(lines)


__all__ = ["OpenQuestion", "answered", "malformed", "open_questions", "render", "uncarried"]
