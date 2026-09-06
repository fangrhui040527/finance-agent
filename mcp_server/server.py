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
from pathlib import Path

from mcp_server import observability as O
from mcp_server import tools as T
from mcp_server.protocol import Server

INSTRUCTIONS = """\
The Analyst Mind: equity research tools with the arithmetic already done.

Use these tools rather than computing any of it yourself. They enforce risk
limits, cost floors and point-in-time correctness that cannot be reproduced by
reasoning over the numbers in conversation.

Three habits that make the output trustworthy:

0. READ WHAT WAS COLLECTED FIRST. daily_digest, news_evidence, fact_snapshot and
   macro_context are the corpus and fact book the collector fills three times a
   day: stories already cleaned, linked and scored, figures stamped with the day
   they became knowable, events dated, macro series vintaged. They cost no
   request. pull_news is a live fetch for what the collector has not yet seen.

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

4. WHEN SOMETHING LOOKS WRONG, LOOK AT THE MACHINE. Start at scorecard: one
   line per dimension - robustness, performance, efficiency, quality,
   maintainability, usability, reasoning - with the evidence, or an explicit
   CANNOT SCORE where there is none. Then open the matching report:
   system_health (can it run at all), operating_report (what it did and cost),
   efficiency_report (cache, tiers, wasted spend), quality_report
   (calibration, citations, verdicts), maintainability_report (tests and docs
   per module), reasoning_report (how turns ended, what the rails stopped),
   recent_failures (what broke, in which run), run_anatomy (one run, and
   whether the METHOD changed), open_alerts (what tripped unattended).

   A stubbed backend or a missing database explains more odd output than any
   amount of reasoning about the output itself. And a dimension that says
   CANNOT SCORE has not passed - it has not been measured.

5. THE PAPER BOOK IS A RECORD, NOT A RECOMMENDATION. paper_status and
   paper_report read a hypothetical USD 1,000 ledger marked from the cached
   bars (docs/22). Its positions are targets someone recorded inside
   code-enforced caps; its return is a calibration measurement against a
   passive control. Nothing about it is advice, and nothing here can move it.

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
    "pull_news",
    "What a news source carried in a window, and what of it reached the review "
    "queue. Call this before naming a catalyst: attribution says HOW MUCH of a "
    "move was company-specific, this is where the reason for that part comes "
    "from. A broken feed and a quiet window are reported differently - neither "
    "is silence.",
    obj(
        {
            "source": _str("a registered source, e.g. 'gdelt'"),
            "query": _str("search terms, e.g. 'maybank'; omit for the source default"),
            "hours": {"type": "integer", "description": "window back from now (default 24)"},
            "limit": {"type": "integer", "description": "most articles to return (default 20)"},
        }
    ),
)(T.pull_news)

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
            "derive_valuation": {
                "type": "boolean",
                "description": "derive the bear-to-bull range from the stored record through the engines; the model never types one (default false)",
            },
            "as_at": _str("YYYY-MM-DD for the derived range; default today"),
            "archetype": _str("sector archetype for the derived range, e.g. bank"),
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
    "investable_capital",
    "How much money is allowed to be in stocks AT ALL, derived from the user's "
    "[capital] plan: liquid assets minus the emergency floor, near-term goals "
    "and debt above the hurdle. Call this BEFORE size_position - a "
    "portfolio_value you were handed or guessed skips all three locks, and "
    "size_position says so when that happens.",
    obj({"db": _str("optional ledger path (unused today, reserved)")}),
)(T.investable_capital)

S.tool(
    "allocate_capital",
    "Split investable capital across names the USER nominated, under every "
    "concentration limit. It does not choose names - that is the question "
    "before this one, and this system does not answer it. Omit portfolio_value "
    "to derive capital from the [capital] plan. Refuses rather than fabricating "
    "diversification: too few fundable names, or a book that would behave as "
    "one bet, comes back as a refusal with the reason. The result is a CAPITAL "
    "split, not a set of positions - each name still needs its own breakers.",
    obj(
        {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "MIC:CODE:PRICE:STOP:ADV:SECTOR; leave PRICE and ADV "
                "empty with fetch=true to measure them",
            },
            "portfolio_value": _num("investable capital; omit to derive it from [capital]"),
            "fetch": {"type": "boolean", "description": "measure empty price/adv from the feed"},
            "as_at": _str("point-in-time bound for fetched prices (YYYY-MM-DD)"),
            "single_name_limit": _num("fraction, default 0.08"),
            "risk_per_trade": _num("fraction, default 0.0075"),
            "participation": _num("fraction of ADV, default 0.05"),
        },
        ["names"],
    ),
)(T.allocate_capital)

S.tool(
    "rebalance_book",
    "What to change versus what is HELD, with the round-trip cost of each "
    "change. The book comes from account.holdings in config.toml (id, units, "
    "avg_cost, stop, sector) - it cannot be passed in, because a book the user "
    "never stated is not their book. Capital defaults to the book's own market "
    "value; portfolio_value or from_plan sets it instead. Trades worth less "
    "than their own round trip come back as 'hold' with the number, and the "
    "shape of the book before and after is reported by the portfolio-risk "
    "agent. Differences against a target CAPITAL split, not a set of positions.",
    obj(
        {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "extra names to consider alongside the book, as "
                "MIC:CODE:PRICE:STOP:ADV:SECTOR",
            },
            "portfolio_value": _num("capital to split; omit to use the book's own value"),
            "from_plan": {
                "type": "boolean",
                "description": "derive capital from the [capital] waterfall instead",
            },
            "fetch": {"type": "boolean", "description": "measure empty price/adv from the feed"},
            "as_at": _str("point-in-time bound for fetched prices (YYYY-MM-DD)"),
            "single_name_limit": _num("fraction, default 0.08"),
            "risk_per_trade": _num("fraction, default 0.0075"),
        },
        [],
    ),
)(T.rebalance_book)

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
    "ratio_sheet",
    "Margins, returns, leverage, liquidity, growth and accruals, plus the "
    "earnings-quality scores (accruals, Beneish M, Piotroski F, Altman Z), from "
    "the stored statement lines as of a date. Every missing input is named with "
    "the collector that would fill it; a name with nothing stored says so.",
    obj(
        {"instrument": _str("e.g. 'XNAS:AAPL'"), "as_at": _str("YYYY-MM-DD; default today")},
        ["instrument"],
    ),
)(T.ratio_sheet)

S.tool(
    "cost_of_capital",
    "The discount rate, built: risk-free from the stored yield series, premiums "
    "from the dated Damodaran table (cited), beta from the vendor figure or the "
    "industry, cost of debt from interest cover, weights from market value. "
    "Every input labelled, every gap named.",
    obj(
        {
            "instrument": _str("e.g. 'XNAS:AAPL'"),
            "as_at": _str("YYYY-MM-DD; default today"),
            "archetype": _str(
                "bank | utility | cyclical | software | semis | chemicals | hospital | gaming | holding"
            ),
        },
        ["instrument"],
    ),
)(T.cost_of_capital)

S.tool(
    "valuation_range",
    "A bear-to-bull scenario DCF from the stored record: each end a stated set of "
    "assumptions, terminal share reported, refused when fewer than two scenarios "
    "survive the sanity checks (terminal growth above the risk-free rate, margins "
    "above history, and the rest). Never a point target. Name peers to add the "
    "multiple's three contexts.",
    obj(
        {
            "instrument": _str("e.g. 'XNAS:AAPL'"),
            "as_at": _str("YYYY-MM-DD; default today"),
            "archetype": _str("sector archetype, for the industry beta and the method"),
            "peers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "peer instrument ids",
            },
        },
        ["instrument"],
    ),
)(T.valuation_range)

S.tool(
    "peer_set",
    "Who the entity graph says a company's peers are on a date, each with the "
    "edge document behind it. A stated rivalry (competes_with) and a shared "
    "sub-sector are labelled apart; the second is two hops of classification and "
    "reads as speculative. Refused without a built graph. Not financial advice.",
    obj(
        {
            "instrument": _str("e.g. 'MYX:1155'"),
            "as_at": _str("YYYY-MM-DD; default today"),
            "same_market": {
                "type": "boolean",
                "description": "keep peers in the same market (default true)",
            },
        },
        ["instrument"],
    ),
)(T.peer_set)

S.tool(
    "analyst_workup",
    "The twelve-step workup of docs/04 section 2 over the stored record: identity, "
    "the comprehensibility and earnings-quality gates, history, capital allocation, "
    "competitive position, industry and peers, forward drivers, the valuation range, "
    "return decomposition and suggested breakers. Each step says done, partial, "
    "unavailable, manual or not applicable and names the collector that would fill "
    "a gap. A workup, not a stance. Not financial advice.",
    obj(
        {
            "instrument": _str("e.g. 'XNAS:AAPL'"),
            "as_at": _str("YYYY-MM-DD; default today"),
            "archetype": _str("sector archetype, e.g. bank, software"),
        },
        ["instrument"],
    ),
)(T.analyst_workup)

S.tool(
    "method_note",
    "Read the curated method notes: the curriculum (kb_craft), valuation, "
    "technical and risk methods, and the failure library (kb_failures), each "
    "quoted verbatim with its chunk id and its references' licences. Select "
    "by concept key, sector archetype, failure pattern, or free text.",
    obj(
        {
            "collection": _str(
                "kb_craft | kb_method_valuation | kb_method_technical | kb_method_risk | kb_failures"
            ),
            "query": _str("free text"),
            "concept": _str("curriculum key, e.g. 'cash_flow' (kb_craft)"),
            "archetype": _str("sector archetype, e.g. 'bank' (kb_method_valuation)"),
            "pattern": _str("failure pattern tag, e.g. 'accruals_divergence' (kb_failures)"),
            "limit": {"type": "integer", "description": "notes to show (default 4)"},
        },
        ["collection"],
    ),
)(T.method_note)

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
    "open_alerts",
    "Monitor rules currently tripped, and the history of when they opened and "
    "cleared. The other observability tools answer when asked; this reports "
    "what a scheduled `ask.py watch` found while nobody was looking. No "
    "history at all means no rule has been EVALUATED - not that none would fire.",
    obj(
        {
            "history": {"type": "integer", "description": "how many past events (default 10)"},
            "alerts_db": _str("optional alert store path"),
        }
    ),
)(O.open_alerts)

S.tool(
    "quality_report",
    "Is the ANALYSIS any good? Forecast calibration (Brier, stated against "
    "realised), how many claims survived citation verification and why the "
    "rest were dropped, the distribution of attribution verdicts, red-team "
    "activity, and the eval-suite ratchet's health. Refuses to score "
    "calibration below the graded-call minimum rather than reporting luck.",
    obj(
        {
            "days": {"type": "integer", "description": "window (default 30)"},
            "db": _str("ledger path"),
        }
    ),
)(O.quality_report)

S.tool(
    "efficiency_report",
    "Am I paying for what I am getting? Cost per call and per 1k output "
    "tokens, cache HIT RATE (a zero rate across repeated calls means the "
    "cached prefix is changing), tier discipline as a share of spend, and "
    "WASTED spend - calls that billed and returned nothing usable because "
    "they were refused or truncated.",
    obj(
        {
            "days": {"type": "integer", "description": "window (default 7)"},
            "db": _str("ledger path"),
        }
    ),
)(O.efficiency_report)

S.tool(
    "maintainability_report",
    "Can this be changed safely? Test and documentation edges per module "
    "from the codebase graph, which modules have neither, and the runtime "
    "dependency versions. Counts EDGES, not coverage - a module without a "
    "test edge may still be covered indirectly, and the report says so "
    "rather than implying a number it did not measure.",
    obj({"db": _str("codebase graph path (default data/codegraph.db)")}),
)(O.maintainability_report)

S.tool(
    "reasoning_report",
    "How is the model behaving, and what did the user get back? How turns "
    "ENDED (refusal, truncation, end_turn), what the guardrails had to stop "
    "and under which rule, the answered-against-refused split with its most "
    "common reasons, and whether any narrative carried a number the engines "
    "never supplied. Refusal RATE is not a quality score - the design "
    "optimises refusal PRECISION - and the report says so.",
    obj(
        {
            "runs": {"type": "integer", "description": "traced runs to scan (default 20)"},
            "db": _str("ledger path"),
        }
    ),
)(O.reasoning_report)

S.tool(
    "scorecard",
    "Every dimension in one view - robustness, performance, efficiency, "
    "quality, maintainability, usability, reasoning - each with a verdict "
    "and the evidence behind it, or an explicit CANNOT SCORE where the "
    "evidence does not exist yet. Start here, then open the report for "
    "whichever line looks wrong.",
    obj({"db": _str("ledger path")}),
)(O.scorecard)

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

# -- what the collector holds ---------------------------------------------------
# Read these BEFORE pull_news: they are the corpus and the fact book the daily
# collector fills, cost no request and spend no quota. pull_news is a live
# fetch for what the collector has not yet seen.

S.tool(
    "daily_digest",
    "The day's page: per name, the top stories by quality (syndicated copies "
    "folded), tone, what escalated, recent and scheduled events, a snapshot of "
    "the latest figures; then the macro series and the day's collection rows. "
    "Read this first when asked what happened today.",
    obj(
        {
            "day": _str("YYYY-MM-DD; default today (UTC)"),
            "write": {"type": "boolean", "description": "also write data/digests/<day>.md"},
        }
    ),
)(T.daily_digest)

S.tool(
    "fact_snapshot",
    "What the collector holds for one name: the latest figure per concept "
    "with the day it became knowable, events in the last N days, what is "
    "scheduled in the next 60, and documents such as call transcripts. An "
    "empty answer names the sources that would fill it.",
    obj(
        {
            "instrument": _str("e.g. 'MYX:1155' or 'XNAS:NVDA'"),
            "days": {"type": "integer", "description": "event window back from now (default 30)"},
        },
        ["instrument"],
    ),
)(T.fact_snapshot)

S.tool(
    "macro_context",
    "Every recorded macro series at its latest point with its 20-observation "
    "change - Fed funds, yields, the curve, BNM's OPR, MYR/USD, VIX, CPI - or "
    "one series' recent points. Descriptive; never a forecast.",
    obj(
        {
            "series": _str("a series id such as 'DGS10' or 'BNM:OPR'; empty for all"),
            "points": {"type": "integer", "description": "recent points for one series"},
        }
    ),
)(T.macro_context)

S.tool(
    "news_evidence",
    "What the corpus holds about a name, through the news agent's own gate: "
    "hybrid retrieval over collected articles, the entity filter, a freshness "
    "window and a relevance grade. Each story carries five feature dimensions "
    "(relevance, polarity, intensity, uncertainty, forwardness) and a citation. "
    "A refusal means nothing cleared the gate - which is an answer.",
    obj(
        {
            "instrument": _str("the name"),
            "query": _str("what to look for; default the company's name"),
            "days": {"type": "integer", "description": "freshness window (default 7)"},
            "limit": {"type": "integer", "description": "most stories to return (default 6)"},
        },
        ["instrument"],
    ),
)(T.news_evidence)

# -- the paper book (docs/22): read-only ---------------------------------------------
S.tool(
    "paper_status",
    "The paper book's status page: equity, cash, positions, pending targets, each "
    "cap's value against its limit, the fundable set at today's equity (one lot per "
    "name in USD and how many lots the 25% cap allows), turnover headroom, FX quote, "
    "cost drag. A hypothetical USD ledger marked from cached bars; it places nothing. "
    "NO BOOK is the answer when none has been opened.",
    obj({"db": _str("ledger path (default: config.toml [paper] database)")}),
)(T.paper_status)

S.tool(
    "paper_report",
    "The paper book against its passive control: return to date, drawdown, cost "
    "drag, the paper predictions' hit rate, halt and stop state. CANNOT SCORE until "
    "enough sessions are marked - a balance is not a track record.",
    obj(
        {
            "days": {"type": "integer", "description": "window (default 30)"},
            "db": _str("ledger path (default: config.toml [paper] database)"),
        }
    ),
)(O.paper_report)


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


#: The repository root, derived from this file rather than from the process's
#: working directory.
ROOT = Path(__file__).resolve().parent.parent


def _anchor_to_the_repository() -> None:
    """Run against this repo's own files, wherever the client started us.

    An MCP client launches the server as a subprocess and inherits its own
    working directory - and neither Claude Code's `claude mcp add` nor the
    `.mcp.json` schema has a `cwd` field to correct it. Every path this system
    reads is relative: `config.toml`, `data/provenance.db`, `data/graph.db`.
    Started from anywhere else the server does not fail - it quietly loads
    DEFAULT settings, writes a NEW empty ledger next to wherever the client
    happened to be, and answers every question as though this were a fresh
    installation. Silent, plausible, and wrong: the same failure shape as the
    echo backend answering while looking like a model.

    So the server anchors itself. `FINPLANET_NO_CHDIR=1` opts out, for a caller
    that has deliberately arranged its own layout.
    """
    import os

    if os.environ.get("FINPLANET_NO_CHDIR", "").strip() in ("1", "true", "yes"):
        return
    if Path.cwd().resolve() != ROOT:
        os.chdir(ROOT)


def main(argv=None) -> int:
    _anchor_to_the_repository()

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
