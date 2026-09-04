"""A dated record of the official MYR rate, one row per currency per day.

Why this exists, and why a same-day cache would not do the job.

`config.toml` carries `fx_spread_per_side`, and it is the largest unmeasured
number in the system: on a US position the whole fee schedule is about 0.3% a
round trip, and half a percent each way of conversion cost is three times that.
Measuring it needs two numbers at one moment - the rate moomoo actually gave,
and the official rate right then - and needing both at once is exactly why it
has not been measured.

Recording the official rate daily removes the timing problem. Convert whenever
suits, read the rate off the app afterwards, and the official rate for that date
is already here to compare against.

So this is a HISTORY, not a cache. `core/market/cache.PriceCache` deliberately
expires daily because bars are derived data reconstructible from the source;
this is the opposite. A rate is a fact about a date, BNM does not serve old
rates on demand, and a day not written down is a day the comparison can never
be made. Append-only for the same reason the ledger and the corpus are: a rate
you can edit afterwards proves nothing about what you could have known.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import _enable_wal

#: Tracked in git like the other stores in data/. It is small - one short row
#: per currency per day - and text-friendly enough that a year of it costs less
#: than a single price-cache commit.
FX_DB = "data/fx.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_rates (
    currency   TEXT NOT NULL,
    rate_date  TEXT NOT NULL,
    rate       TEXT NOT NULL,
    source     TEXT NOT NULL,
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (currency, rate_date)
);
CREATE INDEX IF NOT EXISTS fx_rates_date ON fx_rates(rate_date);

CREATE TRIGGER IF NOT EXISTS fx_rates_no_update
BEFORE UPDATE ON fx_rates
BEGIN SELECT RAISE(ABORT, 'a published rate is not editable after the fact'); END;
CREATE TRIGGER IF NOT EXISTS fx_rates_no_delete
BEFORE DELETE ON fx_rates
BEGIN SELECT RAISE(ABORT, 'the rate log is append-only: a day you can prune is a day you cannot compare against'); END;
"""


class FxLog:
    """The dated rate history. Append-only; re-recording a day is a no-op."""

    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path | None = FX_DB) -> None:
        # Normalised to str and ":memory:" guarded, exactly as Corpus does it.
        # `Path(":memory:")` is a perfectly good relative path, so the guard is
        # the difference between an in-memory database and a file literally
        # named ":memory:" that every test then shares.
        #
        # `path or FX_DB` resolves "no path given" HERE, once, rather than at
        # each caller. It is not defensive tidying - it is the fix for a bug
        # that reached production: `ask.py fx` passed `a.db or None`, `str(None)`
        # is "None", and a day's rate went into a file called `None` in the
        # working directory. Nothing failed; the row was simply written where
        # nobody would look, and the commit that should have carried it died on
        # the missing path instead. Every test passed because every test names
        # a path - the only untested route was the default one production uses.
        path = str(path or FX_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self) -> FxLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def record(
        self,
        currency: str,
        rate_date: date,
        rate: Decimal,
        source: str = "bnm",
        seen_at: datetime | None = None,
    ) -> bool:
        """Write one rate. True if it was new, False if that day was already held.

        `INSERT OR IGNORE` rather than a read-then-write: two collectors running
        at once would both find the day missing and both insert. The primary key
        decides, once, inside the database - the same reasoning as Corpus.add.
        """
        seen = (seen_at or datetime.now(UTC)).isoformat()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO fx_rates(currency, rate_date, rate, source, seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (currency.upper(), rate_date.isoformat(), str(rate), source, seen),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def record_all(
        self,
        rows: list[tuple[str, date, Decimal]],
        source: str = "bnm",
        seen_at: datetime | None = None,
        only: tuple[str, ...] = (),
    ) -> tuple[int, int]:
        """Record many. Returns (new, already held).

        `only` narrows to the currencies worth keeping. BNM publishes upwards of
        thirty and this book touches two; storing the rest daily is thirty times
        the rows to answer a question about one of them.
        """
        wanted = {c.upper() for c in only}
        new = held = 0
        for code, d, rate in rows:
            if wanted and code.upper() not in wanted:
                continue
            if self.record(code, d, rate, source=source, seen_at=seen_at):
                new += 1
            else:
                held += 1
        return new, held

    def rate_on(self, currency: str, on: date) -> Decimal | None:
        """The rate published ON that date, or None.

        Deliberately exact rather than the nearest earlier day. This exists to
        compare against a conversion made on a known date, and quietly answering
        with a different day's rate would put an unknown error into the one
        number it is meant to measure.
        """
        row = self.conn.execute(
            "SELECT rate FROM fx_rates WHERE currency = ? AND rate_date = ?",
            (currency.upper(), on.isoformat()),
        ).fetchone()
        return Decimal(row["rate"]) if row else None

    def history(self, currency: str, limit: int = 30) -> list[sqlite3.Row]:
        """Newest first."""
        return list(
            self.conn.execute(
                "SELECT * FROM fx_rates WHERE currency = ? ORDER BY rate_date DESC LIMIT ?",
                (currency.upper(), limit),
            )
        )

    def counts(self) -> dict[str, int]:
        n = self.conn.execute("SELECT COUNT(*) AS n FROM fx_rates").fetchone()["n"]
        c = self.conn.execute("SELECT COUNT(DISTINCT currency) AS n FROM fx_rates").fetchone()["n"]
        d = self.conn.execute("SELECT COUNT(DISTINCT rate_date) AS n FROM fx_rates").fetchone()["n"]
        return {"rates": n, "currencies": c, "days": d}
