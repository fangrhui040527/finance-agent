"""What the collector keeps priced beyond the book and its proxies.

`ask.py prices --book` warms two more sets at every close slot: the graph
peers of the book names, and the index book's members once the ledger has
opened it. The `price_stale` monitor rule judges exactly what this step
refreshes, so both read the sets from here. Until 2026-09-30 the rule computed
none of it and took every other cached row for a peer: fifteen rows that comps
had asked about once, in no set anything refreshes, stood in a warning whose
next step could never clear them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from markets.registry import mic_of

#: How many peer symbols one `prices --book` run adds beyond the book and its
#: proxies. The graph grows by edits nobody thinks of as price decisions; the
#: cap keeps one of them from turning the collector's price step into a burst
#: a source throttles, and the round-robin in `warm_peers` keeps the cap from
#: starving the names at the end of the list.
WARM_CAP = 24


def graph_lookup() -> Callable[[str, date], set[str]] | None:
    """The graph's peer function, or None when no graph has been built.

    An unbuilt graph reads as "not built" - which `graph_peers` alone cannot
    say: it answers an empty set for that and for a name with no peers alike.
    """
    from pathlib import Path

    from knowledge.graph.build import DEFAULT_DB

    if not Path(DEFAULT_DB).exists():
        return None
    from mcp_server.tools import graph_peers

    return graph_peers


def warm_peers(
    book: list[str], asof: date, lookup: Callable[[str, date], set[str]], cap: int = WARM_CAP
) -> dict[str, str]:
    """Peer id -> the book name it was found through: same market only,
    nothing already in the book, at most `cap` in all.

    Same market because that is the peer set every comparable is built from -
    `peers_of` lists the others as excluded, and a cache warmed for a name no
    table will read is quota spent on nothing. Round-robin over the book rather
    than first come, so that when the cap binds every name keeps its nearest
    peers instead of the first name keeping all of its own.
    """
    queues: list[tuple[str, list[str]]] = []
    for iid in book:
        try:
            home = mic_of(iid)
        except ValueError:
            continue
        peers: list[str] = []
        for peer in sorted(lookup(iid, asof)):
            try:
                same = mic_of(peer) == home
            except ValueError:
                same = False
            if same and peer not in book:
                peers.append(peer)
        queues.append((iid, peers))

    out: dict[str, str] = {}
    while len(out) < cap and any(q for _, q in queues):
        for iid, q in queues:
            while q:
                peer = q.pop(0)
                if peer not in out:
                    out[peer] = iid
                    break
            if len(out) >= cap:
                break
    return out


def index_members(paper_db: str) -> list[str]:
    """The index book's members, or none while the ledger has not opened it."""
    from engines.paper.book import index_universe_path
    from engines.paper.store import INDEX, PaperStore
    from engines.paper.universe import load_universe

    store = PaperStore.open_existing(paper_db)
    if store is None:
        return []
    with store:
        if not store.has_book(INDEX):
            return []
        return list(load_universe(index_universe_path(store)).ids)
