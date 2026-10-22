"""Month-to-date spend in USD, from every place spend is durably recorded.

The owner's limit is a month and a dollar figure (config.toml [budget]
monthly_usd), and the system spends in more than one process: the collector on
a GitHub runner, a person's `ask.py thesis --narrate` on their own machine. A
runner's data/provenance.db is thrown away with the runner - the collector does
not commit it - so a cap read from that ledger alone would see none of the
spend before this run, every run.

What IS committed is data/corpus.db, and the web evidence ingest records each
paid search there (`evidence_searches`, knowledge/evidence/web.py), cost
included. So month-to-date is that table plus this process's own ledger: the
scheduled spend survives every run, and a local command adds what it spent
itself. A call made on another machine and never committed is the one thing
this cannot see; the Console's workspace spend limit is the backstop for that,
and docs/14 says so.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

#: The table the evidence ingest writes. Read here by name rather than through
#: knowledge/ so the inference client (core) does not import the knowledge layer.
EVIDENCE_TABLE = "evidence_searches"

#: The share of the monthly cap at which `doctor` and the monitor start saying
#: so (the monitor's ALERT is at the cap itself). One number, read by both, so
#: a preflight and a scheduled check cannot disagree about the same month.
MONTHLY_WARN_FRACTION = Decimal("0.85")


def month_start(now: datetime | None = None) -> datetime:
    """00:00 UTC on the first of `now`'s month: the window a monthly cap covers."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def ledger_usd_since(ledger, since: datetime) -> Decimal:
    """USD this process's provenance ledger recorded since `since`."""
    rows = ledger.conn.execute(
        "SELECT cost_usd FROM llm_calls WHERE at >= ?", (since.isoformat(),)
    ).fetchall()
    return sum((Decimal(r[0]) for r in rows), Decimal(0))


def ledger_usd_at(ledger_path: str | Path, since: datetime) -> Decimal:
    """`ledger_usd_since` for a ledger named by its path, read-only: a missing
    file or table is zero, and nothing is created - opening a ProvenanceLedger
    would create the file and apply its schema just to read a sum."""
    return _sum_since(ledger_path, "llm_calls", since)


def committed_usd_since(corpus_path: str | Path, since: datetime) -> Decimal:
    """USD the evidence ingest recorded in the committed corpus since `since`.

    Read-only; a corpus without the table (before the first search) is zero. A
    file that cannot be read raises: a cap that quietly read zero would be no
    cap at all."""
    return _sum_since(corpus_path, EVIDENCE_TABLE, since)


def _sum_since(db_path: str | Path, table: str, since: datetime) -> Decimal:
    path = Path(db_path)
    if str(db_path) == ":memory:" or not path.exists() or path.stat().st_size == 0:
        return Decimal(0)
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        have = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not have:
            return Decimal(0)
        rows = con.execute(
            f"SELECT cost_usd FROM {table} WHERE at >= ?", (since.isoformat(),)
        ).fetchall()
    finally:
        con.close()
    return sum((Decimal(r[0]) for r in rows), Decimal(0))


def month_to_date_usd(ledger=None, corpus_path: str | Path | None = None, now=None) -> Decimal:
    """The month's spend so far: committed evidence spend plus this ledger's."""
    since = month_start(now)
    total = Decimal(0)
    if corpus_path is not None:
        total += committed_usd_since(corpus_path, since)
    if ledger is not None:
        total += ledger_usd_since(ledger, since)
    return total
