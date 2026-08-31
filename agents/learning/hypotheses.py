"""An append-only registry of investment hypotheses.

The prediction log answers "was this single dated call right". Nothing tracked
the HYPOTHESIS above the calls - "MYX banks re-rate as NIM stabilises" - so a
thesis could quietly survive its own dead predictions. Here a hypothesis is a
row that never changes, its life is a sequence of status EVENTS (the current
status is simply the latest event), and predictions are LINKED to it so the
reflection agent can grade cohorts by the idea rather than by string-matched
patterns.

Statuses, deliberately few:
  exploring  - an idea with no falsifiable predictions logged yet
  testing    - linked predictions exist and are not yet all graded
  validated  - the graded cohort supports it (a judgement, recorded with why)
  rejected   - the graded cohort broke it (record what broke it)
  monitoring - validated once, kept under watch rather than trusted forever

Append-only is enforced by SQLite triggers, exactly like the prediction log:
a registry you can rewrite is a diary, not a record.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.provenance.ledger import _enable_wal

STATUSES = ("exploring", "testing", "validated", "rejected", "monitoring")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    thesis        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT 'user'
);
CREATE TABLE IF NOT EXISTS hypothesis_events (
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
    at            TEXT NOT NULL,
    status        TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS hypothesis_links (
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(hypothesis_id),
    prediction_id TEXT NOT NULL,
    UNIQUE (hypothesis_id, prediction_id)
);
CREATE INDEX IF NOT EXISTS hyp_events_id_at ON hypothesis_events(hypothesis_id, at);

CREATE TRIGGER IF NOT EXISTS hypotheses_no_update
BEFORE UPDATE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'a hypothesis is immutable; its life is recorded as events'); END;
CREATE TRIGGER IF NOT EXISTS hypotheses_no_delete
BEFORE DELETE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses are never deleted: a registry missing its failures grades itself'); END;
CREATE TRIGGER IF NOT EXISTS hyp_events_no_update
BEFORE UPDATE ON hypothesis_events
BEGIN SELECT RAISE(ABORT, 'status history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hyp_events_no_delete
BEFORE DELETE ON hypothesis_events
BEGIN SELECT RAISE(ABORT, 'status history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hyp_links_no_update
BEFORE UPDATE ON hypothesis_links
BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;
CREATE TRIGGER IF NOT EXISTS hyp_links_no_delete
BEFORE DELETE ON hypothesis_links
BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;
"""


@dataclass(frozen=True)
class HypothesisView:
    """A hypothesis with its derived, current state."""

    hypothesis_id: str
    title: str
    thesis: str
    created_at: datetime
    status: str
    status_note: str
    prediction_ids: tuple[str, ...] = field(default_factory=tuple)
    history: tuple[tuple[datetime, str, str], ...] = field(default_factory=tuple)


class HypothesisStore:
    """Its own connection over the learning database; shares the file, not code."""

    def __init__(self, path: str | Path = "data/learning.db") -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p))
        self.db.row_factory = sqlite3.Row
        _enable_wal(self.db, str(p), timeout_ms=5000)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- writes (all INSERTs; the triggers make anything else impossible) ------

    def create(
        self, title: str, thesis: str, created_by: str = "user", at: datetime | None = None
    ) -> str:
        title = title.strip()
        thesis = thesis.strip()
        if not title or not thesis:
            raise ValueError("a hypothesis needs both a title and a falsifiable thesis")
        at = at or datetime.now(UTC)
        hid = f"H-{at:%Y%m%d}-{abs(hash((title, thesis))) % 10_000:04d}"
        try:
            self.db.execute(
                "INSERT INTO hypotheses (hypothesis_id, title, thesis, created_at, created_by)"
                " VALUES (?,?,?,?,?)",
                (hid, title, thesis, at.isoformat(), created_by),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"hypothesis {hid} already exists: {e}") from e
        self.db.execute(
            "INSERT INTO hypothesis_events (hypothesis_id, at, status, note) VALUES (?,?,?,?)",
            (hid, at.isoformat(), "exploring", "created"),
        )
        self.db.commit()
        return hid

    def transition(
        self, hypothesis_id: str, status: str, note: str = "", at: datetime | None = None
    ) -> None:
        """A new event, never an edit. `rejected` and `validated` REQUIRE a note:
        a verdict with no reason cannot be argued with later, which is the
        point of keeping the history."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        if status in ("validated", "rejected") and not note.strip():
            raise ValueError(f"a {status} verdict requires a note saying why")
        if self._row(hypothesis_id) is None:
            raise KeyError(f"no hypothesis {hypothesis_id!r}")
        at = at or datetime.now(UTC)
        self.db.execute(
            "INSERT INTO hypothesis_events (hypothesis_id, at, status, note) VALUES (?,?,?,?)",
            (hypothesis_id, at.isoformat(), status, note.strip()),
        )
        self.db.commit()

    def link(self, hypothesis_id: str, prediction_id: str) -> None:
        if self._row(hypothesis_id) is None:
            raise KeyError(f"no hypothesis {hypothesis_id!r}")
        try:
            self.db.execute(
                "INSERT INTO hypothesis_links (hypothesis_id, prediction_id) VALUES (?,?)",
                (hypothesis_id, prediction_id),
            )
        except sqlite3.IntegrityError:
            return  # already linked; linking twice is not an event
        self.db.commit()

    # -- reads -----------------------------------------------------------------

    def _row(self, hypothesis_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM hypotheses WHERE hypothesis_id = ?", (hypothesis_id,)
        ).fetchone()

    def get(self, hypothesis_id: str) -> HypothesisView:
        row = self._row(hypothesis_id)
        if row is None:
            raise KeyError(f"no hypothesis {hypothesis_id!r}")
        events = self.db.execute(
            "SELECT at, status, note FROM hypothesis_events WHERE hypothesis_id = ?"
            " ORDER BY at, rowid",
            (hypothesis_id,),
        ).fetchall()
        links = self.db.execute(
            "SELECT prediction_id FROM hypothesis_links WHERE hypothesis_id = ?"
            " ORDER BY prediction_id",
            (hypothesis_id,),
        ).fetchall()
        last = events[-1]
        return HypothesisView(
            hypothesis_id=row["hypothesis_id"],
            title=row["title"],
            thesis=row["thesis"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=last["status"],
            status_note=last["note"],
            prediction_ids=tuple(r["prediction_id"] for r in links),
            history=tuple(
                (datetime.fromisoformat(e["at"]), e["status"], e["note"]) for e in events
            ),
        )

    def all(self, status: str | None = None) -> list[HypothesisView]:
        ids = [
            r["hypothesis_id"]
            for r in self.db.execute(
                "SELECT hypothesis_id FROM hypotheses ORDER BY created_at, hypothesis_id"
            )
        ]
        views = [self.get(i) for i in ids]
        if status is not None:
            views = [v for v in views if v.status == status]
        return views

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> HypothesisStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
