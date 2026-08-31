#!/usr/bin/env python3
"""The Analyst Mind as an MCP server.

    python -m mcp_server.server

Speaks MCP over stdin/stdout, so Claude Desktop or Claude Code launches it as a
subprocess and the reasoning runs on your Claude session rather than through an
API key of this system's own.

WHAT THIS CHANGES, AND WHAT IT DOES NOT

Changes: the narrative layer. Claude reads typed results and writes the thesis,
the explanation, the challenge.

Does NOT change: every number, and every refusal. The engines still compute the
decomposition, the caps, the concentration limits and the cost floors, and the
guardrail chain still runs on every call. A model driving these tools cannot
size past a limit, cannot obtain a position below the cost floor, and cannot
place an order - there is no tool that does. That separation is the point: a
system where the model can talk its way past a risk limit is not a risk system.

CONNECTING IT (Claude Desktop: claude_desktop_config.json)

    {
      "mcpServers": {
        "analyst-mind": {
          "command": "/ABSOLUTE/PATH/.venv/bin/python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "/ABSOLUTE/PATH/finance-agent"
        }
      }
    }

Claude Code:  claude mcp add analyst-mind -- /ABS/.venv/bin/python -m mcp_server.server

Both need ABSOLUTE paths and the project's own interpreter. Verify with
`python -m mcp_server.server --selftest`, which runs the full handshake against
itself and needs no client.
"""

from __future__ import annotations

import sys

from mcp_server import observability as O
from mcp_server import tools as T
from mcp_server.protocol import Server

INSTRUCTIONS = """\
The Analyst Mind: equity research tools with the arithmetic already done.

Use these tools rather than computing any of it yourself. They enforce risk
limits, cost floors and point-in-time correctness that cannot be reproduced by
reasoning over the numbers in conversation.

Three habits that make the output trustworthy:

1. DECOMPOSE BEFORE EXPLAINING. Call why_did_it_move before offering any cause.
   Most single-day moves are market and sector; naming a company-specific reason
   for a market-wide fall is the most common analytical error there is, and the
   tool will tell you the unexplained share so you do not have to guess.

2. A REFUSAL IS AN ANSWER. When a tool returns REFUSED or NO POSITION, that is
   the finding. Report it. Do not retry with softened inputs to obtain a number,
   and do not reason around a cost floor or a position limit - they exist
   because the arithmetic says the trade cannot pay for itself.

3. NEVER INVENT A NUMBER A TOOL CAN GIVE YOU. If price data is absent the tool
   says NO DATA. Say so. An estimated price is indistinguishable from a real one
   once it is in the narrative, and this system is used to make money decisions.

4. WHEN SOMETHING LOOKS WRONG, LOOK AT THE MACHINE. system_health says what
   this installation can currently do; operating_report says what it has been
   doing and what it cost; recent_failures says what broke and in which run;
   run_anatomy opens one run and says whether the METHOD changed since the
   last one. A stubbed backend or a missing database explains more odd output
   than any amount of reasoning about the output itself.

Nothing here places orders, and nothing here is financial advice. Output is
analysis with an evidence chain.
"""

S = Server(name="analyst-mind", version="0.1.0", instructions=INSTRUCTIONS)


def _eprint(*args) -> None:
    """stdout is the JSON-RPC wire; every human-facing line goes to stderr."""
    print(*args, file=sys.stderr)  # noqa: T201


def _num(desc: str) -> dict:
    return {"type": "number", "description": desc}


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S.tool(
    "market_info",
    "Fee schedule, lot size, sessions, cost floor and MINIMUM ECONOMIC POSITION "
    "for a market. Call before sizing anything on an unfamiliar market. "
    "Omit `market` to list every market with an adapter.",
    obj({"market": _str("MIC or instrument id, e.g. 'XKLS' or 'MYX:1155'")}),
)(T.market_info)

S.tool(
    "get_prices",
    "Daily OHLCV bars from the live feed, with 20-day ADV and ATR. `as_at` "
    "bounds the result so no bar after that date is returned - use it whenever "
    "reasoning about what was knowable at a past date.",
    obj(
        {
            "instrument": _str("e.g. 'MYX:1155' or 'XNAS:NVDA'"),
            "bars": {"type": "integer", "description": "how many recent bars to show"},
            "as_at": _str("YYYY-MM-DD; no later bar is returned"),
        },
        ["instrument"],
    ),
)(T.get_prices)

S.tool(
    "why_did_it_move",
    "Decompose a price move into market, sector, style, currency and "
    "idiosyncratic components, and report the UNEXPLAINED share. Call this "
    "before naming any cause. Give market_proxy to measure both legs from the "
    "feed, or supply instrument_return and market_return directly.",
    obj(
        {
            "instrument": _str("the instrument that moved"),
            "instrument_return": _num("realised return, e.g. -0.09"),
            "market_return": _num("index return over the same window"),
            "sector_return": _num("sector return over the same window"),
            "fx_return": _num("currency leg, if reporting in a base currency"),
            "market_proxy": _str("instrument to measure the market leg from, e.g. 'XNAS:SPY'"),
            "bars": {"type": "integer", "description": "trading bars in the window"},
            "as_at": _str("YYYY-MM-DD window end"),
            "beta_market": _num("used only when no factor model is fitted"),
            "beta_sector": _num("used only when no factor model is fitted"),
            "currency": _str("base currency for reporting"),
        },
        ["instrument"],
    ),
)(T.why_did_it_move)

S.tool(
    "fit_factor_model",
    "Fit real market and sector betas by robust (Huber) regression from a CSV "
    "of 'instrument,market,sector' return rows. Refuses below the minimum "
    "observation count rather than fitting betas to noise.",
    obj({"returns_csv": _str("one 'instrument,market,sector' row per line")}, ["returns_csv"]),
)(T.fit_factor_model)

S.tool(
    "compose_thesis",
    "Assemble evidence into a stance and immediately red-team it. Every "
    "breaker needs an executable query - a thesis nothing could falsify is "
    "refused. Fewer than two breakers forfeits the stance entirely.",
    obj(
        {
            "instrument": _str("the subject"),
            "evidence": {
                "type": "array",
                "description": "[{'agent':'a1_fundamentals','text':'...'}]",
                "items": obj(
                    {"agent": _str("which evidence agent"), "text": _str("the finding")},
                    ["agent", "text"],
                ),
            },
            "breakers": {
                "type": "array",
                "description": "what would prove this WRONG, each machine-checkable",
                "items": obj(
                    {
                        "statement": _str("the falsifier in words"),
                        "query": _str("an executable check"),
                        "store": _str("which store it runs against"),
                    },
                    ["statement", "query", "store"],
                ),
            },
            "stance": _str("accumulate | hold | trim | exit | no_view"),
            "horizon_months": {"type": "integer"},
        },
        ["instrument"],
    ),
)(T.compose_thesis)

S.tool(
    "check_portfolio_risk",
    "Concentration (HHI), effective number of bets, portfolio heat and every "
    "limit breach across a book. Effective bets is the one to watch: a book "
    "can look diversified by HHI and collapse into three real bets.",
    obj(
        {
            "positions": {
                "type": "array",
                "items": obj(
                    {
                        "instrument": _str("e.g. 'MYX:1155'"),
                        "weight": _num("fraction of the book, 0-1"),
                        "sector": _str("sector label"),
                        "country": _str("country code"),
                        "currency": _str("defaults to country"),
                        "risk_to_stop": _num("fraction of the book at risk if the stop fills"),
                    },
                    ["instrument", "weight", "sector", "country"],
                ),
            },
            "base_currency": _str("reporting currency"),
            "single_name_limit": _num("override the single-name cap; cannot exceed 0.15"),
            "equity": _num("current equity, for drawdown state"),
            "peak_equity": _num("peak equity, for drawdown state"),
        }
    ),
)(T.check_portfolio_risk)

S.tool(
    "size_position",
    "Turn a stance into a lot count, or into a documented refusal. Applies all "
    "five caps and the market's real fee schedule. NO POSITION is a valid and "
    "frequent outcome - report it rather than retrying with softer inputs.",
    obj(
        {
            "instrument": _str("e.g. 'MYX:1155'"),
            "portfolio_value": _num("total portfolio value"),
            "price": _num("entry price"),
            "stop_price": _num("stop price; must be below entry"),
            "adv_20d": _num("20-day average daily volume in currency"),
            "risk_per_trade": _num("fraction of portfolio at risk, default 0.0075"),
            "single_name_limit": _num("default 0.08"),
            "participation": _num("max fraction of ADV, default 0.05"),
            "win_rate": _num("with payoff, enables the Kelly cap"),
            "payoff": _num("reward-to-risk ratio"),
            "n_trades": {"type": "integer", "description": "sample behind win_rate"},
        },
        ["instrument", "portfolio_value", "price", "stop_price", "adv_20d"],
    ),
)(T.size_position)

S.tool(
    "plan_question",
    "What the system would do with a question: which agents, what it would "
    "cost, and what it refuses outright. Useful before a long piece of work.",
    obj(
        {
            "question": _str("the question in plain words"),
            "instruments": {"type": "array", "items": {"type": "string"}},
            "budget_myr": _num("cap; too small is refused, not cheapened"),
        },
        ["question"],
    ),
)(T.plan_question)

S.tool(
    "explain_concept",
    "Teach one concept from the 30-concept curriculum, in an order it "
    "enforces. Omit `concept` for the next thing this learner can actually "
    "take. Prerequisites are refused, not warned about.",
    obj(
        {
            "concept": _str("concept key, e.g. 'kelly'"),
            "mastered": {
                "type": "array",
                "items": {"type": "string"},
                "description": "concepts already demonstrated",
            },
        }
    ),
)(T.explain_concept)

S.tool(
    "log_prediction",
    "Write a view down BEFORE the outcome is known. Append-only: it can never "
    "be edited or deleted afterwards, which is the entire value. This is the "
    "clock the calibration record runs on.",
    obj(
        {
            "instrument": _str("e.g. 'MYX:1155'"),
            "direction": {"type": "integer", "description": "+1 or -1"},
            "horizon_days": {"type": "integer", "description": "trading horizon"},
            "confidence": _num("strictly between 0 and 1"),
            "thesis": _str("why - a prediction with no thesis teaches nothing"),
            "db": _str("optional store path"),
        },
        ["instrument", "direction", "horizon_days", "confidence", "thesis"],
    ),
)(T.log_prediction)

S.tool(
    "log_hypothesis",
    "Register the IDEA above the predictions - append-only. A hypothesis "
    "gains status events and prediction links over its life but is never "
    "edited, so a thesis cannot quietly survive its own dead calls.",
    obj(
        {
            "title": _str("short name for the idea"),
            "thesis": _str("the falsifiable claim, one sentence"),
            "db": _str("optional store path"),
        },
        ["title", "thesis"],
    ),
)(T.log_hypothesis)

# --------------------------------------------------------------------------
# Watching the machine itself. Read-only, and none of it returns prompt text.
# --------------------------------------------------------------------------

S.tool(
    "system_health",
    "Preflight: what this installation can actually do right now, and what "
    "each gap affects - config, registry, stores, model backend, spend cap, "
    "trace retention, and (with offline=false) whether the price and news "
    "sources are reachable. Call this FIRST when anything behaves oddly: a "
    "missing database or a stubbed backend explains more failures than any "
    "amount of reasoning about the output.",
    obj(
        {
            "offline": {
                "type": "boolean",
                "description": "skip the two network probes (default true)",
            }
        }
    ),
)(O.system_health)

S.tool(
    "operating_report",
    "Cost, latency, model mix and citation health over the last N days: how "
    "many model calls, at what price, on which models, how slow (p50/p95), "
    "how close to the daily budget, and how many claims were DROPPED for "
    "want of a citation. An empty ledger is reported as 'nothing has run', "
    "never as a clean bill of health.",
    obj(
        {
            "days": {"type": "integer", "description": "window in days (default 7)"},
            "db": _str("optional ledger path"),
        }
    ),
)(O.operating_report)

S.tool(
    "recent_failures",
    "Errors, guardrail denials and dropped claims across recent traced runs. "
    "Reports what failed, where, and how often - error text, event name, run "
    "id - so a bug can be located. Never returns prompt or response text: "
    "traces hold verbatim prompts and portfolio positions, and releasing "
    "those is the operator's decision, not a tool's.",
    obj(
        {
            "runs": {"type": "integer", "description": "how many recent runs to scan (default 10)"},
            "db": _str("optional ledger path"),
        }
    ),
)(O.recent_failures)

S.tool(
    "run_anatomy",
    "One traced run in detail: where the time went, what was denied, what "
    "raised - and whether the METHODOLOGY changed since the run before it. "
    "That last part answers the question a surprising run actually raises: "
    "did the system change, or did the world? Omit run_id for the most "
    "recent run.",
    obj({"run_id": _str("e.g. '20260831T083520-5963bf'; omit for the latest")}),
)(O.run_anatomy)

S.tool(
    "explain_path",
    "Why are two entities connected, and how strongly? The multi-hop question "
    "vector search cannot answer. Returns the chain, a per-hop decayed weight, "
    "and one citation per link. An empty result means NOT FOUND CHEAPLY, never "
    "that the two are unconnected.",
    obj(
        {
            "a": _str("an entity: a name, an instrument id, or a node id"),
            "b": _str("the other entity"),
            "asof": _str("YYYY-MM-DD; an edge is invisible outside its validity"),
            "db": _str("optional graph path (default data/graph.db)"),
        },
        ["a", "b"],
    ),
)(T.explain_path)

S.tool(
    "calibration_status",
    "Are the confident calls actually right more often? Stated confidence "
    "against realised hit rate. Cannot be back-filled - only waited for.",
    obj({"db": _str("optional store path")}),
)(T.calibration_status)


def selftest() -> int:
    """Full handshake against ourselves. No client, no network, no keys."""
    import io
    import json

    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "market_info", "arguments": {"market": "MYX:1155"}},
        },
    ]
    out = io.StringIO()
    S.serve(io.StringIO("\n".join(json.dumps(r) for r in reqs)), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]

    _eprint(f"protocol      {lines[0]['result']['protocolVersion']}")
    _eprint(f"server        {lines[0]['result']['serverInfo']}")
    _eprint(f"tools         {len(lines[1]['result']['tools'])}")
    for t in lines[1]["result"]["tools"]:
        _eprint(f"                {t['name']}")
    _eprint(
        f"responses     {len(lines)} for {len([r for r in reqs if 'id' in r])} requests "
        f"(the notification correctly got none)",
    )
    ok = (
        len(lines) == 3
        and "error" not in lines[2]
        and "MINIMUM ECONOMIC POSITION" in lines[2]["result"]["content"][0]["text"]
    )
    _eprint("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    from core.env import load as _load_dotenv
    from core.logging import configure as _configure_logging

    _load_dotenv()
    _configure_logging()
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    if "--list" in argv:
        for t in S.tools.values():
            _eprint(f"{t.name:<24} {t.description.splitlines()[0]}")
        return 0
    return S.serve()


if __name__ == "__main__":
    sys.exit(main())
