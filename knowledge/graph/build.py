"""extract -> validate -> store. The only path into the graph.

    python -m knowledge.graph.build              # -> data/graph.db
    make graph

Three properties this stage owes:

  1. NOTHING REACHES THE STORE UNVALIDATED. Every payload goes through
     validate.parse, which raises. Graphify warns and builds anyway, so a
     malformed extractor degrades its graph quietly for months; here a bad
     extractor fails the build that produced it.

  2. REPRODUCIBLE TO THE BYTE. Extractors emit sorted, build inserts sorted, and
     the store's insert is idempotent on (src, dst, kind, valid_from). Build
     twice, diff the files, expect nothing - which is a test, not a hope. A
     graph you cannot rebuild identically is one you cannot review a change to.

  3. TIER-SCOPED. Everything written here is tier='deterministic'. When a model
     tier arrives it writes its own, and neither rebuild wipes the other.

Parallel edges are kept, never collapsed. Two companies can both compete and
share a sub-sector, and the store holds both because they are different facts.
Graphify's worst reported bug was a generic relation overwriting 144 specific
ones during exactly this step; the shape that prevents it is keeping both.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from knowledge.graph.entity_graph import Confidence, NodeKind
from knowledge.graph.store import DETERMINISTIC, GraphStore
from knowledge.graph.validate import parse

DEFAULT_DB = "data/graph.db"


@dataclass
class BuildReport:
    per_extractor: dict[str, tuple[int, int]] = field(default_factory=dict)
    nodes: int = 0
    edges: int = 0
    citable: int = 0
    hubs: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = ["knowledge graph"]
        for name in sorted(self.per_extractor):
            n, e = self.per_extractor[name]
            lines.append(f"  {name:16} {n:5} nodes  {e:5} edges")
        lines.append(f"  {'TOTAL':16} {self.nodes:5} nodes  {self.edges:5} edges")
        lines.append(
            f"  citable          {self.citable:5} of {self.edges} edges can back a claim; "
            f"the rest are traversable only"
        )
        lines.append(
            "  hubs             " + (", ".join(self.hubs) if self.hubs else
                                     "none - no node is well connected enough to "
                                     "be a meaningless waypoint yet")
        )
        return "\n".join(lines)


def default_extractors(cfg=None):
    """The five deterministic sources, in dependency order.

    market_registry runs LAST because it needs the union of every instrument the
    others declared - it maps companies to their market, and cannot know which
    companies exist until they do.
    """
    from knowledge.graph.extractors.config_book import ConfigBookExtractor
    from knowledge.graph.extractors.curated import CuratedExtractor
    from knowledge.graph.extractors.sectors import SectorExtractor
    return [ConfigBookExtractor.from_config(cfg), SectorExtractor(), CuratedExtractor()]


def build(store: GraphStore, extractors=None, cfg=None,
          tier: str = DETERMINISTIC) -> BuildReport:
    from knowledge.graph.extractors.market_registry import MarketsExtractor

    extractors = list(default_extractors(cfg) if extractors is None else extractors)
    report = BuildReport()
    collected: list[tuple[str, list, list]] = []
    instruments: set[str] = set()

    for ex in extractors:
        nodes, edges = parse(ex.extract(), ex.name)
        collected.append((ex.name, nodes, edges))
        instruments |= {n.node_id for n in nodes if n.kind is NodeKind.COMPANY}

    if not any(getattr(e, "name", "") == "markets" for e in extractors):
        mx = MarketsExtractor(sorted(instruments))
        nodes, edges = parse(mx.extract(), mx.name)
        collected.append((mx.name, nodes, edges))

    # Nodes first, all of them: an edge whose endpoint has not been stored yet is
    # a KeyError, and two extractors legitimately share endpoints.
    for name, nodes, edges in collected:
        report.per_extractor[name] = (len(nodes), len(edges))
        for n in sorted(nodes, key=lambda n: n.node_id):
            store.add_node(n, tier=tier)
    for _, _, edges in collected:
        for e in sorted(edges, key=lambda e: (e.src, e.dst, e.kind.value,
                                              e.valid_from or "")):
            store.add_edge(e, tier=tier)

    counts = store.counts()
    report.nodes, report.edges = counts["nodes"], counts["edges"]
    report.citable = counts["citable"]
    graph = store.load()
    report.hubs = sorted(graph.hubs())
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the knowledge graph, offline")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"output (default {DEFAULT_DB})")
    ap.add_argument("--rebuild", action="store_true",
                    help="delete the database first. Edges are append-only, so a "
                         "rebuild that must not inherit history needs a new file.")
    args = ap.parse_args(argv)

    path = Path(args.db)
    if args.rebuild and path.exists():
        path.unlink()
    with GraphStore(path) as store:
        report = build(store)
    print(report.describe())
    print(f"\n  written to {path}")
    return 0


if __name__ == "__main__":                      # pragma: no cover
    sys.exit(main())
