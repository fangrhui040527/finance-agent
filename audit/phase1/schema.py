"""F1-F3: the published tool schemas, checked the way the API checks them.

The blueprint's sharpest structural finding: Anthropic's structured outputs
reject a JSON Schema carrying `minimum`, `maximum`, `minLength`, `minItems` and
friends with an HTTP 400. This system publishes 26 tool schemas over MCP, and
they are sent to a model by whichever client mounts the server. A constraint
keyword in one of them is a crash the server author never sees, because it
happens in someone else's process.

F2 and F3 check two properties the blueprint implies but does not name: a
`required` entry that names no property is a contract that can never be
satisfied, and a tool whose description does not say what it refuses is a tool
a model will call wrongly and blame the user for.
"""

from __future__ import annotations

from audit._support.scorecard import Check

#: Keywords the API rejects in a structured-output schema. Checked against the
#: schema's own keys only - the same words are perfectly fine in prose, and two
#: of these tools legitimately say "minimum observation count" in a description.
FORBIDDEN = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "multipleOf",
        "uniqueItems",
        "pattern",
        "format",
    }
)


def _walk(node, path="$"):
    """Every (path, key) pair in the schema's own structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def _tools() -> list[dict]:
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return resp["result"]["tools"]


def f1_no_forbidden_constraint_keywords() -> Check:
    c = Check(
        "F1",
        "Functionality",
        1,
        "No tool schema carries a constraint keyword the API rejects",
        "schemas are sanitised of minimum/maxLength/pattern rather than crashing with HTTP 400",
    )
    bad: list[str] = []
    for tool in _tools():
        for path, key in _walk(tool.get("inputSchema", {})):
            # A "properties" child named e.g. "pattern" is a field name, not a
            # keyword. Only a key sitting directly on a schema object counts.
            if key in FORBIDDEN and ".properties" not in path.rsplit(".", 1)[-1]:
                if path.endswith(".properties"):
                    continue
                bad.append(f"{tool['name']}{path}.{key}")
    if bad:
        return c.failed(f"{len(bad)} constraint keyword(s) would 400: {', '.join(bad[:5])}")
    c.evidence = f"{len(_tools())} schemas clean of {len(FORBIDDEN)} rejected keywords"
    return c.ok()


def f2_required_names_a_real_property() -> Check:
    c = Check(
        "F2",
        "Functionality",
        1,
        "Every required argument exists in properties",
        "the contract a caller is held to is a contract it can satisfy",
    )
    broken = []
    for tool in _tools():
        schema = tool.get("inputSchema", {})
        props = set(schema.get("properties") or {})
        for req in schema.get("required") or []:
            if req not in props:
                broken.append(f"{tool['name']}.{req}")
    if broken:
        return c.failed(f"required names no property: {', '.join(broken)}")
    c.evidence = "every required argument is declared"
    return c.ok()


def f3_schemas_are_wire_serialisable() -> Check:
    """The list has to survive the transport that carries it."""
    c = Check(
        "F3",
        "Functionality",
        1,
        "tools/list round-trips through the JSON-RPC wire",
        "the published contract is transport-safe and stable",
    )
    import json

    tools = _tools()
    try:
        frame = json.dumps({"result": {"tools": tools}})
    except (TypeError, ValueError) as e:
        return c.failed(f"the tool list is not JSON-serialisable: {e}")
    if json.loads(frame)["result"]["tools"] != tools:
        return c.failed("the tool list does not survive a JSON round trip")
    missing = [t["name"] for t in tools if not t.get("description", "").strip()]
    if missing:
        return c.failed(f"tools published with no description: {', '.join(missing)}")
    thin = [t["name"] for t in tools if len(t.get("description", "")) < 80]
    if thin:
        return c.failed(
            f"description too thin to route on ({', '.join(thin)}); a model picks tools "
            f"by description and a one-liner gets called for the wrong question"
        )
    c.evidence = f"{len(tools)} tools, {len(frame):,} bytes, all described"
    return c.ok()


CHECKS = (
    f1_no_forbidden_constraint_keywords,
    f2_required_names_a_real_property,
    f3_schemas_are_wire_serialisable,
)
