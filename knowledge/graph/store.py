"""Durable entity graph.

Same shape and the same guarantees as core/provenance/ledger.py, for the same
reason: a graph that dies with the process cannot be reviewed, diffed against
yesterday's, or asked what it looked like last quarter.

Two properties carried over deliberately:

  1. WAL, via the ledger's own _enable_wal - so a build running in one process
     and a query running in another do not lock each other out. The ordering
     subtlety (busy_timeout FIRST) is documented there and is not repeated here;
     reusing the helper is how it stays fixed in one place.

  2. Append-only edges. A relationship that ends is CLOSED by setting valid_to,
     never deleted, so "what did we believe on 2026-03-01" stays answerable. A
     trigger enforces it: the only permitted update is opening -> closed, once.
     Deleting an edge would rewrite history, and every conclusion the system
     ever drew through that edge would become unauditable.

`tier` marks what produced a row - 'deterministic' now, 'semantic' when a model
tier arrives. Re-extraction replaces only its own tier, so a deterministic
rebuild cannot silently wipe model-proposed edges and vice versa. Cheap to carry
now and expensive to retrofit, which is why it is here before anything writes it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path as FsPath

from core.provenance.ledger import _enable_wal, apply_schema
from knowledge.graph.entity_graph import (
    Confidence,
    Edge,
    EdgeKind,
    EntityGraph,
    Node,
    NodeKind,
)

DETERMINISTIC = "deterministic"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id       TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tier          TEXT NOT NULL DEFAULT 'deterministic'
);
CREATE TABLE IF NOT EXISTS edges (
    src           TEXT NOT NULL,
    dst           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    weight        REAL NOT NULL,
    confidence    TEXT NOT NULL,
    source_doc_id TEXT,
    tier          TEXT NOT NULL DEFAULT 'deterministic',
    PRIMARY KEY (src, dst, kind, valid_from)
);
CREATE TRIGGER IF NOT EXISTS edges_close_only
BEFORE UPDATE ON edges
WHEN OLD.valid_to IS NOT NULL
  OR NEW.valid_to IS NULL
  OR OLD.src IS NOT NEW.src OR OLD.dst IS NOT NEW.dst
  OR OLD.kind IS NOT NEW.kind OR OLD.valid_from IS NOT NEW.valid_from
  OR OLD.weight IS NOT NEW.weight OR OLD.confidence IS NOT NEW.confidence
  OR OLD.source_doc_id IS NOT NEW.source_doc_id OR OLD.tier IS NOT NEW.tier
BEGIN
  SELECT RAISE(ABORT, 'graph edges are append-only: an open edge may only be closed by setting valid_to');
END;
CREATE TRIGGER IF NOT EXISTS edges_no_delete
BEFORE DELETE ON edges
BEGIN
  SELECT RAISE(ABORT, 'graph edges are append-only: close an edge with valid_to instead of deleting it');
END;
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, valid_from);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, valid_from);
CREATE INDEX IF NOT EXISTS edges_tier ON edges(tier);
"""


class EdgeNotOpen(ValueError):
    """close_edge was given an edge that is absent, or already closed."""


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _from_iso(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


class GraphStore:
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: FsPath | str = ":memory:") -> None:
        path = str(path)
        if path != ":memory:":
            FsPath(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        apply_schema(self.conn, SCHEMA, INDEXES, timeout_ms=self.BUSY_TIMEOUT_MS)
        self.conn.commit()

    # -- writes ---------------------------------------------------------------

    def add_node(self, node: Node, tier: str = DETERMINISTIC) -> None:
        """Upsert, MERGING metadata, and writing nothing when nothing changed.

        A node is an identity, not an assertion - relabelling is not rewriting
        history, so unlike edges this may change in place. But several
        extractors legitimately describe the same node from different angles:
        the book knows it is held, the market registry knows its MIC. Replacing
        metadata wholesale makes the node's attributes depend on which extractor
        happened to run last, and `held` disappears the moment the registry
        touches it.

        Writing nothing when nothing changed is what keeps a rebuild over
        unchanged sources byte-identical. A file that changes when the graph did
        not makes every graph diff unreviewable, which is the reason to have a
        stored graph at all.
        """
        row = self.conn.execute(
            "SELECT kind, label, metadata_json, tier FROM nodes WHERE node_id = ?",
            (node.node_id,),
        ).fetchone()
        metadata = dict(node.metadata)
        if row is not None:
            merged = json.loads(row[2])
            merged.update(metadata)
            metadata = merged
        payload = (
            node.kind.value,
            node.label or (row[1] if row else ""),
            json.dumps(metadata, sort_keys=True),
            tier,
        )
        if row is not None and tuple(row) == payload:
            return
        self.conn.execute(
            "INSERT INTO nodes (node_id, kind, label, metadata_json, tier) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET "
            "kind=excluded.kind, label=excluded.label, "
            "metadata_json=excluded.metadata_json, tier=excluded.tier",
            (node.node_id, *payload),
        )
        self.conn.commit()

    def add_edge(self, edge: Edge, tier: str = DETERMINISTIC) -> None:
        """Insert, or leave the existing row alone.

        Idempotent by (src, dst, kind, valid_from) so a rebuild over unchanged
        sources produces an unchanged database - which is the determinism test.
        It also means a re-run cannot overwrite an edge that has since been
        closed, because the closed row wins by being already there.
        """
        for nid in (edge.src, edge.dst):
            if not self._node_exists(nid):
                raise KeyError(f"node {nid!r} must be stored before an edge referencing it")
        self.conn.execute(
            "INSERT OR IGNORE INTO edges "
            "(src, dst, kind, valid_from, valid_to, weight, confidence, source_doc_id, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                edge.src,
                edge.dst,
                edge.kind.value,
                _iso(edge.valid_from) or "",
                _iso(edge.valid_to),
                edge.weight,
                edge.confidence.value,
                edge.source_doc_id,
                tier,
            ),
        )
        self.conn.commit()

    def close_edge(
        self, src: str, dst: str, kind: EdgeKind, valid_from: date | None, valid_to: date
    ) -> None:
        """End a relationship. The only mutation this store permits."""
        cur = self.conn.execute(
            "UPDATE edges SET valid_to = ? "
            "WHERE src = ? AND dst = ? AND kind = ? AND valid_from = ? AND valid_to IS NULL",
            (_iso(valid_to), src, dst, kind.value, _iso(valid_from) or ""),
        )
        if cur.rowcount == 0:
            raise EdgeNotOpen(
                f"no open {kind.value} edge {src} -> {dst} opening "
                f"{valid_from}; it is absent or already closed"
            )
        self.conn.commit()

    def close_missing(self, tier: str, keep, on: date) -> list[tuple]:
        """Close every OPEN edge in `tier` that `keep` no longer contains.

        This is the half of "re-extraction replaces only its own tier" that the
        module docstring promised and nothing implemented. Without it a curated
        row deleted from the yaml left its edge asserted forever, and the only
        way to drop it was `--rebuild`, which deletes the whole file - taking
        every other tier with it, including a semantic one that no deterministic
        source could reproduce.

        CLOSED, never deleted. A relationship the sources stopped asserting on a
        given date is exactly what valid_to records, so "what did we believe in
        March" survives a source being corrected. Deleting would make every
        conclusion drawn through that edge unauditable, which is the whole
        reason edges are append-only.

        Returns the keys it closed, so a build can report them.
        """
        wanted = {(e.src, e.dst, e.kind.value, _iso(e.valid_from) or "") for e in keep}
        rows = self.conn.execute(
            "SELECT src, dst, kind, valid_from FROM edges WHERE tier = ? AND valid_to IS NULL",
            (tier,),
        ).fetchall()
        closed = []
        for row in rows:
            if tuple(row) in wanted:
                continue
            if row[3] and date.fromisoformat(row[3]) >= on:
                # An edge that opens on or after the closing date would become an
                # interval containing no days, which Edge refuses to construct.
                continue
            self.conn.execute(
                "UPDATE edges SET valid_to = ? "
                "WHERE src = ? AND dst = ? AND kind = ? AND valid_from = ?",
                (_iso(on), *row),
            )
            closed.append(tuple(row))
        self.conn.commit()
        return closed

    # -- reads ----------------------------------------------------------------

    def _node_exists(self, node_id: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
            is not None
        )

    def load(self, tier: str | None = None) -> EntityGraph:
        """Rebuild the in-memory graph. Deterministic order, so a dump is stable.

        `tier` filters EDGES only. Every node loads regardless, because a node is
        an identity and an edge is an assertion - which tier first observed that
        Maybank exists says nothing about who may reference it. Filtering nodes
        too would orphan every cross-tier edge, and a semantic edge between two
        deterministically-discovered companies is the normal case, not the
        exception.
        """
        g = EntityGraph()
        eq = (
            "SELECT src, dst, kind, valid_from, valid_to, weight, confidence, "
            "source_doc_id FROM edges"
        )
        args: tuple = ()
        if tier is not None:
            eq += " WHERE tier = ?"
            args = (tier,)
        for nid, kind, label, meta in self.conn.execute(
            "SELECT node_id, kind, label, metadata_json FROM nodes ORDER BY node_id"
        ):
            g.add_node(Node(nid, NodeKind(kind), label, json.loads(meta)))
        rows = self.conn.execute(eq + " ORDER BY src, dst, kind, valid_from", args)
        for src, dst, kind, vf, vt, weight, conf, doc in rows:
            g.add_edge(
                Edge(
                    src,
                    dst,
                    EdgeKind(kind),
                    weight,
                    doc,
                    Confidence(conf),
                    _from_iso(vf),
                    _from_iso(vt),
                )
            )
        return g

    def live_edges(self, on: date) -> list[Edge]:
        """What the graph asserted on one date. The as-of read, done in SQL."""
        rows = self.conn.execute(
            "SELECT src, dst, kind, valid_from, valid_to, weight, confidence, source_doc_id "
            "FROM edges WHERE valid_from != '' AND valid_from <= ? "
            "AND (valid_to IS NULL OR valid_to > ?) "
            "ORDER BY src, dst, kind, valid_from",
            (_iso(on), _iso(on)),
        )
        return [
            Edge(s, d, EdgeKind(k), w, doc, Confidence(c), _from_iso(vf), _from_iso(vt))
            for s, d, k, vf, vt, w, c, doc in rows
        ]

    def counts(self) -> dict[str, int]:
        n = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        e = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        citable = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE confidence = ? "
            "AND source_doc_id IS NOT NULL AND TRIM(source_doc_id) != ''",
            (Confidence.EXTRACTED.value,),
        ).fetchone()[0]
        closed = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE valid_to IS NOT NULL"
        ).fetchone()[0]
        return {"nodes": n, "edges": e, "citable": citable, "closed": closed}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
