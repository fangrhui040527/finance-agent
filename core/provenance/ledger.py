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
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from core.llm.tiers import TaskClass, Tier, Usage, cost_usd

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
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd        TEXT    NOT NULL,
    cost_myr        TEXT    NOT NULL,
    fx_rate         TEXT    NOT NULL,
    fx_asof         TEXT    NOT NULL,
    latency_ms      REAL    NOT NULL DEFAULT 0
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


def _enable_wal(conn: sqlite3.Connection, path: str, timeout_ms: int) -> None:
    """WAL, so a writing daemon and a reading session coexist.

    Order and tolerance both matter, and getting either wrong is worse than not
    setting WAL at all:

      1. busy_timeout is set FIRST. Changing journal_mode needs a brief exclusive
         lock, so the PRAGMA that makes concurrency safe is itself a concurrency
         hazard. Without a timeout already in force it fails instantly against a
         competing writer.
      2. Losing the race is fine. journal_mode is a persistent property of the
         DATABASE FILE, not of the connection - once any connection sets WAL,
         every later one inherits it. So a failure here means someone else
         already did it, or is doing it now.

    Raising would turn a harmless race into a lost write on the one log that
    cannot be reconstructed.
    """
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    if path == ":memory:":
        return  # memory databases have no journal to switch
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass


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
    latency_ms: float = 0.0


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
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript(INDEXES)
        self.conn.commit()

    def _migrate(self) -> None:
        """Add run_id to ledgers written before it existed.

        ALTER TABLE ADD COLUMN is a schema change, not a row UPDATE, so the
        append-only triggers do not fire. Existing rows get the column default -
        correctly, since no run owned them and nothing timed them.

        `latency_ms` was added because the number already existed and was thrown
        away: core/llm/client.py timed every call and emitted it to the trace
        ONLY WHEN TRACING WAS ON, so an ordinary run lost it. docs/01 section 10
        wants p95 latency in the nightly fitness function, and it could not have
        it from a measurement that survived only under a debug flag.
        """
        for table in ("llm_calls", "claims"):
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if "run_id" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")

        calls = {r[1] for r in self.conn.execute("PRAGMA table_info(llm_calls)")}
        if "latency_ms" not in calls:
            self.conn.execute("ALTER TABLE llm_calls ADD COLUMN latency_ms REAL NOT NULL DEFAULT 0")
        # Cache writes bill at 1.25x input and were never recorded, because the
        # product never asked for a cache. It does now (core/llm/backends.py).
        if "cache_write_tokens" not in calls:
            self.conn.execute(
                "ALTER TABLE llm_calls ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ProvenanceLedger:
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
        latency_ms: float = 0.0,
    ) -> CallRecord:
        at = at or datetime.now(UTC)
        rid = self.run_id if run_id is None else run_id
        usd = cost_usd(tier, usage)
        myr = usd * fx_rate
        ph = prompt_hash(prompt)
        self.conn.execute(
            "INSERT INTO llm_calls (at, agent, task_class, tier, model_id, prompt_hash, run_id,"
            " input_tokens, output_tokens, cached_tokens, cache_write_tokens, cost_usd,"
            " cost_myr, fx_rate, fx_asof, latency_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                at.isoformat(),
                agent,
                task_class.value,
                tier.value,
                model_id,
                ph,
                rid,
                usage.input_tokens,
                usage.output_tokens,
                usage.cached_input_tokens,
                usage.cache_write_tokens,
                str(usd),
                str(myr),
                str(fx_rate),
                at.isoformat(),
                float(latency_ms),
            ),
        )
        self.conn.commit()
        return CallRecord(
            agent,
            task_class,
            tier,
            model_id,
            ph,
            usage,
            usd,
            myr,
            fx_rate,
            at,
            rid,
            float(latency_ms),
        )

    def record_claim(
        self,
        agent: str,
        text: str,
        citations: list[dict],
        survived: bool,
        dropped_reason: str | None = None,
        at: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        at = at or datetime.now(UTC)
        rid = self.run_id if run_id is None else run_id
        self.conn.execute(
            "INSERT INTO claims (at, agent, run_id, claim_text, citations_json, survived,"
            " dropped_reason) VALUES (?,?,?,?,?,?,?)",
            (
                at.isoformat(),
                agent,
                rid,
                text,
                json.dumps(citations),
                int(survived),
                dropped_reason,
            ),
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
            (start.isoformat(), end.isoformat()),
        )

    def calls_for_run(self, run_id: str) -> list[sqlite3.Row]:
        """Everything one job did, as a unit. This is what run_id is for."""
        return self._rows("SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))

    def latencies_between(self, start: datetime, end: datetime) -> list[float]:
        """Every recorded call duration in a window, for the p95 fitness term.

        Zeros are excluded: 0.0 is the column default for rows written before
        the column existed and for callers that do not time. Counting them as
        instant calls would make the p95 look better the more untimed history
        the ledger holds.
        """
        rows = self._rows(
            "SELECT latency_ms FROM llm_calls WHERE at >= ? AND at <= ? AND latency_ms > 0",
            (start.isoformat(), end.isoformat()),
        )
        return [float(r["latency_ms"]) for r in rows]

    def runs_between(self, start: datetime, end: datetime) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT run_id FROM llm_calls WHERE at >= ? AND at <= ?"
                " AND run_id != '' ORDER BY run_id",
                (start.isoformat(), end.isoformat()),
            )
        ]
