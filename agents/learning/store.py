"""Durable storage for the outcome queue and the lesson store.

docs/14 section 2 asks the operator to log every view for three to six months.
That instruction was unfollowable as first built: both stores were dictionaries
in memory, so the record died with the process. The single thing in this system
that cannot be caught up on later was the one thing not written down.

Same shape as the provenance ledger: SQLite, append-only where it matters.
A prediction row is never updated - grading INSERTs into `outcomes` and the
pending view is a LEFT JOIN. That way the original statement, horizon and
confidence stay exactly as written, which is the whole point of keeping them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from agents.learning.reflection import (
    Horizon, Lesson, LessonStore, Outcome, OutcomeQueue, Prediction, Status,
)
from core.contracts.provenance_marker import Author, ProvenanceMarker

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id   TEXT PRIMARY KEY,
    instrument_id   TEXT NOT NULL,
    agent           TEXT NOT NULL,
    made_at         TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    statement       TEXT NOT NULL,
    direction       INTEGER NOT NULL,
    confidence      REAL NOT NULL,
    grade_on        TEXT NOT NULL,
    context_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id     TEXT PRIMARY KEY REFERENCES predictions(prediction_id),
    graded_on         TEXT NOT NULL,
    realised_return   REAL NOT NULL,
    benchmark_return  REAL NOT NULL,
    correct           INTEGER NOT NULL,
    note              TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id             TEXT PRIMARY KEY,
    text                  TEXT NOT NULL,
    pattern               TEXT NOT NULL,
    instances             INTEGER NOT NULL,
    distinct_instruments  INTEGER NOT NULL,
    hit_rate              REAL NOT NULL,
    created_at            TEXT NOT NULL,
    created_by            TEXT NOT NULL,
    pinned                INTEGER NOT NULL DEFAULT 0,
    status                TEXT NOT NULL,
    last_confirmed        TEXT,
    supersedes            TEXT,
    evidence_json         TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS predictions_grade_on ON predictions(grade_on);

-- A prediction is written once. Editing the statement, horizon or confidence
-- after the fact would turn the log into a flattering memory, which is exactly
-- what it exists to prevent.
CREATE TRIGGER IF NOT EXISTS predictions_no_update
BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT,
  'a prediction is written once: edit it and the log becomes a memory'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete
BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT,
  'predictions are never deleted: a log missing its losers produces confident, wrong calibration'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update
BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'an outcome is graded once'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete
BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are never deleted'); END;
"""

from core.provenance.ledger import _enable_wal

DEFAULT_PATH = Path("data/learning.db")


class LearningStore:
    """One SQLite file holding the record that time cannot be caught up on."""

    #: Ten seconds. A daemon writing the night's grades must not fail an
    #: interactive read, and an interactive read must not fail because a daemon
    #: is mid-sweep. Long enough to ride out either; short enough that a genuine
    #: deadlock surfaces rather than hanging the session.
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.db.row_factory = sqlite3.Row
        # Shared helper: busy_timeout first, then WAL, tolerating a lost race.
        # See core/provenance/ledger._enable_wal for why both matter.
        _enable_wal(self.db, str(self.path), self.BUSY_TIMEOUT_MS)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "LearningStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- predictions ---------------------------------------------------------

    def record(self, p: Prediction) -> None:
        try:
            self.db.execute(
                "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p.prediction_id, p.instrument_id, p.agent, p.made_at.isoformat(),
                 p.horizon.value, p.statement, p.direction, p.confidence,
                 p.grade_on.isoformat(), json.dumps(p.context)),
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                f"{p.prediction_id} is already logged. Predictions are written once; "
                "log a new one rather than revising this."
            ) from None
        self.db.commit()

    def record_outcome(self, o: Outcome) -> None:
        self.db.execute(
            "INSERT INTO outcomes VALUES (?,?,?,?,?,?)",
            (o.prediction_id, o.graded_on.isoformat(), o.realised_return,
             o.benchmark_return, int(o.correct), o.note),
        )
        self.db.commit()

    def _to_prediction(self, r: sqlite3.Row) -> Prediction:
        return Prediction(
            prediction_id=r["prediction_id"], instrument_id=r["instrument_id"],
            agent=r["agent"], made_at=datetime.fromisoformat(r["made_at"]),
            horizon=Horizon(r["horizon"]), statement=r["statement"],
            direction=r["direction"], confidence=r["confidence"],
            grade_on=date.fromisoformat(r["grade_on"]),
            context=json.loads(r["context_json"]),
        )

    def pending(self) -> list[Prediction]:
        rows = self.db.execute(
            "SELECT p.* FROM predictions p LEFT JOIN outcomes o"
            " USING (prediction_id) WHERE o.prediction_id IS NULL"
            " ORDER BY p.grade_on"
        ).fetchall()
        return [self._to_prediction(r) for r in rows]

    def graded(self) -> list[Outcome]:
        rows = self.db.execute("SELECT * FROM outcomes ORDER BY graded_on").fetchall()
        return [Outcome(r["prediction_id"], date.fromisoformat(r["graded_on"]),
                        r["realised_return"], r["benchmark_return"],
                        bool(r["correct"]), r["note"]) for r in rows]

    def calibration_pairs(self) -> list[tuple[float, bool]]:
        """Graded (confidence, correct) pairs, EXCLUDING no-view predictions.

        direction 0 expresses no view, so it can be neither right nor wrong.
        Counting it would let a run of honest "I don't know" entries lift the
        hit rate for free - which is the opposite of what a calibration record
        is for.
        """
        rows = self.db.execute(
            "SELECT p.confidence, o.correct FROM predictions p"
            " JOIN outcomes o USING (prediction_id)"
            " WHERE p.direction != 0"
        ).fetchall()
        return [(r["confidence"], bool(r["correct"])) for r in rows]

    def instruments_for(self, pattern_ids: list[str]) -> set[str]:
        if not pattern_ids:
            return set()
        marks = ",".join("?" * len(pattern_ids))
        rows = self.db.execute(
            f"SELECT DISTINCT instrument_id FROM predictions WHERE prediction_id IN ({marks})",
            pattern_ids,
        ).fetchall()
        return {r["instrument_id"] for r in rows}

    # -- queue round-trip -----------------------------------------------------

    def load_queue(self) -> OutcomeQueue:
        """Rebuild the in-memory queue from disk. This is what makes the
        three-to-six month clock survive a restart."""
        q = OutcomeQueue()
        for p in self.pending():
            q.enqueue(p)
        q._graded = self.graded()
        return q

    # -- lessons -------------------------------------------------------------

    def save_lesson(self, l: Lesson) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (l.lesson_id, l.text, l.pattern, l.instances, l.distinct_instruments,
             l.hit_rate, l.created_at.isoformat(), l.marker.created_by.value,
             int(l.marker.pinned), l.status.value,
             l.last_confirmed.isoformat() if l.last_confirmed else None,
             l.supersedes, json.dumps(list(l.evidence))),
        )
        self.db.commit()

    def load_lessons(self) -> LessonStore:
        store = LessonStore()
        for r in self.db.execute("SELECT * FROM lessons").fetchall():
            store.add(Lesson(
                lesson_id=r["lesson_id"], text=r["text"], pattern=r["pattern"],
                instances=r["instances"], distinct_instruments=r["distinct_instruments"],
                hit_rate=r["hit_rate"],
                created_at=datetime.fromisoformat(r["created_at"]),
                marker=ProvenanceMarker(
                    created_by=Author(r["created_by"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    pinned=bool(r["pinned"])),
                status=Status(r["status"]),
                last_confirmed=(datetime.fromisoformat(r["last_confirmed"])
                                if r["last_confirmed"] else None),
                supersedes=r["supersedes"],
                evidence=tuple(json.loads(r["evidence_json"])),
            ))
        return store

    def sync_lessons(self, store: LessonStore) -> int:
        for l in store.all():
            self.save_lesson(l)
        return len(store.all())

    # -- summary --------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        one = lambda q: self.db.execute(q).fetchone()[0]
        return {
            "logged": one("SELECT COUNT(*) FROM predictions"),
            "graded": one("SELECT COUNT(*) FROM outcomes"),
            "pending": one("SELECT COUNT(*) FROM predictions p LEFT JOIN outcomes o"
                           " USING (prediction_id) WHERE o.prediction_id IS NULL"),
            "lessons": one("SELECT COUNT(*) FROM lessons WHERE status='active'"),
        }
