"""Every backtest ever run, counted - so the multiple-testing correction is real.

`deflated_sharpe` in `engines/backtest/metrics.py` corrects an observed Sharpe
for how many strategies were tried before this one looked good. The correction
is only as honest as the number it is given, and that number has an obvious
failure mode: try twenty rules, report the winner, pass `n_trials=1`, and the
correction politely confirms a result that is selection.

Nobody does that on purpose. It happens because the other nineteen runs were in
a terminal that has since been closed, and by the time the winner is written up
the count is a memory rather than a record.

So the count is a record. Every evaluation writes a row before it is scored, and
`n_trials` is read back from the ledger rather than passed in. Append-only, on
the same rule as every other store here: a ledger that can drop its losers
produces exactly the confident wrong answer it exists to prevent.

The window is part of the key. Ten rules tried on 2010-2020 do not inflate the
correction for a rule tried on 2015-2025 - those are different experiments -
but re-running the same rule on the same window does not inflate it either,
because what counts is DISTINCT configurations, not repetitions: an identical
re-run is not even written, it answers with the row it already has.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from core.provenance.ledger import _enable_wal, apply_schema

DEFAULT_PATH = Path("data/trials.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
  trial_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at     TEXT NOT NULL,
  rule       TEXT NOT NULL,
  universe   TEXT NOT NULL,
  start_day  TEXT NOT NULL,
  end_day    TEXT NOT NULL,
  sessions   INTEGER NOT NULL,
  net_sharpe REAL NOT NULL,
  net_cagr   REAL NOT NULL,
  note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS trials_window ON trials (universe, start_day, end_day);
CREATE TRIGGER IF NOT EXISTS trials_no_update
BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT,
  'a trial is recorded once: editing it is how a backtest forgets what it tried'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_delete
BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT,
  'trials are never deleted: a ledger missing its failures under-corrects every survivor'); END;
"""


@dataclass(frozen=True)
class Trial:
    trial_id: int
    ran_at: datetime
    rule: str
    universe: str
    start_day: date
    end_day: date
    sessions: int
    net_sharpe: float
    net_cagr: float
    note: str = ""


class TrialLedger:
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        # The shared helpers, in the shared order: busy_timeout first, then
        # WAL tolerating a lost race, then the DDL under BEGIN IMMEDIATE. This
        # was the one store still in rollback-journal mode, so a gate writing
        # its row while a report read the history could meet a lock neither
        # side retried. See core/provenance/ledger for why the order matters.
        _enable_wal(self.conn, self.path, self.BUSY_TIMEOUT_MS)
        apply_schema(self.conn, SCHEMA, timeout_ms=self.BUSY_TIMEOUT_MS)

    def __enter__(self) -> TrialLedger:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def universe_key(instruments) -> str:
        """Order-independent, so the same names in a different order are one universe."""
        return ",".join(sorted(instruments))

    def record(
        self,
        rule: str,
        universe: str,
        start_day: date,
        end_day: date,
        sessions: int,
        net_sharpe: float,
        net_cagr: float,
        note: str = "",
        now: datetime | None = None,
    ) -> int:
        """The row's id - the existing one when this configuration is already on the ledger.

        The same rule on the same universe, window and session count is the
        same experiment. Running it again is reproducibility, not a second
        guess at the data, and a row per run would deflate every survivor for
        the sin of being repeatable. Trial 7 of data/trials.db is such a
        re-run of trial 3 (momentum_12_1, twelve minutes later, byte for
        byte); it stays, because the ledger is append-only, and the distinct
        count below sees it once.
        """
        existing = self.conn.execute(
            "SELECT trial_id FROM trials WHERE rule = ? AND universe = ? AND start_day = ? "
            "AND end_day = ? AND sessions = ? ORDER BY trial_id LIMIT 1",
            (rule, universe, start_day.isoformat(), end_day.isoformat(), int(sessions)),
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cur = self.conn.execute(
            "INSERT INTO trials (ran_at, rule, universe, start_day, end_day, sessions, "
            "net_sharpe, net_cagr, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (now or datetime.now(UTC)).isoformat(),
                rule,
                universe,
                start_day.isoformat(),
                end_day.isoformat(),
                int(sessions),
                float(net_sharpe),
                float(net_cagr),
                note,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def distinct_rules(self, universe: str, start_day: date, end_day: date) -> int:
        """How many DIFFERENT configurations have been tried on exactly this experiment.

        A configuration is a rule and the session count it scored - the same
        rule over more or fewer sessions of the same window saw different
        data and is a further try. Distinct rather than total: re-running one
        configuration is not a second guess at the data, and counting it
        would deflate every result for the sin of being reproducible. The
        name stays for the gate that calls it.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT rule, sessions FROM trials "
            "WHERE universe = ? AND start_day = ? AND end_day = ?)",
            (universe, start_day.isoformat(), end_day.isoformat()),
        ).fetchone()
        return int(row[0]) if row else 0

    def history(self, universe: str = "", limit: int = 50) -> list[Trial]:
        sql = "SELECT * FROM trials"
        args: list = []
        if universe:
            sql += " WHERE universe = ?"
            args.append(universe)
        sql += " ORDER BY trial_id DESC LIMIT ?"
        args.append(limit)
        return [
            Trial(
                trial_id=r["trial_id"],
                ran_at=datetime.fromisoformat(r["ran_at"]),
                rule=r["rule"],
                universe=r["universe"],
                start_day=date.fromisoformat(r["start_day"]),
                end_day=date.fromisoformat(r["end_day"]),
                sessions=r["sessions"],
                net_sharpe=r["net_sharpe"],
                net_cagr=r["net_cagr"],
                note=r["note"],
            )
            for r in self.conn.execute(sql, args)
        ]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
