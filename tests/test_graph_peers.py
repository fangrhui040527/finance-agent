"""Peers are read off the graph, as of a date, with the edge document behind each one."""

from __future__ import annotations

from datetime import date

from agents.base import AgentContext
from agents.evidence.agents import A7SectorTechnology
from core.guardrails.defaults import default_engine
from knowledge.graph.build import build
from knowledge.graph.entity_graph import Confidence, Edge, EdgeKind, EntityGraph, Node, NodeKind
from knowledge.graph.evidence import CuratedCorpus
from knowledge.graph.peers import book_id, peers_of
from knowledge.graph.store import GraphStore
from knowledge.retrieval.pipeline import Router

ASOF = date(2026, 8, 28)
OPENED = date(2020, 1, 1)


def _node(nid: str, kind: NodeKind) -> Node:
    return Node(nid, kind, nid.split(":")[-1])


def small() -> EntityGraph:
    g = EntityGraph()
    for nid, kind in (
        ("CO:XKLS:1155", NodeKind.COMPANY),
        ("CO:XKLS:1023", NodeKind.COMPANY),
        ("CO:XKLS:1295", NodeKind.COMPANY),
        ("CO:XKLS:5347", NodeKind.COMPANY),
        ("CO:XNAS:NVDA", NodeKind.COMPANY),
        ("SUB:banks", NodeKind.SUBSECTOR),
        ("SUB:electric-utilities", NodeKind.SUBSECTOR),
    ):
        g.add_node(_node(nid, kind))
    # a stated, citable rivalry
    g.add_edge(
        Edge(
            "CO:XKLS:1155",
            "CO:XKLS:1023",
            EdgeKind.COMPETES_WITH,
            0.9,
            "curated:supply_chain#maybank-cimb-compete",
            Confidence.EXTRACTED,
            OPENED,
        )
    )
    # an inferred rivalry: not citable, so never a direct peer
    g.add_edge(
        Edge(
            "CO:XKLS:1295",
            "CO:XKLS:1155",
            EdgeKind.COMPETES_WITH,
            0.9,
            "guess:1",
            Confidence.INFERRED,
            OPENED,
        )
    )
    # a rivalry in another market
    g.add_edge(
        Edge(
            "CO:XNAS:NVDA",
            "CO:XKLS:1155",
            EdgeKind.COMPETES_WITH,
            0.6,
            "curated:supply_chain#odd",
            Confidence.EXTRACTED,
            OPENED,
        )
    )
    for co in ("CO:XKLS:1155", "CO:XKLS:1023", "CO:XKLS:1295"):
        g.add_edge(
            Edge(
                co,
                "SUB:banks",
                EdgeKind.CLASSIFIED_IN,
                1.0,
                f"curated:sectors#{co}",
                Confidence.EXTRACTED,
                OPENED,
            )
        )
    g.add_edge(
        Edge(
            "CO:XKLS:5347",
            "SUB:electric-utilities",
            EdgeKind.CLASSIFIED_IN,
            1.0,
            "curated:sectors#CO:XKLS:5347",
            Confidence.EXTRACTED,
            OPENED,
        )
    )
    return g


def test_direct_and_subsector_peers_are_kept_apart_and_labelled():
    ps = peers_of(small(), "MYX:1155", ASOF)
    assert ps.node_id == "CO:XKLS:1155" and ps.subsector == "banks"
    assert {p.instrument_id for p in ps.direct} == {"MYX:1023"}
    assert {p.instrument_id for p in ps.same_subsector} == {"MYX:1295"}
    direct = ps.direct[0]
    assert direct.evidence == ("curated:supply_chain#maybank-cimb-compete",)
    assert abs(direct.weight - 0.45 * 0.9) < 1e-9 and direct.strength == "indirect"
    sibling = ps.same_subsector[0]
    assert sibling.strength == "speculative" and len(sibling.evidence) == 2
    assert ps.ids == {"MYX:1023", "MYX:1295"}
    assert "competes_with" in ps.text() and "shared sub-sector only" in ps.text()


def test_other_market_peers_are_excluded_not_dropped():
    ps = peers_of(small(), "MYX:1155", ASOF)
    assert [p.instrument_id for p in ps.excluded] == ["XNAS:NVDA"]
    assert "excluded, other market: XNAS:NVDA (XNAS)" in ps.text()
    everywhere = peers_of(small(), "MYX:1155", ASOF, same_market=False)
    assert "XNAS:NVDA" in everywhere.ids and not everywhere.excluded


def test_the_peer_set_is_point_in_time_and_honest_about_absence():
    before = peers_of(small(), "MYX:1155", date(2019, 6, 1))
    assert not before.peers and "no live, citable competes_with edge" in before.note
    alone = peers_of(small(), "MYX:5347", ASOF)
    assert not alone.peers and alone.subsector == "electric-utilities"
    unknown = peers_of(small(), "MYX:9999", ASOF)
    assert unknown.node_id is None and "not in the graph" in unknown.note and not unknown.ids
    assert "not in the graph" in unknown.text()


def test_book_ids_come_from_entities_yaml():
    assert book_id("XKLS:1155") == "MYX:1155"
    assert book_id("XNAS:NVDA") == "XNAS:NVDA"
    assert book_id("XLON:NOPE") == "XLON:NOPE"


def test_the_built_graph_names_maybanks_stated_rivals(tmp_path):
    with GraphStore(tmp_path / "g.db") as store:
        build(store)
        g = store.load()
    ps = peers_of(g, "MYX:1155", ASOF)
    assert {"MYX:1023", "MYX:1295"} <= {p.instrument_id for p in ps.direct}
    assert all(e.startswith("curated:supply_chain#") for p in ps.direct for e in p.evidence)
    nvda = peers_of(g, "XNAS:NVDA", ASOF)
    assert "XNAS:AMD" in nvda.ids and all(p.market == "XNAS" for p in nvda.peers)


def _ctx() -> AgentContext:
    from datetime import UTC, datetime

    reg = {"a7_sector_technology": {"traverse", "peers", "sector_primer", "retrieve"}}
    return AgentContext(
        router=Router({}),
        engine=default_engine(reg),
        now=datetime(2026, 8, 28, tzinfo=UTC),
        holdings=set(),
        watchlist=set(),
    )


def test_a7_peers_are_cited_from_the_curated_rows_and_refuse_without_a_graph(tmp_path):
    with GraphStore(tmp_path / "g.db") as store:
        build(store)
        g = store.load()
    a7 = A7SectorTechnology(_ctx(), g, CuratedCorpus().citation)
    out = a7.peers("MYX:1155", ASOF)
    assert out[0].kind == "peer_set" and out[0].numbers["direct"] >= 2
    peers = [f for f in out if f.kind == "peer"]
    assert peers and all(f.citations and f.all_citations_required for f in peers)
    assert all(c.source == "kb_supply_chain" or c.source for f in peers for c in f.citations)
    none = A7SectorTechnology(_ctx(), None).peers("MYX:1155", ASOF)
    assert none[0].text == "no graph is loaded" and "make graph" in none[0].caveats[0]


# -- a checked edge must not read like a guess -------------------------------------------------


def test_a_curated_peer_says_it_is_curated_and_a_documented_one_does_not():
    """A person writing a rivalry down and a filing stating it are both citable
    and they are not the same claim. Until 2026-09-06 they printed identically,
    so every peer in the book read as though a document backed it."""
    g = small()
    g.add_edge(
        Edge(
            "CO:XKLS:1155",
            "CO:XKLS:5347",
            EdgeKind.COMPETES_WITH,
            0.9,
            "edgar:0001234567-25-000001#item1-competition",
            Confidence.EXTRACTED,
            OPENED,
        )
    )
    by_id = {p.instrument_id: p for p in peers_of(g, "MYX:1155", ASOF).direct}
    curated, documented = by_id["MYX:1023"], by_id["MYX:5347"]

    assert not curated.verified
    assert "curated not verified" in curated.describe()

    assert documented.verified
    assert "curated not verified" not in documented.describe()


def test_every_name_in_the_book_that_can_have_a_peer_has_one(tmp_path):
    """Seven of the nine had none on 2026-09-06, which left `peer_set`, the
    workup's competitive step and the comps half of a valuation empty for them,
    with nothing anywhere saying so.

    Press Metal is the honest exception: no other primary aluminium smelter is
    listed on Bursa, and an invented peer is worse than the refusal `peers_of`
    already prints.
    """
    from core.config import load as load_config

    cfg = load_config()
    # Built from the checked-in yaml, not from data/graph.db: the question is
    # what the FILES say, and a stale build would answer for them.
    with GraphStore(tmp_path / "g.db") as store:
        build(store)
        g = store.load()
    book = tuple(dict.fromkeys(tuple(cfg.holdings) + tuple(cfg.watchlist)))
    bare = [iid for iid in book if not peers_of(g, iid, ASOF).peers]
    assert bare == ["MYX:8869"], f"names with no peer: {bare}"
