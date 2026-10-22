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
from datetime import date
from pathlib import Path

from knowledge.graph.entity_graph import NodeKind
from knowledge.graph.store import DETERMINISTIC, GraphStore
from knowledge.graph.validate import parse

DEFAULT_DB = "data/graph.db"
CODE_DB = "data/codegraph.db"


@dataclass
class BuildReport:
    per_extractor: dict[str, tuple[int, int]] = field(default_factory=dict)
    nodes: int = 0
    edges: int = 0
    citable: int = 0
    hubs: list[str] = field(default_factory=list)
    closed: list[tuple] = field(default_factory=list)
    """Edges this build's sources stopped asserting, closed rather than deleted."""
    reasserted: list[tuple] = field(default_factory=list)
    """Closed edges the sources assert again, opened as a new interval."""

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
            "  hubs             "
            + (
                ", ".join(self.hubs)
                if self.hubs
                else "none - no node is well connected enough to be a meaningless waypoint yet"
            )
        )
        if self.reasserted:
            lines.append(
                f"  reasserted       {len(self.reasserted)} closed edge"
                f"{'s' if len(self.reasserted) != 1 else ''} the sources assert again, "
                "opened as a new interval"
            )
        if self.closed:
            lines.append(
                f"  closed           {len(self.closed)} edge"
                f"{'s' if len(self.closed) != 1 else ''} the sources no "
                f"longer assert (closed, not deleted - history stands)"
            )
            lines += [f"    {src} --{kind}--> {dst}" for src, dst, kind, _ in self.closed[:10]]
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


def build(
    store: GraphStore,
    extractors=None,
    cfg=None,
    tier: str = DETERMINISTIC,
    skip_markets: bool = False,
    prune_on: date | None = None,
    today: date | None = None,
    summarize: bool = True,
) -> BuildReport:
    """Extract, validate, store.

    `prune_on` closes edges of THIS tier that the sources no longer produce,
    dated that day. Without it a deleted curated row stays asserted forever;
    with it, only this tier is touched - a semantic tier's edges survive a
    deterministic rebuild, which is what the tier column is for.

    `summarize=False` skips the report's counts and hubs, which load the whole
    graph: once per sweep is nothing, once per batch of a 22,000-row rebuild is
    most of its time.
    """
    from knowledge.graph.extractors.market_registry import MarketsExtractor

    extractors = list(default_extractors(cfg) if extractors is None else extractors)
    report = BuildReport()
    collected: list[tuple[str, list, list]] = []
    instruments: set[str] = set()

    for ex in extractors:
        nodes, edges = parse(ex.extract(), ex.name)
        collected.append((ex.name, nodes, edges))
        instruments |= {n.node_id for n in nodes if n.kind is NodeKind.COMPANY}

    if not skip_markets and not any(getattr(e, "name", "") == "markets" for e in extractors):
        mx = MarketsExtractor(sorted(instruments))
        nodes, edges = parse(mx.extract(), mx.name)
        collected.append((mx.name, nodes, edges))

    # Nodes first, all of them: an edge whose endpoint has not been stored yet is
    # a KeyError, and two extractors legitimately share endpoints.
    for name, nodes, edges in collected:
        report.per_extractor[name] = (len(nodes), len(edges))
        for n in sorted(nodes, key=lambda n: n.node_id):
            store.add_node(n, tier=tier)
    on = prune_on or today or date.today()
    for _, _, edges in collected:
        for e in sorted(edges, key=lambda e: (e.src, e.dst, e.kind.value, e.valid_from or "")):
            store.add_edge(e, tier=tier)
            if store.reassert(e, tier, on):
                report.reasserted.append((e.src, e.dst, e.kind.value))

    if prune_on is not None:
        report.closed = store.close_missing(
            tier, [e for _, _, edges in collected for e in edges], prune_on
        )

    if not summarize:
        return report
    counts = store.counts()
    report.nodes, report.edges = counts["nodes"], counts["edges"]
    report.citable = counts["citable"]
    graph = store.load()
    report.hubs = sorted(graph.hubs())
    return report


def link_articles(store: GraphStore, articles, tier: str = DETERMINISTIC) -> None:
    """Stored articles into the event layer, with their companies' markets.

    The one path both a sweep and a rebuild from the corpus take
    (knowledge/graph/derive.py), so linking as you go and rebuilding from
    scratch cannot disagree about what an article put in the graph."""
    from knowledge.graph.extractors.gdelt import GdeltExtractor

    build(store, extractors=[GdeltExtractor(articles)], tier=tier, summarize=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the knowledge graph, offline")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"output (default {DEFAULT_DB})")
    ap.add_argument(
        "--code",
        action="store_true",
        help="build the CODEBASE graph instead: modules, symbols, "
        f"imports and calls over this repository (-> {CODE_DB})",
    )
    ap.add_argument("--root", default=".", help="repository root for --code")
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="delete the database first, discarding EVERY tier's "
        "history. Prefer --prune, which closes only what this "
        "tier stopped asserting and leaves other tiers alone.",
    )
    ap.add_argument(
        "--prune",
        action="store_true",
        help="close edges of this tier the sources no longer assert, "
        "dated today. Closed, never deleted.",
    )
    args = ap.parse_args(argv)

    path = Path(args.db if args.db != DEFAULT_DB or not args.code else CODE_DB)
    from knowledge.corpus import CORPUS_DB
    from knowledge.graph import derive

    if derive.derived(path) or (not args.code and derive.wanted(path, CORPUS_DB)):
        # Its stamp says which corpus row it was derived to; a spine build or a
        # prune here would change it behind that stamp's back (a prune closes
        # every market edge the spine does not assert, events' companies too).
        # And where it is not built yet but should be derived, a spine-only file
        # here would be a graph with no stamp, which nothing then takes over.
        print(
            f"{path} is rebuilt from the corpus (knowledge/graph/derive.py) and is not "
            "built here. `python ask.py stores graph` brings it up to date.",
            file=sys.stderr,
        )
        return 2
    if args.rebuild and path.exists():
        path.unlink()
    with GraphStore(path) as store:
        prune_on = date.today() if args.prune else None
        if args.code:
            from knowledge.graph.extractors.code import CodeExtractor

            report = build(
                store, extractors=[CodeExtractor(args.root)], skip_markets=True, prune_on=prune_on
            )
        else:
            report = build(store, prune_on=prune_on)
    print(report.describe())
    print(f"\n  written to {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
