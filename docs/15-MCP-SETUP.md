# Running this as an MCP server

The reasoning runs on **your** Claude session. The engines still decide every
number.

## Why this is the right architecture for this system

This repository is ~9,000 lines of deterministic finance code and, deliberately,
almost no prompting. Attribution, the five sizing caps, concentration limits,
cost floors, point-in-time correctness — all of it is arithmetic that must give
the same answer every time, and none of it wants a language model anywhere near
it.

What it *does* want a model for is the part code is bad at: reading a filing,
weighing conflicting evidence, writing a thesis a human can argue with, and
attacking that thesis honestly.

MCP splits it exactly along that line:

| | Decides | Where it runs |
|---|---|---|
| **Engines** | every number, every limit, every refusal | this process, deterministic |
| **Claude** | narrative, judgement, synthesis | your Claude session |

The alternative — this system holding an API key and calling the model itself —
puts a *second* reasoning layer inside a codebase whose entire value proposition
is that reasoning cannot move the numbers. That backend still exists
(`core/llm/backends.py`) and is the right choice for unattended jobs. For sitting
down and researching a company, MCP is better.

## What the model cannot do through these tools

Worth being concrete, because "let the model drive" sounds alarming for something
that touches money. Enforced in code and tested in `tests/test_mcp_server.py` and
stress section 9:

- **Cannot place, route, or simulate an order.** No such tool exists. A grep over
  the whole repository (`test_no_execution_anywhere.py`) fails the build if one
  is ever added.
- **Cannot size past a cap.** All five caps run on every `size_position` call.
  Asking repeatedly with a smaller portfolio just refuses seven times.
- **Cannot widen a limit.** `single_name_limit` above 15% is refused by
  `Limits.__post_init__`, not by a prompt.
- **Cannot obtain a position below the market's cost floor.** RM 4,706 on Bursa,
  HKD 28,081 on HKEX.
- **Cannot get a number out of a non-quantity.** NaN, infinity, negative and zero
  inputs are refused *before* they reach a cap. Each of these produced a real
  position before the guards existed.
- **Cannot take a stance without two machine-checkable breakers.**
- **Cannot invent prices.** A missing feed returns `NO DATA`, never silence.

The model can, of course, write whatever narrative it likes. That is why every
analysis tool returns its numbers alongside the text, and why the unexplained
share is always on screen.

## Connecting it

Both clients need **absolute paths** and the project's own interpreter.

**Working directory is handled for you.** An MCP client launches the server as a
subprocess and it inherits the client's working directory; Claude Code's
`claude mcp add` has no `cwd` flag to correct that. Every path this system reads
is relative — `config.toml`, `data/provenance.db`, `data/graph.db` — so started
elsewhere the server would not fail, it would quietly load *default* settings and
write a *new empty* ledger beside wherever the client happened to be, answering
every question as though this were a fresh installation. So the server anchors
itself to its own repository at startup (`mcp_server/server.py`,
`_anchor_to_the_repository`). `FINPLANET_NO_CHDIR=1` opts out.

### Claude Desktop

`claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`)

```json
{
  "mcpServers": {
    "analyst-mind": {
      "command": "/ABSOLUTE/PATH/finance-agent/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/ABSOLUTE/PATH/finance-agent"
    }
  }
}
```

Windows: `"command": "C:\\path\\finance-agent\\.venv\\Scripts\\python.exe"`.

### Claude Code

```bash
claude mcp add analyst-mind --scope user -- /ABSOLUTE/PATH/.venv/bin/python -m mcp_server.server
```

Windows, with the path spelled in full:

```bash
claude mcp add analyst-mind --scope user -- "C:/path/finance-agent/.venv/Scripts/python.exe" -m mcp_server.server
```

`--scope user` makes it available in every project; drop it for this project
only. Confirm with `claude mcp list` — the row should say **Connected**.

### Verify before connecting

```bash
make mcp-check          # Windows: run mcp-check
```

Runs the full `initialize → tools/list → tools/call` handshake against itself. If
that fails, no client will do better — fix it here, where the error is visible.

## Must it run locally?

**Yes, and that is the right answer for this system.** Three reasons:

1. **Your positions never leave your machine.** `check_portfolio_risk` takes your
   real book. `log_prediction` writes your real calls. On stdio that data goes to
   a subprocess you own and nowhere else. A hosted server would need all of it
   uploaded.
2. **No auth surface.** stdio has no port, no token, no TLS to get wrong. A
   remote MCP server holding portfolio data needs all three done properly.
3. **The stores are local.** SQLite provenance ledger, SQLite prediction log,
   `config.toml`. These are files on your disk by design.

A remote HTTP/SSE transport is possible later and would let you reach it from
claude.ai. It is a real project — auth, transport, hosting, and the decision to
put your book on someone else's machine — not a config flag. Local first.

**This also means it cannot be connected to a Claude Code *web* session**, which
runs in a container elsewhere and cannot reach your laptop. Use the Claude Code
CLI or Claude Desktop on the machine that holds the data.

## Subscription or API?

Plainly:

- **Interactive research — you asking questions, Claude calling tools —** is what
  a Max plan is for, and the better setup. No per-token cost, and you get the
  strongest model rather than the one the budget rail allows.
- **Unattended and scheduled work** — a daily brief at 06:00 with nobody
  watching — is what the API and `core/llm/backends.py` are for. Don't drive a
  subscription session from a cron job.

Both can be true at once. The seam already supports it: `backend_from_env()`
picks the API backend for headless jobs, and MCP serves the interactive path.

## A first session

```
Use market_info for XKLS, then get_prices for MYX:1155 over 60 bars.
Then why_did_it_move with market_proxy — I want the decomposition before
any explanation. If the unexplained share is under 30%, don't invent a
company story.
```

Then the workup:

```
compose_thesis for MYX:1155. Evidence from what we just found. Two
breakers, each with a query that could actually be run. Give me the red
team in full — I want the strongest case against, not a summary.
```

Then, and this is the part that compounds:

```
log_prediction: +1, 63 days, whatever confidence you actually hold.
```

`calibration_status` is worthless for months and then becomes the only number in
this repository that tells you whether any of it works.

---

## Transport notes (2026-08-31)

- **stdout purity is tested**: the serve loop emits JSON-RPC lines only;
  selftest/--list human output goes to stderr, and `print` is lint-forbidden
  inside `mcp_server/`.
- **Version negotiation**: `initialize` echoes the client's protocolVersion
  when it is one this server actually speaks (2024-11-05 … 2025-06-18), else
  offers its own - never claims a version it has not implemented.
- The web app (`make web`) exposes the same tool functions over HTTP for a
  browser; the MCP surface remains the model-facing one.

---

## Watching the machine itself (2026-08-31)

Four read-only tools let the model that drives this system also monitor it.
They exist because a stubbed backend or a missing database explains more odd
output than any amount of reasoning about the output.

| Tool | Answers |
|---|---|
| `system_health` | What can this installation do right now, and what does each gap affect? (`offline=false` also probes the price and news sources.) |
| `operating_report` | Over N days: calls, spend, budget headroom, which MODELS actually answered, p50/p95 latency, cache hits, and the dropped-claim rate. |
| `recent_failures` | Across recent traced runs: errors with their text, guardrail denials, refusals, and the run id to open. |
| `run_anatomy` | One run: where the time went, what was denied - and whether the METHODOLOGY changed since the run before it. |
| `open_alerts` | What a scheduled `ask.py watch` found while nobody was looking, and since when. |
| `scorecard` | **Start here.** Every dimension in one line with its evidence, or an explicit CANNOT SCORE. |
| `quality_report` | Calibration (Brier, stated vs realised), claim survival and why the rest dropped, verdict distribution, red-team activity, eval-ratchet health. |
| `efficiency_report` | Cost per call and per 1k output, cache HIT RATE, tier discipline as a share of spend, and wasted spend. |
| `maintainability_report` | Test and doc edges per module from the codebase graph, modules with neither, dependency versions. |
| `reasoning_report` | How turns ENDED, what the rails stopped and under which rule, answered-vs-refused with reasons, numeric faithfulness. |

Two properties hold across all of them:

* **Absent evidence is reported as absent.** An empty ledger is "nothing has
  run yet", never a clean bill of health; a failure scan states how many runs
  it looked at, because an untraced run cannot be reported on.
* **No prompt text is ever returned.** Traces hold verbatim prompts,
  responses and whatever positions were passed in. These tools give the
  error, the event and the run id; reading the text is a decision the
  operator makes by opening `debug/<run_id>/`.

### What each dimension rests on, and what it cannot say

| Dimension | Evidence | Honest limit |
|---|---|---|
| performance | ledger latency, spend | none |
| efficiency | ledger tokens, cache fields, stop_reason | rows written before those columns report "unrecorded" |
| robustness | preflight checks, trace errors, guardrail denials | only traced runs can be reported on |
| quality | graded predictions, claims table, verdict codes, eval suites | calibration refuses below the graded minimum; eval **shape**, never a pass rate nobody ran |
| maintainability | codebase graph edges | counts EDGES, not coverage - a module without a test edge may still be covered indirectly |
| usability | answered-vs-refused in traces | refusal RATE is not a score; the design optimises refusal PRECISION |
| reasoning | stop_reason, rail denials, the numeric-faithfulness check | a flagged number may be a rounding, not an invention - it is a list to read, not a verdict |

`run_anatomy`'s methodology check is the one worth knowing about. The
manifest hashes the system prompts, the registry, the tool surface and the
package versions - never the run id or the timestamp - so an equal hash means
an equal method, and a surprising run can be attributed to the DATA rather
than to a change nobody remembers making.
