"""Human-authored relationships: supply, competition, commodity exposure.

The only source of SUPPLIES / CUSTOMER_OF / COMPETES_WITH / EXPOSED_TO in the
deterministic tier, and the only file a person is expected to edit by hand.

kb_supply_chain is created_by: human in agents/registry.yaml, so no agent may
ever write here. Deterministic extraction does not violate that: it is a build
step run by a person, not an agent action.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from knowledge.graph.entity_graph import (
    EDGE_INVERSE, Confidence, EdgeKind, NodeKind)
from knowledge.graph.extractors.base import (
    Extractor, company_node, edge, node, sorted_payload)
from knowledge.graph.ids import kind_of, node_id

DATA = Path(__file__).resolve().parents[1] / "data" / "supply_chain.yaml"
DOC = "curated:supply_chain"


class CuratedExtractor(Extractor):
    name = "curated"

    def __init__(self, path: Path | str = DATA) -> None:
        self.path = Path(path)

    def extract(self) -> dict:
        raw = yaml.safe_load(self.path.read_text()) or {}
        nodes: list[dict] = []
        edges: list[dict] = []

        for cid, label in sorted((raw.get("commodities") or {}).items()):
            nodes.append(node(NodeKind.COMMODITY, cid, label))

        seen: set[str] = set()
        for row in raw.get("edges") or []:
            rid = row.get("id")
            if not rid:
                raise ValueError(
                    f"{self.path.name}: a row has no id. The id is what its citation "
                    f"points at, so a row without one cannot be cited."
                )
            if rid in seen:
                raise ValueError(f"{self.path.name}: duplicate row id {rid!r}")
            seen.add(rid)
            src, dst = _ref(row["source"]), _ref(row["target"])
            nodes += [n for n in (_implied(row["source"]), _implied(row["target"]))
                      if n is not None]
            kind = EdgeKind(row["relation"])
            common = dict(doc=f"{DOC}#{rid}", confidence=Confidence.EXTRACTED,
                          weight=float(row.get("weight", 1.0)),
                          valid_from=_as_date(row["valid_from"]),
                          valid_to=_as_date(row.get("valid_to")))
            edges.append(edge(src, dst, kind, **common))
            # Both readings, written out. The store holds literal edges, so a
            # relationship that is true from either end is stored from either
            # end - the graph does not infer the reverse at query time.
            inverse = EDGE_INVERSE.get(kind)
            if inverse is not None and (src, dst, inverse) != (dst, src, kind):
                edges.append(edge(dst, src, inverse, **common))
        return sorted_payload(nodes, edges)


def _ref(raw: str) -> str:
    """`MYX:1155` -> a company id; `CM:aluminium` -> itself."""
    kind = kind_of(raw)
    return raw if kind is not None else node_id(NodeKind.COMPANY, raw)


def _implied(raw: str) -> dict | None:
    """Declare the company nodes the rows reference, so the payload validates
    standalone. Commodities are declared explicitly in the file."""
    if kind_of(raw) is not None:
        return None
    return company_node(raw)


def _as_date(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
