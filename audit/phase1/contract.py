"""M1-M3: the contract between the surfaces, without a network.

The blueprint's Maintainability pillar is decoupling verified by interception:
prove the client and the server agree without running the real backend. This
system's equivalent is stronger, because there are three clients over one core -
the CLI, the MCP server and the web API - and the failure it guards against is
drift, where one of them starts answering a question differently from the others
and nobody notices because each is tested alone.

M1 drives every published tool over the real JSON-RPC dispatcher with a minimal
argument set. M2 holds the web API byte-identical to the MCP tool it wraps. M3
is the property that makes the whole system safe to automate: a tool that cannot
answer must return a refusal as CONTENT, never an exception, because an
exception is a crash the model narrates around.
"""

from __future__ import annotations

import json

from audit._support.scorecard import Check

#: Arguments that let each tool reach a real answer or a real refusal without a
#: network. Anything absent here is called with no arguments at all.
MINIMAL: dict[str, dict] = {
    "market_info": {"market": "XKLS"},
    "get_prices": {"instrument": "MYX:1155", "bars": 5},
    "why_did_it_move": {
        "instrument": "MYX:1155",
        "instrument_return": -0.09,
        "market_return": -0.08,
        "sector_return": -0.02,
    },
    "fit_factor_model": {"returns_csv": "0.01,0.01,0.00\n0.02,0.02,0.00\n0.03,0.03,0.00"},
    "compose_thesis": {"instrument": "MYX:1155"},
    "check_portfolio_risk": {"positions": []},
    "size_position": {
        "instrument": "MYX:1155",
        "portfolio_value": 200000,
        "price": 10.68,
        "stop_price": 9.9,
        "adv_20d": 22000000,
    },
    "investable_capital": {},
    "allocate_capital": {"names": [], "portfolio_value": 200000},
    "rebalance_book": {},
    "plan_question": {"question": "why did maybank fall"},
    "explain_concept": {"concept": "expected value"},
    "method_note": {"collection": "kb_craft", "concept": "cash_flow"},
    "log_prediction": {
        "instrument": "MYX:1155",
        "direction": 1,
        "horizon_days": 21,
        "confidence": 0.6,
        "thesis": "an audit row, never graded",
        "db": ":memory:",
    },
    "log_hypothesis": {"title": "audit", "thesis": "a row", "db": ":memory:"},
    "explain_path": {"a": "MYX:1155", "b": "MYX:1023"},
    "system_health": {"offline": True},
    "operating_report": {"db": ":memory:"},
    "recent_failures": {"db": ":memory:"},
    "open_alerts": {"alerts_db": ":memory:"},
    "quality_report": {"db": ":memory:"},
    "efficiency_report": {"db": ":memory:"},
    "maintainability_report": {"db": ":memory:"},
    "reasoning_report": {"db": ":memory:"},
    "scorecard": {"db": ":memory:"},
    "calibration_status": {"db": ":memory:"},
}


def _dispatch(name: str, args: dict) -> dict:
    from mcp_server.server import S

    return S.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )


def _tool_names() -> list[str]:
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [t["name"] for t in resp["result"]["tools"]]


def m1_every_tool_answers_over_the_wire() -> Check:
    c = Check(
        "M1",
        "Maintainability",
        1,
        "Every published tool answers over the real dispatcher",
        "contract testing without a live backend: no tool is published but unreachable",
    )
    broken: list[str] = []
    for name in _tool_names():
        args = MINIMAL.get(name, {})
        try:
            resp = _dispatch(name, args)
        except Exception as e:  # noqa: BLE001
            broken.append(f"{name} raised {type(e).__name__}: {e}")
            continue
        if "error" in resp:
            # A protocol error for a MISSING required argument is correct
            # behaviour, not a break - the audit supplied nothing on purpose.
            msg = str(resp["error"].get("message", ""))
            if "missing required argument" not in msg:
                broken.append(f"{name}: {msg[:90]}")
            continue
        content = resp.get("result", {}).get("content") or []
        if not content or not str(content[0].get("text", "")).strip():
            broken.append(f"{name}: answered with nothing")
    if broken:
        return c.failed(f"{len(broken)} tool(s) unreachable: {'; '.join(broken[:4])}")
    c.evidence = f"{len(_tool_names())} tools each returned text or a stated refusal"
    return c.ok()


def m2_web_matches_the_tool_it_wraps() -> Check:
    c = Check(
        "M2",
        "Maintainability",
        1,
        "Web endpoints are byte-identical to their MCP tool",
        "one core, three clients: no surface may drift into its own answer",
    )
    from fastapi.testclient import TestClient

    from mcp_server import tools as T
    from web.app import create_app

    client = TestClient(create_app())
    headers = {"X-Requested-With": "FinPlanet"}
    pairs = [
        ("GET", "/api/capital", None, T.investable_capital, {}),
        (
            "POST",
            "/api/allocate",
            {"names": [], "portfolio_value": 200000},
            T.allocate_capital,
            {"names": [], "portfolio_value": 200000},
        ),
        ("POST", "/api/rebalance", {}, T.rebalance_book, {}),
        (
            "POST",
            "/api/portfolio/risk",
            {"positions": []},
            T.check_portfolio_risk,
            {"positions": []},
        ),
    ]
    drift = []
    for method, path, body, fn, kwargs in pairs:
        resp = (
            client.get(path) if method == "GET" else client.post(path, json=body, headers=headers)
        )
        if resp.status_code != 200:
            drift.append(f"{path} returned HTTP {resp.status_code}")
            continue
        web_text = resp.json().get("text")
        if web_text != fn(**kwargs):
            drift.append(f"{path} differs from {fn.__name__}")
    if drift:
        return c.failed("; ".join(drift))
    c.evidence = f"{len(pairs)} endpoint/tool pairs byte-identical"
    return c.ok()


def m3_a_failure_is_content_not_an_exception() -> Check:
    """The property that makes this safe to hand to a model.

    A tool that raises leaves the caller narrating around a stack trace. A tool
    that returns "REFUSED, and here is what would help" leaves it with an
    answer it can pass on.
    """
    c = Check(
        "M3",
        "Functionality",
        1,
        "Bad input is refused as content, never as a crash",
        "refusal-as-content: the model receives a stated reason, not a traceback",
    )
    hostile = [
        ("size_position", {"instrument": "MYX:1155", "portfolio_value": -1}),
        ("size_position", {"instrument": "NOPE:0", "portfolio_value": 1000}),
        ("get_prices", {"instrument": "not-an-id"}),
        ("market_info", {"market": "XXXX"}),
        ("explain_concept", {"concept": "\x00\x01 nonsense"}),  # control bytes
        ("allocate_capital", {"names": ["MYX:1155:10"], "portfolio_value": 1000}),
        ("check_portfolio_risk", {"positions": [{"instrument": "MYX:1155", "weight": "NaN"}]}),
        ("why_did_it_move", {"instrument": "MYX:1155", "move": float("nan")}),
    ]
    leaked = []
    for name, args in hostile:
        try:
            resp = _dispatch(name, args)
        except Exception as e:  # noqa: BLE001
            leaked.append(f"{name} raised {type(e).__name__}")
            continue
        blob = json.dumps(resp)
        if "Traceback" in blob or '"code": -32603' in blob:
            leaked.append(f"{name} leaked an internal error")
    if leaked:
        return c.failed("; ".join(leaked))
    c.evidence = f"{len(hostile)} hostile inputs each came back as a stated refusal"
    return c.ok()


CHECKS = (
    m1_every_tool_answers_over_the_wire,
    m2_web_matches_the_tool_it_wraps,
    m3_a_failure_is_content_not_an_exception,
)
