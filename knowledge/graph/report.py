"""What the graph looks like right now, for a person to read.

Three audiences, one page:

  the operator   what is it made of, and did the last build change anything
  the reviewer   which edges are waiting on a human ruling
  the analyst    what is connected that no single row says

Text, not HTML. This is read next to a terminal that just ran `make graph`, and
a report you have to open a browser for is a report you check less often.
"""

from __future__ import annotations

from datetime import date

from knowledge.graph.analyze import (
    Diff,
    god_nodes,
    graph_diff,
    orphans,
    review_queue,
    surprising_connections,
)
from knowledge.graph.entity_graph import Confidence, EntityGraph, NodeKind

RULE = "-" * 70


def _counts(graph: EntityGraph) -> list[str]:
    by_kind: dict[NodeKind, int] = {}
    for n in graph.nodes():
        by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
    edges = graph.edges()
    citable = sum(1 for e in edges if e.citable)
    lines = [
        f"{len(graph.nodes())} nodes, {len(edges)} edges, "
        f"{citable} citable ({len(edges) - citable} traversable only)"
    ]
    lines += [
        f"  {k.value:12} {v}"
        for k, v in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0].value))
    ]
    return lines


def render(graph: EntityGraph, *, asof: date, diff: Diff | None = None, limit: int = 15) -> str:
    """The whole page. `diff` is what the last build changed, when you have it."""
    out: list[str] = ["KNOWLEDGE GRAPH", RULE]
    out += _counts(graph)

    out += ["", "HUBS", RULE]
    hubs = god_nodes(graph)
    if not hubs:
        out.append("  none - no node is well connected enough to be a meaningless waypoint")
    else:
        out += [f"  {g.describe()}" for g in hubs]
        if any(not g.blocking for g in hubs):
            out.append("  (approaching means: split it before traversal has to refuse it)")

    out += ["", "AWAITING A HUMAN RULING", RULE]
    queue = review_queue(graph)
    if not queue:
        out.append("  none - every edge is EXTRACTED from a named source")
    else:
        out.append(
            f"  {len(queue)} edge{'s' if len(queue) != 1 else ''}: "
            "traversable, and none of them may back a claim"
        )
        for e in queue[:limit]:
            mark = "??" if e.confidence is Confidence.AMBIGUOUS else " ~"
            out.append(
                f"  {mark} {graph.label(e.src)} --{e.kind.value}--> "
                f"{graph.label(e.dst)}  [{e.confidence.value}, "
                f"w={e.weight:.2f}, {e.source_doc_id or 'no source'}]"
            )
        if len(queue) > limit:
            out.append(f"  ... and {len(queue) - limit} more")
        out.append(
            "  promote one into a curated file with a real basis, or "
            "delete it. Leaving it here is neither."
        )

    out += ["", "NOTHING IS CONNECTED TO THESE", RULE]
    lonely = orphans(graph)
    if not lonely:
        out.append("  none")
    else:
        out.append(
            f"  {len(lonely)} orphan{'s' if len(lonely) != 1 else ''} - "
            "docs/02 section 3 makes this the web-search trigger"
        )
        out += [f"    {graph.label(n)} ({n})" for n in lonely[:limit]]

    out += ["", f"CONNECTED WITHOUT BEING WRITTEN DOWN (as of {asof})", RULE]
    surprises = surprising_connections(graph, asof=asof, limit=limit)
    if not surprises:
        out.append("  none - every company link is either direct or runs through a shared label")
    else:
        for s in surprises:
            out.append(f"  {s.path.describe()}")

    modules = [n for n in graph.nodes() if n.kind is NodeKind.PRODUCT]
    if modules:
        from knowledge.graph.analyze import undocumented_modules, untested_modules

        out += ["", "CODE: NOTHING TESTS THESE", RULE]
        untested = untested_modules(graph, ignore=("tests_",))
        if not untested:
            out.append("  none - every module that defines something has a test importing it")
        else:
            out += [f"  {graph.label(n)}" for n in untested[:limit]]
            out.append(
                "  INFERRED: a module exercised only through a helper "
                "reads as untested. Over-reports, never under-reports."
            )
        undoc = undocumented_modules(graph)
        out += ["", "CODE: NO PROSE NAMES THESE", RULE]
        out.append(
            f"  {len(undoc)} of {len(modules)} modules. Most need no page - "
            "read this as a question about the ones you expected written up."
        )

    if diff is not None:
        out += ["", "WHAT THE LAST BUILD CHANGED", RULE, diff.describe()]
    return "\n".join(out)


def render_diff(before: EntityGraph, after: EntityGraph) -> str:
    return graph_diff(before, after).describe()
