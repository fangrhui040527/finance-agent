"""What the system saw, kept, so a month of watching adds up to something.

`ask.py news` fetches a source, prints it, and forgets it. That is right for a
person checking a feed by hand and wrong for anything scheduled: run it nightly
for a month and you have thirty screens of scrollback and an empty disk. This is
the other half - the Silver layer given somewhere to live, so the thirtieth day
can ask what the first one saw.

Three properties, each of which is a way this could have been useless:

  * **Append-only**, like the ledger, the alert log and the prediction store.
    The corpus is evidence about what was knowable *at the time*; a record you
    can go back and tidy proves nothing about what you knew when you acted.

  * **Deduplication ACROSS runs, not within one.** `FeedAdapter._seen` is a set
    on the instance, so it dies with the process. That is sufficient for one
    interactive fetch and useless for a schedule: a syndicated wire story is
    still on the wire tomorrow, so a daily sweep would store it again every day
    it stays there, and a month of watching would report a volume of news that
    is mostly one story counted thirty times. The `dup_hash` index here is what
    makes the dedup outlive the process.

  * **A failed sweep is recorded AS a failure.** `knowledge/feeds/adapter.py`
    goes to some trouble to keep a broken feed distinguishable from a quiet one
    at fetch time; that distinction is worthless if it is thrown away one line
    later. An empty table is otherwise the same shape whether the month was
    quiet or the network refused every request in it - and the second is not
    hypothetical: the sweep can run somewhere whose egress policy answers 403 to
    the news API, and it would look exactly like nothing happening in the world.

The unit stored is `knowledge.news.features.Article` - already deduplicated,
entity-linked and feature-extracted by the adapter. This layer adds persistence
and the cross-run identity; it does not re-derive anything.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.provenance.ledger import _enable_wal
from knowledge.news.features import Article

#: Tracked in git like every other database here - see .gitignore, which keeps
#: out secrets and derived files and nothing else. This one is neither.
CORPUS_DB = "data/corpus.db"

OK = "ok"
FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    doc_id           TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    title            TEXT NOT NULL,
    body             TEXT NOT NULL,
    source_domain    TEXT NOT NULL,
    published_at     TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'en',
    countries_json   TEXT NOT NULL DEFAULT '[]',
    instruments_json TEXT NOT NULL DEFAULT '[]',
    themes_json      TEXT NOT NULL DEFAULT '[]',
    dup_hash         TEXT NOT NULL DEFAULT '',
    relevance        REAL,
    escalated        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS articles_published ON articles(published_at);

-- The cross-run dedup, held by the database rather than by a set that dies
-- with the process. Partial, because '' means "this adapter did not compute
-- one" and those must not collapse into a single row.
CREATE UNIQUE INDEX IF NOT EXISTS articles_dup
    ON articles(dup_hash) WHERE dup_hash <> '';

CREATE TRIGGER IF NOT EXISTS articles_no_update
BEFORE UPDATE ON articles
BEGIN SELECT RAISE(ABORT, 'what the system saw is not editable after the fact'); END;
CREATE TRIGGER IF NOT EXISTS articles_no_delete
BEFORE DELETE ON articles
BEGIN SELECT RAISE(ABORT, 'the corpus is append-only: a record you can prune proves nothing'); END;

CREATE TABLE IF NOT EXISTS sweeps (
    run_id     TEXT NOT NULL,
    at         TEXT NOT NULL,
    source     TEXT NOT NULL,
    since      TEXT NOT NULL,
    status     TEXT NOT NULL,
    fetched    INTEGER NOT NULL DEFAULT 0,
    kept       INTEGER NOT NULL DEFAULT 0,
    stored     INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    unlinked   INTEGER NOT NULL DEFAULT 0,
    escalated  INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sweeps_source_at ON sweeps(source, at);

CREATE TRIGGER IF NOT EXISTS sweeps_no_update
BEFORE UPDATE ON sweeps
BEGIN SELECT RAISE(ABORT, 'a sweep that happened is not editable'); END;
CREATE TRIGGER IF NOT EXISTS sweeps_no_delete
BEFORE DELETE ON sweeps
BEGIN SELECT RAISE(ABORT, 'sweep history is append-only: a failed month must stay a failed month'); END;
"""


@dataclass
class StoreStats:
    """What one source contributed. `stored` is the only number that grew the
    corpus - `duplicates` here counts what the DATABASE already held, which is
    a different question from the adapter's within-batch duplicate count."""

    stored: int = 0
    duplicates: int = 0

    def __str__(self) -> str:
        return f"stored {self.stored}, already held {self.duplicates}"


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _dt(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Corpus:
    """The article store. One connection, opened per process, closed explicitly."""

    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path = CORPUS_DB) -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Columns added after the first corpus shipped. ADD COLUMN only - the
        append-only triggers forbid touching a row, and a migration that
        rewrote rows would be exactly the edit they exist to refuse."""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(articles)")}
        if "quality" not in have:
            self.conn.execute("ALTER TABLE articles ADD COLUMN quality REAL")
        if "fetched_for" not in have:
            self.conn.execute(
                "ALTER TABLE articles ADD COLUMN fetched_for TEXT NOT NULL DEFAULT ''"
            )
        sweeps = {r[1] for r in self.conn.execute("PRAGMA table_info(sweeps)")}
        if "slot" not in sweeps:
            self.conn.execute("ALTER TABLE sweeps ADD COLUMN slot TEXT NOT NULL DEFAULT ''")

    # -- reads about the corpus itself ----------------------------------------

    def coverage(self, days: int = 0) -> list[tuple[str, str, int, int]]:
        """(source, instrument fetched for, kept, named) rows, worst share first.

        What a per-name source actually delivered ABOUT the name it was asked
        for. The article count alone cannot say: on the corpus of 2026-09-06,
        GDELT had returned 575 articles across nine companies and 457 of them
        named no book company at all - a number invisible in "575 collected".
        Rows carry a `fetched_for`, so only what was collected after the column
        existed appears here; older rows are silent rather than counted as
        misses.
        """
        where = "WHERE fetched_for <> ''"
        args: list = []
        if days > 0:
            where += " AND first_seen_at >= ?"
            args.append(_iso(datetime.now(UTC) - timedelta(days=days)))
        rows = self.conn.execute(
            f"""SELECT source, fetched_for, count(*) AS kept,
                       sum(instruments_json LIKE '%' || '"' || fetched_for || '"' || '%') AS named
                  FROM articles {where}
                 GROUP BY source, fetched_for""",
            args,
        ).fetchall()
        out = [(r["source"], r["fetched_for"], int(r["kept"]), int(r["named"] or 0)) for r in rows]
        out.sort(key=lambda r: (r[3] / r[2] if r[2] else 1.0, r[0], r[1]))
        return out

    # -- writes ---------------------------------------------------------------

    def add(self, art: Article, source: str, seen_at: datetime | None = None) -> bool:
        """Store one article. False means the corpus already held it.

        `INSERT OR IGNORE` rather than a read-then-write: two sweeps racing on
        the same wire story would both find nothing and both insert. The unique
        index decides, once, inside the database.
        """
        seen = _iso(seen_at or datetime.now(UTC))
        features = art.features
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO articles
               (doc_id, source, title, body, source_domain, published_at, first_seen_at,
                language, countries_json, instruments_json, themes_json, dup_hash,
                relevance, escalated, quality, fetched_for)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                art.doc_id,
                source,
                art.title,
                art.body,
                art.source_domain,
                _iso(art.published_at),
                seen,
                art.language,
                json.dumps(list(art.countries)),
                json.dumps(list(art.instruments)),
                json.dumps(list(art.themes)),
                art.dup_hash or "",
                getattr(features, "relevance", None),
                int(bool(art.escalated)),
                art.quality,
                art.fetched_for or "",
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def add_all(self, articles, source: str, seen_at: datetime | None = None) -> StoreStats:
        stats = StoreStats()
        for art in articles:
            if self.add(art, source, seen_at):
                stats.stored += 1
            else:
                stats.duplicates += 1
        return stats

    def record_sweep(
        self,
        run_id: str,
        source: str,
        since: datetime,
        status: str,
        *,
        at: datetime | None = None,
        slot: str = "",
        fetched: int = 0,
        kept: int = 0,
        stored: int = 0,
        duplicates: int = 0,
        unlinked: int = 0,
        escalated: int = 0,
        detail: str = "",
    ) -> None:
        """Every attempt, including the ones that found nothing and the ones
        that could not run. The row IS the difference between those two."""
        self.conn.execute(
            """INSERT INTO sweeps
               (run_id, at, source, since, status, slot, fetched, kept, stored,
                duplicates, unlinked, escalated, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                _iso(at or datetime.now(UTC)),
                source,
                _iso(since),
                status,
                slot,
                fetched,
                kept,
                stored,
                duplicates,
                unlinked,
                escalated,
                detail,
            ),
        )
        self.conn.commit()

    # -- reads ----------------------------------------------------------------

    def last_success(self, source: str) -> datetime | None:
        """When this source was last read successfully - the watermark a sweep
        resumes from. A FAILED sweep deliberately does not move it: resuming
        from a failure would put the window that was never read behind us."""
        row = self.conn.execute(
            "SELECT MAX(at) AS at FROM sweeps WHERE source = ? AND status = ?",
            (source, OK),
        ).fetchone()
        return _dt(row["at"]) if row and row["at"] else None

    def slot_runs(self, since: datetime, until: datetime) -> dict[str, int]:
        """How many distinct sweep RUNS each slot had over [since, until).

        Counting runs, not rows: one sweep writes a row per source, and the
        question this answers is "did the collector fire", not "how many
        sources answered". Rows written before the slot column existed carry
        '' and are not counted - the corpus is append-only, so the honest
        reading is that their slot is unknown, never that they were a slot
        that ran.

        BOTH ENDS ARE BOUNDED because the caller compares this against a count
        of days. Left open, a half-day at either edge counts firings against
        days that were never owed, or owes days whose firings fall outside -
        and on a five-day window that arithmetic reported a shortfall of one on
        every daily slot for a collector that had missed nothing at all.
        """
        rows = self.conn.execute(
            """SELECT slot, COUNT(DISTINCT run_id) AS runs FROM sweeps
                WHERE at >= ? AND at < ? AND slot <> '' GROUP BY slot""",
            (_iso(since), _iso(until)),
        ).fetchall()
        return {r["slot"]: int(r["runs"]) for r in rows}

    def run_count(self, since: datetime, until: datetime) -> int:
        """Distinct sweep runs over [since, until), whatever slot they carried.

        The companion to `slot_runs`: it answers "did the collector run at all",
        which is the only thing that can be asked of history written before the
        slot column existed.
        """
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT run_id) AS runs FROM sweeps WHERE at >= ? AND at < ?",
            (_iso(since), _iso(until)),
        ).fetchone()
        return int(row["runs"]) if row else 0

    def first_slot_row(self) -> datetime | None:
        """When the sweeps table first recorded a slot at all.

        Before this moment nothing can be said about which slots ran, so a rule
        that counts missed slots must not count the silence in front of it as
        misses. Returns None while no row carries a slot.
        """
        row = self.conn.execute("SELECT MIN(at) AS at FROM sweeps WHERE slot <> ''").fetchone()
        return _dt(row["at"]) if row and row["at"] else None

    def articles(
        self,
        since: datetime | None = None,
        source: str | None = None,
        instrument: str | None = None,
        limit: int = 500,
        min_quality: float | None = None,
        published_since: datetime | None = None,
    ) -> list[Article]:
        """Newest first. `instrument` filters on the linked ids, so an article
        the linker never attached to anything is not returned by it.

        `min_quality` keeps rows scored at or above it AND rows with no score
        (the corpus before scoring existed); `published_since` windows on the
        publisher's date rather than on when the sweep first saw it."""
        sql = ["SELECT * FROM articles"]
        where, args = [], []
        if since is not None:
            where.append("first_seen_at >= ?")
            args.append(_iso(since))
        if published_since is not None:
            where.append("published_at >= ?")
            args.append(_iso(published_since))
        if source is not None:
            where.append("source = ?")
            args.append(source)
        if min_quality is not None:
            where.append("(quality IS NULL OR quality >= ?)")
            args.append(float(min_quality))
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY published_at DESC, doc_id LIMIT ?")
        args.append(max(1, limit))
        rows = self.conn.execute(" ".join(sql), args).fetchall()
        out = [self._to_article(r) for r in rows]
        if instrument is not None:
            out = [a for a in out if instrument in a.instruments]
        return out

    def counts(self) -> dict[str, int]:
        articles = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        linked = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE instruments_json <> '[]'"
        ).fetchone()[0]
        escalated = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE escalated = 1"
        ).fetchone()[0]
        sweeps = self.conn.execute("SELECT COUNT(*) FROM sweeps").fetchone()[0]
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM sweeps WHERE status = ?", (FAILED,)
        ).fetchone()[0]
        return {
            "articles": articles,
            "linked": linked,
            "escalated": escalated,
            "sweeps": sweeps,
            "failed_sweeps": failed,
        }

    def sweeps(self, limit: int = 20, source: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM sweeps"
        args: list = []
        if source is not None:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY at DESC LIMIT ?"
        args.append(max(1, limit))
        return list(self.conn.execute(sql, args).fetchall())

    @staticmethod
    def _to_article(row: sqlite3.Row) -> Article:
        keys = row.keys()
        return Article(
            doc_id=row["doc_id"],
            title=row["title"],
            body=row["body"],
            source_domain=row["source_domain"],
            published_at=_dt(row["published_at"]),
            language=row["language"],
            countries=json.loads(row["countries_json"]),
            instruments=json.loads(row["instruments_json"]),
            themes=json.loads(row["themes_json"]),
            dup_hash=row["dup_hash"] or None,
            quality=row["quality"] if "quality" in keys else None,
            fetched_for=row["fetched_for"] if "fetched_for" in keys else "",
            escalated=bool(row["escalated"]) if "escalated" in keys else False,
        )

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Corpus:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
