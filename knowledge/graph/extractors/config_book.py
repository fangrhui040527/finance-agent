"""The book: what you hold and what you are watching.

Nodes only, no edges. Owning a share is not a relationship between two entities
in the world - it is a fact about you - and putting it in the graph as an edge
would let a traversal conclude that two companies are connected because you
happen to own both.

What this does buy is the seed set. Every exposure question starts from
something you hold, and a graph with no seeds answers nothing.
"""

from __future__ import annotations

from knowledge.graph.extractors.base import Extractor, company_node, sorted_payload


class ConfigBookExtractor(Extractor):
    name = "config_book"

    def __init__(self, holdings=(), watchlist=()) -> None:
        self.holdings = tuple(holdings)
        self.watchlist = tuple(watchlist)

    @classmethod
    def from_config(cls, cfg=None) -> ConfigBookExtractor:
        if cfg is None:
            from core.config import load

            cfg = load()
        return cls(cfg.holdings, cfg.watchlist)

    def extract(self) -> dict:
        nodes = [company_node(iid, held=True) for iid in self.holdings]
        held = {n["id"] for n in nodes}
        nodes += [
            n
            for n in (company_node(iid, watched=True) for iid in self.watchlist)
            if n["id"] not in held
        ]
        return sorted_payload(nodes, [])
