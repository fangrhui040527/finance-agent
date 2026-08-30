"""The one contract every extractor satisfies.

Extractors speak plain dicts, never Node and Edge objects. That is graphify's
stage separation and it buys two things: an extractor never imports the graph,
so it is testable against its own source alone, and everything reaching the
graph has passed through validate.parse, which raises.

    {"nodes": [{"id", "kind", "label", "metadata"}],
     "edges": [{"source", "target", "relation", "confidence",
                "source_doc_id", "weight", "valid_from", "valid_to"}]}

Output is SORTED. Determinism is a build-level guarantee - build twice, get a
byte-identical database - and it only holds if every stage under it is ordered.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.ids import display_names, label_for, node_id


class Extractor(ABC):
    """One source, one extractor. `name` appears in every error and report."""

    name: str

    @abstractmethod
    def extract(self) -> dict: ...


def node(kind: NodeKind, raw: str, label: str | None = None, **metadata) -> dict:
    return {
        "id": node_id(kind, raw),
        "kind": kind.value,
        "label": label if label is not None else label_for(kind, raw),
        "metadata": dict(metadata),
    }


def edge(src: str, dst: str, relation: EdgeKind, *, doc: str | None,
         confidence: Confidence, valid_from: date, weight: float = 1.0,
         valid_to: date | None = None) -> dict:
    return {
        "source": src,
        "target": dst,
        "relation": relation.value,
        "confidence": confidence.value,
        "source_doc_id": doc,
        "weight": weight,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat() if valid_to else None,
    }


def company_node(raw: str, **metadata) -> dict:
    """Every extractor mints company nodes through here.

    The store upserts nodes, so whichever extractor runs last decides the label.
    One helper is what stops that being a race between a real name and a stock
    code.
    """
    cid = node_id(NodeKind.COMPANY, raw)
    iid = cid.split(":", 1)[1]
    return {"id": cid, "kind": NodeKind.COMPANY.value,
            "label": display_names().get(iid) or _fallback_label(raw, iid),
            "metadata": dict(metadata)}


def _fallback_label(raw: str, iid: str) -> str:
    """An instrument entities.yaml has never heard of keeps its written form."""
    return label_for(NodeKind.COMPANY, raw) if ":" not in raw else iid


def sorted_payload(nodes: list[dict], edges: list[dict]) -> dict:
    """Deduplicate nodes by id, then order everything. See the module docstring.

    Nodes dedupe rather than raise: two extractors legitimately declare the same
    company, and only the validator cares about duplicates WITHIN one payload.
    """
    by_id = {n["id"]: n for n in nodes}
    return {
        "nodes": [by_id[k] for k in sorted(by_id)],
        "edges": sorted(edges, key=lambda e: (e["source"], e["target"],
                                              e["relation"], e["valid_from"])),
    }
