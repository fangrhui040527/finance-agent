"""Who a company's peers are, read off the entity graph as of a date.

Two readings, kept apart because they are not equally strong:

- a **direct** peer is the other end of a live, citable ``competes_with`` edge
  (a human wrote the row and vouches for it: ``curated:supply_chain#<id>``);
- a **same-subsector** peer shares a ``classified_in`` sub-sector. That is two
  hops through the classification spine, and the graph's own per-hop decay
  makes it read as speculative - a shared label is not a stated rivalry.

Either way the peer set is a fact about the graph on a date, with the edge
documents as its evidence. Nothing is inferred from names or sectors that the
graph does not hold; a company the graph does not know gets an empty set that
says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import yaml

from knowledge.graph.entity_graph import EDGE_DECAY, Edge, EdgeKind, EntityGraph, NodeKind
from knowledge.graph.ids import ENTITIES_FILE, display_names, kind_of, node_id
from knowledge.graph.ids import instrument_id as canonical_id
from markets.registry import mic_of

DIRECT_KINDS = (EdgeKind.COMPETES_WITH,)


@lru_cache(maxsize=1)
def _book_ids() -> dict[str, str]:
    """Canonical ``XKLS:1155`` -> the spelling the book writes (``MYX:1155``).

    The graph resolves every id to its MIC; config, the fact book and the paper
    book write the market prefix. entities.yaml is the one file that carries
    both, so it is the map - not a second alias table.
    """
    if not ENTITIES_FILE.exists():
        return {}
    raw = yaml.safe_load(ENTITIES_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for iid in raw.get("companies") or {}:
        canon = canonical_id(str(iid))
        if canon:
            out[canon] = str(iid)
    return out


def book_id(canonical: str) -> str:
    """``XKLS:1155`` -> ``MYX:1155`` when the book knows the name, else unchanged."""
    return _book_ids().get(canonical, canonical)


def _strength(weight: float) -> str:
    """The same thresholds ``Path.strength`` uses, so a peer reads like a path."""
    if weight >= 0.5:
        return "direct"
    if weight >= 0.2:
        return "indirect"
    return "speculative"


@dataclass(frozen=True)
class Peer:
    instrument_id: str  # the book's spelling
    node_id: str
    label: str
    relation: str  # competes_with | same_subsector
    weight: float
    strength: str
    evidence: tuple[str, ...]
    market: str  # MIC

    def describe(self) -> str:
        via = "stated rivalry" if self.relation == "competes_with" else "shared sub-sector only"
        return (
            f"{self.label} ({self.instrument_id}): {self.relation}, weight {self.weight:.2f}, "
            f"{self.strength} ({via})"
        )


@dataclass(frozen=True)
class PeerSet:
    instrument_id: str
    node_id: str | None
    asof: date
    subsector: str | None
    peers: tuple[Peer, ...]
    excluded: tuple[Peer, ...]  # peers in another market, kept out of comparables
    note: str = ""

    @property
    def ids(self) -> set[str]:
        return {p.instrument_id for p in self.peers}

    @property
    def direct(self) -> tuple[Peer, ...]:
        return tuple(p for p in self.peers if p.relation == "competes_with")

    @property
    def same_subsector(self) -> tuple[Peer, ...]:
        return tuple(p for p in self.peers if p.relation == "same_subsector")

    def text(self) -> str:
        if self.node_id is None:
            return f"{self.instrument_id} peers as of {self.asof}: {self.note}"
        head = (
            f"{self.instrument_id} peers as of {self.asof}: {len(self.direct)} stated, "
            f"{len(self.same_subsector)} by sub-sector"
            + (f" ({self.subsector})" if self.subsector else " (no sub-sector recorded)")
        )
        rows = [head] + [f"  - {p.describe()}" for p in self.peers]
        if self.excluded:
            rows.append(
                "  excluded, other market: "
                + ", ".join(f"{p.instrument_id} ({p.market})" for p in self.excluded)
            )
        if self.note:
            rows.append(f"  note: {self.note}")
        return "\n".join(rows)


def _company_end(edge: Edge, me: str) -> str | None:
    other = edge.dst if edge.src == me else edge.src
    return other if kind_of(other) is NodeKind.COMPANY else None


def _peer(graph: EntityGraph, other: str, relation: str, weight: float, evidence) -> Peer:
    canon = other.split(":", 1)[1] if other.startswith("CO:") else other
    iid = book_id(canon)
    try:
        market = mic_of(canon)
    except ValueError:
        market = ""
    label = display_names().get(canon) or graph.label(other)
    return Peer(iid, other, label, relation, weight, _strength(weight), tuple(evidence), market)


def peers_of(
    graph: EntityGraph,
    instrument_id: str,
    asof: date,
    *,
    same_market: bool = True,
    include_subsector: bool = True,
) -> PeerSet:
    """The peer set the graph supports on ``asof``.

    ``same_market`` keeps comparables in one currency and accounting regime;
    peers elsewhere are listed as excluded rather than dropped silently.
    """
    nid = node_id(NodeKind.COMPANY, instrument_id)
    if graph.node(nid) is None:
        return PeerSet(
            instrument_id,
            None,
            asof,
            None,
            (),
            (),
            f"{instrument_id} is not in the graph ({len(graph.nodes())} entities); add it to "
            "knowledge/graph/data/ and rebuild rather than guessing its peers",
        )
    try:
        home = mic_of(instrument_id)
    except ValueError:
        home = ""

    found: dict[str, Peer] = {}
    for edge in graph.neighbours(nid) + graph.inbound(nid):
        if edge.kind not in DIRECT_KINDS or not edge.live_at(asof) or not edge.citable:
            continue
        other = _company_end(edge, nid)
        if other is None:
            continue
        weight = EDGE_DECAY[edge.kind] * edge.weight
        peer = _peer(graph, other, "competes_with", weight, (edge.source_doc_id,))
        if other not in found or found[other].weight < weight:
            found[other] = peer

    subsector: str | None = None
    if include_subsector:
        for up in graph.neighbours(nid):
            if up.kind is not EdgeKind.CLASSIFIED_IN or not up.live_at(asof):
                continue
            if kind_of(up.dst) is not NodeKind.SUBSECTOR:
                continue
            subsector = graph.label(up.dst)
            for down in graph.inbound(up.dst):
                if down.kind is not EdgeKind.CLASSIFIED_IN or not down.live_at(asof):
                    continue
                if (
                    down.src == nid
                    or down.src in found
                    or kind_of(down.src) is not NodeKind.COMPANY
                ):
                    continue
                weight = EDGE_DECAY[EdgeKind.CLASSIFIED_IN] ** 2 * up.weight * down.weight
                evidence = tuple(d for d in (up.source_doc_id, down.source_doc_id) if d)
                found[down.src] = _peer(graph, down.src, "same_subsector", weight, evidence)

    ordered = sorted(
        found.values(), key=lambda p: (p.relation != "competes_with", -p.weight, p.instrument_id)
    )
    if same_market and home:
        kept = tuple(p for p in ordered if p.market == home)
        excluded = tuple(p for p in ordered if p.market != home)
    else:
        kept, excluded = tuple(ordered), ()
    note = ""
    if not kept:
        note = "no live, citable competes_with edge and no sub-sector sibling" + (
            " in the same market" if same_market else ""
        )
    return PeerSet(instrument_id, nid, asof, subsector, kept, excluded, note)
