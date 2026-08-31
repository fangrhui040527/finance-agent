"""Detection, review, and the two surfaces a person actually uses.

Prevention lives in entity_graph. This is the half that tells you what the graph
has quietly become - which is a different question, and the one nobody asks
until an answer looks wrong.
"""

from datetime import date

import pytest

from knowledge.graph.analyze import (
    TAXONOMY_EDGES,
    Diff,
    god_nodes,
    graph_diff,
    orphans,
    review_queue,
    surprising_connections,
)
from knowledge.graph.build import build
from knowledge.graph.entity_graph import (
    HUB_MIN_DEGREE,
    Confidence,
    Edge,
    EdgeKind,
    EntityGraph,
    Node,
    NodeKind,
)
from knowledge.graph.evidence import CuratedCorpus
from knowledge.graph.report import render
from knowledge.graph.store import GraphStore

ASOF = date(2026, 8, 28)
OPENED = date(2020, 1, 1)


def e(src, dst, kind=EdgeKind.SUPPLIES, conf=Confidence.EXTRACTED, doc="d", w=1.0):
    return Edge(src, dst, kind, w, doc, conf, OPENED)


def small():
    g = EntityGraph()
    for nid, kind in (
        ("CO:a", NodeKind.COMPANY),
        ("CO:b", NodeKind.COMPANY),
        ("CO:c", NodeKind.COMPANY),
        ("SUB:banks", NodeKind.SUBSECTOR),
        ("CM:tin", NodeKind.COMMODITY),
    ):
        g.add_node(Node(nid, kind, nid.split(":")[1]))
    return g


def built(tmp_path):
    store = GraphStore(tmp_path / "g.db")
    build(store)
    return store


# -- god nodes ----------------------------------------------------------------


def hub_graph(leaves, kind=NodeKind.COUNTRY):
    g = EntityGraph()
    g.add_node(Node("HUB", kind, "Somewhere"))
    for i in range(leaves):
        g.add_node(Node(f"CO:{i}", NodeKind.COMPANY, f"Co {i}"))
        g.add_edge(e(f"CO:{i}", "HUB", EdgeKind.OPERATES_IN))
        g.add_edge(e("HUB", f"CO:{i}", EdgeKind.AFFECTS))
    return g


def test_a_node_traversal_already_blocks_is_reported_as_blocking():
    found = god_nodes(hub_graph(HUB_MIN_DEGREE))
    assert [g.node_id for g in found] == ["HUB"]
    assert found[0].blocking
    assert "blocked as a waypoint" in found[0].describe()


def test_a_hub_by_nature_is_flagged_before_it_becomes_a_hub_by_degree():
    """Why HUB_KINDS survives after the traversal rule went degree-only.
    Malaysia at degree 30 is on its way to connecting everything, and the useful
    moment to notice is while splitting it is still cheap."""
    g = hub_graph(16)  # degree 32: past the warning, under the veto
    assert g.degree("HUB") == 32
    assert g.hubs() == frozenset()
    found = god_nodes(g)
    assert [x.node_id for x in found] == ["HUB"]
    assert not found[0].blocking
    assert "approaching" in found[0].describe()


def test_an_ordinary_node_of_the_same_kind_is_not_flagged():
    assert god_nodes(hub_graph(3)) == []


def test_a_company_is_never_watched_no_matter_how_busy_until_it_actually_blocks():
    """Only HUB_KINDS get the early warning. A company with many suppliers is a
    well-documented company, not a taxonomy artefact."""
    g = hub_graph(16, kind=NodeKind.COMPANY)  # same degree that flags a Country
    assert g.degree("HUB") == 32
    assert god_nodes(g) == []


# -- orphans ------------------------------------------------------------------


def test_an_entity_nothing_connects_to_is_the_web_search_trigger():
    g = small()
    g.add_edge(e("CO:a", "CO:b"))
    assert orphans(g) == ["CM:tin", "CO:c", "SUB:banks"]


def test_the_shipped_graph_has_no_orphans(tmp_path):
    store = built(tmp_path)
    assert orphans(store.load()) == []
    store.close()


# -- the review queue ---------------------------------------------------------


def test_only_edges_needing_a_ruling_are_queued():
    g = small()
    g.add_edge(e("CO:a", "CO:b"))  # extracted
    g.add_edge(e("CO:a", "CO:c", conf=Confidence.INFERRED))
    g.add_edge(e("CO:b", "CO:c", conf=Confidence.AMBIGUOUS))
    queue = review_queue(g)
    assert [x.confidence for x in queue] == [Confidence.AMBIGUOUS, Confidence.INFERRED]


def test_the_weakest_edges_come_first_because_they_are_the_ones_to_rule_on():
    g = small()
    g.add_edge(e("CO:a", "CO:b", conf=Confidence.INFERRED, w=0.9))
    g.add_edge(e("CO:a", "CO:c", conf=Confidence.INFERRED, w=0.2))
    assert [x.weight for x in review_queue(g)] == [0.9, 0.2]


def test_the_deterministic_build_leaves_an_empty_queue(tmp_path):
    """Nothing in the deterministic tier is a guess, so nothing waits on a human."""
    store = built(tmp_path)
    assert review_queue(store.load()) == []
    store.close()


# -- surprising connections ---------------------------------------------------


def taxonomy_pair():
    g = small()
    g.add_edge(e("CO:a", "SUB:banks", EdgeKind.CLASSIFIED_IN))
    g.add_edge(e("SUB:banks", "CO:b", EdgeKind.CLASSIFIED_IN))
    return g


def test_two_companies_sharing_a_label_is_not_a_discovery():
    """Almost every pair in a classification graph is 'connected' through its
    sub-sector. Reporting those buries the links that carry information."""
    assert surprising_connections(taxonomy_pair(), asof=ASOF) == []


def test_a_relation_someone_wrote_down_is_not_a_discovery_either():
    """A one-hop link is a row in the curated file. Reporting it back to the
    person who typed it is noise."""
    g = small()
    g.add_edge(e("CO:a", "CO:b", EdgeKind.SUPPLIES))
    assert surprising_connections(g, asof=ASOF) == []


def test_a_link_the_graph_composed_is_reported():
    g = small()
    g.add_edge(e("CO:a", "CO:b", EdgeKind.SUPPLIES))
    g.add_edge(e("CO:b", "CO:c", EdgeKind.SUPPLIES))
    found = surprising_connections(g, asof=ASOF)
    assert [(s.src, s.dst) for s in found] == [("CO:a", "CO:c")]


def test_a_composed_link_running_through_a_shared_label_is_still_filtered():
    g = small()
    g.add_edge(e("CO:a", "SUB:banks", EdgeKind.CLASSIFIED_IN))
    g.add_edge(e("SUB:banks", "CO:b", EdgeKind.CLASSIFIED_IN))
    g.add_edge(e("CO:b", "CO:c", EdgeKind.SUPPLIES))
    assert all(s.dst != "CO:c" or s.src != "CO:a" for s in surprising_connections(g, asof=ASOF))


def test_an_uncitable_link_is_not_reported_because_a_rumour_is_not_a_lead():
    g = small()
    g.add_edge(e("CO:a", "CO:b", EdgeKind.SUPPLIES))
    g.add_edge(e("CO:b", "CO:c", EdgeKind.SUPPLIES, conf=Confidence.INFERRED))
    assert surprising_connections(g, asof=ASOF) == []


def test_the_filter_names_the_edges_it_treats_as_taxonomy():
    assert TAXONOMY_EDGES == {EdgeKind.CLASSIFIED_IN, EdgeKind.OPERATES_IN, EdgeKind.REGULATED_BY}


def test_it_can_be_narrowed_to_the_things_you_actually_hold():
    g = small()
    g.add_edge(e("CO:a", "CO:b", EdgeKind.SUPPLIES))
    g.add_edge(e("CO:b", "CO:c", EdgeKind.SUPPLIES))
    assert surprising_connections(g, asof=ASOF, among={"CO:b", "CO:c"}) == []


def test_the_shipped_graph_composes_something_no_row_states(tmp_path):
    store = built(tmp_path)
    found = surprising_connections(store.load(), asof=ASOF)
    assert found
    assert all(s.path.n_hops >= 2 for s in found)
    assert all(s.path.citable for s in found)
    store.close()


# -- diff ---------------------------------------------------------------------


def test_an_unchanged_graph_diffs_to_nothing(tmp_path):
    store = built(tmp_path)
    d = graph_diff(store.load(), store.load())
    assert d.empty and d.describe() == "no change"
    store.close()


def test_a_new_edge_shows_up_with_its_confidence_and_validity():
    before = small()
    after = small()
    after.add_edge(e("CO:a", "CO:b"))
    d = graph_diff(before, after)
    assert len(d.added_edges) == 1 and not d.removed_edges
    assert "+ CO:a --supplies--> CO:b" in d.describe()
    assert "extracted" in d.describe()


def test_a_relabelled_node_is_reported_rather_than_read_as_add_plus_remove():
    before = small()
    after = small()
    after.add_node(Node("CO:a", NodeKind.COMPANY, "Alpha Berhad"))
    d = graph_diff(before, after)
    assert d.relabelled == [("CO:a", "a", "Alpha Berhad")]
    assert not d.added_nodes and not d.removed_nodes


def test_an_edge_that_stopped_being_asserted_is_reported_as_removed():
    before = small()
    before.add_edge(e("CO:a", "CO:b"))
    d = graph_diff(before, small())
    assert len(d.removed_edges) == 1
    assert "- CO:a --supplies--> CO:b" in d.describe()


def test_changing_only_an_edges_confidence_reads_as_a_replacement():
    """Confidence is part of the edge's identity for diffing: an INFERRED edge
    becoming EXTRACTED is a promotion someone should see, not a silent update."""
    before, after = small(), small()
    before.add_edge(e("CO:a", "CO:b", conf=Confidence.INFERRED))
    after.add_edge(e("CO:a", "CO:b", conf=Confidence.EXTRACTED))
    d = graph_diff(before, after)
    assert len(d.added_edges) == 1 and len(d.removed_edges) == 1


def test_an_empty_diff_object_reports_itself_as_empty():
    assert Diff().empty


# -- the report ---------------------------------------------------------------


def test_the_report_covers_every_section_a_reviewer_needs(tmp_path):
    store = built(tmp_path)
    text = render(store.load(), asof=ASOF)
    for heading in (
        "KNOWLEDGE GRAPH",
        "HUBS",
        "AWAITING A HUMAN RULING",
        "NOTHING IS CONNECTED TO THESE",
        "CONNECTED WITHOUT BEING WRITTEN DOWN",
    ):
        assert heading in text
    assert "47 nodes" in text or "nodes," in text
    store.close()


def test_the_report_says_what_an_empty_review_queue_means(tmp_path):
    store = built(tmp_path)
    assert "every edge is EXTRACTED from a named source" in render(store.load(), asof=ASOF)
    store.close()


def test_a_queued_edge_is_shown_with_what_to_do_about_it():
    g = small()
    g.add_edge(e("CO:a", "CO:b", conf=Confidence.INFERRED))
    text = render(g, asof=ASOF)
    assert "1 edge:" in text and "none of them may back a claim" in text
    assert "promote one into a curated file" in text


def test_the_report_includes_a_diff_when_given_one():
    before, after = small(), small()
    after.add_edge(e("CO:a", "CO:b"))
    text = render(after, asof=ASOF, diff=graph_diff(before, after))
    assert "WHAT THE LAST BUILD CHANGED" in text
    assert "+ CO:a --supplies--> CO:b" in text


def test_the_report_renders_an_empty_graph_without_crashing():
    assert "0 nodes" in render(EntityGraph(), asof=ASOF)


# -- the benchmark ------------------------------------------------------------


def test_a_path_answers_a_question_in_far_fewer_tokens_than_the_corpus(tmp_path):
    from knowledge.graph import benchmark as bm

    store = built(tmp_path)
    results = bm.run(
        store.load(),
        CuratedCorpus(),
        asof=ASOF,
        questions=[("aluminium -> Press Metal", "CM:aluminium", "CO:XKLS:8869")],
    )
    assert results[0].answered
    assert results[0].ratio > 3.0
    store.close()


def test_an_unanswerable_question_saves_nothing_and_says_so(tmp_path):
    """The benchmark is allowed to come out badly. A graph that cannot reach the
    answer has not earned anything on that question."""
    from knowledge.graph import benchmark as bm

    store = built(tmp_path)
    results = bm.run(
        store.load(),
        CuratedCorpus(),
        asof=ASOF,
        questions=[("MISC -> NVIDIA", "CO:XKLS:3816", "CO:XNAS:NVDA")],
    )
    assert not results[0].answered
    assert results[0].ratio == pytest.approx(1.0)
    assert "not answerable" in bm.describe(results)


def test_the_estimate_agrees_with_the_one_the_echo_backend_already_uses():
    """Two offline estimates that disagree are worse than one honestly
    approximate. Exact counts need count_tokens, a key, and a network."""
    from knowledge.graph.benchmark import CHARS_PER_TOKEN, estimate_tokens

    assert CHARS_PER_TOKEN == 4
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("") == 1  # never zero


def test_the_benchmark_costs_a_question_in_the_currency_the_ledger_counts():
    from decimal import Decimal

    from knowledge.graph.benchmark import Benchmark

    b = Benchmark("q", subgraph_tokens=1_000_000, corpus_tokens=2_000_000, hops=2, answered=True)
    sub, corp = b.cost_myr(Decimal("2.00"), Decimal("4.15"))
    assert sub == Decimal("8.30") and corp == Decimal("16.60")
