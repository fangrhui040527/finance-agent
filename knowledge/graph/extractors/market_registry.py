"""Where a company is listed, and who regulates it there.

Named market_registry, not markets: a module called markets.py inside a package
shadows the top-level markets package for anything run with this directory on
sys.path, and the failure is an import error in an unrelated file.

Reads markets/registry.py - the adapter set this repo already trusts for cost
floors and calendars - so the graph cannot disagree with the sizing engine about
which market a name belongs to.

Takes its instrument set as an ARGUMENT rather than reading the book itself.
That keeps it a pure function of its input while still depending on what every
other extractor declared: build runs this last, over the union.
"""

from __future__ import annotations

from datetime import date

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.base import (
    Extractor, company_node, edge, node, sorted_payload)
from knowledge.graph.ids import PREFIX, instrument_id, node_id

#: A registry entry is a stated fact about a market, so the registry is the
#: document. Naming a filing here would be a citation nobody can produce.
DOC = "markets/registry.py"

#: These relationships hold as long as the listing does. No adapter records when
#: a company listed, and inventing a date per company would be worse than one
#: honest floor: this is when the repository began asserting the mapping.
ASSERTED_FROM = date(2020, 1, 1)

COUNTRY_NAMES = {"MY": "Malaysia", "US": "United States",
                 "SG": "Singapore", "HK": "Hong Kong"}


class MarketsExtractor(Extractor):
    name = "markets"

    def __init__(self, instruments=()) -> None:
        self.instruments = tuple(instruments)

    def extract(self) -> dict:
        from markets.registry import get, supported

        nodes: list[dict] = []
        edges: list[dict] = []
        wanted: dict[str, list[str]] = {}
        for raw in self.instruments:
            iid = instrument_id(_strip_prefix(raw))
            if not iid:
                continue                       # not a listed instrument; skip quietly
            mic = iid.split(":", 1)[0]
            if mic in supported():
                wanted.setdefault(mic, []).append(node_id(NodeKind.COMPANY, iid))

        for mic in sorted(wanted):
            a = get(mic)
            country = node(NodeKind.COUNTRY, a.country,
                           COUNTRY_NAMES.get(a.country, a.country), mic=mic)
            regulator = node(NodeKind.REGULATOR, a.regulator, mic=mic)
            nodes += [country, regulator]
            for cid in sorted(set(wanted[mic])):
                nodes.append(company_node(cid, mic=mic))
                edges.append(edge(cid, country["id"], EdgeKind.OPERATES_IN,
                                  doc=f"{DOC}#{mic}", confidence=Confidence.EXTRACTED,
                                  valid_from=ASSERTED_FROM))
                edges.append(edge(cid, regulator["id"], EdgeKind.REGULATED_BY,
                                  doc=f"{DOC}#{mic}", confidence=Confidence.EXTRACTED,
                                  valid_from=ASSERTED_FROM))
        return sorted_payload(nodes, edges)


def _strip_prefix(raw: str) -> str:
    """Accept either `MYX:1155` or an already-minted `CO:XKLS:1155`."""
    head = PREFIX[NodeKind.COMPANY] + ":"
    return raw[len(head):] if raw.upper().startswith(head) else raw
