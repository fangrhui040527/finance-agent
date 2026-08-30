"""Schema gate between an extractor and the graph.

Adapted from Graphify-Labs/graphify's validate.py (Apache-2.0, docs/10) with one
deliberate change. Graphify validates an extraction, prints a warning and builds
anyway (build.py:875-893), so a malformed extractor degrades the graph quietly
and the damage surfaces months later as a missing relation nobody can explain.

Here it raises. Same posture as RegistryError: a malformed input is a startup
failure, never a warning. An extractor that emits a bad edge is a bug in the
extractor, and the cheapest place to find it is the moment it runs.

Extractors speak plain dicts, not Node and Edge objects, so an extractor never
imports the graph and can be tested against its source alone:

    {"nodes": [{"id": ..., "kind": ..., "label": ...}],
     "edges": [{"source": ..., "target": ..., "relation": ...,
                "confidence": ..., "source_doc_id": ..., "weight": 1.0,
                "valid_from": "2026-01-01", "valid_to": null}]}
"""

from __future__ import annotations

from datetime import date

from knowledge.graph.entity_graph import (
    Confidence, Edge, EdgeKind, Node, NodeKind,
)

REQUIRED_NODE_FIELDS = ("id", "kind")
REQUIRED_EDGE_FIELDS = ("source", "target", "relation", "confidence", "valid_from")

MAX_ERRORS_SHOWN = 20


class ExtractionError(ValueError):
    """An extraction does not satisfy the schema. Carries every error, not the first."""

    def __init__(self, origin: str, errors: list[str]) -> None:
        self.origin = origin
        self.errors = errors
        shown = errors[:MAX_ERRORS_SHOWN]
        more = "" if len(errors) <= MAX_ERRORS_SHOWN else f"\n  ... and {len(errors) - MAX_ERRORS_SHOWN} more"
        super().__init__(
            f"extraction from {origin!r} has {len(errors)} schema "
            f"error{'s' if len(errors) != 1 else ''}:\n  "
            + "\n  ".join(shown) + more
        )


def _parse_date(raw, where: str, errors: list[str]) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        errors.append(f"{where}: expected an ISO date string, got {type(raw).__name__}")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"{where}: {raw!r} is not an ISO date (YYYY-MM-DD)")
        return None


def _enum(raw, enum_cls, where: str, errors: list[str]):
    try:
        return enum_cls(raw)
    except ValueError:
        allowed = ", ".join(sorted(m.value for m in enum_cls))
        errors.append(f"{where}: {raw!r} is not a {enum_cls.__name__} ({allowed})")
        return None


def validate_extraction(payload, origin: str = "extraction") -> list[str]:
    """Every problem with this payload, as strings. Empty list means clean."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"payload is {type(payload).__name__}, expected a dict with 'nodes' and 'edges'"]
    for key in ("nodes", "edges"):
        if key not in payload:
            errors.append(f"payload has no {key!r} key")
        elif not isinstance(payload[key], list):
            errors.append(f"{key!r} is {type(payload[key]).__name__}, expected a list")
    if errors:
        return errors

    declared: set[str] = set()
    for i, raw in enumerate(payload["nodes"]):
        where = f"node[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: expected a dict, got {type(raw).__name__}")
            continue
        for f in REQUIRED_NODE_FIELDS:
            if f not in raw:
                errors.append(f"{where}: missing required field {f!r}")
        nid = raw.get("id")
        if isinstance(nid, str) and nid.strip():
            if nid in declared:
                errors.append(f"{where}: id {nid!r} is declared twice")
            declared.add(nid)
        elif "id" in raw:
            errors.append(f"{where}: id must be a non-blank string, got {nid!r}")
        if "kind" in raw:
            _enum(raw["kind"], NodeKind, f"{where}.kind", errors)

    for i, raw in enumerate(payload["edges"]):
        where = f"edge[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: expected a dict, got {type(raw).__name__}")
            continue
        for f in REQUIRED_EDGE_FIELDS:
            if f not in raw:
                errors.append(f"{where}: missing required field {f!r}")
        src, dst = raw.get("source"), raw.get("target")
        for role, nid in (("source", src), ("target", dst)):
            if nid is not None and nid not in declared:
                errors.append(
                    f"{where}: {role} {nid!r} is not declared in this extraction's nodes"
                )
        if src is not None and src == dst:
            errors.append(f"{where}: {src!r} relates to itself; a self-loop carries no signal")

        kind = _enum(raw["relation"], EdgeKind, f"{where}.relation", errors) \
            if "relation" in raw else None
        conf = _enum(raw["confidence"], Confidence, f"{where}.confidence", errors) \
            if "confidence" in raw else None

        weight = raw.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            errors.append(f"{where}.weight: expected a number, got {weight!r}")
        elif not 0.0 < float(weight) <= 1.0:
            errors.append(f"{where}.weight: {weight} is outside (0, 1]")

        doc = raw.get("source_doc_id")
        if doc is not None and not isinstance(doc, str):
            errors.append(f"{where}.source_doc_id: expected a string, got {type(doc).__name__}")
            doc = None
        # Graphify's rule: provenance belongs on the edge, not only the node. Ours
        # is narrower and stricter - an EXTRACTED edge claims a document says so,
        # and must name it. An INFERRED edge may have none, and cannot be cited.
        if conf is Confidence.EXTRACTED and not (doc or "").strip():
            errors.append(
                f"{where}: confidence is 'extracted' but no source_doc_id names the "
                f"document it was extracted from"
            )

        vf = _parse_date(raw.get("valid_from"), f"{where}.valid_from", errors)
        vt = _parse_date(raw.get("valid_to"), f"{where}.valid_to", errors)
        if "valid_from" in raw and raw["valid_from"] is None:
            errors.append(
                f"{where}.valid_from: must be a date. An edge with no start is not "
                f"traversable at any as-of, so it would be silently invisible."
            )
        if vf and vt and vt <= vf:
            errors.append(
                f"{where}: valid [{vf}, {vt}) contains no days"
            )
        del kind
    return errors


def assert_valid(payload, origin: str = "extraction") -> None:
    """Raise unless the extraction is clean. The gate nothing reaches build past."""
    errors = validate_extraction(payload, origin)
    if errors:
        raise ExtractionError(origin, errors)


def parse(payload, origin: str = "extraction") -> tuple[list[Node], list[Edge]]:
    """Validated dicts -> typed objects. Raises before producing anything partial."""
    assert_valid(payload, origin)
    nodes = [
        Node(
            node_id=n["id"],
            kind=NodeKind(n["kind"]),
            label=n.get("label", "") or "",
            metadata=dict(n.get("metadata") or {}),
        )
        for n in payload["nodes"]
    ]
    edges = [
        Edge(
            src=e["source"],
            dst=e["target"],
            kind=EdgeKind(e["relation"]),
            weight=float(e.get("weight", 1.0)),
            source_doc_id=e.get("source_doc_id"),
            confidence=Confidence(e["confidence"]),
            valid_from=_as_date(e.get("valid_from")),
            valid_to=_as_date(e.get("valid_to")),
        )
        for e in payload["edges"]
    ]
    return nodes, edges


def _as_date(raw) -> date | None:
    if raw is None or isinstance(raw, date):
        return raw
    return date.fromisoformat(raw)
