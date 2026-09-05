"""The structured half of what the collector keeps: facts, events, series, documents.

`knowledge/corpus.py` holds prose - headlines and summaries, entity-linked and
feature-scored. Most of what an analyst actually reads is not prose: a quarter's
revenue, an insider's purchase, a policy rate, a consensus EPS, the date the
next results land. Until this store existed none of that had anywhere to live,
so the agents that consume it (A1 fundamentals, A5 events, A6 macro, A8
ownership) were handed hand-typed numbers on the command line or nothing.

Four tables, one rule each:

  * **observations** - a figure about an instrument, stamped with `known_at`:
    the day it became knowable, never the period it describes. This is the
    on-disk form of `core.market.pointintime.Fact`, and `as_fact_store` hands
    exactly that to A1 so the look-ahead guard still applies.
  * **events** - something dated: a filing, an insider trade, a scheduled
    result, a rating change. Announced and effective dates are separate
    columns because conflating them is the A5 prohibition in docs/02.
  * **series** - a macro or market time series. Vintaged: a revised value is
    a NEW row with a later `known_at`, and `series()` returns the vintage that
    was knowable on the day asked about.
  * **documents** - long text with a date: an earnings-call transcript, a
    filing's own summary. Chunked by the retrieval index, never here.

And the two properties every store in this repository shares: append-only,
enforced by SQLite triggers rather than convention, and every pull recorded -
including the ones that could not run - so an empty table can never be
mistaken for a quiet world.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.provenance.ledger import _enable_wal, apply_schema

FACTS_DB = "data/facts.db"

OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"  # no key, or the plan does not include the endpoint

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    source         TEXT NOT NULL,
    instrument_id  TEXT NOT NULL,
    concept        TEXT NOT NULL,
    period_end     TEXT NOT NULL DEFAULT '',
    known_at       TEXT NOT NULL,
    value_text     TEXT NOT NULL,
    value_num      REAL,
    unit           TEXT NOT NULL DEFAULT '',
    currency       TEXT NOT NULL DEFAULT '',
    payload_json   TEXT NOT NULL DEFAULT '{}',
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (source, instrument_id, concept, period_end, value_text)
);
CREATE INDEX IF NOT EXISTS observations_lookup
    ON observations(instrument_id, concept, known_at);

CREATE TABLE IF NOT EXISTS events (
    source         TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    instrument_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    announced_at   TEXT NOT NULL,
    effective_at   TEXT,
    title          TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (source, event_id)
);
CREATE INDEX IF NOT EXISTS events_lookup ON events(instrument_id, kind, announced_at);

CREATE TABLE IF NOT EXISTS series (
    source         TEXT NOT NULL,
    series_id      TEXT NOT NULL,
    obs_date       TEXT NOT NULL,
    value_text     TEXT NOT NULL,
    value_num      REAL NOT NULL,
    known_at       TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (source, series_id, obs_date, value_text)
);
CREATE INDEX IF NOT EXISTS series_lookup ON series(series_id, obs_date, known_at);

CREATE TABLE IF NOT EXISTS documents (
    source         TEXT NOT NULL,
    doc_id         TEXT NOT NULL,
    instrument_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    published_at   TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (source, doc_id)
);
CREATE INDEX IF NOT EXISTS documents_lookup ON documents(instrument_id, kind, published_at);

CREATE TABLE IF NOT EXISTS pulls (
    run_id     TEXT NOT NULL,
    at         TEXT NOT NULL,
    source     TEXT NOT NULL,
    status     TEXT NOT NULL,
    fetched    INTEGER NOT NULL DEFAULT 0,
    stored     INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS pulls_source_at ON pulls(source, at);
"""

_GUARDS = """
CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, 'what was known is not editable after the fact: {t} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, 'a record you can prune proves nothing: {t} is append-only'); END;
"""


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _dt(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def as_decimal(raw) -> Decimal | None:
    """A Decimal from whatever a vendor sent, or None. Never a guess."""
    if raw is None or raw == "" or raw == ".":
        return None
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


@dataclass(frozen=True)
class Observation:
    source: str
    instrument_id: str
    concept: str
    known_at: date
    value: Decimal | None = None
    text: str = ""
    period_end: date | None = None
    unit: str = ""
    currency: str = ""
    payload: dict = field(default_factory=dict)

    @property
    def value_text(self) -> str:
        return str(self.value) if self.value is not None else self.text


@dataclass(frozen=True)
class EventRecord:
    source: str
    event_id: str
    instrument_id: str
    kind: str
    announced_at: datetime
    title: str
    effective_at: datetime | None = None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SeriesPoint:
    source: str
    series_id: str
    obs_date: date
    value: Decimal
    known_at: date
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Document:
    source: str
    doc_id: str
    instrument_id: str
    kind: str
    title: str
    body: str
    published_at: datetime
    payload: dict = field(default_factory=dict)


@dataclass
class StoreStats:
    stored: int = 0
    duplicates: int = 0

    def __str__(self) -> str:
        return f"stored {self.stored}, already held {self.duplicates}"

    def __iadd__(self, other: StoreStats) -> StoreStats:
        self.stored += other.stored
        self.duplicates += other.duplicates
        return self


class FactBook:
    """The structured store. One connection per process, closed explicitly."""

    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path | None = FACTS_DB) -> None:
        path = str(path or FACTS_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        apply_schema(
            self.conn,
            SCHEMA,
            *(
                _GUARDS.format(t=t)
                for t in ("observations", "events", "series", "documents", "pulls")
            ),
            timeout_ms=self.BUSY_TIMEOUT_MS,
        )
        self.conn.commit()

    # -- writes ---------------------------------------------------------------

    def add_observations(self, rows, fetched_at: datetime | None = None) -> StoreStats:
        stats = StoreStats()
        at = _iso(fetched_at or datetime.now(UTC))
        for o in rows:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO observations
                   (source, instrument_id, concept, period_end, known_at, value_text,
                    value_num, unit, currency, payload_json, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    o.source,
                    o.instrument_id,
                    o.concept,
                    o.period_end.isoformat() if o.period_end else "",
                    o.known_at.isoformat(),
                    o.value_text,
                    _num(o.value) if o.value is not None else None,
                    o.unit,
                    o.currency,
                    json.dumps(o.payload, sort_keys=True, default=str),
                    at,
                ),
            )
            stats.stored += cur.rowcount == 1
            stats.duplicates += cur.rowcount != 1
        self.conn.commit()
        return stats

    def add_events(self, rows, fetched_at: datetime | None = None) -> StoreStats:
        stats = StoreStats()
        at = _iso(fetched_at or datetime.now(UTC))
        for e in rows:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO events
                   (source, event_id, instrument_id, kind, announced_at, effective_at,
                    title, payload_json, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    e.source,
                    e.event_id,
                    e.instrument_id,
                    e.kind,
                    _iso(e.announced_at),
                    _iso(e.effective_at) if e.effective_at else None,
                    e.title,
                    json.dumps(e.payload, sort_keys=True, default=str),
                    at,
                ),
            )
            stats.stored += cur.rowcount == 1
            stats.duplicates += cur.rowcount != 1
        self.conn.commit()
        return stats

    def add_series(self, rows, fetched_at: datetime | None = None) -> StoreStats:
        stats = StoreStats()
        at = _iso(fetched_at or datetime.now(UTC))
        for p in rows:
            num = _num(p.value)
            if num is None:
                continue  # a non-finite point is not a point
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO series
                   (source, series_id, obs_date, value_text, value_num, known_at,
                    payload_json, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    p.source,
                    p.series_id,
                    p.obs_date.isoformat(),
                    str(p.value),
                    num,
                    p.known_at.isoformat(),
                    json.dumps(p.payload, sort_keys=True, default=str),
                    at,
                ),
            )
            stats.stored += cur.rowcount == 1
            stats.duplicates += cur.rowcount != 1
        self.conn.commit()
        return stats

    def add_documents(self, rows, fetched_at: datetime | None = None) -> StoreStats:
        stats = StoreStats()
        at = _iso(fetched_at or datetime.now(UTC))
        for d in rows:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO documents
                   (source, doc_id, instrument_id, kind, title, body, published_at,
                    payload_json, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    d.source,
                    d.doc_id,
                    d.instrument_id,
                    d.kind,
                    d.title,
                    d.body,
                    _iso(d.published_at),
                    json.dumps(d.payload, sort_keys=True, default=str),
                    at,
                ),
            )
            stats.stored += cur.rowcount == 1
            stats.duplicates += cur.rowcount != 1
        self.conn.commit()
        return stats

    def record_pull(
        self,
        run_id: str,
        source: str,
        status: str,
        *,
        at: datetime | None = None,
        fetched: int = 0,
        stored: int = 0,
        detail: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO pulls (run_id, at, source, status, fetched, stored, detail)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, _iso(at or datetime.now(UTC)), source, status, fetched, stored, detail[:400]),
        )
        self.conn.commit()

    # -- reads ----------------------------------------------------------------

    def last_success(self, source: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(at) AS at FROM pulls WHERE source = ? AND status = ?", (source, OK)
        ).fetchone()
        return _dt(row["at"]) if row and row["at"] else None

    def observations(
        self,
        instrument_id: str | None = None,
        concept: str | None = None,
        asof: date | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[Observation]:
        sql, where, args = ["SELECT * FROM observations"], [], []
        if instrument_id is not None:
            where.append("instrument_id = ?")
            args.append(instrument_id)
        if concept is not None:
            where.append("concept = ?")
            args.append(concept)
        if asof is not None:
            where.append("known_at <= ?")
            args.append(asof.isoformat())
        if source is not None:
            where.append("source = ?")
            args.append(source)
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY period_end DESC, known_at DESC, concept LIMIT ?")
        args.append(max(1, limit))
        return [self._to_observation(r) for r in self.conn.execute(" ".join(sql), args)]

    def latest(
        self, instrument_id: str, concept: str, asof: date | None = None
    ) -> Observation | None:
        """The most recent period's figure, as it was knowable on `asof`."""
        rows = self.observations(instrument_id, concept, asof=asof, limit=1)
        return rows[0] if rows else None

    def events(
        self,
        instrument_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
    ) -> list[EventRecord]:
        sql, where, args = ["SELECT * FROM events"], [], []
        if instrument_id is not None:
            where.append("instrument_id = ?")
            args.append(instrument_id)
        if kind is not None:
            where.append("kind = ?")
            args.append(kind)
        if since is not None:
            where.append("COALESCE(effective_at, announced_at) >= ?")
            args.append(_iso(since))
        if until is not None:
            where.append("COALESCE(effective_at, announced_at) <= ?")
            args.append(_iso(until))
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY COALESCE(effective_at, announced_at) DESC LIMIT ?")
        args.append(max(1, limit))
        return [self._to_event(r) for r in self.conn.execute(" ".join(sql), args)]

    def series(
        self,
        series_id: str,
        since: date | None = None,
        asof: date | None = None,
        limit: int = 5000,
    ) -> list[SeriesPoint]:
        """One point per observation date: the latest vintage knowable on `asof`."""
        sql, where, args = ["SELECT * FROM series WHERE series_id = ?"], [], [series_id]
        if since is not None:
            where.append("obs_date >= ?")
            args.append(since.isoformat())
        if asof is not None:
            where.append("known_at <= ?")
            args.append(asof.isoformat())
        if where:
            sql.append("AND " + " AND ".join(where))
        sql.append("ORDER BY obs_date ASC, known_at ASC")
        by_date: dict[str, SeriesPoint] = {}
        for r in self.conn.execute(" ".join(sql), args):
            by_date[r["obs_date"]] = self._to_point(r)  # later vintage overwrites
        points = [by_date[k] for k in sorted(by_date)]
        return points[-limit:]

    def series_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT series_id FROM series ORDER BY 1")]

    def documents(
        self,
        instrument_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[Document]:
        sql, where, args = ["SELECT * FROM documents"], [], []
        if instrument_id is not None:
            where.append("instrument_id = ?")
            args.append(instrument_id)
        if kind is not None:
            where.append("kind = ?")
            args.append(kind)
        if since is not None:
            where.append("published_at >= ?")
            args.append(_iso(since))
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY published_at DESC LIMIT ?")
        args.append(max(1, limit))
        return [self._to_document(r) for r in self.conn.execute(" ".join(sql), args)]

    def pulls(self, limit: int = 20, source: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM pulls", []
        if source is not None:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY at DESC LIMIT ?"
        args.append(max(1, limit))
        return list(self.conn.execute(sql, args))

    def counts(self) -> dict[str, int]:
        def one(q, *a):
            return self.conn.execute(q, a).fetchone()[0]

        return {
            "observations": one("SELECT COUNT(*) FROM observations"),
            "events": one("SELECT COUNT(*) FROM events"),
            "series_points": one("SELECT COUNT(*) FROM series"),
            "series": one("SELECT COUNT(DISTINCT series_id) FROM series"),
            "documents": one("SELECT COUNT(*) FROM documents"),
            "pulls": one("SELECT COUNT(*) FROM pulls"),
            "failed_pulls": one("SELECT COUNT(*) FROM pulls WHERE status = ?", FAILED),
        }

    # -- the bridge to A1 ------------------------------------------------------

    def as_fact_store(self, instrument_ids=None, asof: date | None = None):
        """A `core.market.pointintime.FactStore` from the observations table.

        Only observations with a numeric value AND a period end become Facts -
        a fact without a period is a snapshot, not a reported figure, and the
        point-in-time guard needs both dates to mean anything.
        """
        from core.market.pointintime import Fact, FactStore
        from markets.registry import get as market_get
        from markets.registry import mic_of

        store = FactStore()
        wanted = set(instrument_ids) if instrument_ids else None
        sql = "SELECT * FROM observations WHERE period_end <> '' AND value_num IS NOT NULL"
        args: list = []
        if asof is not None:
            sql += " AND known_at <= ?"
            args.append(asof.isoformat())
        for r in self.conn.execute(sql, args):
            iid = r["instrument_id"]
            if wanted is not None and iid not in wanted:
                continue
            try:
                standard = market_get(mic_of(iid)).accounting_standard
            except (KeyError, ValueError):
                continue
            store.add(
                Fact(
                    instrument_id=iid,
                    concept=r["concept"],
                    period_end=date.fromisoformat(r["period_end"]),
                    known_at=date.fromisoformat(r["known_at"]),
                    value=Decimal(r["value_text"]),
                    currency=r["currency"] or "",
                    accounting_standard=standard,
                    source_doc_id=f"{r['source']}:{iid}:{r['concept']}:{r['period_end']}",
                )
            )
        return store

    # -- rows -> records ------------------------------------------------------

    @staticmethod
    def _to_observation(r: sqlite3.Row) -> Observation:
        value = as_decimal(r["value_text"]) if r["value_num"] is not None else None
        return Observation(
            source=r["source"],
            instrument_id=r["instrument_id"],
            concept=r["concept"],
            known_at=date.fromisoformat(r["known_at"]),
            value=value,
            text="" if value is not None else r["value_text"],
            period_end=date.fromisoformat(r["period_end"]) if r["period_end"] else None,
            unit=r["unit"],
            currency=r["currency"],
            payload=json.loads(r["payload_json"]),
        )

    @staticmethod
    def _to_event(r: sqlite3.Row) -> EventRecord:
        return EventRecord(
            source=r["source"],
            event_id=r["event_id"],
            instrument_id=r["instrument_id"],
            kind=r["kind"],
            announced_at=_dt(r["announced_at"]),
            title=r["title"],
            effective_at=_dt(r["effective_at"]) if r["effective_at"] else None,
            payload=json.loads(r["payload_json"]),
        )

    @staticmethod
    def _to_point(r: sqlite3.Row) -> SeriesPoint:
        return SeriesPoint(
            source=r["source"],
            series_id=r["series_id"],
            obs_date=date.fromisoformat(r["obs_date"]),
            value=Decimal(r["value_text"]),
            known_at=date.fromisoformat(r["known_at"]),
            payload=json.loads(r["payload_json"]),
        )

    @staticmethod
    def _to_document(r: sqlite3.Row) -> Document:
        return Document(
            source=r["source"],
            doc_id=r["doc_id"],
            instrument_id=r["instrument_id"],
            kind=r["kind"],
            title=r["title"],
            body=r["body"],
            published_at=_dt(r["published_at"]),
            payload=json.loads(r["payload_json"]),
        )

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> FactBook:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
