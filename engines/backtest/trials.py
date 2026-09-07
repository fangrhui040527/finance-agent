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
because what counts is DISTINCT rules, not repetitions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

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
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
        """How many DIFFERENT rules have been tried on exactly this experiment.

        Distinct rather than total: re-running one rule is not a second guess at
        the data, and counting it would deflate every result for the sin of
        being reproducible.
        """
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT rule) FROM trials "
            "WHERE universe = ? AND start_day = ? AND end_day = ?",
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
