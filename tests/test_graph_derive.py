"""data/graph.db rebuilt from the corpus: deterministic, incremental, and
never a second copy of a story the corpus refused.

The spine comes from the repository's own config and yaml, as `make graph`
builds it; the events from a tmp_path corpus. Offline throughout.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from knowledge import stores
from knowledge.corpus import Corpus
from knowledge.graph import derive
from knowledge.graph.store import GraphStore
from knowledge.news.features import Article

PUBLISHED = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def _article(doc_id: str, title: str, instruments: list[str], key: str = "") -> Article:
    return Article(
        doc_id=doc_id,
        title=title,
        body="",
        source_domain="example.com",
        published_at=PUBLISHED,
        instruments=instruments,
        dup_hash="h-" + doc_id,
        title_key=key or "k-" + doc_id,
    )


ROWS = [
    _article("a", "TNB restores power within 77 minutes after Johor substation fire", ["MYX:5347"]),
    _article("b", "Maybank profit rose on wider margins", ["MYX:1155"]),
    _article("c", "Malaysian ringgit steady in early trade", []),
    # Stored under Tenaga before the rule that excludes it merged (2026-10-18).
    _article("d", "Half Yearly Results, Half yearly Earnings for Shree TNB Polymers", ["MYX:5347"]),
]
MORE = [
    _article("e", "Maybank and TNB both rise as the index firms", ["MYX:1155", "MYX:5347"]),
    _article("f", "Tenaga Nasional plans a new grid project", ["MYX:5347"]),
]


def _corpus(path: Path, rows) -> Path:
    with Corpus(path) as c:
        for a in rows:
            c.add(a, "fixture")
    return path


def _sets(path: Path):
    con = sqlite3.connect(path)
    try:
        nodes = set(con.execute("SELECT node_id, kind, label FROM nodes"))
        edges = set(
            con.execute(
                "SELECT src, dst, kind, valid_from, valid_to, confidence, source_doc_id FROM edges"
            )
        )
    finally:
        con.close()
    return nodes, edges


def _events(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT node_id FROM nodes WHERE kind = 'Event'")}
    finally:
        con.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def corpus(tmp_path) -> Path:
    return _corpus(tmp_path / "data" / "corpus.db", ROWS)


def test_a_rebuild_is_byte_identical_and_does_not_depend_on_the_date(corpus, tmp_path, monkeypatch):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    derive.build_from_corpus(corpus, a)

    import knowledge.graph.build as build_mod

    class _Later(date):
        @classmethod
        def today(cls):
            return date(2027, 6, 30)

    monkeypatch.setattr(build_mod, "date", _Later)
    derive.build_from_corpus(corpus, b)
    assert _sha(a) == _sha(b)


def test_events_come_from_linked_rows_only_and_every_one_is_inferred(corpus, tmp_path):
    out = tmp_path / "g.db"
    derive.build_from_corpus(corpus, out)
    events = _events(out)
    assert len(events) == 2, events  # a and b; c names no one; d is re-checked away
    with GraphStore(out) as store:
        g = store.load()
    affects = [e for e in g.edges() if e.kind.value == "affects" and e.src in events]
    assert len(affects) == 2 and all(not e.citable for e in affects)
    assert all(e.valid_to is not None for e in affects), "an event edge is born closed"


def test_a_link_todays_rules_exclude_does_not_reach_the_graph(corpus, tmp_path):
    """The digest reads stored links by today's rules (#92); the graph read the
    raw column, so a story the rules now exclude stayed linked there forever."""
    out = tmp_path / "g.db"
    derive.build_from_corpus(corpus, out)
    con = sqlite3.connect(out)
    linked = {r[0] for r in con.execute("SELECT source_doc_id FROM edges WHERE kind = 'affects'")}
    con.close()
    assert "d" not in linked and {"a", "b"} <= linked


def test_sync_from_a_watermark_gives_the_graph_a_rebuild_gives(corpus, tmp_path):
    graph = tmp_path / "data" / "graph.db"
    assert "rebuilt" in derive.sync(graph, corpus)
    assert derive.sync(graph, corpus) == "current"
    _corpus(corpus, MORE)
    assert derive.sync(graph, corpus).startswith("+")

    full = tmp_path / "full.db"
    derive.build_from_corpus(corpus, full)
    assert _sets(graph) == _sets(full)


def test_changed_inputs_rebuild_the_graph(corpus, tmp_path, monkeypatch):
    graph = tmp_path / "data" / "graph.db"
    derive.sync(graph, corpus)
    monkeypatch.setattr(derive, "DERIVE_VERSION", derive.DERIVE_VERSION + 1)
    assert "rebuilt" in derive.sync(graph, corpus)


def test_a_corpus_that_is_not_the_one_derived_from_rebuilds(corpus, tmp_path):
    graph = tmp_path / "data" / "graph.db"
    derive.sync(graph, corpus)
    other = _corpus(tmp_path / "other" / "corpus.db", MORE)
    assert "rebuilt" in derive.sync(graph, other)
    assert _events(graph) == _events(_built(other, tmp_path / "check.db"))


def _built(corpus: Path, out: Path) -> Path:
    derive.build_from_corpus(corpus, out)
    return out


def test_a_graph_this_module_did_not_write_is_never_taken_over(corpus, tmp_path):
    graph = tmp_path / "data" / "graph.db"
    with GraphStore(graph):
        pass
    before = _sha(graph)
    assert not derive.wanted(graph, corpus)
    with pytest.raises(derive.DeriveError, match="not derived here"):
        derive.sync(graph, corpus)
    assert _sha(graph) == before


def test_a_graph_holding_another_tier_is_not_replaced(corpus, tmp_path):
    graph = tmp_path / "data" / "graph.db"
    derive.sync(graph, corpus)
    con = sqlite3.connect(graph)
    con.execute(
        "INSERT INTO nodes (node_id, kind, label, metadata_json, tier) "
        "VALUES ('X:1', 'company', 'x', '{}', 'semantic')"
    )
    con.commit()
    con.close()
    with pytest.raises(derive.DeriveError, match="non-deterministic"):
        derive.rebuild(graph, corpus)


def test_the_graph_is_derived_only_where_git_carries_the_corpus_as_parts(corpus, tmp_path):
    graph = tmp_path / "data" / "graph.db"
    assert not derive.wanted(graph, corpus), "no parts: a missing graph stays missing"
    assert derive.ready(graph, corpus) is False and not graph.exists()
    stores.split(corpus)
    assert derive.wanted(graph, corpus)
    assert derive.ready(graph, corpus) is True and derive.derived(graph)


def test_the_spine_build_refuses_a_derived_graph(corpus, tmp_path, capsys):
    from knowledge.graph.build import main

    graph = tmp_path / "data" / "graph.db"
    derive.sync(graph, corpus)
    before = _sha(graph)
    assert main(["--db", str(graph), "--prune"]) == 2
    assert "stores graph" in capsys.readouterr().err
    assert _sha(graph) == before


def test_a_sweep_links_what_the_corpus_kept_and_not_its_refusals(tmp_path):
    """2,542 of the committed graph's 15,898 events were second copies: the
    sweep linked every normalised article, the ones the corpus refused as
    already held too. A story held under one doc_id must not become a second
    event under another."""
    from knowledge.sweep import _link_graph

    graph = tmp_path / "graph.db"
    corpus = tmp_path / "corpus.db"
    held = _article("x1", "Maybank wins a bond award", ["MYX:1155"], key="same")
    again = _article("x2", "Maybank wins a bond award", ["MYX:1155"], key="same")
    with Corpus(corpus) as c:
        first = c.add_all([held], "fixture")
        second = c.add_all([again], "fixture")
    assert [a.doc_id for a in first.added] == ["x1"]
    assert second.added == [] and second.duplicates == 1
    _link_graph(first.added + second.added, str(graph), str(corpus))
    assert len(_events(graph)) == 1
    con = sqlite3.connect(graph)
    docs = {r[0] for r in con.execute("SELECT source_doc_id FROM edges WHERE kind = 'affects'")}
    con.close()
    assert docs == {"x1"}
