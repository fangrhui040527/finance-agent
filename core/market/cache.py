"""A same-trading-day cache for daily bars.

Stooq enforces a daily hit quota and Yahoo throttles bursts; both serve DAILY
bars, so within one session the second fetch of the same symbol buys nothing
but quota burn. The cache keys on (feed, symbol) and expires at the day
boundary - a daily bar cannot change until a new session prints.

SQLite so the CLI, the MCP server and the web app share it across processes.
The cache is NOT append-only on purpose: it is derived data, reconstructible
from the source, and holding stale rows would be the dishonest choice.

WHAT IT REFUSES TO HOLD. A feed that answers 403 answers with a page, not with
bars, and until 2026-09-06 that page was cached like any other body: all 27
Stooq rows in this repository's cache held the same 796 bytes of HTML. Nothing
downstream was fooled - the CSV parser rejects it and the price read refuses -
but the cache then reported a hit for a symbol it could not price, so the next
process skipped the fetch that might have worked and every one of them looked
like a quota-saving success. `looks_like_bars` is the gate: a body that is not
a CSV of bars is neither stored nor served, and a poisoned row already in the
file is dropped the first time it is read.
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


def looks_like_bars(body: str) -> bool:
    """Whether a body is a CSV of daily bars rather than an error page.

    Deliberately shallow: an HTML document, a plain-text refusal ("No data"),
    an empty body or a header with nothing under it. Anything that parses as
    rows is left to the parser, which has the column rules.
    """
    text = (body or "").strip()
    if not text:
        return False
    if text[:1] == "<" or "<html" in text[:200].lower():
        return False
    lines = text.splitlines()
    return len(lines) >= 2 and lines[0].count(",") >= 4


def offline() -> bool:
    """FINPLANET_OFFLINE=1: serve the cache whatever its date, never fetch.

    For a process that has no route to a price host but has a cache another
    process filled - the nightly feedback routine reads bars the collector
    fetched hours earlier, from a session that cannot reach Yahoo at all. The
    served day is reported (`last_served_from`) so nothing pretends a cached
    bar is today's.
    """
    import os

    return os.environ.get("FINPLANET_OFFLINE", "").strip() in ("1", "true", "yes")


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
        if not looks_like_bars(body):
            # An error page cached under a symbol's name. Drop it rather than
            # report a hit: a hit here stops the caller from trying the fetch
            # that would have worked.
            self.conn.execute("DELETE FROM price_csv WHERE feed = ? AND symbol = ?", (feed, symbol))
            self.conn.commit()
            return None
        if fetched_on != self._today() and not offline():
            return None  # a new session may have printed; the cached day is over
        self.last_served_from = fetched_on
        return body

    #: The day the last `get` answered from, for a caller that wants to say so.
    last_served_from: str | None = None

    def put(self, feed: str, symbol: str, body: str) -> None:
        if not looks_like_bars(body):
            return  # an error page is not a cache hit; let the next process try
        self.conn.execute(
            "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES (?,?,?,?)"
            " ON CONFLICT(feed, symbol) DO UPDATE SET fetched_on=excluded.fetched_on,"
            " body=excluded.body",
            (feed, symbol, self._today(), body),
        )
        self.conn.commit()

    def prune_unusable(self) -> int:
        """Drop every cached body that is not a CSV of bars. Returns the count."""
        rows = self.conn.execute("SELECT feed, symbol, body FROM price_csv").fetchall()
        bad = [(f, s) for f, s, b in rows if not looks_like_bars(b)]
        self.conn.executemany("DELETE FROM price_csv WHERE feed = ? AND symbol = ?", bad)
        self.conn.commit()
        return len(bad)

    def close(self) -> None:
        self.conn.close()
