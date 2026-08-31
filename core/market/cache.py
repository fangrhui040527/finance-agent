"""A same-trading-day cache for daily bars.

Stooq enforces a daily hit quota and Yahoo throttles bursts; both serve DAILY
bars, so within one session the second fetch of the same symbol buys nothing
but quota burn. The cache keys on (feed, symbol) and expires at the day
boundary - a daily bar cannot change until a new session prints.

SQLite so the CLI, the MCP server and the web app share it across processes.
The cache is NOT append-only on purpose: it is derived data, reconstructible
from the source, and holding stale rows would be the dishonest choice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from core.provenance.ledger import _enable_wal

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_csv (
    feed      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    fetched_on TEXT NOT NULL,
    body      TEXT NOT NULL,
    PRIMARY KEY (feed, symbol)
);
"""


class PriceCache:
    def __init__(
        self,
        path: str | Path = "data/price_cache.db",
        today: Callable[[], str] | None = None,
    ) -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(p)
        self.conn = sqlite3.connect(self._path)
        _enable_wal(self.conn, self._path, timeout_ms=5000)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._today = today or (lambda: datetime.now(UTC).date().isoformat())

    def get(self, feed: str, symbol: str) -> str | None:
        row = self.conn.execute(
            "SELECT fetched_on, body FROM price_csv WHERE feed = ? AND symbol = ?",
            (feed, symbol),
        ).fetchone()
        if row is None:
            return None
        fetched_on, body = row
        if fetched_on != self._today():
            return None  # a new session may have printed; the cached day is over
        return body

    def put(self, feed: str, symbol: str, body: str) -> None:
        self.conn.execute(
            "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES (?,?,?,?)"
            " ON CONFLICT(feed, symbol) DO UPDATE SET fetched_on=excluded.fetched_on,"
            " body=excluded.body",
            (feed, symbol, self._today(), body),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
