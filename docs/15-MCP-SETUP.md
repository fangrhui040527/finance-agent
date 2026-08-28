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
claude mcp add analyst-mind -- /ABSOLUTE/PATH/.venv/bin/python -m mcp_server.server
```

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
