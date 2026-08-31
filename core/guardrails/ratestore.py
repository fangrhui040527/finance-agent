"""Optional persistence for the tool-rail rate limit.

The in-memory `RateLimitPolicy` counts per process, so the CLI, the MCP server
and the web app each got the full allowance - three processes, three budgets.
Backed by this store they share one window. Still per-machine, still advisory:
the MONEY cap lives in `InferenceClient` against the ledger, not here.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from core.provenance.ledger import _enable_wal

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_hits (
    rail TEXT NOT NULL,
    at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_hits_rail_at ON rate_hits(rail, at);
"""


class SqliteRateLimitStore:
    def __init__(self, path: str | Path = "data/rate_limit.db") -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        _enable_wal(self.conn, str(p), timeout_ms=5000)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def count_and_add(self, rail: str, window_seconds: float, now: float | None = None) -> int:
        """Prune, count the window, record this hit; returns the count BEFORE it."""
        t = time.time() if now is None else now
        cutoff = t - window_seconds
        self.conn.execute("DELETE FROM rate_hits WHERE rail = ? AND at < ?", (rail, cutoff))
        n = self.conn.execute(
            "SELECT COUNT(*) FROM rate_hits WHERE rail = ? AND at >= ?", (rail, cutoff)
        ).fetchone()[0]
        self.conn.execute("INSERT INTO rate_hits (rail, at) VALUES (?, ?)", (rail, t))
        self.conn.commit()
        return int(n)

    def close(self) -> None:
        self.conn.close()
