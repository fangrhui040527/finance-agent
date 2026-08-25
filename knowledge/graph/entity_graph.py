"""Entity graph and multi-hop traversal.

docs/02 A7 and docs/07 P9. The rule that matters: EVERY multi-hop claim ships
with its traversal path attached and a per-hop decay weight. A three-hop
inference is visibly weaker than a direct link, and the UI renders it as such.
An impact claim without a path cannot be emitted.

Backed by an in-memory adjacency structure with the same interface a Neo4j
driver would expose, so swapping the store touches this file only.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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

MAX_HOPS = 4
MIN_PATH_WEIGHT = 0.05


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: NodeKind
    label: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0
    source_doc_id: str | None = None
    as_of: datetime | None = None

    @property
    def citable(self) -> bool:
        """An edge with no source document cannot support an emitted claim."""
        return self.source_doc_id is not None


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
        return all(h.edge.citable for h in self.hops)

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

    def add_node(self, node: Node) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: Edge, bidirectional: bool = False) -> None:
        for nid in (edge.src, edge.dst):
            if nid not in self._nodes:
                raise KeyError(f"node {nid!r} must be added before an edge referencing it")
        self._out.setdefault(edge.src, []).append(edge)
        if bidirectional:
            self._out.setdefault(edge.dst, []).append(
                Edge(edge.dst, edge.src, edge.kind, edge.weight, edge.source_doc_id, edge.as_of)
            )

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def label(self, node_id: str) -> str:
        n = self._nodes.get(node_id)
        return n.label or node_id if n else node_id

    def neighbours(self, node_id: str) -> list[Edge]:
        return self._out.get(node_id, [])

    def traverse(
        self,
        start: str,
        target: str | None = None,
        max_hops: int = MAX_HOPS,
        min_weight: float = MIN_PATH_WEIGHT,
        require_citable: bool = True,
        allowed_edges: set[EdgeKind] | None = None,
    ) -> list[Path]:
        """Best-first search over decayed path weight.

        Returns paths sorted strongest first. Weight multiplies the per-hop decay
        by the edge's own confidence, so three weak hops cannot outrank one
        strong link.
        """
        if start not in self._nodes:
            return []
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
                if any(h.edge.dst == edge.dst for h in hops) or edge.dst == start:
                    continue                       # no cycles
                nw = w * EDGE_DECAY[edge.kind] * edge.weight
                if nw < min_weight:
                    continue
                new_hops = hops + (Hop(edge, self.label(edge.src), self.label(edge.dst)),)
                path = Path(new_hops, nw)
                if target is None or edge.dst == target:
                    results.append(path)
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
        max_hops: int = MAX_HOPS,
    ) -> list[tuple[str, Path]]:
        """Event -> country -> sector -> supplier -> your holding.

        The multi-hop question vector search cannot answer. Returns one best path
        per affected holding, strongest first.
        """
        best: dict[str, Path] = {}
        for path in self.traverse(event_node, max_hops=max_hops):
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
