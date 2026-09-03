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
from datetime import UTC, datetime
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
        self.conn.commit()

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
                relevance, escalated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                0,
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
               (run_id, at, source, since, status, fetched, kept, stored,
                duplicates, unlinked, escalated, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                _iso(at or datetime.now(UTC)),
                source,
                _iso(since),
                status,
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

    def articles(
        self,
        since: datetime | None = None,
        source: str | None = None,
        instrument: str | None = None,
        limit: int = 500,
    ) -> list[Article]:
        """Newest first. `instrument` filters on the linked ids, so an article
        the linker never attached to anything is not returned by it."""
        sql = ["SELECT * FROM articles"]
        where, args = [], []
        if since is not None:
            where.append("first_seen_at >= ?")
            args.append(_iso(since))
        if source is not None:
            where.append("source = ?")
            args.append(source)
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
        sweeps = self.conn.execute("SELECT COUNT(*) FROM sweeps").fetchone()[0]
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM sweeps WHERE status = ?", (FAILED,)
        ).fetchone()[0]
        return {
            "articles": articles,
            "linked": linked,
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
        )

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Corpus:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
