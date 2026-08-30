"""Entity graph and multi-hop traversal.

docs/02 A7 and docs/07 P9. The rule that matters: EVERY multi-hop claim ships
with its traversal path attached and a per-hop decay weight. A three-hop
inference is visibly weaker than a direct link, and the UI renders it as such.
An impact claim without a path cannot be emitted.

Three gates, deliberately separate, because conflating them is how a graph
starts laundering inference as evidence:

  1. STRUCTURE - an edge must join two declared nodes, carry a decay entry for
     its kind, and have a weight in (0, 1] so a hop can never strengthen a path.
  2. TIME      - an edge is traversable only at a date its validity interval
     contains. Same discipline core/market/pointintime.py applies to facts, and
     for the same reason: a relationship that became true in 2026 must not be
     visible to a 2020 question.
  3. EVIDENCE  - an edge may back an emitted claim only if it was EXTRACTED from
     a named source document. An INFERRED edge may be traversed and shown; it
     may not be cited.

Confidence is adapted from Graphify-Labs/graphify (Apache-2.0, docs/10), whose
schema requires provenance on every edge and labels each one
EXTRACTED / INFERRED / AMBIGUOUS. The binary `citable` that used to live here was
the two-value version of the same idea.

Backed by an in-memory adjacency structure with the same interface a Neo4j
driver would expose, so swapping the store touches this file only.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:                                  # pragma: no cover
    from core.contracts.answer import Citation


class GraphSchemaError(RuntimeError):
    """A table in this module is internally inconsistent. Raised at import.

    A missing decay entry used to be a KeyError in the middle of a user's query.
    Startup is the honest place to fail: the tables are checked in below.
    """


class NodeKind(str, Enum):
    COMPANY = "Company"
    SECTOR = "Sector"
    SUBSECTOR = "SubSector"
    COUNTRY = "Country"
    PRODUCT = "Product"
    TECHNOLOGY = "Technology"
    COMMODITY = "Commodity"
    REGULATOR = "Regulator"
    EVENT = "Event"


class EdgeKind(str, Enum):
    SUPPLIES = "supplies"
    CUSTOMER_OF = "customer_of"
    COMPETES_WITH = "competes_with"
    OWNS = "owns"
    OPERATES_IN = "operates_in"
    EXPOSED_TO = "exposed_to"
    SUBSTITUTES = "substitutes"
    REGULATED_BY = "regulated_by"
    CLASSIFIED_IN = "classified_in"
    AFFECTS = "affects"


class Confidence(str, Enum):
    """How the edge came to exist. Only one of these may support a claim.

    The ordering is deliberate: a missing or unstated confidence falls to the
    WEAKEST value, never a midpoint. Absence of a label is an absence of
    evidence about the edge, so the safe reading is the least it could be - not
    a coin flip that reads as half-supported.
    """

    EXTRACTED = "extracted"    # stated in a source: a filing field, a registry entry
    INFERRED = "inferred"      # deduced: shared classification, co-occurrence
    AMBIGUOUS = "ambiguous"    # uncertain - never backs a claim; goes to review


# How much signal survives one hop of each kind. A supply relationship carries
# more than a shared sector label.
EDGE_DECAY: dict[EdgeKind, float] = {
    EdgeKind.SUPPLIES: 0.70,
    EdgeKind.CUSTOMER_OF: 0.70,
    EdgeKind.OWNS: 0.85,
    EdgeKind.EXPOSED_TO: 0.60,
    EdgeKind.COMPETES_WITH: 0.45,
    EdgeKind.SUBSTITUTES: 0.45,
    EdgeKind.OPERATES_IN: 0.40,
    EdgeKind.CLASSIFIED_IN: 0.35,
    EdgeKind.REGULATED_BY: 0.50,
    EdgeKind.AFFECTS: 0.75,
}

# The reverse reading of a relation, where one exists. Only these kinds may be
# added bidirectionally.
#
# The bug this fixes: bidirectional used to mint the reverse edge with the SAME
# kind, so "A supplies B" quietly also asserted "B supplies A" - reversing a
# supply chain and every exposure conclusion drawn through it.
#
# The absences are the point. "A owns B" does not mean "B owns A", and there is
# no OWNED_BY in the vocabulary, so ownership cannot be made bidirectional by
# accident. A caller who wants the reverse reading must model it and say what
# document supports it.
EDGE_INVERSE: dict[EdgeKind, EdgeKind] = {
    EdgeKind.SUPPLIES: EdgeKind.CUSTOMER_OF,
    EdgeKind.CUSTOMER_OF: EdgeKind.SUPPLIES,
    EdgeKind.COMPETES_WITH: EdgeKind.COMPETES_WITH,   # symmetric
    EdgeKind.SUBSTITUTES: EdgeKind.SUBSTITUTES,       # symmetric
    # "A is exposed to B" and "B affects A" are one relationship read from each
    # end, and both readings are needed: without the pair, "aluminium fell, who
    # is hurt" traverses out of the commodity and finds nothing, because _out is
    # directed. The decays differ deliberately - a shock propagating outward
    # from a commodity (0.75) is a stronger inference than guessing a company's
    # inputs from the company (0.60).
    EdgeKind.EXPOSED_TO: EdgeKind.AFFECTS,
    EdgeKind.AFFECTS: EdgeKind.EXPOSED_TO,
}

MAX_HOPS = 4
MIN_PATH_WEIGHT = 0.05

#: Below this degree nothing is a hub, however lopsided the graph is. Without a
#: floor a five-node graph declares its busiest node a hub at degree 4 and stops
#: answering anything.
HUB_MIN_DEGREE = 50

#: Hubs by nature, once they get big. NOT a traversal veto - the repo's own
#: canonical path is event -> Sector -> your holding, and vetoing Sector by kind
#: would delete the exact question the graph exists to answer. What makes
#: `Malaysia` a bad waypoint is having four hundred neighbours, not being a
#: Country, so the veto is degree-based (see EntityGraph.hubs) and this list is
#: only the early-warning surface analyze.god_nodes() reports on.
HUB_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.SECTOR, NodeKind.COUNTRY, NodeKind.COMMODITY}
)


def _check_tables() -> None:
    """Every EdgeKind needs a decay, and inversion must be an involution."""
    missing = sorted(k.value for k in EdgeKind if k not in EDGE_DECAY)
    if missing:
        raise GraphSchemaError(
            f"EDGE_DECAY has no entry for {', '.join(missing)}. Every EdgeKind needs "
            "one, or traversal fails mid-query on whichever path happens to reach it."
        )
    bad = [f"{k.value}->{v.value}" for k, v in EDGE_INVERSE.items()
           if EDGE_INVERSE.get(v) is not k]
    if bad:
        raise GraphSchemaError(
            f"EDGE_INVERSE is not self-consistent: {', '.join(bad)}. "
            "The inverse of the inverse must be the original relation."
        )
    for kind, decay in EDGE_DECAY.items():
        if not 0.0 < decay <= 1.0:
            raise GraphSchemaError(
                f"EDGE_DECAY[{kind.value}] = {decay} is outside (0, 1]. A hop that "
                "does not lose signal makes a four-hop guess look like a filing."
            )


_check_tables()


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: NodeKind
    label: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    """One directed relation, stamped with where it came from and when it held.

    valid_from / valid_to describe a HALF-OPEN interval [valid_from, valid_to):
    the closing date is the first day the relation no longer holds. Half-open is
    what makes supersession clean - a contract that ends the day another begins
    produces exactly one live edge on that date, not two and not zero.

    valid_from = None means the start is unknown, and an edge with an unknown
    start is not traversable at any date. That is the point-in-time rule, not an
    oversight: an edge that cannot say when it became true cannot be shown to a
    question asked before it did.
    """

    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0
    source_doc_id: str | None = None
    confidence: Confidence = Confidence.AMBIGUOUS
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(
                f"edge weight {self.weight} is outside (0, 1]: a weight above 1 lets a "
                f"hop strengthen a path, which defeats decay ({self.src} -> {self.dst})"
            )
        if self.valid_to is not None:
            if self.valid_from is None:
                raise ValueError(
                    f"edge {self.src} -> {self.dst} closes on {self.valid_to} but never "
                    "states when it opened"
                )
            if self.valid_to <= self.valid_from:
                raise ValueError(
                    f"edge {self.src} -> {self.dst} is valid [{self.valid_from}, "
                    f"{self.valid_to}), which contains no days"
                )

    @property
    def sourced(self) -> bool:
        return bool(self.source_doc_id and self.source_doc_id.strip())

    @property
    def citable(self) -> bool:
        """An edge backs a claim only if a document states it.

        Two conditions, not one. A source document is necessary and no longer
        sufficient: an edge INFERRED from two companies sharing a sector label
        can name the document the labels came from, and still is not a document
        saying those companies are related.
        """
        return self.sourced and self.confidence is Confidence.EXTRACTED

    def live_at(self, on: date) -> bool:
        """Was this relation in force on `on`? See the interval note above."""
        if self.valid_from is None or on < self.valid_from:
            return False
        return self.valid_to is None or on < self.valid_to


@dataclass(frozen=True)
class Hop:
    edge: Edge
    from_label: str
    to_label: str

    def describe(self) -> str:
        return f"{self.from_label} --{self.edge.kind.value}--> {self.to_label}"


@dataclass(frozen=True)
class Path:
    hops: tuple[Hop, ...]
    weight: float

    @property
    def n_hops(self) -> int:
        return len(self.hops)

    @property
    def start(self) -> str:
        return self.hops[0].edge.src if self.hops else ""

    @property
    def end(self) -> str:
        return self.hops[-1].edge.dst if self.hops else ""

    @property
    def citable(self) -> bool:
        """Every hop must carry a source, or the path cannot be emitted."""
        return bool(self.hops) and all(h.edge.citable for h in self.hops)

    @property
    def weakest_confidence(self) -> Confidence:
        """A path is only as good as its worst hop. What the review list sorts on."""
        order = [Confidence.AMBIGUOUS, Confidence.INFERRED, Confidence.EXTRACTED]
        return min((h.edge.confidence for h in self.hops),
                   key=order.index, default=Confidence.AMBIGUOUS)

    @property
    def strength(self) -> str:
        if self.weight >= 0.5:
            return "direct"
        if self.weight >= 0.2:
            return "indirect"
        return "speculative"

    def describe(self) -> str:
        chain = " · ".join(h.describe() for h in self.hops)
        return f"[{self.strength}, {self.n_hops} hop{'s' if self.n_hops != 1 else ''}, w={self.weight:.2f}] {chain}"

    def evidence(self) -> tuple[str, ...]:
        return tuple(h.edge.source_doc_id for h in self.hops if h.edge.source_doc_id)


class PathRequired(ValueError):
    """docs/02 A7: an impact claim without a traversal path cannot be emitted."""


class EntityGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}
        self._hubs: frozenset[str] | None = None

    def add_node(self, node: Node) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: Edge, bidirectional: bool = False) -> None:
        for nid in (edge.src, edge.dst):
            if nid not in self._nodes:
                raise KeyError(f"node {nid!r} must be added before an edge referencing it")
        self._index(edge)
        if bidirectional:
            inverse = EDGE_INVERSE.get(edge.kind)
            if inverse is None:
                raise ValueError(
                    f"{edge.kind.value!r} has no inverse relation, so it cannot be added "
                    f"bidirectionally. Reversing it would assert something no document "
                    f"says. Add the reverse edge explicitly with its own source."
                )
            self._index(Edge(
                edge.dst, edge.src, inverse, edge.weight, edge.source_doc_id,
                edge.confidence, edge.valid_from, edge.valid_to,
            ))

    def _index(self, edge: Edge) -> None:
        self._out.setdefault(edge.src, []).append(edge)
        self._in.setdefault(edge.dst, []).append(edge)
        self._hubs = None                      # degrees changed; recompute lazily

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[Node]:
        return [self._nodes[k] for k in sorted(self._nodes)]

    def edges(self) -> list[Edge]:
        return [e for src in sorted(self._out) for e in self._out[src]]

    def label(self, node_id: str) -> str:
        n = self._nodes.get(node_id)
        return n.label or node_id if n else node_id

    def neighbours(self, node_id: str) -> list[Edge]:
        """Edges leaving this node."""
        return self._out.get(node_id, [])

    def inbound(self, node_id: str) -> list[Edge]:
        """Edges arriving at this node. Answers 'who supplies X' without a scan."""
        return self._in.get(node_id, [])

    def candidates(self, raw: str) -> list[str]:
        """Every node a typed name could mean. Empty, one, or several.

        The label pass matters because every report prints labels, so the
        obvious thing a person does is copy one back in - and `Thermal coal` is
        stored as `CM:coal`, which no minting rule would produce from its own
        label.
        """
        from knowledge.graph.ids import IdError, node_id

        if raw in self._nodes:
            return [raw]
        found: list[str] = []
        for kind in NodeKind:
            try:
                candidate = node_id(kind, raw)
            except IdError:
                continue
            if candidate in self._nodes and candidate not in found:
                found.append(candidate)
        folded = " ".join(raw.strip().split()).casefold()
        found += [n.node_id for n in self._nodes.values()
                  if n.label.casefold() == folded and n.node_id not in found]
        return sorted(found)

    def resolve(self, raw: str) -> str | None:
        """A typed name -> exactly one node id, or None.

        One resolver, on the graph, because every surface needs it and two
        copies drift - the CLI and the MCP tool each grew their own, and each
        forgot a different NodeKind.

        AMBIGUITY IS REFUSED, not broken by enum order. `Aluminium` is both a
        sub-sector and a commodity in the shipped data, and silently preferring
        one answers a question the user did not ask. Callers show
        `candidates()` and let the person choose.
        """
        found = self.candidates(raw)
        return found[0] if len(found) == 1 else None

    def degree(self, node_id: str) -> int:
        return len(self._out.get(node_id, [])) + len(self._in.get(node_id, []))

    def hubs(self) -> frozenset[str]:
        """Nodes too well connected to be a meaningful waypoint.

        A node is a hub at degree >= max(HUB_MIN_DEGREE, p99 degree). Traversal
        may terminate AT one; it may not route THROUGH one unless it is the seed.

        Without this, "Maybank and NVDA are connected" is true - via
        `Malaysia -> ... -> United States` - and worthless. The p99 term catches
        a hub nobody predicted; the floor stops a sparse graph inventing one.

        Degree, not kind. A shipping sector with three members IS the exposure
        path; a financials sector with sixty is noise. The distinction is how
        many neighbours it has, and only degree can see that.
        """
        if self._hubs is not None:
            return self._hubs
        degrees = sorted(self.degree(n) for n in self._nodes)
        if not degrees:
            self._hubs = frozenset()
            return self._hubs
        # Nearest-rank p99: the smallest degree at or above the 99th percentile.
        p99 = degrees[min(len(degrees) - 1, int(0.99 * len(degrees)))]
        cutoff = max(HUB_MIN_DEGREE, p99)
        self._hubs = frozenset(n for n in self._nodes if self.degree(n) >= cutoff)
        return self._hubs

    def traverse(
        self,
        start: str,
        *,
        asof: date,
        target: str | None = None,
        max_hops: int = MAX_HOPS,
        min_weight: float = MIN_PATH_WEIGHT,
        require_citable: bool = True,
        allowed_edges: set[EdgeKind] | None = None,
    ) -> list[Path]:
        """Best-first search over decayed path weight, as of a stated date.

        Returns paths sorted strongest first. Weight multiplies the per-hop decay
        by the edge's own confidence, so three weak hops cannot outrank one
        strong link.

        `asof` is required, not defaulted. A traversal with no date silently
        answers a question about today using edges that may not have existed when
        the question was asked, which is the look-ahead bug in graph form.

        THIS IS A HEURISTIC, NOT AN EXHAUSTIVE SEARCH. A node is re-expanded only
        on a strictly better weight, so a lower-weight route to the target is not
        explored once a higher-weight route to the same intermediate node has
        been seen - even when the higher-weight one dead-ends. That bound is what
        keeps a dense graph queryable; the consequence is that a missing path
        means "not found cheaply", never "does not exist". Do not build a
        negative claim on an empty result.
        """
        if start not in self._nodes:
            return []
        # Computed once per traversal. The seed is exempt: asking "what is
        # exposed to Malaysia" is a legitimate question, and refusing to leave
        # the node you were asked about answers nothing.
        hubs = self.hubs() - {start}
        results: list[Path] = []
        # (-weight, counter, node, hops)
        counter = 0
        frontier: list[tuple[float, int, str, tuple[Hop, ...]]] = [(-1.0, 0, start, ())]
        best_seen: dict[str, float] = {start: 1.0}

        while frontier:
            neg_w, _, node_id, hops = heapq.heappop(frontier)
            w = -neg_w
            if len(hops) >= max_hops:
                continue
            for edge in self.neighbours(node_id):
                if allowed_edges and edge.kind not in allowed_edges:
                    continue
                if require_citable and not edge.citable:
                    continue
                if not edge.live_at(asof):
                    continue
                if any(h.edge.dst == edge.dst for h in hops) or edge.dst == start:
                    continue                       # no cycles
                nw = w * EDGE_DECAY[edge.kind] * edge.weight
                if nw < min_weight:
                    continue
                new_hops = hops + (Hop(edge, self.label(edge.src), self.label(edge.dst)),)
                path = Path(new_hops, nw)
                if target is None or edge.dst == target:
                    results.append(path)
                if edge.dst in hubs:
                    continue           # may end at a hub, never route through one
                if nw > best_seen.get(edge.dst, 0.0):
                    best_seen[edge.dst] = nw
                    counter += 1
                    heapq.heappush(frontier, (-nw, counter, edge.dst, new_hops))
        results.sort(key=lambda p: -p.weight)
        return results

    def impact_of(
        self,
        event_node: str,
        holdings: set[str],
        *,
        asof: date,
        max_hops: int = MAX_HOPS,
    ) -> list[tuple[str, Path]]:
        """Event -> country -> sector -> supplier -> your holding.

        The multi-hop question vector search cannot answer. Returns one best path
        per affected holding, strongest first.
        """
        best: dict[str, Path] = {}
        for path in self.traverse(event_node, asof=asof, max_hops=max_hops):
            if path.end in holdings and path.weight > best.get(path.end, Path((), 0.0)).weight:
                best[path.end] = path
        return sorted(best.items(), key=lambda kv: -kv[1].weight)


def require_path(claim: str, path: Path | None) -> Path:
    """Gate every emitted impact claim. There is no unpathed variant."""
    if path is None or not path.hops:
        raise PathRequired(f"impact claim {claim!r} has no traversal path and cannot be emitted")
    if not path.citable:
        raise PathRequired(f"impact claim {claim!r} traverses an edge with no source document")
    return path


def path_to_citations(
    path: Path,
    lookup: Callable[[str], "Citation | None"],
) -> list["Citation"]:
    """Turn a traversal path into citations the output gate can verify.

    The seam that was missing. Path.evidence() hands back source_doc_id strings;
    Claim wants Citation objects carrying a verbatim quoted_span that
    verify_claim rechecks against the chunk. Without this function every
    graph-backed claim reached the output gate uncited and was dropped, so a
    multi-hop exposure finding could not be emitted in any configuration.

    `lookup(source_doc_id) -> Citation | None` is the corpus talking. The graph
    stores which document supports an edge; only the corpus knows the text.

    ALL-OR-NOTHING, on purpose. A partially cited path is the dangerous case,
    worse than an uncited one: the claim passes verification on the hops that
    were cited while an uncited hop carries the actual inferential load. It
    reads as evidence and is not. Refuse the whole path instead.
    """
    require_path("path -> citations", path)
    out: list["Citation"] = []
    for hop in path.hops:
        doc_id = hop.edge.source_doc_id
        cite = lookup(doc_id) if doc_id else None
        if cite is None:
            raise PathRequired(
                f"hop {hop.describe()} cites {doc_id!r}, which the corpus cannot "
                f"produce. A path is cited whole or not at all."
            )
        out.append(cite)
    return out
