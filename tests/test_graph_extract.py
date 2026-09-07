"""The five deterministic extractors.

Each is tested against its own source alone, which is the point of extractors
speaking plain dicts: none of them imports the graph, so none of them can be
tested only through it.

Every payload here goes through validate.parse. An extractor whose output does
not satisfy the schema fails its own test rather than degrading the graph three
builds later.
"""

from datetime import UTC, date, datetime

import pytest

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.base import company_node, sorted_payload
from knowledge.graph.extractors.config_book import ConfigBookExtractor
from knowledge.graph.extractors.curated import CuratedExtractor
from knowledge.graph.extractors.gdelt import (
    EVENT_VALID_DAYS,
    GdeltExtractor,
    _index_from_aliases,
)
from knowledge.graph.extractors.market_registry import MarketsExtractor
from knowledge.graph.extractors.sectors import SectorExtractor
from knowledge.graph.validate import parse
from knowledge.news.features import Article


def checked(extractor):
    """Every extractor's output must survive the gate. Returns typed objects."""
    return parse(extractor.extract(), extractor.name)


# -- the shipped data files ---------------------------------------------------


def test_the_checked_in_sector_map_validates():
    nodes, edges = checked(SectorExtractor())
    assert nodes and edges
    assert all(e.kind is EdgeKind.CLASSIFIED_IN for e in edges)
    assert all(e.confidence is Confidence.EXTRACTED and e.citable for e in edges)


def test_the_checked_in_supply_chain_validates():
    nodes, edges = checked(CuratedExtractor())
    assert {e.kind for e in edges} <= {
        EdgeKind.SUPPLIES,
        EdgeKind.CUSTOMER_OF,
        EdgeKind.COMPETES_WITH,
        EdgeKind.SUBSTITUTES,
        EdgeKind.EXPOSED_TO,
        EdgeKind.AFFECTS,
    }
    assert all(e.citable for e in edges)


def test_every_curated_edge_cites_the_row_it_came_from():
    """A row's id is what its citation points at, so a reader can find it.

    Unless the row names a primary document in `verified:`, in which case the
    edge cites THAT - which is the whole difference between "a person wrote
    this down" and "a filing says so".
    """
    _, edges = checked(CuratedExtractor())
    assert all(e.source_doc_id and e.source_doc_id.strip() for e in edges)
    unverified = [e for e in edges if e.source_doc_id.startswith("curated:supply_chain#")]
    assert unverified, "the checked-in file is curated; some row must still cite it"


def test_a_verified_row_cites_the_document_instead_of_the_curated_list(tmp_path):
    """Until 2026-09-06 a checked edge and a seed guess read identically."""
    p = tmp_path / "sc.yaml"
    p.write_text(
        "edges:\n  - id: aapl-googl\n    source: 'XNAS:AAPL'\n    target: 'XNAS:GOOGL'\n"
        "    relation: competes_with\n    valid_from: 2020-01-01\n"
        "    verified: 'edgar:0000320193-25-000106#item1-competition'\n",
        encoding="utf-8",
    )
    _, edges = checked(CuratedExtractor(p))
    assert edges and all(
        e.source_doc_id == "edgar:0000320193-25-000106#item1-competition" for e in edges
    )
    assert all(e.citable for e in edges), "naming the real document does not weaken the edge"


def test_a_row_cannot_verify_itself(tmp_path):
    """Pointing `verified:` back at this file would launder a seed into a check."""
    p = tmp_path / "sc.yaml"
    p.write_text(
        "edges:\n  - id: circular\n    source: 'MYX:1155'\n    target: 'MYX:1023'\n"
        "    relation: competes_with\n    valid_from: 2020-01-01\n"
        "    verified: 'curated:supply_chain#circular'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="verify itself"):
        CuratedExtractor(p).extract()


def test_the_classification_spine_reaches_a_sector_from_every_company():
    nodes, edges = checked(SectorExtractor())
    subs = {e.src for e in edges if e.dst.startswith("SEC:")}
    for e in edges:
        if e.src.startswith("CO:"):
            assert e.dst in subs, f"{e.src} classifies into {e.dst}, which no sector owns"


def test_a_company_classified_under_a_subsector_no_sector_declares_is_refused(tmp_path):
    p = tmp_path / "sectors.yaml"
    p.write_text(
        "sectors:\n  Financials: [Banks]\n"
        "companies:\n  'MYX:1155': {subsector: Wizardry, valid_from: 2020-01-01}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no sector declares"):
        SectorExtractor(p).extract()


def test_a_curated_row_with_no_id_is_refused_because_it_could_not_be_cited(tmp_path):
    p = tmp_path / "sc.yaml"
    p.write_text(
        "edges:\n  - source: 'MYX:1155'\n    target: 'MYX:1023'\n"
        "    relation: competes_with\n    valid_from: 2020-01-01\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cannot be cited"):
        CuratedExtractor(p).extract()


def test_a_duplicate_curated_row_id_is_refused(tmp_path):
    p = tmp_path / "sc.yaml"
    row = (
        "  - id: dupe\n    source: 'MYX:1155'\n    target: 'MYX:1023'\n"
        "    relation: competes_with\n    valid_from: 2020-01-01\n"
    )
    p.write_text("edges:\n" + row + row, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate row id"):
        CuratedExtractor(p).extract()


def test_an_exposure_row_is_stored_from_both_ends():
    """'Press Metal is exposed to aluminium' and 'aluminium affects Press Metal'
    are one relationship read from each end. Without both, asking the commodity
    who it hurts traverses out of a directed edge and finds nothing."""
    _, edges = checked(CuratedExtractor())
    fwd = {(e.src, e.dst) for e in edges if e.kind is EdgeKind.EXPOSED_TO}
    back = {(e.src, e.dst) for e in edges if e.kind is EdgeKind.AFFECTS}
    assert fwd
    assert {(d, s) for s, d in fwd} == back


def test_a_symmetric_relation_is_not_duplicated_onto_itself():
    """competes_with inverts to itself, so emitting both readings of the SAME
    pair would be one edge written twice."""
    _, edges = checked(CuratedExtractor())
    pairs = [(e.src, e.dst) for e in edges if e.kind is EdgeKind.COMPETES_WITH]
    assert len(pairs) == len(set(pairs))


# -- the book -----------------------------------------------------------------


def test_the_book_contributes_seeds_and_no_edges():
    """Owning two companies is a fact about you, not a relationship between
    them. An edge here would let a traversal connect them through your account."""
    nodes, edges = checked(ConfigBookExtractor(["MYX:1155"], ["XNAS:NVDA"]))
    assert edges == []
    assert {n.node_id for n in nodes} == {"CO:XKLS:1155", "CO:XNAS:NVDA"}
    assert nodes[0].metadata == {"held": True}


def test_a_name_on_both_lists_is_held_not_watched():
    nodes, _ = checked(ConfigBookExtractor(["MYX:1155"], ["MYX:1155"]))
    assert len(nodes) == 1 and nodes[0].metadata == {"held": True}


def test_an_empty_book_is_valid_and_empty():
    assert checked(ConfigBookExtractor()) == ([], [])


# -- the market registry ------------------------------------------------------


def test_a_company_is_placed_in_its_country_and_under_its_regulator():
    nodes, edges = checked(MarketsExtractor(["MYX:1155"]))
    kinds = {(e.src, e.kind, e.dst) for e in edges}
    assert ("CO:XKLS:1155", EdgeKind.OPERATES_IN, "CN:my") in kinds
    assert any(k is EdgeKind.REGULATED_BY and d.startswith("RG:") for _, k, d in kinds)
    assert {n.kind for n in nodes} == {NodeKind.COMPANY, NodeKind.COUNTRY, NodeKind.REGULATOR}


def test_the_registry_is_named_as_the_source_rather_than_an_invented_filing():
    _, edges = checked(MarketsExtractor(["MYX:1155"]))
    assert all(e.source_doc_id.startswith("markets/registry.py#") for e in edges)


def test_it_accepts_an_already_minted_node_id_as_well_as_an_instrument_id():
    a, _ = checked(MarketsExtractor(["MYX:1155"]))
    b, _ = checked(MarketsExtractor(["CO:XKLS:1155"]))
    assert [n.node_id for n in a] == [n.node_id for n in b]


def test_a_market_with_no_adapter_is_skipped_rather_than_crashing_the_build():
    """The unsupported MIC is chosen at RUNTIME, not written down. This test
    named XTKS until Tokyo was registered, at which point it started asserting
    the opposite of what it says - the same trap that already cost this
    repository one stale test."""
    from markets.registry import supported

    unsupported = next(m for m in ("XTAE", "XBOM", "XKRX", "XSWX", "XPAR") if m not in supported())
    nodes, edges = checked(MarketsExtractor([f"{unsupported}:0001", "not-an-instrument"]))
    assert (nodes, edges) == ([], [])


def test_every_supported_market_names_a_regulator():
    """On the MarketAdapter ABC, so a market added later cannot forget it."""
    from markets.registry import get, supported

    for mic in supported():
        assert get(mic).regulator.strip()


# -- gdelt: the one that must NOT be citable ----------------------------------


def article(doc_id="doc:1", instruments=("MYX:1155",), when=None):
    return Article(
        doc_id=doc_id,
        title="Bank probe widens",
        body="Regulators acted.",
        source_domain="example.com",
        published_at=when or datetime(2026, 8, 1, tzinfo=UTC),
        instruments=list(instruments),
    )


def test_a_news_link_is_inferred_and_therefore_cannot_back_a_claim():
    """link_entities is a substring match. It establishes that an article
    MENTIONS a company - never that the event affects it. 'Maybank was not among
    the banks named' links Maybank exactly as strongly as a story about it."""
    _, edges = checked(GdeltExtractor([article()]))
    assert edges[0].confidence is Confidence.INFERRED
    assert edges[0].sourced and not edges[0].citable


def test_a_news_edge_expires_so_an_old_story_stops_answering_todays_question():
    _, edges = checked(GdeltExtractor([article()]))
    e = edges[0]
    assert (e.valid_to - e.valid_from).days == EVENT_VALID_DAYS
    assert e.live_at(date(2026, 9, 1))
    assert not e.live_at(date(2027, 1, 1))


def test_an_article_linked_to_nothing_produces_nothing():
    assert checked(GdeltExtractor([article(instruments=())])) == ([], [])


def test_the_news_linker_and_the_graph_share_one_surface_form_table():
    """Two lists would drift, and a company linked in one and invisible in the
    other is a silently missing exposure path."""
    index = _index_from_aliases()
    assert index["Maybank"] == "MYX:1155"
    assert index["NVIDIA"] == "XNAS:NVDA"


def test_a_fixture_feed_can_drive_the_extractor_offline(tmp_path):
    import json

    p = tmp_path / "feed.jsonl"
    p.write_text(
        json.dumps(
            {
                "id": "1",
                "title": "Maybank raises guidance",
                "body": "The bank lifted its outlook.",
                "domain": "example.com",
                "published_at": "2026-08-01T00:00:00+00:00",
                "language": "en",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    nodes, edges = checked(GdeltExtractor.from_fixture(p))
    assert any(n.kind is NodeKind.EVENT for n in nodes)
    assert edges and edges[0].dst == "CO:XKLS:1155"


# -- the shared helpers -------------------------------------------------------


def test_every_extractor_labels_a_company_the_same_way():
    """The store upserts nodes, so whichever extractor runs last decides the
    label. One helper is what stops that being a race between a real name and a
    stock code."""
    assert company_node("MYX:1155")["label"] == "Maybank"
    assert company_node("Maybank")["label"] == "Maybank"
    assert company_node("CO:XKLS:1155")["label"] == "Maybank"


def test_a_company_no_alias_table_knows_keeps_its_written_form():
    assert company_node("XKLS:9999")["label"] == "XKLS:9999"


def test_payloads_come_out_ordered_because_determinism_depends_on_it():
    p = sorted_payload(
        [company_node("XNAS:NVDA"), company_node("MYX:1155"), company_node("MYX:1155")],
        [
            {"source": "b", "target": "a", "relation": "supplies", "valid_from": "2020-01-01"},
            {"source": "a", "target": "b", "relation": "supplies", "valid_from": "2020-01-01"},
        ],
    )
    assert [n["id"] for n in p["nodes"]] == ["CO:XKLS:1155", "CO:XNAS:NVDA"]
    assert [e["source"] for e in p["edges"]] == ["a", "b"]
