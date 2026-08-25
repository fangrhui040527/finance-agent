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


class ProvenanceLedger:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
    ) -> CallRecord:
        at = at or datetime.now(timezone.utc)
        usd = cost_usd(tier, usage)
        myr = usd * fx_rate
        ph = prompt_hash(prompt)
        self.conn.execute(
            "INSERT INTO llm_calls (at, agent, task_class, tier, model_id, prompt_hash,"
            " input_tokens, output_tokens, cached_tokens, cost_usd, cost_myr, fx_rate, fx_asof)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                at.isoformat(), agent, task_class.value, tier.value, model_id, ph,
                usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
                str(usd), str(myr), str(fx_rate), at.isoformat(),
            ),
        )
        self.conn.commit()
        return CallRecord(agent, task_class, tier, model_id, ph, usage, usd, myr, fx_rate, at)

    def record_claim(
        self, agent: str, text: str, citations: list[dict], survived: bool,
        dropped_reason: str | None = None, at: datetime | None = None,
    ) -> None:
        at = at or datetime.now(timezone.utc)
        self.conn.execute(
            "INSERT INTO claims (at, agent, claim_text, citations_json, survived, dropped_reason)"
            " VALUES (?,?,?,?,?,?)",
            (at.isoformat(), agent, text, json.dumps(citations), int(survived), dropped_reason),
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

    def calls(self) -> Iterator[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        yield from self.conn.execute("SELECT * FROM llm_calls ORDER BY id")
