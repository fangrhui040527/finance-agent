"""L2 feed seam and P9 graph. Both exist to stop unsourced claims reaching a user."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from knowledge.feeds.adapter import (
    FeedAdapter, FeedError, FixtureFeed, GdeltFeed, IngestStats, RawRecord,
    REGISTRY, link_entities,
)
from knowledge.graph.entity_graph import (
    EDGE_DECAY, EDGE_INVERSE, Confidence, Edge, EdgeKind, EntityGraph, MAX_HOPS,
    MIN_PATH_WEIGHT, Node, NodeKind, Path, PathRequired, path_to_citations, require_path,
)

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)

INDEX = {
    "Maybank": "MYX:1155",
    "Malayan Banking": "MYX:1155",
    "Maybank Islamic": "MYX:1155-I",
    "Tenaga": "MYX:5347",
    "NVIDIA": "XNAS:NVDA",
}


def rows():
    return [
        {"id": "1", "title": "Maybank posts record quarter",
         "body": "Malayan Banking reported net profit above consensus, beating estimates.",
         "published_at": "2026-08-24T09:00:00+00:00", "domain": "reuters.com"},
        {"id": "2", "title": "Maybank posts record quarter",
         "body": "Malayan Banking reported net profit above consensus, beating estimates.",
         "published_at": "2026-08-24T09:12:00+00:00", "domain": "themalaysianreserve.com"},
        {"id": "3", "title": "Weather turns cooler",
         "body": "Rain is expected across the peninsula this week.",
         "published_at": "2026-08-24T10:00:00+00:00", "domain": "weather.local"},
    ]


# -- feeds -------------------------------------------------------------------

def test_a_wire_duplicate_is_dropped_before_it_reaches_the_index():
    feed = FixtureFeed(records=rows())
    arts, stats = feed.normalize(feed.fetch(SINCE), entity_index=INDEX)
    assert stats.fetched == 3
    assert stats.duplicates == 1
    assert stats.kept == 2


def test_an_item_that_links_to_nothing_is_counted_not_attributed():
    feed = FixtureFeed(records=rows())
    arts, stats = feed.normalize(feed.fetch(SINCE), entity_index=INDEX)
    assert stats.unlinked == 1
    weather = next(a for a in arts if "Rain" in a.body)
    assert weather.instruments == []


def test_longest_surface_form_wins_so_a_subsidiary_is_not_the_parent():
    assert link_entities("Maybank Islamic launched a fund", INDEX)[0] == "MYX:1155-I"


def test_entity_linking_resolves_an_alias_to_the_same_instrument():
    assert link_entities("Malayan Banking said", INDEX) == ["MYX:1155"]


def test_items_older_than_the_watermark_are_not_refetched():
    feed = FixtureFeed(records=rows())
    assert feed.fetch(datetime(2026, 8, 25, tzinfo=timezone.utc)) == []


def test_dedup_state_persists_across_fetches_within_one_adapter():
    feed = FixtureFeed(records=rows())
    feed.normalize(feed.fetch(SINCE), entity_index=INDEX)
    _, second = feed.normalize(feed.fetch(SINCE), entity_index=INDEX)
    assert second.kept == 0
    assert second.duplicates == 3, "all three re-fetched rows are already seen"


def test_the_live_feed_refuses_to_pretend_it_has_data():
    """GDELT is wired now, but the property that mattered when it was a stub
    still holds: a feed that cannot be read says so. It never returns [].
    Detail lives in tests/test_gdelt_feed.py; this is the seam-level guard."""
    def unreachable(req, timeout=None):
        raise OSError("network down")

    with pytest.raises(FeedError):
        GdeltFeed(opener=unreachable).fetch(SINCE)


def test_the_live_feed_never_touches_the_network_under_test():
    """CI is offline by design (docs/12 section 2.5). Any test that reaches
    GDELT for real would pass on a laptop and fail in the pipeline."""
    calls: list = []

    def record(req, timeout=None):
        calls.append(req.full_url)
        raise OSError("refused")

    with pytest.raises(FeedError):
        GdeltFeed(opener=record).fetch(SINCE)
    assert calls and calls[0].startswith(GdeltFeed.DOC_API)


def test_a_new_source_is_a_registry_entry_not_a_pipeline_change():
    assert set(REGISTRY) == {"fixture", "gdelt"}
    assert all(issubclass(c, FeedAdapter) for c in REGISTRY.values())


def test_reading_from_disk_matches_reading_inline(tmp_path):
    p = tmp_path / "news.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows()))
    disk = FixtureFeed(path=p)
    inline = FixtureFeed(records=rows())
    a, _ = disk.normalize(disk.fetch(SINCE), entity_index=INDEX)
    b, _ = inline.normalize(inline.fetch(SINCE), entity_index=INDEX)
    assert [x.doc_id for x in a] == [x.doc_id for x in b]


# -- graph -------------------------------------------------------------------

OPENED = date(2026, 1, 1)
ASOF = date(2026, 8, 28)


def supply_chain():
    g = EntityGraph()
    for nid, kind, label in [
        ("EV:redsea", NodeKind.EVENT, "Red Sea shipping disruption"),
        ("SEC:shipping", NodeKind.SECTOR, "Shipping"),
        ("SEC:chemicals", NodeKind.SECTOR, "Chemicals"),
        ("CO:MISC", NodeKind.COMPANY, "MISC Berhad"),
        ("CO:PCHEM", NodeKind.COMPANY, "Petronas Chemicals"),
        ("CO:MAYBANK", NodeKind.COMPANY, "Maybank"),
    ]:
        g.add_node(Node(nid, kind, label))
    g.add_edge(Edge("EV:redsea", "SEC:shipping", EdgeKind.AFFECTS, 1.0, "doc:1",
                    Confidence.EXTRACTED, OPENED))
    g.add_edge(Edge("SEC:shipping", "CO:MISC", EdgeKind.CLASSIFIED_IN, 1.0, "doc:2",
                    Confidence.EXTRACTED, OPENED))
    g.add_edge(Edge("SEC:shipping", "SEC:chemicals", EdgeKind.EXPOSED_TO, 0.8, "doc:3",
                    Confidence.EXTRACTED, OPENED))
    g.add_edge(Edge("SEC:chemicals", "CO:PCHEM", EdgeKind.CLASSIFIED_IN, 1.0, "doc:4",
                    Confidence.EXTRACTED, OPENED))
    return g


def test_more_hops_means_less_signal_and_the_label_says_so():
    g = supply_chain()
    impacts = dict(g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:MISC", "CO:PCHEM"}))
    assert impacts["CO:MISC"].strength == "indirect"
    assert impacts["CO:PCHEM"].strength == "speculative"
    assert impacts["CO:MISC"].weight > impacts["CO:PCHEM"].weight


def test_an_unrelated_holding_is_simply_absent_not_weakly_linked():
    g = supply_chain()
    assert "CO:MAYBANK" not in dict(g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:MAYBANK"}))


def test_an_edge_with_no_source_document_cannot_carry_a_claim():
    g = supply_chain()
    g.add_node(Node("CO:RUMOUR", NodeKind.COMPANY, "Rumour Bhd"))
    g.add_edge(Edge("SEC:shipping", "CO:RUMOUR", EdgeKind.CLASSIFIED_IN, 1.0,
                    source_doc_id=None, confidence=Confidence.EXTRACTED, valid_from=OPENED))
    assert "CO:RUMOUR" not in dict(g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:RUMOUR"}))


def test_every_path_ships_with_its_own_evidence():
    g = supply_chain()
    path = dict(g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:PCHEM"}))["CO:PCHEM"]
    assert set(path.evidence()) == {"doc:1", "doc:3", "doc:4"}
    assert path.citable


def test_the_path_describes_itself_in_readable_form():
    g = supply_chain()
    path = dict(g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:MISC"}))["CO:MISC"]
    assert "--affects-->" in path.describe()
    assert "Red Sea shipping disruption" in path.describe()


def test_an_impact_claim_without_a_path_cannot_be_emitted():
    with pytest.raises(PathRequired, match="no traversal path"):
        require_path("shipping hits Maybank", None)


def test_an_impact_claim_over_an_unsourced_edge_cannot_be_emitted():
    g = supply_chain()
    g.add_node(Node("CO:X", NodeKind.COMPANY, "X"))
    g.add_edge(Edge("SEC:shipping", "CO:X", EdgeKind.CLASSIFIED_IN, 1.0,
                    source_doc_id=None, confidence=Confidence.EXTRACTED, valid_from=OPENED))
    paths = g.traverse("EV:redsea", asof=ASOF, target="CO:X", require_citable=False)
    with pytest.raises(PathRequired, match="no source document"):
        require_path("shipping hits X", paths[0])


def test_three_weak_hops_cannot_outrank_one_strong_link():
    g = supply_chain()
    g.add_node(Node("CO:DIRECT", NodeKind.COMPANY, "Direct Supplier"))
    g.add_edge(Edge("EV:redsea", "CO:DIRECT", EdgeKind.AFFECTS, 1.0, "doc:9",
                    Confidence.EXTRACTED, OPENED))
    ranked = g.impact_of("EV:redsea", asof=ASOF, holdings={"CO:DIRECT", "CO:PCHEM"})
    assert ranked[0][0] == "CO:DIRECT"


def test_traversal_stops_before_the_weight_becomes_meaningless():
    g = supply_chain()
    for path in g.traverse("EV:redsea", asof=ASOF):
        assert path.weight >= MIN_PATH_WEIGHT
        assert path.n_hops <= MAX_HOPS


def test_a_supply_link_survives_a_hop_better_than_a_sector_label():
    assert EDGE_DECAY[EdgeKind.SUPPLIES] > EDGE_DECAY[EdgeKind.CLASSIFIED_IN]


def test_an_edge_to_an_unknown_node_is_refused():
    g = EntityGraph()
    g.add_node(Node("A", NodeKind.COMPANY))
    with pytest.raises(KeyError, match="must be added before"):
        g.add_edge(Edge("A", "B", EdgeKind.SUPPLIES, 1.0, "doc:1",
                        Confidence.EXTRACTED, OPENED))


def test_traversal_does_not_loop():
    g = EntityGraph()
    for n in "ABC":
        g.add_node(Node(n, NodeKind.COMPANY))
    g.add_edge(Edge("A", "B", EdgeKind.SUPPLIES, 1.0, "d", Confidence.EXTRACTED, OPENED),
               bidirectional=True)
    g.add_edge(Edge("B", "C", EdgeKind.SUPPLIES, 1.0, "d", Confidence.EXTRACTED, OPENED),
               bidirectional=True)
    for p in g.traverse("A", asof=ASOF):
        seen = [h.edge.dst for h in p.hops]
        assert len(seen) == len(set(seen))
