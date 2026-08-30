"""Looking at the graph rather than querying it.

Prevention is in entity_graph (hubs are not routed through, uncitable edges are
not traversed). This is DETECTION: what the graph has quietly become, which is
a different question and the one nobody asks until an answer looks wrong.

Adapted from graphify's analyze.py (Apache-2.0, docs/10) - god nodes, surprising
connections, and graph diff are its ideas. `orphans` and `review_queue` are
ours: docs/02 section 3 makes an entity with no edges the web-search trigger,
and nothing in graphify has an edge-confidence queue because nothing in it
distinguishes an inferred edge from a stated one.

Every function here is a pure read. Nothing writes, so running an analysis can
never be what changed the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from knowledge.graph.entity_graph import (
    HUB_KINDS, HUB_MIN_DEGREE, Confidence, Edge, EdgeKind, EntityGraph, NodeKind, Path,
)


@dataclass(frozen=True)
class GodNode:
    node_id: str
    label: str
    kind: NodeKind
    degree: int
    blocking: bool
    """True when traversal already refuses to route through it."""

    def describe(self) -> str:
        state = "blocked as a waypoint" if self.blocking else "approaching the threshold"
        return f"{self.label} ({self.kind.value}, degree {self.degree}) - {state}"


def god_nodes(graph: EntityGraph, warn_at: float = 0.5) -> list[GodNode]:
    """Nodes that connect too much to mean anything, and the ones getting there.

    Two populations, deliberately reported together:

      - `blocking=True`  - already over the degree threshold, so traversal will
        not route through them. Prevention has happened; this is the receipt.
      - `blocking=False` - a HUB_KINDS node (Sector, Country, Commodity) past
        `warn_at` of the threshold. Hubs by nature, not yet hubs by degree.

    The second is the whole reason HUB_KINDS survives after the traversal rule
    became degree-only. `Malaysia` at degree 30 is on its way to connecting
    everything to everything, and the useful moment to notice is before it does,
    while the sub-sector split that would fix it is still cheap.
    """
    hubs = graph.hubs()
    out: list[GodNode] = []
    for node in graph.nodes():
        degree = graph.degree(node.node_id)
        blocking = node.node_id in hubs
        watched = node.kind in HUB_KINDS and degree >= warn_at * HUB_MIN_DEGREE
        if blocking or watched:
            out.append(GodNode(node.node_id, graph.label(node.node_id), node.kind,
                               degree, blocking))
    return sorted(out, key=lambda g: (-g.degree, g.node_id))


def orphans(graph: EntityGraph) -> list[str]:
    """Nodes nothing connects to. docs/02 section 3: the web-search trigger.

    An orphan is not a bug in the graph - it is the graph saying it has never
    been told anything about this entity, which is exactly when going and
    looking is worth the money.
    """
    return sorted(n.node_id for n in graph.nodes() if graph.degree(n.node_id) == 0)


def review_queue(graph: EntityGraph) -> list[Edge]:
    """Edges a person still has to rule on, weakest first.

    An INFERRED edge is traversable and uncitable: it can shape what the system
    looks at and can never back what it says. It stays in that state until
    somebody promotes it into a curated file with a real basis, or deletes it.

    Without a queue that is not a workflow, it is a landfill - the edges
    accumulate, nothing reads them, and the graph slowly fills with material
    that is neither trusted nor thrown away.
    """
    order = {Confidence.AMBIGUOUS: 0, Confidence.INFERRED: 1}
    pending = [e for e in graph.edges() if e.confidence in order]
    return sorted(pending, key=lambda e: (order[e.confidence], -e.weight,
                                          e.src, e.dst, e.kind.value))


@dataclass(frozen=True)
class Surprise:
    src: str
    dst: str
    path: Path

    def describe(self) -> str:
        return f"{self.src} -> {self.dst}: {self.path.describe()}"


#: A shared classification is not a discovery. Two banks are both banks.
TAXONOMY_EDGES = frozenset({EdgeKind.CLASSIFIED_IN, EdgeKind.OPERATES_IN,
                            EdgeKind.REGULATED_BY})


def surprising_connections(
    graph: EntityGraph,
    *,
    asof: date,
    among: set[str] | None = None,
    min_hops: int = 2,
    max_hops: int = 3,
    limit: int = 50,
) -> list[Surprise]:
    """Company pairs the graph CONNECTS that no single row states.

    Two filters, and both carry the idea.

    `min_hops = 2`, because a one-hop link is not a discovery - it is a row
    somebody wrote in the curated file, and reporting "MISC supplies Petronas
    Chemicals" back to the person who typed it is noise. A surprise is something
    the graph composed: A supplies B, B is exposed to the same commodity as C.

    No taxonomy hop, because almost every pair in a classification graph is
    "connected" through their sub-sector, country or regulator, and reporting
    those buries the handful of links that carry information. A path with no
    taxonomy hop is an economic relationship.

    Citable paths only: a surprise you cannot source is a rumour, and this list
    exists for a person deciding what to spend time looking into.
    """
    companies = [n.node_id for n in graph.nodes() if n.kind is NodeKind.COMPANY
                 and (among is None or n.node_id in among)]
    out: list[Surprise] = []
    seen: set[tuple[str, str]] = set()
    for src in companies:
        for path in graph.traverse(src, asof=asof, max_hops=max_hops):
            dst = path.end
            if dst == src or graph.node(dst) is None:
                continue
            if graph.node(dst).kind is not NodeKind.COMPANY:
                continue
            if among is not None and dst not in among:
                continue
            if path.n_hops < min_hops:
                continue                       # a stated row, not a discovery
            if any(h.edge.kind in TAXONOMY_EDGES for h in path.hops):
                continue
            key = tuple(sorted((src, dst)))
            if key in seen:
                continue
            seen.add(key)
            out.append(Surprise(src, dst, path))
    out.sort(key=lambda s: (-s.path.weight, s.src, s.dst))
    return out[:limit]


@dataclass
class Diff:
    """What one ingest changed. The unit of review."""

    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    added_edges: list[Edge] = field(default_factory=list)
    removed_edges: list[Edge] = field(default_factory=list)
    relabelled: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added_nodes or self.removed_nodes or self.added_edges
                    or self.removed_edges or self.relabelled)

    def describe(self) -> str:
        if self.empty:
            return "no change"
        lines = []
        for nid in self.added_nodes:
            lines.append(f"  + node {nid}")
        for nid in self.removed_nodes:
            lines.append(f"  - node {nid}")
        for nid, before, after in self.relabelled:
            lines.append(f"  ~ node {nid}: {before!r} -> {after!r}")
        for e in self.added_edges:
            lines.append(f"  + {e.src} --{e.kind.value}--> {e.dst} "
                         f"[{e.confidence.value}, from {e.valid_from}]")
        for e in self.removed_edges:
            lines.append(f"  - {e.src} --{e.kind.value}--> {e.dst} "
                         f"[{e.confidence.value}, from {e.valid_from}]")
        return "\n".join(lines)


def _edge_key(e: Edge):
    return (e.src, e.dst, e.kind.value, e.valid_from, e.valid_to,
            e.weight, e.confidence.value, e.source_doc_id)


def graph_diff(before: EntityGraph, after: EntityGraph) -> Diff:
    """What changed between two graphs. How an ingest gets reviewed.

    A build you cannot diff is a build you have to take on trust, which is why
    the store is byte-reproducible in the first place: identical sources give an
    identical file, so anything this reports is a real change to what the system
    believes.

    A "removed" edge in a deterministic rebuild should be rare and is worth
    reading closely - edges are append-only, so it means a source stopped
    asserting something rather than a row being deleted.
    """
    b_nodes = {n.node_id: n for n in before.nodes()}
    a_nodes = {n.node_id: n for n in after.nodes()}
    b_edges = {_edge_key(e): e for e in before.edges()}
    a_edges = {_edge_key(e): e for e in after.edges()}

    return Diff(
        added_nodes=sorted(set(a_nodes) - set(b_nodes)),
        removed_nodes=sorted(set(b_nodes) - set(a_nodes)),
        relabelled=sorted(
            (nid, b_nodes[nid].label, a_nodes[nid].label)
            for nid in set(a_nodes) & set(b_nodes)
            if b_nodes[nid].label != a_nodes[nid].label
        ),
        added_edges=[a_edges[k] for k in sorted(set(a_edges) - set(b_edges),
                                                key=lambda k: tuple(map(str, k)))],
        removed_edges=[b_edges[k] for k in sorted(set(b_edges) - set(a_edges),
                                                  key=lambda k: tuple(map(str, k)))],
    )
