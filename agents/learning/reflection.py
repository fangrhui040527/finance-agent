"""A15, reflection. P13.

docs/13. The hard part of a self-learning loop is not writing lessons; it is
refusing to. hermes-agent's background reviewer has a write bias - it exists to
produce memory, so it produces memory. Here the bias is INVERTED: the prompt and
the gate both push towards "no lesson", and a lesson has to survive to be born.

Three properties that follow from that inversion:
  1. Outcomes are scored on a DEFERRED queue at a horizon set before the call.
     Grading a call the day after is grading noise.
  2. A lesson needs repeats across independent instruments. One trade is an
     anecdote regardless of how vivid it was.
  3. Nothing is ever deleted. Lessons go active -> stale -> archived, and an
     archived lesson that starts working again can come back.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

from agents.base import Agent, Finding
from core.contracts.provenance_marker import Author, ProvenanceMarker, is_managed
from core.llm.tiers import TaskClass

#: docs/13 section 3.2. Below these a candidate is an anecdote.
MIN_INSTANCES = 5
MIN_DISTINCT_INSTRUMENTS = 3
MIN_HIT_RATE = 0.60
STALE_AFTER_DAYS = 180
STALE_HIT_RATE = 0.45


class Horizon(str, Enum):
    """Set at prediction time. Changing it afterwards is how backtests lie."""

    D1 = "1d"
    D5 = "5d"
    D21 = "21d"
    D63 = "63d"
    D252 = "252d"

    @property
    def sessions(self) -> int:
        return {"1d": 1, "5d": 5, "21d": 21, "63d": 63, "252d": 252}[self.value]


class Status(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Prediction:
    """A falsifiable statement with a grading date fixed in advance."""

    prediction_id: str
    instrument_id: str
    agent: str
    made_at: datetime
    horizon: Horizon
    statement: str
    direction: int                  # +1, -1, or 0 for "no view expressed"
    confidence: float
    grade_on: date
    context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be a probability")
        if self.grade_on <= self.made_at.date():
            raise ValueError(
                "grade_on must be in the future at prediction time; a horizon set "
                "after the fact is not a horizon"
            )


@dataclass(frozen=True)
class Outcome:
    prediction_id: str
    graded_on: date
    realised_return: float
    benchmark_return: float
    correct: bool
    note: str = ""

    @property
    def excess(self) -> float:
        return self.realised_return - self.benchmark_return


class OutcomeQueue:
    """Deferred grading. Nothing is scored before its date."""

    def __init__(self) -> None:
        self._pending: dict[str, Prediction] = {}
        self._graded: list[Outcome] = []

    def enqueue(self, p: Prediction) -> None:
        self._pending[p.prediction_id] = p

    def due(self, today: date) -> list[Prediction]:
        return [p for p in self._pending.values() if p.grade_on <= today]

    def grade(self, prediction_id: str, today: date, realised: float,
              benchmark: float, note: str = "") -> Outcome:
        p = self._pending.get(prediction_id)
        if p is None:
            raise KeyError(f"{prediction_id} is not pending; it may already be graded")
        if today < p.grade_on:
            raise ValueError(
                f"{prediction_id} grades on {p.grade_on}; grading it on {today} would "
                "score noise and flatter the model"
            )
        correct = (p.direction == 0) or (p.direction * (realised - benchmark) > 0)
        o = Outcome(prediction_id, today, realised, benchmark, correct, note)
        self._graded.append(o)
        del self._pending[prediction_id]
        return o

    @property
    def graded(self) -> list[Outcome]:
        return list(self._graded)

    def pending_count(self) -> int:
        return len(self._pending)


@dataclass
class Lesson:
    """An agent-written rule. Carries its own evidence and its own kill switch."""

    lesson_id: str
    text: str
    pattern: str                      # the machine-checkable condition it applies to
    instances: int
    distinct_instruments: int
    hit_rate: float
    created_at: datetime
    marker: ProvenanceMarker
    status: Status = Status.ACTIVE
    last_confirmed: datetime | None = None
    supersedes: str | None = None
    evidence: tuple[str, ...] = ()

    def editable(self) -> bool:
        return is_managed(self.marker)


def _lesson_id(pattern: str, text: str) -> str:
    return hashlib.sha256(f"{pattern}\x00{text}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Calibration:
    """Are stated confidences honest? Brier plus the bucket table, because a good
    Brier score hides a model that is confidently wrong in one band."""

    n: int
    brier: float
    buckets: tuple[tuple[float, float, int], ...]     # (stated, realised, n)

    def overconfident_bands(self, tolerance: float = 0.10) -> list[tuple[float, float, int]]:
        return [b for b in self.buckets if b[2] >= 5 and b[0] - b[1] > tolerance]


def calibrate(pairs: list[tuple[float, bool]], bins: int = 5) -> Calibration:
    if not pairs:
        return Calibration(0, float("nan"), ())
    brier = sum((c - (1.0 if hit else 0.0)) ** 2 for c, hit in pairs) / len(pairs)
    buckets: list[tuple[float, float, int]] = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        band = [(c, h) for c, h in pairs if (lo <= c < hi or (i == bins - 1 and c == 1.0))]
        if band:
            stated = sum(c for c, _ in band) / len(band)
            realised = sum(1 for _, h in band if h) / len(band)
            buckets.append((stated, realised, len(band)))
    return Calibration(len(pairs), brier, tuple(buckets))


class LessonStore:
    """Append-only. Status changes; rows do not disappear."""

    def __init__(self) -> None:
        self._rows: dict[str, Lesson] = {}

    def add(self, lesson: Lesson) -> None:
        self._rows[lesson.lesson_id] = lesson

    def get(self, lesson_id: str) -> Lesson | None:
        return self._rows.get(lesson_id)

    def active(self) -> list[Lesson]:
        return [l for l in self._rows.values() if l.status is Status.ACTIVE]

    def all(self) -> list[Lesson]:
        return list(self._rows.values())

    def transition(self, lesson_id: str, status: Status, when: datetime) -> Lesson:
        l = self._rows[lesson_id]
        if not l.editable():
            raise PermissionError(
                f"{lesson_id} was not created by an agent (or is pinned); its lifecycle "
                "is not agent-managed (docs/13 section 2.1)"
            )
        l.status = status
        if status is Status.ACTIVE:
            l.last_confirmed = when
        return l

    def supersede(self, old_id: str, new: Lesson, when: datetime) -> Lesson:
        """Contradiction resolves by superseding, never by editing in place. The
        old text stays readable so the change of mind is auditable."""
        self.transition(old_id, Status.ARCHIVED, when)
        new.supersedes = old_id
        self.add(new)
        return new


class A15Reflection(Agent):
    """Reviews graded outcomes and mostly declines to write anything."""

    agent_id = "a15_reflection"
    collections = ("kb_lessons",)
    tools = ("grade_queue", "propose_lesson", "calibrate", "curate")
    tier = TaskClass.REFLECTION_DEEP

    #: The inverted prompt. docs/13 section 4: the default answer is no lesson.
    SYSTEM = (
        "You review graded predictions. Your default output is NO LESSON.\n"
        "A lesson may only be proposed when ALL of the following hold:\n"
        f"  - the pattern repeated at least {MIN_INSTANCES} times\n"
        f"  - across at least {MIN_DISTINCT_INSTRUMENTS} distinct instruments\n"
        "  - the pattern was stated as a rule BEFORE the outcomes, not fitted after\n"
        "  - a counter-example search was run and is reported\n"
        "Write nothing about a single memorable trade. Write nothing that restates "
        "a lesson already in the store. If in doubt, output NO LESSON and say what "
        "evidence would change that."
    )

    def __init__(self, ctx, queue: OutcomeQueue, store: LessonStore) -> None:
        super().__init__(ctx)
        self.queue = queue
        self.store = store

    def run(self, today: date, cohort: dict[str, list[Outcome]] | None = None) -> list[Finding]:
        self._guard_tool("grade_queue")
        due = self.queue.due(today)
        out = [Finding(
            self.agent_id, "queue",
            f"{len(due)} predictions due for grading, {self.queue.pending_count()} pending",
            numbers={"due": float(len(due)), "pending": float(self.queue.pending_count())},
        )]
        if cohort:
            for pattern, outcomes in sorted(cohort.items()):
                out.extend(self.propose(pattern, outcomes, today))
        out.extend(self.curate(today))
        return out

    def propose(self, pattern: str, outcomes: list[Outcome],
                today: date, instruments: set[str] | None = None) -> list[Finding]:
        """The gate. Most candidates die here, and that is the feature."""
        self._guard_tool("propose_lesson")
        n = len(outcomes)
        distinct = len(instruments) if instruments is not None else n
        hits = sum(1 for o in outcomes if o.correct)
        rate = hits / n if n else 0.0

        reasons = []
        if n < MIN_INSTANCES:
            reasons.append(f"only {n} instances, needs {MIN_INSTANCES}")
        if distinct < MIN_DISTINCT_INSTRUMENTS:
            reasons.append(f"only {distinct} distinct instruments, needs {MIN_DISTINCT_INSTRUMENTS}")
        if rate < MIN_HIT_RATE:
            reasons.append(f"hit rate {rate:.0%} below the {MIN_HIT_RATE:.0%} bar")
        if reasons:
            return [Finding(
                self.agent_id, "no_lesson",
                f"no lesson written for {pattern!r}: " + "; ".join(reasons),
                numbers={"instances": float(n), "hit_rate": rate},
                caveats=["a pattern that has not repeated is an anecdote"],
            )]

        now = datetime.now(timezone.utc)
        text = (f"When {pattern}, the observed outcome held in {hits} of {n} cases "
                f"across {distinct} instruments.")
        lesson = Lesson(
            lesson_id=_lesson_id(pattern, text),
            text=text, pattern=pattern, instances=n,
            distinct_instruments=distinct, hit_rate=rate,
            created_at=now,
            marker=ProvenanceMarker(created_by=Author.AGENT, created_at=now),
            last_confirmed=now,
            evidence=tuple(o.prediction_id for o in outcomes),
        )
        existing = self.store.get(lesson.lesson_id)
        if existing is not None:
            self.store.transition(existing.lesson_id, Status.ACTIVE, now)
            return [Finding(self.agent_id, "lesson_confirmed",
                            f"existing lesson reconfirmed: {existing.text}",
                            numbers={"hit_rate": rate})]
        self.store.add(lesson)
        return [Finding(
            self.agent_id, "lesson_written", lesson.text,
            numbers={"instances": float(n), "distinct": float(distinct), "hit_rate": rate},
            caveats=["a written lesson biases future analysis; it is reviewed every "
                     f"{STALE_AFTER_DAYS} days and archived if it stops working"],
        )]

    def curate(self, today: date) -> list[Finding]:
        """Lifecycle. Never deletes."""
        self._guard_tool("curate")
        now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        out: list[Finding] = []
        for l in self.store.active():
            if not l.editable():
                continue
            age = (now - (l.last_confirmed or l.created_at)).days
            if l.hit_rate < STALE_HIT_RATE:
                self.store.transition(l.lesson_id, Status.ARCHIVED, now)
                out.append(Finding(self.agent_id, "lesson_archived",
                                   f"archived {l.lesson_id}: hit rate fell to {l.hit_rate:.0%}",
                                   caveats=["archived, not deleted; it can be revived if it "
                                            "starts working again"]))
            elif age > STALE_AFTER_DAYS:
                self.store.transition(l.lesson_id, Status.STALE, now)
                out.append(Finding(self.agent_id, "lesson_stale",
                                   f"{l.lesson_id} unconfirmed for {age} days; marked stale"))
        return out

    def calibration(self, pairs: list[tuple[float, bool]]) -> list[Finding]:
        self._guard_tool("calibrate")
        c = calibrate(pairs)
        if c.n == 0:
            return [Finding(self.agent_id, "calibration", "no graded predictions yet")]
        bands = c.overconfident_bands()
        return [Finding(
            self.agent_id, "calibration",
            f"Brier {c.brier:.3f} over {c.n} graded predictions",
            numbers={"brier": c.brier, "n": float(c.n)},
            caveats=[f"stated {s:.0%} confidence realised {r:.0%} over {n} calls"
                     for s, r, n in bands],
        )]
