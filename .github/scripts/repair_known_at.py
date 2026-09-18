"""Re-stamp fact-book rows whose known_at lies after the day they were fetched.

WHY SUCH ROWS EXIST. `core.market.pointintime.Fact` refuses a figure whose
known_at precedes its period end, and until 2026-09-18 two collectors met
that guard by clamping known_at UP to the period: `known_at = max(today,
period)`. For a reported figure the clamp never fires. For an estimate of a
future period, and for a print a vendor files under a fiscal-period end that
falls after the announcement, it fires every time - and it dates a figure that
is public today to a day that has not arrived. On 2026-09-18 data/facts.db held
49 such rows: NVIDIA's FY27 Q2 EPS print (2.22 against 2.1384, fetched 5
September, labelled 2026-09-30) and 46 FMP consensus rows dated 2027 to 2031,
three revisions of one AAPL figure among them sharing a single vintage. None
of the 49 was visible to any as-of read.

WHAT IT DOES. For every observation with known_at after the day of its
fetched_at, known_at becomes that day and the row is labelled `forward` in
payload_json - the same label the repaired collectors now write, and the one
the A1 bridge needs to build the row at all. Nothing else on the row changes,
no other row is touched, and the store records the run in `pulls` under the
source `repair_known_at` so the change is on the record inside the store it
changed, not only in a commit message.

WHAT IT CANNOT DO. The fetch day is the earliest date the store can vouch for.
The true public date of NVIDIA's print was late August; the store never saw
it, so the row reads knowable from 5 September - later than true, never
earlier, which is the side the guard needs. Inventing the earlier date is
exactly what a point-in-time store must not do.

The observations table forbids UPDATE by trigger, and rightly: what was known
is not editable after the fact. This is the one exception, and it corrects
the stamp to what was true rather than to what is convenient, so the guard is
lifted for the length of one transaction and put back by the same helper the
store runs on every open.

Usage, from the repository root:

    uv run python .github/scripts/repair_known_at.py [data/facts.db] [--dry-run]

It prints one line per row it changed, then the count before and after.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.provenance.ledger import apply_schema  # noqa: E402
from knowledge.facts import _GUARDS, OK, FactBook  # noqa: E402

#: The `pulls` source the repair records itself under.
SOURCE = "repair_known_at"

_KEY = "source = ? AND instrument_id = ? AND concept = ? AND period_end = ? AND value_text = ?"


@dataclass(frozen=True)
class Change:
    """One row as it was, and the day it moves to."""

    source: str
    instrument_id: str
    concept: str
    period_end: str
    value_text: str
    known_at: str
    fetched_on: str

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.source, self.instrument_id, self.concept, self.period_end, self.value_text)

    def __str__(self) -> str:
        return (
            f"{self.source:<8} {self.instrument_id:<10} {self.concept:<24} for {self.period_end}"
            f"  {self.value_text:>14}  known_at {self.known_at} -> {self.fetched_on}"
        )


def mis_stamped(book: FactBook) -> list[Change]:
    """Every observation stamped knowable after the day it was fetched."""
    rows = book.conn.execute(
        """SELECT source, instrument_id, concept, period_end, value_text, known_at,
                  substr(fetched_at, 1, 10) AS fetched_on
           FROM observations
           WHERE known_at > substr(fetched_at, 1, 10)
           ORDER BY source, instrument_id, concept, period_end, fetched_at"""
    )
    return [Change(*r) for r in rows]


def repair(path: str | Path, *, dry_run: bool = False, now: datetime | None = None) -> list[Change]:
    """Re-stamp the mis-stamped rows in the fact book at `path`; return them."""
    with FactBook(path) as book:
        changes = mis_stamped(book)
        if dry_run or not changes:
            return changes
        conn = book.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TRIGGER IF EXISTS observations_no_update")
            for c in changes:
                (payload_json,) = conn.execute(
                    f"SELECT payload_json FROM observations WHERE {_KEY}", c.key
                ).fetchone()
                payload = {**json.loads(payload_json), "forward": True}
                conn.execute(
                    f"UPDATE observations SET known_at = ?, payload_json = ? WHERE {_KEY}",
                    (c.fetched_on, json.dumps(payload, sort_keys=True, default=str), *c.key),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        # The guard goes back the way FactBook() puts it there on every open,
        # so a crash between the commit above and this line leaves nothing
        # unguarded past the next process that opens the store.
        apply_schema(conn, _GUARDS.format(t="observations"), timeout_ms=FactBook.BUSY_TIMEOUT_MS)
        stamp = now or datetime.now(UTC)
        book.record_pull(
            f"repair-known-at:{stamp.isoformat(timespec='seconds')}",
            SOURCE,
            OK,
            at=stamp,
            fetched=len(changes),
            stored=len(changes),
            detail=f"known_at := fetch day, forward label set, on {len(changes)} rows: "
            + "; ".join(
                f"{c.source} {c.instrument_id} {c.concept} {c.period_end} "
                f"{c.known_at}->{c.fetched_on}"
                for c in changes
            ),
        )
        # Fold the WAL into the file itself: the tracked copy is the database
        # file, and a repair that lives only in a -wal sidecar is not committed.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", default="data/facts.db", help="the fact book")
    ap.add_argument(
        "--dry-run", action="store_true", help="print what would change and touch nothing"
    )
    a = ap.parse_args(argv)
    changes = repair(a.path, dry_run=a.dry_run)
    for c in changes:
        sys.stdout.write(f"{c}\n")
    with FactBook(a.path) as book:
        after = len(mis_stamped(book))
    verb = "would move" if a.dry_run else "moved"
    sys.stdout.write(
        f"{len(changes)} rows had known_at after the day they were fetched; {verb} "
        f"{len(changes)} to that day; {after} remain\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
