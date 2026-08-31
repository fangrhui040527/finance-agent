"""Which lesson to surface, when several apply.

docs/13 section 5 lists this as a P13 item and nothing implemented it: the store
returns `active()` in insertion order, so with twenty lessons the twentieth is
seen last regardless of whether it is the one that has been right twenty times.

Shape borrowed from FinMem's compound score (docs/12): a memory's rank is a
product of how recent, how relevant and how important it is, rather than any one
of those alone. Multiplicative, not additive, because a lesson that fails ANY
term should sink - a lesson with a great hit rate on one instrument is not a
rule, and an additive score lets one strong term carry a weak one.

WHAT IS DELIBERATELY DIFFERENT. FinMem scores a memory's importance from an LLM
rating at write time. Here importance is EVIDENCE: how many instances, across
how many instruments, at what hit rate - all of which the lesson already carries
because the inverted gate in reflection.py refused to write it otherwise. No
model is consulted to rank a lesson, and none should be: a model asked "how
important is this lesson" will say "very".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from agents.learning.reflection import Lesson, Status

#: A lesson confirmed within this window scores full recency. Roughly a quarter,
#: because that is the cadence at which the outcomes that confirm one arrive.
RECENCY_HALFLIFE_DAYS = 90.0

#: Below this, a lesson is not evidence of anything and the gate should not have
#: written it. Scored at zero rather than excluded, so a store that somehow holds
#: one still ranks it last instead of hiding it.
MIN_INSTANCES = 5
MIN_DISTINCT = 3

#: A hit rate at or below chance carries no information. 0.5 is the point where
#: a directional rule stops beating a coin.
CHANCE = 0.5


@dataclass(frozen=True)
class Scored:
    lesson: Lesson
    score: float
    recency: float
    relevance: float
    evidence: float

    def describe(self) -> str:
        return (
            f"{self.score:.3f}  {self.lesson.text[:60]}\n"
            f"        recency {self.recency:.2f} × relevance {self.relevance:.2f} "
            f"× evidence {self.evidence:.2f}"
        )


def recency(lesson: Lesson, now: datetime) -> float:
    """Exponential decay from last confirmation, not from creation.

    A lesson written a year ago and confirmed last week is current. Decaying
    from `created_at` would retire exactly the rules that keep being right.
    """
    anchor = lesson.last_confirmed or lesson.created_at
    days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    return 0.5 ** (days / RECENCY_HALFLIFE_DAYS)


def relevance(lesson: Lesson, context: str) -> float:
    """Does this lesson's pattern apply to what is being asked?

    Deliberately crude - a substring test on the machine-checkable `pattern`
    field, which exists precisely so applicability is decidable without a model.
    A lesson whose pattern does not appear scores 0.1 rather than 0: it is
    demoted, not hidden, because a pattern string is a narrow test and the
    lesson may still be worth reading.
    """
    if not context:
        return 1.0  # no context given: rank on merit alone
    return 1.0 if lesson.pattern.lower() in context.lower() else 0.1


def evidence(lesson: Lesson) -> float:
    """How much this lesson actually rests on.

    Three terms, each capped, so no single one can carry the score:

      breadth   distinct instruments, saturating - a rule seen on eight names is
                not twice the rule seen on four
      depth     instances, log-scaled for the same reason
      edge      hit rate above chance, doubled to span [0, 1]

    A lesson at exactly the write gate's minimum (5 instances, 3 instruments,
    60% hit rate) scores low but non-zero. That is intentional: the gate decides
    what may be written, this decides what is read first, and the two should not
    both be pass/fail on the same numbers.
    """
    if lesson.instances < MIN_INSTANCES or lesson.distinct_instruments < MIN_DISTINCT:
        return 0.0
    breadth = min(1.0, lesson.distinct_instruments / 8.0)
    depth = min(1.0, math.log1p(lesson.instances) / math.log1p(20))
    edge = max(0.0, min(1.0, (lesson.hit_rate - CHANCE) * 2.0))
    return breadth * depth * edge


def score(lesson: Lesson, now: datetime, context: str = "") -> Scored:
    r, v, e = recency(lesson, now), relevance(lesson, context), evidence(lesson)
    return Scored(lesson, r * v * e, r, v, e)


def rank(
    lessons, now: datetime, context: str = "", limit: int | None = None, include_stale: bool = False
) -> list[Scored]:
    """Best first. Archived lessons never rank - they were retired for cause.

    Stale ones are excluded by default and can be asked for: `curate` marks a
    lesson stale for going unconfirmed, which is a reason to stop leading with
    it, not a reason to pretend it was never written.
    """
    allowed = {Status.ACTIVE} | ({Status.STALE} if include_stale else set())
    out = sorted(
        (score(l, now, context) for l in lessons if l.status in allowed),
        key=lambda s: (-s.score, s.lesson.lesson_id),
    )
    return out[:limit] if limit else out
