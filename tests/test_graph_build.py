"""The build: validated, reproducible, tier-scoped - and hub-aware.

The determinism test is the load-bearing one. A graph you cannot rebuild
identically is a graph whose diff you cannot review, and a diff you cannot
review is how a bad edge lives in a supply chain for a year.
"""
from datetime import date

import pytest

from knowledge.graph.build import DEFAULT_DB, build, main
from knowledge.graph.entity_graph import (
    Confidence, Edge, EdgeKind, EntityGraph, HUB_MIN_DEGREE, Node, NodeKind,
)
from knowledge.graph.extractors.base import Extractor, company_node, edge, sorted_payload
from knowledge.graph.store import DETERMINISTIC, GraphStore
from knowledge.graph.validate import ExtractionError

ASOF = date(2026, 8, 28)
OPENED = date(2020, 1, 1)


# -- the real build -----------------------------------------------------------

def built(tmp_path, name="graph.db"):
    store = GraphStore(tmp_path / name)
    report = build(store)
    return store, report


def test_the_shipped_sources_build_a_graph_with_no_network_and_no_keys(tmp_path):
    store, report = built(tmp_path)
    assert report.nodes > 30 and report.edges > 50
    assert set(report.per_extractor) == {"config_book", "sectors", "curated", "markets"}
    store.close()


def test_every_edge_the_deterministic_tier_produces_can_back_a_claim(tmp_path):
    """Nothing in the deterministic tier is a guess. The INFERRED edges arrive
    with the news layer, which is not part of the default build."""
    store, report = built(tmp_path)
    assert report.citable == report.edges
    store.close()


def test_the_market_extractor_sees_companies_the_other_extractors_declared(tmp_path):
    """It runs last on purpose: it maps companies to their market and cannot
    know which companies exist until the others have said so."""
    store, report = built(tmp_path)
    nodes, _ = report.per_extractor["markets"]
    assert nodes > 2                       # more than one country + one regulator
    g = store.load()
    assert any(e.kind is EdgeKind.OPERATES_IN for e in g.neighbours("CO:XKLS:1155"))
    store.close()


def test_a_real_multi_hop_question_gets_a_real_answer(tmp_path):
    """Aluminium moves; which of the things I hold is exposed, and through what."""
    store, _ = built(tmp_path)
    g = store.load()
    hits = dict(g.impact_of("CM:aluminium", {"CO:XKLS:8869", "CO:XKLS:1155"}, asof=ASOF))
    assert "CO:XKLS:8869" in hits
    assert "CO:XKLS:1155" not in hits       # a bank is not exposed to aluminium
    assert hits["CO:XKLS:8869"].citable
    assert "Press Metal" in hits["CO:XKLS:8869"].describe()
    store.close()


def test_the_graph_labels_companies_with_names_not_stock_codes(tmp_path):
    store, _ = built(tmp_path)
    g = store.load()
    assert g.label("CO:XKLS:1155") == "Maybank"
    assert "Maybank" in g.traverse("CO:XKLS:1155", asof=ASOF)[0].describe()
    store.close()


# -- determinism --------------------------------------------------------------

def test_building_twice_produces_a_byte_identical_database(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        with GraphStore(p) as s:
            build(s)
    assert a.read_bytes() == b.read_bytes()


def test_rebuilding_over_an_existing_database_changes_nothing(tmp_path):
    p = tmp_path / "g.db"
    with GraphStore(p) as s:
        build(s)
    before = p.read_bytes()
    with GraphStore(p) as s:
        build(s)
    assert p.read_bytes() == before


# -- the gate -----------------------------------------------------------------

class BadExtractor(Extractor):
    name = "bad"

    def extract(self):
        return {"nodes": [company_node("MYX:1155")],
                "edges": [{"source": "CO:XKLS:1155", "target": "CO:NOWHERE",
                           "relation": "supplies", "confidence": "extracted",
                           "source_doc_id": "d", "valid_from": "2020-01-01"}]}


def test_a_malformed_extractor_fails_the_build_that_produced_it(tmp_path):
    """Graphify warns and builds anyway, so bad extraction degrades its graph
    quietly for months. Here the build that fed it is the thing that fails."""
    with GraphStore(tmp_path / "g.db") as s:
        with pytest.raises(ExtractionError, match="not declared"):
            build(s, extractors=[BadExtractor()])


def test_nothing_is_written_when_one_extractor_is_malformed(tmp_path):
    """Validated in full before anything is stored, so a bad source cannot leave
    a half-built graph behind."""
    p = tmp_path / "g.db"
    with GraphStore(p) as s:
        with pytest.raises(ExtractionError):
            build(s, extractors=[BadExtractor()])
        assert s.counts() == {"nodes": 0, "edges": 0, "citable": 0, "closed": 0}


# -- parallel edges: graphify's worst reported bug ----------------------------

class PairExtractor(Extractor):
    """Two relations between the same pair, emitted in a chosen order."""

    def __init__(self, name, kinds):
        self.name = name
        self.kinds = kinds

    def extract(self):
        a, b = company_node("MYX:1155"), company_node("MYX:1023")
        return sorted_payload([a, b], [
            edge(a["id"], b["id"], k, doc=f"d:{k.value}",
                 confidence=Confidence.EXTRACTED, valid_from=OPENED)
            for k in self.kinds])


@pytest.mark.parametrize("order", [
    [EdgeKind.SUPPLIES, EdgeKind.CLASSIFIED_IN],
    [EdgeKind.CLASSIFIED_IN, EdgeKind.SUPPLIES],
])
def test_a_generic_relation_never_displaces_a_specific_one(tmp_path, order):
    """Graphify's worst reported bug: alphabetical last-write-wins silently
    rewrote 144 specific `calls` edges into generic `references`, dropping those
    call sites out of the call graph.

    That comes from collapsing to a simple graph. _out holds parallel edges, so
    both survive - and insertion order decides nothing.
    """
    with GraphStore(tmp_path / f"{order[0].value}.db") as s:
        build(s, extractors=[PairExtractor("pair", order)])
        kinds = {e.kind for e in s.load().neighbours("CO:XKLS:1155")}
    assert {EdgeKind.SUPPLIES, EdgeKind.CLASSIFIED_IN} <= kinds


# -- tiers --------------------------------------------------------------------

def test_a_deterministic_rebuild_leaves_another_tier_alone(tmp_path):
    with GraphStore(tmp_path / "g.db") as s:
        build(s)
        s.add_edge(Edge("CO:XKLS:1155", "CO:XKLS:1023", EdgeKind.EXPOSED_TO, 0.4,
                        "model:1", Confidence.INFERRED, OPENED), tier="semantic")
        before = s.counts()["edges"]
        build(s)                                   # deterministic, again
        assert s.counts()["edges"] == before
        assert len(s.load(tier="semantic").edges()) == 1
        assert all(e.confidence is Confidence.EXTRACTED
                   for e in s.load(tier=DETERMINISTIC).edges())


# -- hubs ---------------------------------------------------------------------

def hub_graph(n_leaves, hub_kind=NodeKind.COUNTRY):
    """One hub with n_leaves companies hanging off it, each joined BOTH ways -
    so the hub's degree is 2 * n_leaves, and a route in can find a route out."""
    g = EntityGraph()
    g.add_node(Node("HUB", hub_kind, "Somewhere"))
    for i in range(n_leaves):
        g.add_node(Node(f"CO:{i}", NodeKind.COMPANY, f"Co {i}"))
        g.add_edge(Edge(f"CO:{i}", "HUB", EdgeKind.OPERATES_IN, 1.0, "d",
                        Confidence.EXTRACTED, OPENED))
        g.add_edge(Edge("HUB", f"CO:{i}", EdgeKind.AFFECTS, 1.0, "d",
                        Confidence.EXTRACTED, OPENED))
    return g


def test_a_sparse_graph_declares_nothing_a_hub():
    """Without the floor, a five-node graph calls its busiest node a hub at
    degree 4 and stops answering anything."""
    assert hub_graph(3).hubs() == frozenset()


def test_a_node_with_hundreds_of_neighbours_is_a_hub():
    g = hub_graph(HUB_MIN_DEGREE)
    assert g.degree("HUB") == 2 * HUB_MIN_DEGREE
    assert g.hubs() == frozenset({"HUB"})


def test_a_traversal_may_end_at_a_hub_but_not_route_through_it():
    """'Maybank and NVDA are connected' is true via Malaysia -> ... -> United
    States, and worthless. Ending AT the country is a real answer; passing
    through it connects everything to everything."""
    g = hub_graph(HUB_MIN_DEGREE)
    reached = {p.end for p in g.traverse("CO:0", asof=ASOF)}
    assert reached == {"HUB"}               # the hub itself, and nothing beyond


def test_the_seed_is_exempt_because_asking_about_a_hub_is_a_real_question():
    g = hub_graph(HUB_MIN_DEGREE)
    reached = {p.end for p in g.traverse("HUB", asof=ASOF)}
    assert len(reached) == HUB_MIN_DEGREE


def test_a_sector_with_three_members_is_still_a_path(tmp_path):
    """Why the rule is degree and not kind. The repo's canonical exposure path
    is event -> Sector -> your holding; vetoing Sector by kind would delete the
    exact question the graph exists to answer."""
    store, _ = built(tmp_path)
    g = store.load()
    assert g.hubs() == frozenset()
    hits = dict(g.impact_of("CM:crude_oil", {"CO:XKLS:5183"}, asof=ASOF))
    assert "CO:XKLS:5183" in hits
    store.close()


def test_the_hub_set_is_recomputed_when_an_edge_arrives():
    g = hub_graph((HUB_MIN_DEGREE - 2) // 2)          # degree 48, just under
    assert g.degree("HUB") == HUB_MIN_DEGREE - 2
    assert g.hubs() == frozenset()
    g.add_node(Node("CO:extra", NodeKind.COMPANY))
    g.add_edge(Edge("CO:extra", "HUB", EdgeKind.OPERATES_IN, 1.0, "d",
                    Confidence.EXTRACTED, OPENED))
    g.add_edge(Edge("HUB", "CO:extra", EdgeKind.AFFECTS, 1.0, "d",
                    Confidence.EXTRACTED, OPENED))
    assert g.degree("HUB") == HUB_MIN_DEGREE
    assert "HUB" in g.hubs()


def test_an_empty_graph_has_no_hubs():
    assert EntityGraph().hubs() == frozenset()


# -- the cli ------------------------------------------------------------------

def test_the_cli_writes_a_database_and_reports_what_it_built(tmp_path, capsys):
    p = tmp_path / "cli.db"
    assert main(["--db", str(p)]) == 0
    out = capsys.readouterr().out
    assert p.exists()
    assert "TOTAL" in out and "citable" in out and str(p) in out


def test_rebuild_starts_from_an_empty_file(tmp_path):
    """Edges are append-only, so a rebuild that must not inherit a closed edge
    needs a new file rather than a delete the store would refuse."""
    p = tmp_path / "cli.db"
    main(["--db", str(p)])
    with GraphStore(p) as s:
        s.close_edge("CO:XKLS:1155", "CO:XKLS:1023", EdgeKind.COMPETES_WITH,
                     OPENED, date(2026, 1, 1))
        assert s.counts()["closed"] == 1
    main(["--db", str(p), "--rebuild"])
    with GraphStore(p) as s:
        assert s.counts()["closed"] == 0


def test_the_default_output_path_is_gitignored_data():
    assert DEFAULT_DB.startswith("data/")


# -- the whole loop, on the shipped data --------------------------------------

def a7_over(graph, corpus):
    from agents.base import AgentContext
    from agents.evidence.agents import A7SectorTechnology
    from core.guardrails.defaults import default_engine
    from knowledge.retrieval.pipeline import Router
    ctx = AgentContext(router=Router({}),
                       engine=default_engine({"a7_sector_technology": {"traverse"}}))
    return A7SectorTechnology(ctx, graph, corpus.citation)


def test_the_built_graph_produces_an_answer_a_user_could_actually_read(tmp_path):
    """The whole phase in one test: checked-in yaml -> extractor -> validator ->
    store -> traversal -> citation -> output gate -> answered.

    Every link in that chain was missing at the start of phase 1, and the graph
    could not emit a claim in any configuration. This is the end of the loop.
    """
    from knowledge.graph.evidence import CuratedCorpus
    corpus = CuratedCorpus()
    store, _ = built(tmp_path)
    agent = a7_over(store.load(), corpus)

    findings = agent.run("CM:aluminium", {"CO:XKLS:8869"}, asof=ASOF)
    answer = agent.emit(findings, corpus.chunk, confidence=0.5)

    assert answer.answered is True
    assert not answer.dropped
    claim = answer.claims[0]
    assert "Press Metal is exposed via" in claim.text
    assert claim.citations[0].chunk_id == "curated:supply_chain#presmetal-aluminium"
    assert "Primary aluminium smelting" in claim.citations[0].quoted_span
    store.close()


def test_a_curated_row_edited_after_a_claim_cited_it_fails_verification(tmp_path):
    """Which is the point of rechecking the span rather than trusting the graph.
    The row is the document; change the document and the citation stops
    verifying, exactly as an edited filing chunk would."""
    from knowledge.graph.evidence import CuratedCorpus
    corpus = CuratedCorpus()
    store, _ = built(tmp_path)
    agent = a7_over(store.load(), corpus)
    findings = agent.run("CM:aluminium", {"CO:XKLS:8869"}, asof=ASOF)

    edited = {"curated:supply_chain#presmetal-aluminium": "Something else entirely."}
    answer = agent.emit(findings, lambda s, c: edited.get(c), confidence=0.5)
    assert answer.answered is False
    store.close()


def test_a_curated_citation_can_never_outrank_a_filing():
    """METHOD_KB sits four tiers below FILINGS. A relationship a person wrote
    down must not overrule what the company said about itself."""
    from core.contracts.answer import TrustTier
    from knowledge.graph.evidence import TRUST
    assert TRUST is TrustTier.METHOD_KB
    assert TRUST > TrustTier.FILINGS      # higher value = lower trust


def test_the_corpus_covers_every_document_the_extractors_cite(tmp_path):
    """The integrity check. An extractor minting a doc id the corpus cannot
    produce is a path that silently stops being citable."""
    from knowledge.graph.evidence import CuratedCorpus
    corpus = CuratedCorpus()
    store, _ = built(tmp_path)
    missing = sorted({
        e.source_doc_id for e in store.load().edges()
        if e.source_doc_id.startswith("curated:")
        and corpus.citation(e.source_doc_id) is None})
    assert not missing, f"cited but not in the corpus: {missing}"
    store.close()


def test_an_unknown_document_id_yields_nothing_rather_than_an_empty_citation():
    from knowledge.graph.evidence import CuratedCorpus
    corpus = CuratedCorpus()
    assert corpus.citation("curated:supply_chain#never-written") is None
    assert corpus.chunk("kb_filings", "curated:sectors#CO:XKLS:1155") is None


def test_the_corpus_is_empty_rather_than_broken_when_the_files_are_missing(tmp_path):
    from knowledge.graph.evidence import CuratedCorpus
    assert len(CuratedCorpus(tmp_path / "none.yaml", tmp_path / "gone.yaml")) == 0


# -- pruning through the build ------------------------------------------------

class OneEdge(Extractor):
    name = "pair"

    def __init__(self, emit: bool):
        self.emit = emit

    def extract(self):
        a, b = company_node("MYX:1155"), company_node("MYX:1023")
        rows = [edge(a["id"], b["id"], EdgeKind.COMPETES_WITH, doc="d",
                     confidence=Confidence.EXTRACTED, valid_from=OPENED)] if self.emit else []
        return sorted_payload([a, b], rows)


def test_a_source_row_that_disappears_is_closed_by_the_next_build(tmp_path):
    with GraphStore(tmp_path / "g.db") as s:
        build(s, extractors=[OneEdge(True)], skip_markets=True)
        assert s.counts()["edges"] == 1
        report = build(s, extractors=[OneEdge(False)], skip_markets=True,
                       prune_on=date(2026, 6, 1))
        assert len(report.closed) == 1
        assert s.counts() == {"nodes": 2, "edges": 1, "citable": 1, "closed": 1}
        assert "closed" in report.describe()


def test_without_prune_a_deleted_row_stays_asserted_forever(tmp_path):
    """Why prune exists. The default is still no-prune, because closing an edge
    is a claim about the world and a build should not make it by accident."""
    with GraphStore(tmp_path / "g.db") as s:
        build(s, extractors=[OneEdge(True)], skip_markets=True)
        report = build(s, extractors=[OneEdge(False)], skip_markets=True)
        assert report.closed == []
        assert s.counts()["closed"] == 0


def test_pruning_the_real_build_closes_nothing_when_the_sources_are_unchanged(tmp_path):
    with GraphStore(tmp_path / "g.db") as s:
        build(s)
        assert build(s, prune_on=date(2026, 6, 1)).closed == []
