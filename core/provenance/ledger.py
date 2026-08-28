"""Append-only provenance ledger.

docs/01 section 7 and docs/08 section 8: every number an agent emitted, its
sources, as_of, model deployment, prompt hash, tokens and cost. This is what
makes the system auditable, and it is the training set for the growth layer.

Append-only is enforced here rather than trusted: there is no update or delete.
docs/07 P0 says the ledger looks like overhead in week one and is unaddable later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from core.llm.tiers import Tier, TaskClass, Usage, cost_usd

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT    NOT NULL,
    agent           TEXT    NOT NULL,
    task_class      TEXT    NOT NULL,
    tier            TEXT    NOT NULL,
    model_id        TEXT    NOT NULL,
    prompt_hash     TEXT    NOT NULL,
    run_id          TEXT    NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cached_tokens   INTEGER NOT NULL,
    cost_usd        TEXT    NOT NULL,
    cost_myr        TEXT    NOT NULL,
    fx_rate         TEXT    NOT NULL,
    fx_asof         TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT    NOT NULL,
    agent           TEXT    NOT NULL,
    run_id          TEXT    NOT NULL DEFAULT '',
    claim_text      TEXT    NOT NULL,
    citations_json  TEXT    NOT NULL,
    survived        INTEGER NOT NULL,
    dropped_reason  TEXT
);
CREATE TRIGGER IF NOT EXISTS llm_calls_no_update
BEFORE UPDATE ON llm_calls
BEGIN SELECT RAISE(ABORT, 'provenance ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS llm_calls_no_delete
BEFORE DELETE ON llm_calls
BEGIN SELECT RAISE(ABORT, 'provenance ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS claims_no_update
BEFORE UPDATE ON claims
BEGIN SELECT RAISE(ABORT, 'provenance ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS claims_no_delete
BEFORE DELETE ON claims
BEGIN SELECT RAISE(ABORT, 'provenance ledger is append-only'); END;
"""

# Created AFTER _migrate(), because llm_calls_run indexes a column an older
# ledger does not have yet. Running this inside SCHEMA fails on exactly the
# databases the migration exists to rescue.
INDEXES = """
CREATE INDEX IF NOT EXISTS llm_calls_at ON llm_calls(at);
CREATE INDEX IF NOT EXISTS llm_calls_run ON llm_calls(run_id);
"""

# docs/08: mid-market was ~4.04 on 24 Aug 2026; 4.15 is the planning rate that
# absorbs a Malaysian card's foreign-transaction markup on USD charges.
DEFAULT_FX_MYR_PER_USD = Decimal("4.15")


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CallRecord:
    agent: str
    task_class: TaskClass
    tier: Tier
    model_id: str
    prompt_hash: str
    usage: Usage
    cost_usd: Decimal
    cost_myr: Decimal
    fx_rate: Decimal
    at: datetime
    run_id: str = ""


class ProvenanceLedger:
    """Append-only, and now durable enough for two processes to share.

    `run_id` groups the calls one job made. Without it the only grouping keys are
    agent and timestamp proximity, so "what did the 03:00 sweep do" can only be
    asked as "what happened between 03:00 and 03:05" - which stops being the same
    question the moment two jobs overlap.
    """

    #: Long enough that a slow writer does not fail a reader; short enough that a
    #: genuine deadlock surfaces instead of hanging the session.
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: Path | str = ":memory:", run_id: str = "") -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.run_id = run_id
        if path != ":memory:":
            # WAL lets a writing daemon and a reading MCP session coexist. Under
            # the default rollback journal they block each other, and the reader
            # is the interactive one - the session waits on the background job.
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(f"PRAGMA busy_timeout={self.BUSY_TIMEOUT_MS}")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript(INDEXES)
        self.conn.commit()

    def _migrate(self) -> None:
        """Add run_id to ledgers written before it existed.

        ALTER TABLE ADD COLUMN is a schema change, not a row UPDATE, so the
        append-only triggers do not fire. Existing rows get '' - correctly, since
        no run owned them.
        """
        for table in ("llm_calls", "claims"):
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if "run_id" not in cols:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProvenanceLedger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def record_call(
        self,
        agent: str,
        task_class: TaskClass,
        tier: Tier,
        model_id: str,
        prompt: str,
        usage: Usage,
        fx_rate: Decimal = DEFAULT_FX_MYR_PER_USD,
        at: datetime | None = None,
        run_id: str | None = None,
    ) -> CallRecord:
        at = at or datetime.now(timezone.utc)
        rid = self.run_id if run_id is None else run_id
        usd = cost_usd(tier, usage)
        myr = usd * fx_rate
        ph = prompt_hash(prompt)
        self.conn.execute(
            "INSERT INTO llm_calls (at, agent, task_class, tier, model_id, prompt_hash, run_id,"
            " input_tokens, output_tokens, cached_tokens, cost_usd, cost_myr, fx_rate, fx_asof)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                at.isoformat(), agent, task_class.value, tier.value, model_id, ph, rid,
                usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
                str(usd), str(myr), str(fx_rate), at.isoformat(),
            ),
        )
        self.conn.commit()
        return CallRecord(agent, task_class, tier, model_id, ph, usage, usd, myr,
                          fx_rate, at, rid)

    def record_claim(
        self, agent: str, text: str, citations: list[dict], survived: bool,
        dropped_reason: str | None = None, at: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        at = at or datetime.now(timezone.utc)
        rid = self.run_id if run_id is None else run_id
        self.conn.execute(
            "INSERT INTO claims (at, agent, run_id, claim_text, citations_json, survived,"
            " dropped_reason) VALUES (?,?,?,?,?,?,?)",
            (at.isoformat(), agent, rid, text, json.dumps(citations), int(survived),
             dropped_reason),
        )
        self.conn.commit()

    def total_cost_myr(self) -> Decimal:
        row = self.conn.execute("SELECT cost_myr FROM llm_calls").fetchall()
        return sum((Decimal(r[0]) for r in row), Decimal(0))

    def cost_by_tier_myr(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for tier, cost in self.conn.execute("SELECT tier, cost_myr FROM llm_calls"):
            out[tier] = out.get(tier, Decimal(0)) + Decimal(cost)
        return out

    # -- windowed queries ----------------------------------------------------

    def cost_since(self, start: datetime) -> Decimal:
        """Spend from `start` to now.

        The reason this exists: `total_cost_myr()` is a LIFETIME sum, and
        core/llm/client.py compared it against a field named `daily_budget_myr`.
        That was invisible while every ledger was in-memory and per-process -
        lifetime and today were the same number. Against a durable file it makes
        the budget a one-way cap that permanently bricks the caller once
        cumulative spend passes it.
        """
        rows = self.conn.execute(
            "SELECT cost_myr FROM llm_calls WHERE at >= ?", (start.isoformat(),)
        ).fetchall()
        return sum((Decimal(r[0]) for r in rows), Decimal(0))

    def cost_by_agent_myr(self, since: datetime | None = None) -> dict[str, Decimal]:
        sql = "SELECT agent, cost_myr FROM llm_calls"
        args: tuple = ()
        if since is not None:
            sql += " WHERE at >= ?"
            args = (since.isoformat(),)
        out: dict[str, Decimal] = {}
        for agent, cost in self.conn.execute(sql, args):
            out[agent] = out.get(agent, Decimal(0)) + Decimal(cost)
        return out

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        """Own cursor with its own row_factory.

        `calls()` used to assign self.conn.row_factory as a side effect, and as a
        generator it only did so on first iteration - so an interleaved read
        elsewhere got tuples or Rows depending on iteration order. A long-lived
        daemon interleaves constantly.
        """
        cur = self.conn.cursor()
        cur.row_factory = sqlite3.Row
        return cur.execute(sql, args).fetchall()

    def calls(self) -> Iterator[sqlite3.Row]:
        yield from self._rows("SELECT * FROM llm_calls ORDER BY id")

    def calls_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self._rows(
            "SELECT * FROM llm_calls WHERE at >= ? AND at <= ? ORDER BY id",
            (start.isoformat(), end.isoformat()))

    def calls_for_run(self, run_id: str) -> list[sqlite3.Row]:
        """Everything one job did, as a unit. This is what run_id is for."""
        return self._rows(
            "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))

    def runs_between(self, start: datetime, end: datetime) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT run_id FROM llm_calls WHERE at >= ? AND at <= ?"
            " AND run_id != '' ORDER BY run_id", (start.isoformat(), end.isoformat()))]
