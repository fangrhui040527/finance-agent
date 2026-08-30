"""Graph schema: confidence, validity, inversion, and the citation seam.

The seam is the reason this file exists. Before it, a multi-hop exposure claim
could not be emitted in ANY configuration - the path was computed, the evidence
was held as source_doc_id strings, and nothing turned those into a Citation the
output gate could verify. Every graph-backed claim was dropped as uncited and
A7 was structurally incapable of answering. test_a_graph_backed_exposure_claim_
survives_the_output_gate is that hole, closed.
"""
from datetime import date, datetime, timezone

import pytest

from agents.base import AgentContext
from agents.evidence.agents import A7SectorTechnology
from core.contracts.answer import Citation, TrustTier
from core.guardrails.defaults import default_engine
from knowledge.graph.entity_graph import (
    EDGE_DECAY, EDGE_INVERSE, Confidence, Edge, EdgeKind, EntityGraph,
    GraphSchemaError, Node, NodeKind, Path, PathRequired, _check_tables,
    path_to_citations,
)
from knowledge.graph.validate import (
    ExtractionError, assert_valid, parse, validate_extraction,
)
from knowledge.retrieval.pipeline import Router

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
ASOF = date(2026, 8, 25)
OPENED = date(2026, 1, 1)


def edge(src, dst, kind=EdgeKind.SUPPLIES, weight=1.0, doc="doc:1",
         conf=Confidence.EXTRACTED, vf=OPENED, vt=None):
    return Edge(src, dst, kind, weight, doc, conf, vf, vt)


def two_hop():
    """EV:x --affects--> SEC:s --classified_in--> CO:a, both extracted and live."""
    g = EntityGraph()
    for nid, kind, lbl in [("EV:x", NodeKind.EVENT, "Port closure"),
                           ("SEC:s", NodeKind.SECTOR, "Shipping"),
                           ("CO:a", NodeKind.COMPANY, "Alpha Bhd")]:
        g.add_node(Node(nid, kind, lbl))
    g.add_edge(edge("EV:x", "SEC:s", EdgeKind.AFFECTS, doc="doc:1"))
    g.add_edge(edge("SEC:s", "CO:a", EdgeKind.CLASSIFIED_IN, doc="doc:2"))
    return g


# -- confidence --------------------------------------------------------------

def test_an_unstated_confidence_falls_to_the_weakest_value_not_a_midpoint():
    """A missing label is an absence of evidence about the edge, not a coin flip."""
    assert Edge("A", "B", EdgeKind.SUPPLIES).confidence is Confidence.AMBIGUOUS


def test_an_inferred_edge_may_name_a_document_and_still_not_be_citable():
    """The distinction the old binary `citable` could not express.

    An edge deduced from two companies sharing a sector label can name the
    document the labels came from. That document does not say the companies are
    related, so the edge is sourced and still cannot back a claim.
    """
    e = edge("A", "B", conf=Confidence.INFERRED)
    assert e.sourced and not e.citable


def test_an_extracted_edge_with_no_document_is_not_citable():
    assert not edge("A", "B", doc=None).citable
    assert not edge("A", "B", doc="   ").citable


def test_only_an_extracted_edge_backed_by_a_document_is_citable():
    assert edge("A", "B").citable


def test_an_inferred_edge_is_not_traversed_when_a_claim_is_the_goal():
    g = two_hop()
    g.add_node(Node("CO:guess", NodeKind.COMPANY, "Guess Bhd"))
    g.add_edge(edge("SEC:s", "CO:guess", EdgeKind.CLASSIFIED_IN,
                    conf=Confidence.INFERRED))
    reached = {p.end for p in g.traverse("EV:x", asof=ASOF)}
    assert "CO:guess" not in reached
    assert "CO:guess" in {p.end for p in g.traverse("EV:x", asof=ASOF,
                                                    require_citable=False)}


def test_a_path_is_only_as_confident_as_its_weakest_hop():
    g = two_hop()
    g.add_node(Node("CO:b", NodeKind.COMPANY, "Beta"))
    g.add_edge(edge("SEC:s", "CO:b", EdgeKind.CLASSIFIED_IN, conf=Confidence.INFERRED))
    path = next(p for p in g.traverse("EV:x", asof=ASOF, require_citable=False)
                if p.end == "CO:b")
    assert path.weakest_confidence is Confidence.INFERRED


def test_an_empty_path_is_not_citable():
    assert not Path((), 0.0).citable


# -- validity intervals ------------------------------------------------------

def test_an_edge_is_invisible_before_its_validity_begins():
    e = edge("A", "B", vf=date(2026, 6, 1))
    assert not e.live_at(date(2026, 5, 31))
    assert e.live_at(date(2026, 6, 1))


def test_the_closing_date_is_the_first_day_the_relation_no_longer_holds():
    """Half-open [from, to). A contract ending the day another begins leaves
    exactly one live edge on that date - not two, and not zero."""
    e = edge("A", "B", vf=date(2026, 1, 1), vt=date(2026, 6, 1))
    assert e.live_at(date(2026, 5, 31))
    assert not e.live_at(date(2026, 6, 1))


def test_an_edge_that_cannot_say_when_it_opened_is_traversable_at_no_date():
    e = edge("A", "B", vf=None)
    assert not e.live_at(date(2026, 6, 1))
    assert not e.live_at(date(1970, 1, 1))


def test_an_interval_containing_no_days_is_refused_at_construction():
    with pytest.raises(ValueError, match="contains no days"):
        Edge("A", "B", EdgeKind.SUPPLIES, valid_from=date(2026, 6, 1),
             valid_to=date(2026, 6, 1))
    with pytest.raises(ValueError, match="contains no days"):
        Edge("A", "B", EdgeKind.SUPPLIES, valid_from=date(2026, 6, 2),
             valid_to=date(2026, 6, 1))


def test_an_edge_cannot_close_without_saying_when_it_opened():
    with pytest.raises(ValueError, match="never states when it opened"):
        Edge("A", "B", EdgeKind.SUPPLIES, valid_to=date(2026, 6, 1))


def test_a_relation_that_has_ended_does_not_answer_todays_question():
    g = two_hop()
    g.add_node(Node("CO:old", NodeKind.COMPANY, "Former supplier"))
    g.add_edge(edge("SEC:s", "CO:old", EdgeKind.SUPPLIES,
                    vf=OPENED, vt=date(2026, 6, 1)))
    assert "CO:old" not in {p.end for p in g.traverse("EV:x", asof=ASOF)}
    assert "CO:old" in {p.end for p in g.traverse("EV:x", asof=date(2026, 3, 1))}


def test_a_traversal_cannot_be_run_without_stating_its_as_of_date():
    """Required, not defaulted. A traversal with no date answers a question
    about today using edges that may not have existed when it was asked."""
    with pytest.raises(TypeError):
        two_hop().traverse("EV:x")


# -- weight ------------------------------------------------------------------

def test_a_weight_above_one_would_let_a_hop_strengthen_a_path_and_is_refused():
    with pytest.raises(ValueError, match=r"outside \(0, 1\]"):
        Edge("A", "B", EdgeKind.SUPPLIES, weight=1.4)


def test_a_zero_or_negative_weight_is_refused():
    for w in (0.0, -0.5):
        with pytest.raises(ValueError, match=r"outside \(0, 1\]"):
            Edge("A", "B", EdgeKind.SUPPLIES, weight=w)


# -- inversion ---------------------------------------------------------------

def test_a_bidirectional_supply_edge_reverses_into_customer_of_not_supplies():
    """The bug: the reverse edge used to be minted with the SAME kind, so
    'A supplies B' silently also asserted 'B supplies A' - reversing a supply
    chain and every exposure conclusion drawn through it."""
    g = EntityGraph()
    for n in "AB":
        g.add_node(Node(n, NodeKind.COMPANY))
    g.add_edge(edge("A", "B", EdgeKind.SUPPLIES), bidirectional=True)
    back = g.neighbours("B")
    assert len(back) == 1
    assert back[0].kind is EdgeKind.CUSTOMER_OF
    assert back[0].src == "B" and back[0].dst == "A"


def test_a_relation_with_no_reverse_reading_cannot_be_made_bidirectional():
    g = EntityGraph()
    for n in "AB":
        g.add_node(Node(n, NodeKind.COMPANY))
    with pytest.raises(ValueError, match="no inverse relation"):
        g.add_edge(edge("A", "B", EdgeKind.OWNS), bidirectional=True)


def test_a_symmetric_relation_reverses_into_itself():
    g = EntityGraph()
    for n in "AB":
        g.add_node(Node(n, NodeKind.COMPANY))
    g.add_edge(edge("A", "B", EdgeKind.COMPETES_WITH), bidirectional=True)
    assert g.neighbours("B")[0].kind is EdgeKind.COMPETES_WITH


def test_the_reverse_edge_keeps_the_originals_evidence_and_validity():
    g = EntityGraph()
    for n in "AB":
        g.add_node(Node(n, NodeKind.COMPANY))
    g.add_edge(edge("A", "B", EdgeKind.SUPPLIES, weight=0.8, doc="doc:9",
                    vf=OPENED, vt=date(2027, 1, 1)), bidirectional=True)
    back = g.neighbours("B")[0]
    assert (back.source_doc_id, back.weight, back.valid_from, back.valid_to,
            back.confidence) == ("doc:9", 0.8, OPENED, date(2027, 1, 1),
                                 Confidence.EXTRACTED)


# -- load-time table checks --------------------------------------------------

def test_an_edge_kind_with_no_decay_entry_fails_at_import_not_mid_query():
    """It used to be a KeyError inside a user's traversal, on whichever path
    happened to reach the new kind first."""
    saved = EDGE_DECAY.pop(EdgeKind.SUPPLIES)
    try:
        with pytest.raises(GraphSchemaError, match="EDGE_DECAY has no entry"):
            _check_tables()
    finally:
        EDGE_DECAY[EdgeKind.SUPPLIES] = saved


def test_inversion_must_be_an_involution():
    EDGE_INVERSE[EdgeKind.OWNS] = EdgeKind.AFFECTS
    try:
        with pytest.raises(GraphSchemaError, match="not self-consistent"):
            _check_tables()
    finally:
        del EDGE_INVERSE[EdgeKind.OWNS]


def test_a_decay_of_one_would_make_a_four_hop_guess_look_like_a_filing():
    saved = EDGE_DECAY[EdgeKind.SUPPLIES]
    EDGE_DECAY[EdgeKind.SUPPLIES] = 1.4
    try:
        with pytest.raises(GraphSchemaError, match=r"outside \(0, 1\]"):
            _check_tables()
    finally:
        EDGE_DECAY[EdgeKind.SUPPLIES] = saved


def test_the_shipped_tables_are_consistent():
    _check_tables()
    assert set(EDGE_DECAY) == set(EdgeKind)


# -- reverse index -----------------------------------------------------------

def test_who_supplies_this_company_is_answerable_without_a_full_scan():
    g = two_hop()
    assert [e.src for e in g.inbound("CO:a")] == ["SEC:s"]
    assert g.inbound("EV:x") == []
    assert g.degree("SEC:s") == 2


# -- traversal is a heuristic ------------------------------------------------

def test_traversal_can_miss_a_path_that_exists_and_the_docstring_says_so():
    """Recorded, not hidden. A node is re-expanded only on a strictly better
    weight, so a lighter-but-shorter route into a node is never expanded once a
    heavier longer one has reached it.

    Here X is reached twice: A->P->R->X at three hops carrying 0.61 (all `owns`,
    decay 0.85), and A->Q->X at two hops carrying 0.12 (both `classified_in`,
    decay 0.35). The heavy route wins the re-expansion, and at max_hops=3 it has
    no hops left to spend on X->T. The light two-hop route could have afforded
    it and is never expanded, so T is not found - though A->Q->X->T exists.

    An empty result therefore means 'not found cheaply', never 'does not
    exist' - which is why no negative claim may be built on one.
    """
    g = EntityGraph()
    for n in ("A", "P", "R", "X", "Q", "T"):
        g.add_node(Node(n, NodeKind.COMPANY))
    for a, b in (("A", "P"), ("P", "R"), ("R", "X"), ("X", "T")):
        g.add_edge(edge(a, b, EdgeKind.OWNS))               # decay 0.85
    for a, b in (("A", "Q"), ("Q", "X")):
        g.add_edge(edge(a, b, EdgeKind.CLASSIFIED_IN))      # decay 0.35

    assert g.traverse("A", asof=ASOF, target="T", max_hops=3) == []
    assert g.traverse("A", asof=ASOF, target="T", max_hops=4) != []
    assert "heuristic" in EntityGraph.traverse.__doc__.lower()


# -- the citation seam -------------------------------------------------------

CORPUS = {
    ("kb_sector", "doc:1"): "The port closure has halted shipping through the strait.",
    ("kb_sector", "doc:2"): "Alpha Bhd is classified within the shipping sector.",
}
EVIDENCE = {
    "doc:1": Citation(source="kb_sector", chunk_id="doc:1",
                      quoted_span="halted shipping through the strait",
                      trust=TrustTier.METHOD_KB, as_of=NOW),
    "doc:2": Citation(source="kb_sector", chunk_id="doc:2",
                      quoted_span="classified within the shipping sector",
                      trust=TrustTier.METHOD_KB, as_of=NOW),
}


def chunk_lookup(source, chunk_id):
    return CORPUS.get((source, chunk_id))


def a7(graph=None, evidence=None):
    return A7SectorTechnology(
        AgentContext(router=Router({}),
                     engine=default_engine({"a7_sector_technology": {"traverse"}}),
                     now=NOW),
        graph, evidence)


def test_a_path_becomes_one_citation_per_hop():
    path = dict(two_hop().impact_of("EV:x", {"CO:a"}, asof=ASOF))["CO:a"]
    cites = path_to_citations(path, EVIDENCE.get)
    assert [c.chunk_id for c in cites] == ["doc:1", "doc:2"]


def test_a_path_the_corpus_cannot_cite_is_refused_whole_not_partially():
    """The dangerous middle. A claim citing two of three hops passes the output
    gate while the uncited hop carries the inferential load - it reads as
    evidence and is not."""
    path = dict(two_hop().impact_of("EV:x", {"CO:a"}, asof=ASOF))["CO:a"]
    partial = {"doc:1": EVIDENCE["doc:1"]}
    with pytest.raises(PathRequired, match="cited whole or not at all"):
        path_to_citations(path, partial.get)


def test_an_empty_path_yields_no_citations():
    with pytest.raises(PathRequired, match="no traversal path"):
        path_to_citations(Path((), 0.0), EVIDENCE.get)


def test_a_graph_backed_exposure_claim_survives_the_output_gate():
    """THE test this whole phase exists for.

    Before the seam, A7 emitted findings with citations=[] and verify_answer
    dropped every one as 'no citation verified against its chunk'. The path was
    computed, the evidence was held, and the answer was always a refusal. No
    configuration of the system could emit a multi-hop exposure claim.
    """
    agent = a7(two_hop(), EVIDENCE.get)
    findings = agent.run("EV:x", {"CO:a"})
    answer = agent.emit(findings, chunk_lookup, confidence=0.55)

    assert answer.answered is True
    assert len(answer.claims) == 1
    assert not answer.dropped
    claim = answer.claims[0]
    assert "Alpha Bhd is exposed via" in claim.text
    assert [c.chunk_id for c in claim.citations] == ["doc:1", "doc:2"]


def test_the_same_finding_without_the_seam_is_still_refused():
    """The regression guard. If evidence is not wired, the gate must still bite -
    an exposure claim is never emitted on the strength of the path alone."""
    agent = a7(two_hop())
    answer = agent.emit(agent.run("EV:x", {"CO:a"}), chunk_lookup, confidence=0.55)
    assert answer.answered is False
    assert answer.dropped[0].dropped_reason == "no citation verified against its chunk"


def test_a_fabricated_span_is_dropped_even_though_the_graph_vouched_for_it():
    """The graph says which document supports an edge. It does not get to say
    what the document contains - verify_claim rechecks that against the text."""
    bad = dict(EVIDENCE)
    bad["doc:2"] = Citation(source="kb_sector", chunk_id="doc:2",
                            quoted_span="Alpha Bhd supplies the entire strait",
                            trust=TrustTier.METHOD_KB, as_of=NOW)
    agent = a7(two_hop(), bad.get)
    answer = agent.emit(agent.run("EV:x", {"CO:a"}), chunk_lookup, confidence=0.55)
    assert answer.answered is False


def test_a_missing_document_is_reported_in_the_finding_rather_than_skipped():
    """Silently dropping the path would lose the fact that the graph points at a
    document the corpus cannot produce. That is a corpus integrity problem and
    has to survive into the audit trail."""
    agent = a7(two_hop(), {"doc:1": EVIDENCE["doc:1"]}.get)
    findings = agent.run("EV:x", {"CO:a"})
    assert len(findings) == 1
    assert any("evidence unavailable" in c for c in findings[0].caveats)


def test_a7_dates_its_traversal_from_its_own_context_clock():
    agent = a7(two_hop(), EVIDENCE.get)
    assert agent.run("EV:x", {"CO:a"}, asof=date(2025, 1, 1)) == []
    assert agent.run("EV:x", {"CO:a"}) != []


# -- the validator -----------------------------------------------------------

def clean_payload():
    return {
        "nodes": [{"id": "CO:a", "kind": "Company", "label": "Alpha"},
                  {"id": "CO:b", "kind": "Company", "label": "Beta"}],
        "edges": [{"source": "CO:a", "target": "CO:b", "relation": "supplies",
                   "confidence": "extracted", "source_doc_id": "doc:1",
                   "weight": 0.9, "valid_from": "2026-01-01", "valid_to": None}],
    }


def test_a_clean_extraction_validates_and_parses():
    assert validate_extraction(clean_payload()) == []
    nodes, edges = parse(clean_payload(), "test")
    assert [n.node_id for n in nodes] == ["CO:a", "CO:b"]
    assert edges[0].kind is EdgeKind.SUPPLIES
    assert edges[0].valid_from == date(2026, 1, 1)
    assert edges[0].citable


def test_a_malformed_extraction_raises_rather_than_warning_and_building():
    """Graphify validates, prints a warning and builds anyway, so a bad
    extractor degrades the graph quietly. Same posture as RegistryError here:
    a malformed input is a startup failure."""
    bad = clean_payload()
    bad["edges"][0]["relation"] = "definitely_supplies"
    with pytest.raises(ExtractionError, match="is not a EdgeKind"):
        assert_valid(bad, "sector_map")


def test_every_error_is_reported_not_just_the_first():
    bad = {"nodes": [{"id": "CO:a", "kind": "Planet"}],
           "edges": [{"source": "CO:a", "target": "CO:ghost", "relation": "orbits",
                      "confidence": "certain", "weight": 3.0, "valid_from": "yesterday"}]}
    errors = validate_extraction(bad, "test")
    assert len(errors) >= 5
    joined = " ".join(errors)
    for fragment in ("NodeKind", "EdgeKind", "Confidence", "not declared",
                     "outside (0, 1]", "not an ISO date"):
        assert fragment in joined


def test_an_edge_claiming_extraction_must_name_the_document():
    """Graphify's rule - provenance on the edge, not only the node. Ours is
    stricter: an EXTRACTED edge asserts a document says so, and must name it."""
    bad = clean_payload()
    del bad["edges"][0]["source_doc_id"]
    errors = validate_extraction(bad)
    assert any("no source_doc_id" in e for e in errors)


def test_an_inferred_edge_needs_no_document():
    ok = clean_payload()
    ok["edges"][0]["confidence"] = "inferred"
    del ok["edges"][0]["source_doc_id"]
    assert validate_extraction(ok) == []


def test_an_edge_to_an_undeclared_node_is_refused():
    bad = clean_payload()
    bad["edges"][0]["target"] = "CO:nowhere"
    assert any("not declared" in e for e in validate_extraction(bad))


def test_a_self_loop_is_refused():
    bad = clean_payload()
    bad["edges"][0]["target"] = "CO:a"
    assert any("relates to itself" in e for e in validate_extraction(bad))


def test_a_duplicate_node_id_is_refused():
    bad = clean_payload()
    bad["nodes"].append({"id": "CO:a", "kind": "Company"})
    assert any("declared twice" in e for e in validate_extraction(bad))


def test_an_edge_with_a_null_valid_from_is_refused_as_silently_invisible():
    bad = clean_payload()
    bad["edges"][0]["valid_from"] = None
    assert any("not traversable at any as-of" in e for e in validate_extraction(bad))


def test_a_missing_required_field_names_the_field():
    bad = clean_payload()
    del bad["edges"][0]["confidence"]
    assert any("missing required field 'confidence'" in e
               for e in validate_extraction(bad))


def test_something_that_is_not_an_extraction_at_all_is_rejected_cleanly():
    assert validate_extraction("nodes and edges")[0].startswith("payload is str")
    assert validate_extraction({"nodes": []}) == ["payload has no 'edges' key"]
    assert validate_extraction({"nodes": {}, "edges": []})[0].startswith("'nodes' is dict")


def test_the_error_message_caps_its_own_length():
    bad = {"nodes": [], "edges": [
        {"source": "x", "target": "y", "relation": "nope",
         "confidence": "nope", "valid_from": "nope"} for _ in range(30)]}
    with pytest.raises(ExtractionError) as exc:
        assert_valid(bad, "flood")
    assert "and " in str(exc.value) and "more" in str(exc.value)
    assert len(exc.value.errors) > 20


def test_the_validator_survives_input_that_is_not_shaped_like_an_extraction():
    """An extractor bug produces nonsense, not a tidy error. The gate must name
    what is wrong rather than raising on its own way in."""
    bad = {
        "nodes": ["CO:a", {"id": "", "kind": "Company"}, {"id": 7, "kind": "Company"},
                  {"id": "CO:b", "kind": "Company"}],
        "edges": [None,
                  {"source": "CO:b", "target": "CO:b", "relation": "supplies",
                   "confidence": "extracted", "source_doc_id": 42,
                   "weight": True, "valid_from": 20260101, "valid_to": ["soon"]}],
    }
    errors = validate_extraction(bad, "chaos")
    joined = " ".join(errors)
    assert "node[0]: expected a dict, got str" in joined
    assert "id must be a non-blank string" in joined
    assert "edge[0]: expected a dict, got NoneType" in joined
    assert "source_doc_id: expected a string, got int" in joined
    assert "weight: expected a number, got True" in joined
    assert "valid_from: expected an ISO date string, got int" in joined
    assert "valid_to: expected an ISO date string, got list" in joined


def test_the_validator_accepts_real_date_objects_as_well_as_iso_strings():
    ok = clean_payload()
    ok["edges"][0]["valid_from"] = date(2026, 1, 1)
    ok["edges"][0]["valid_to"] = date(2027, 1, 1)
    assert validate_extraction(ok) == []
    _, edges = parse(ok)
    assert edges[0].valid_to == date(2027, 1, 1)


def test_a_backwards_interval_is_caught_before_it_reaches_the_edge_constructor():
    bad = clean_payload()
    bad["edges"][0]["valid_to"] = "2025-06-01"
    assert any("contains no days" in e for e in validate_extraction(bad))


def test_an_extraction_with_no_nodes_or_edges_is_valid_and_empty():
    assert validate_extraction({"nodes": [], "edges": []}) == []
    assert parse({"nodes": [], "edges": []}) == ([], [])


def test_a_node_missing_its_required_fields_names_them():
    errors = validate_extraction({"nodes": [{"label": "orphan"}], "edges": []})
    assert "node[0]: missing required field 'id'" in errors
    assert "node[0]: missing required field 'kind'" in errors
