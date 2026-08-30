"""Durable graph: append-only edges, as-of reads, and a reproducible build.

The append-only property is the one that matters. A graph you can edit in place
cannot answer "what did we believe on 2026-03-01", so every conclusion drawn
through a since-changed edge becomes unauditable - the same reason the
provenance ledger refuses UPDATE and DELETE.
"""
import sqlite3
from datetime import date

import pytest

from knowledge.graph.entity_graph import (
    Confidence, Edge, EdgeKind, EntityGraph, Node, NodeKind,
)
from knowledge.graph.store import DETERMINISTIC, EdgeNotOpen, GraphStore

OPENED = date(2026, 1, 1)
ASOF = date(2026, 8, 25)


def store_with(*, path=":memory:"):
    s = GraphStore(path)
    for nid, kind, lbl in [("CO:a", NodeKind.COMPANY, "Alpha"),
                           ("CO:b", NodeKind.COMPANY, "Beta"),
                           ("SEC:s", NodeKind.SECTOR, "Shipping")]:
        s.add_node(Node(nid, kind, lbl, {"mic": "XKLS"}))
    s.add_edge(Edge("CO:a", "CO:b", EdgeKind.SUPPLIES, 0.9, "doc:1",
                    Confidence.EXTRACTED, OPENED))
    s.add_edge(Edge("SEC:s", "CO:a", EdgeKind.CLASSIFIED_IN, 1.0, "doc:2",
                    Confidence.INFERRED, OPENED))
    return s


def test_a_stored_graph_reloads_with_everything_that_makes_an_edge_citable():
    g = store_with().load()
    assert isinstance(g, EntityGraph)
    e = g.neighbours("CO:a")[0]
    assert (e.kind, e.weight, e.source_doc_id, e.confidence, e.valid_from) == (
        EdgeKind.SUPPLIES, 0.9, "doc:1", Confidence.EXTRACTED, OPENED)
    assert e.citable
    assert not g.neighbours("SEC:s")[0].citable   # inferred survives the round trip


def test_node_metadata_and_labels_survive_the_round_trip():
    g = store_with().load()
    n = g.node("CO:a")
    assert n.label == "Alpha" and n.metadata == {"mic": "XKLS"} and n.kind is NodeKind.COMPANY


def test_an_edge_cannot_be_stored_before_the_nodes_it_joins():
    s = GraphStore()
    with pytest.raises(KeyError, match="must be stored before"):
        s.add_edge(Edge("CO:ghost", "CO:other", EdgeKind.SUPPLIES, 1.0, "d",
                        Confidence.EXTRACTED, OPENED))


# -- append-only -------------------------------------------------------------

def test_an_edge_cannot_be_deleted():
    s = store_with()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.conn.execute("DELETE FROM edges")


def test_an_edge_cannot_be_rewritten_in_place():
    s = store_with()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.conn.execute("UPDATE edges SET weight = 0.1 WHERE src = 'CO:a'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.conn.execute("UPDATE edges SET source_doc_id = 'doc:invented'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.conn.execute("UPDATE edges SET confidence = 'extracted' WHERE src = 'SEC:s'")


def test_a_relationship_that_ends_is_closed_rather_than_removed():
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    assert s.counts() == {"nodes": 3, "edges": 2, "citable": 1, "closed": 1}
    e = s.load().neighbours("CO:a")[0]
    assert e.valid_to == date(2026, 6, 1)


def test_a_closed_edge_still_answers_a_question_asked_while_it_was_open():
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    g = s.load()
    assert g.traverse("CO:a", asof=date(2026, 3, 1), target="CO:b") != []
    assert g.traverse("CO:a", asof=ASOF, target="CO:b") == []


def test_an_edge_can_only_be_closed_once():
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    with pytest.raises(EdgeNotOpen, match="already closed"):
        s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 7, 1))


def test_closing_an_edge_that_does_not_exist_is_an_error_not_a_silent_noop():
    with pytest.raises(EdgeNotOpen, match="absent or already closed"):
        store_with().close_edge("CO:b", "CO:a", EdgeKind.SUPPLIES, OPENED, ASOF)


def test_a_closed_edge_cannot_be_reopened():
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        s.conn.execute("UPDATE edges SET valid_to = NULL WHERE src = 'CO:a'")


def test_a_rerun_cannot_reopen_an_edge_that_has_since_been_closed():
    """Idempotent insert means the closed row wins by already being there."""
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    s.add_edge(Edge("CO:a", "CO:b", EdgeKind.SUPPLIES, 0.9, "doc:1",
                    Confidence.EXTRACTED, OPENED))
    assert s.load().neighbours("CO:a")[0].valid_to == date(2026, 6, 1)


# -- as-of reads -------------------------------------------------------------

def test_live_edges_answers_what_the_graph_asserted_on_one_date():
    s = store_with()
    s.close_edge("CO:a", "CO:b", EdgeKind.SUPPLIES, OPENED, date(2026, 6, 1))
    assert len(s.live_edges(date(2026, 3, 1))) == 2
    assert [e.src for e in s.live_edges(ASOF)] == ["SEC:s"]
    assert s.live_edges(date(2025, 12, 31)) == []


def test_an_edge_with_no_start_date_is_stored_but_never_live():
    s = store_with()
    s.add_edge(Edge("CO:b", "SEC:s", EdgeKind.CLASSIFIED_IN, 1.0, "d",
                    Confidence.INFERRED))
    assert s.counts()["edges"] == 3
    assert all(e.src != "CO:b" for e in s.live_edges(ASOF))


# -- determinism and tiers ---------------------------------------------------

def test_building_the_same_graph_twice_produces_the_same_database(tmp_path):
    """The determinism guarantee, in the currency that matters: bytes."""
    first = tmp_path / "a.db"
    second = tmp_path / "b.db"
    for p in (first, second):
        s = store_with(path=str(p))
        s.add_edge(Edge("CO:a", "CO:b", EdgeKind.SUPPLIES, 0.9, "doc:1",
                        Confidence.EXTRACTED, OPENED))     # a redundant re-run
        s.close()
    assert first.read_bytes() == second.read_bytes()


def test_reloading_is_ordered_so_a_dump_is_stable():
    g = store_with().load()
    assert [n.node_id for n in g.nodes()] == ["CO:a", "CO:b", "SEC:s"]
    assert [(e.src, e.dst) for e in g.edges()] == [("CO:a", "CO:b"), ("SEC:s", "CO:a")]


def test_a_deterministic_rebuild_does_not_wipe_another_tiers_edges():
    """Why `tier` is carried before anything writes it. When a model tier
    arrives, a deterministic rebuild must not delete what it proposed."""
    s = store_with()
    s.add_edge(Edge("CO:b", "SEC:s", EdgeKind.EXPOSED_TO, 0.5, "doc:9",
                    Confidence.INFERRED, OPENED), tier="semantic")
    assert s.counts()["edges"] == 3
    assert len(s.load(tier=DETERMINISTIC).edges()) == 2
    assert len(s.load(tier="semantic").edges()) == 1
    assert len(s.load().edges()) == 3


def test_a_node_may_be_relabelled_because_identity_is_not_an_assertion():
    s = store_with()
    s.add_node(Node("CO:a", NodeKind.COMPANY, "Alpha Berhad", {"mic": "XKLS"}))
    assert s.load().node("CO:a").label == "Alpha Berhad"
    assert s.counts()["nodes"] == 3


# -- durability --------------------------------------------------------------

def test_the_graph_outlives_the_process(tmp_path):
    p = tmp_path / "graph.db"
    store_with(path=str(p)).close()
    with GraphStore(str(p)) as reopened:
        assert reopened.counts()["edges"] == 2
        assert reopened.load().neighbours("CO:a")[0].citable


def test_a_file_backed_store_uses_wal_so_a_build_and_a_query_can_overlap(tmp_path):
    p = tmp_path / "graph.db"
    with GraphStore(str(p)) as s:
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_counts_separates_what_can_back_a_claim_from_what_merely_exists():
    assert store_with().counts() == {"nodes": 3, "edges": 2, "citable": 1, "closed": 0}
