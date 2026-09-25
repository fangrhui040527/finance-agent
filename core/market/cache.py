"""A same-trading-day cache for daily bars.

Stooq enforces a daily hit quota and Yahoo throttles bursts; both serve DAILY
bars, so within one session the second fetch of the same symbol buys nothing
but quota burn. The cache keys on (feed, symbol) and expires at the day
boundary - a daily bar cannot change until a new session prints.

SQLite so the CLI, the MCP server and the web app share it across processes.
The cache is NOT append-only on purpose: it is derived data, reconstructible
from the source, and holding stale rows would be the dishonest choice.

WHAT IT REFUSES TO SERVE. A body fetched DURING a session is not the session's
bar. On 2026-09-08 XNAS:SPY was fetched mid-session and Yahoo answered with an
in-progress row whose open was carried over from the previous day - open above
its own high, which the bar parser correctly drops as corrupt. The body still
looked fresh (fetched today, last CSV row dated today), so every later sweep
that UTC day took a cache hit and never refetched, and the US market proxy
silently ended four sessions earlier than the names it was measuring. The
`us_close` sweep would have got the finished bar. The gate is the body's own
disagreement with itself: it carries a ROW the parser will not accept as a BAR,
which is what an in-progress session looks like on the wire. Such a body is a
miss, so the next process fetches again and gets the finished session.

Not caught: an in-progress row that happens to be self-consistent parses as a
bar, and is then cached and read as that session's close. This gate cannot see
that one. Nothing else currently can either.

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
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from core.provenance.ledger import _enable_wal

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_csv (
    feed      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    fetched_on TEXT NOT NULL,
    fetched_at TEXT,
    body      TEXT NOT NULL,
    PRIMARY KEY (feed, symbol)
);
CREATE TABLE IF NOT EXISTS listed_name (
    feed    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    name    TEXT NOT NULL,
    seen_on TEXT NOT NULL,
    PRIMARY KEY (feed, symbol)
);
"""

#: `listed_name` is the company name a source printed beside a symbol's bars
#: (Yahoo's chart `meta.longName`). Kept because a code written from memory can
#: name another company, and the bars alone cannot say so: 5264.KL prices
#: whatever Bursa lists under 5264. The name is the one check a price fetch
#: carries for free. Derived data, replaced on each fetch like the bodies.

#: `fetched_on` is the day, and the day cannot say whether a market had shut.
#: `fetched_at` is the instant, added later and therefore nullable: every row
#: cached before this column existed reads NULL, which `price_state` reports as
#: `unknown` rather than guessing. Backfilling it would be inventing a time.
_ADD_FETCHED_AT = "ALTER TABLE price_csv ADD COLUMN fetched_at TEXT"


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


def last_usable_bar_day(body: str) -> date | None:
    """The last row that SURVIVES the bar parser, not merely the last row.

    The difference is the whole point: an in-progress row carries a date and
    looks like data, and is thrown away downstream. Asking the parser rather
    than re-implementing its rules keeps one definition of "a usable bar".
    The import is deferred because the feed owns the cache, not the reverse.
    """
    from core.market.feed import PriceFeed, PriceFeedError

    try:
        bars = PriceFeed.parse(body)
    except PriceFeedError:
        return None
    return bars[-1].day if bars else None


def last_dated_row(body: str) -> date | None:
    """The date on the last row that carries one, parseable as a bar or not."""
    for line in reversed((body or "").strip().splitlines()):
        head = line.split(",", 1)[0].strip()[:10]
        try:
            return date.fromisoformat(head)
        except ValueError:
            continue
    return None


def is_mid_session(body: str) -> bool:
    """Whether the body ends in a row the bar parser will not take.

    A body that ends Friday when Friday is the last session it saw is complete,
    whatever day it is read on. A body that carries a row for a LATER day than
    its last usable bar was fetched while that day was still trading: the row is
    the session so far, not the session.
    """
    usable = last_usable_bar_day(body)
    dated = last_dated_row(body)
    return usable is not None and dated is not None and usable < dated


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
        now: Callable[[], str] | None = None,
    ) -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(p)
        # One cache lives for the whole process inside `default_feed()`, and the
        # web app answers sync endpoints from a threadpool: the thread that opens
        # this connection is not the thread that reads it next. sqlite3 refuses
        # that by default, so it is allowed here - and allowing it is not enough
        # on its own. The module's serialized mode protects one statement at a
        # time; `get` is a read followed by a conditional delete and a commit,
        # `put` a write followed by a commit, and two threads interleaving those
        # on one connection commit each other's half-done work or read a row
        # mid-replacement. `_lock` makes each method one step, so the connection
        # is shared but never entered twice at once. Every other store opens per
        # call and needs neither.
        self.conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        _enable_wal(self.conn, self._path, timeout_ms=5000)
        self.conn.executescript(SCHEMA)
        if "fetched_at" not in {r[1] for r in self.conn.execute("PRAGMA table_info(price_csv)")}:
            try:
                self.conn.execute(_ADD_FETCHED_AT)
            except sqlite3.OperationalError:  # pragma: no cover - another process won
                pass
        self.conn.commit()
        self._today = today or (lambda: datetime.now(UTC).date().isoformat())
        self._now = now or (lambda: datetime.now(UTC).isoformat())

    def get(self, feed: str, symbol: str, mic: str | None = None) -> str | None:
        """The cached body for a symbol, or None when the caller should fetch.

        `mic` is the market the symbol trades on, when the caller can name it.
        With it, a body is stale once that market has FINISHED A SESSION since
        the body was pulled - the rule `fetched_on` was standing in for. On
        2026-09-22 the us_close collector, 2h51m late, arrived at 00:08 UTC and
        refetched every book name, stamping each Bursa row fetched-on Tuesday
        with Monday's close as its last bar. Bursa then shut at 09:00 UTC, and
        the day rule served Monday's closes to every read for the rest of
        Tuesday, the bursa_close sweep at 09:25 included: the row was today's,
        the session was not. Without `mic` the day rule stands alone, which is
        the older, looser answer, never a fresher one.
        """
        with self._lock:
            return self._get(feed, symbol, mic)

    def _get(self, feed: str, symbol: str, mic: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT fetched_on, fetched_at, body FROM price_csv WHERE feed = ? AND symbol = ?",
            (feed, symbol),
        ).fetchone()
        if row is None:
            return None
        fetched_on, fetched_at, body = row
        if not looks_like_bars(body):
            # An error page cached under a symbol's name. Drop it rather than
            # report a hit: a hit here stops the caller from trying the fetch
            # that would have worked.
            self.conn.execute("DELETE FROM price_csv WHERE feed = ? AND symbol = ?", (feed, symbol))
            self.conn.commit()
            return None
        if offline():
            self.last_served_from = fetched_on
            return body
        if fetched_on != self._today():
            return None  # a new session may have printed; the cached day is over
        if is_mid_session(body):
            # Fetched while the last session it saw was still trading. A hit
            # here freezes the symbol a session behind for the rest of the day,
            # and the reader cannot tell that from a market that was shut.
            return None
        if mic and fetched_at:
            from core.market.calendar import last_session_close

            pulled = datetime.fromisoformat(fetched_at)
            if pulled.tzinfo is None:
                pulled = pulled.replace(tzinfo=UTC)
            shut = last_session_close(mic, datetime.fromisoformat(self._now()))
            if shut is not None and pulled < shut:
                # The market has closed a session since this was pulled; the
                # body's last bar is at best the session before it.
                return None
        self.last_served_from = fetched_on
        return body

    #: The day the last `get` answered from, for a caller that wants to say so.
    last_served_from: str | None = None

    def put(self, feed: str, symbol: str, body: str) -> None:
        if not looks_like_bars(body):
            return  # an error page is not a cache hit; let the next process try
        with self._lock:
            self.conn.execute(
                "INSERT INTO price_csv (feed, symbol, fetched_on, fetched_at, body)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(feed, symbol) DO UPDATE SET fetched_on=excluded.fetched_on,"
                " fetched_at=excluded.fetched_at, body=excluded.body",
                (feed, symbol, self._today(), self._now(), body),
            )
            self.conn.commit()

    def fetched_at(self, feed: str, symbol: str) -> datetime | None:
        """When this body was pulled, or None for a row cached before the column.

        The instant, not the day: whether the newest bar in the body is a close
        or the session so far turns on where the fetch fell relative to that
        market's shut, and `fetched_on` cannot answer that. See
        `core.market.calendar.price_state`, which owns the rule.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT fetched_at FROM price_csv WHERE feed = ? AND symbol = ?",
                (feed, symbol),
            ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            at = datetime.fromisoformat(row[0])
        except ValueError:
            return None
        return at if at.tzinfo else at.replace(tzinfo=UTC)

    def put_name(self, feed: str, symbol: str, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        with self._lock:
            self.conn.execute(
                "INSERT INTO listed_name (feed, symbol, name, seen_on) VALUES (?,?,?,?)"
                " ON CONFLICT(feed, symbol) DO UPDATE SET name=excluded.name,"
                " seen_on=excluded.seen_on",
                (feed, symbol, name, self._today()),
            )
            self.conn.commit()

    def name(self, feed: str, symbol: str) -> str | None:
        """The company name the source last printed for this symbol, if any."""
        with self._lock:
            row = self.conn.execute(
                "SELECT name FROM listed_name WHERE feed = ? AND symbol = ?", (feed, symbol)
            ).fetchone()
        return row[0] if row else None

    def prune_unusable(self) -> int:
        """Drop every cached body that is not a CSV of bars. Returns the count."""
        with self._lock:
            rows = self.conn.execute("SELECT feed, symbol, body FROM price_csv").fetchall()
            bad = [(f, s) for f, s, b in rows if not looks_like_bars(b)]
            self.conn.executemany("DELETE FROM price_csv WHERE feed = ? AND symbol = ?", bad)
            self.conn.commit()
        return len(bad)

    def close(self) -> None:
        self.conn.close()
