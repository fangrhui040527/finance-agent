"""Company -> SubSector -> Sector, from a checked-in classification.

Two levels, not one. A single level puts sixty Malaysian names under
"Financials" and makes every pair of them look connected; the sub-sector carries
the distinction between a retail bank and an insurer.

Edges are EXTRACTED and cite the yaml file itself. That is the honest
provenance: a person wrote the classification down and vouches for it, and no
filing is being quoted. Minting a plausible filing id would be a fabricated
citation that path_to_citations would refuse anyway.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.base import (
    Extractor, company_node, edge, node, sorted_payload)
from knowledge.graph.ids import node_id

DATA = Path(__file__).resolve().parents[1] / "data" / "sectors.yaml"
DOC = "curated:sectors"


class SectorExtractor(Extractor):
    name = "sectors"

    def __init__(self, path: Path | str = DATA) -> None:
        self.path = Path(path)

    def extract(self) -> dict:
        raw = yaml.safe_load(self.path.read_text()) or {}
        nodes: list[dict] = []
        edges: list[dict] = []

        sub_to_sector: dict[str, str] = {}
        for sector, subs in sorted((raw.get("sectors") or {}).items()):
            s_node = node(NodeKind.SECTOR, sector)
            nodes.append(s_node)
            for sub in sorted(subs or []):
                sub_node = node(NodeKind.SUBSECTOR, sub)
                nodes.append(sub_node)
                sub_to_sector[sub] = sector
                edges.append(edge(
                    sub_node["id"], s_node["id"], EdgeKind.CLASSIFIED_IN,
                    doc=f"{DOC}#{sub_node['id']}", confidence=Confidence.EXTRACTED,
                    valid_from=_ASSERTED_FROM))

        for iid, spec in sorted((raw.get("companies") or {}).items()):
            sub = spec["subsector"]
            if sub not in sub_to_sector:
                raise ValueError(
                    f"{self.path.name}: {iid} is classified under {sub!r}, which no "
                    f"sector declares. A sub-sector with no parent is a dead end."
                )
            cid = node_id(NodeKind.COMPANY, iid)
            nodes.append(company_node(iid))
            edges.append(edge(
                cid, node_id(NodeKind.SUBSECTOR, sub), EdgeKind.CLASSIFIED_IN,
                doc=f"{DOC}#{cid}", confidence=Confidence.EXTRACTED,
                valid_from=_as_date(spec["valid_from"]),
                valid_to=_as_date(spec.get("valid_to"))))
        return sorted_payload(nodes, edges)


#: When the SubSector -> Sector spine began being asserted. The taxonomy itself
#: has no history: it is this repository's own scheme, not a vendor's.
_ASSERTED_FROM = date(2020, 1, 1)


def _as_date(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
