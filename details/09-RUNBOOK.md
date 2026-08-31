# 09 — Runbook

Every command, what it needs, and what it prints.

**Nothing here needs the network or an API key.** Anything that would is called
out explicitly.

---

## Setup

```bash
make install      # uv sync --frozen --python 3.11
```

Runtime dependencies: `pydantic`, `pyyaml`, `anthropic` (the model seam - lazily
imported, `EchoBackend` keeps everything offline), and `fastapi` + `uvicorn`
(the web surface). The engines and the tests import none of the last three.
Dev tooling (`uv sync --frozen`): pytest, hypothesis, ruff, pyright, pre-commit.

Windows: `run.bat` mirrors every `Makefile` target. `tests/test_config.py`
asserts that parity, so a Windows user cannot be quietly running a smaller set
of checks.

## The five checks

```bash
make test         # 1,073 tests, ~15s
make verify       # 14 sections, PASS/FAIL
make stress       # 161 held, exits with the finding count
make trace        # end-to-end, writes debug/<run-id>/
make mcp-check    # MCP handshake against itself
make doctor       # preflight: what this installation can actually do
make web          # the twelve screens on http://127.0.0.1:8765
make lint         # ruff check + format check     make typecheck  # pyright
make cov          # tests with the coverage floor (92%)
```

Run all five before pushing. `make test` alone is not enough — see
`08-VERIFICATION.md` for why each one exists.

## The graph

```bash
make graph         # build data/graph.db from checked-in sources
make codegraph     # build the code graph over this repository
make graph-report  # ask.py graph --report
```

Both builds are byte-reproducible; CI builds each twice and runs `cmp`.

## Configuration

```bash
make config        # print the loaded config, with its source
```

Edit `config.toml`, or copy it to `config.local.toml` to keep personal numbers
out of git — the loader prefers the local file.

**Two settings do nothing until you fill them in:**

```toml
holdings = []
watchlist = []
```

The escalation gate (`knowledge/news/features.should_escalate`) only lets an
item through if it touches one of these names. **With both empty, nothing ever
escalates** — a background sweep would run every night and surface nothing.
Keep the watchlist short: the gate exists so a person can read the output.

---

## `ask.py` — the CLI

Ten subcommands.

### `plan` — what the system would do with a question

```bash
python ask.py plan "why did maybank fall today" --instrument MYX:1155
```

Prints the intent, the agents it would run, the estimated cost — or the refusal
and what would help.

### `why` — decompose a move before naming a cause

```bash
python ask.py why MYX:1155 --move -0.090 --market -0.080 --sector -0.020
python ask.py why XNAS:NVDA --fetch --against XNAS:SPY --days 5     # needs a feed
```

Without `--fetch` the numbers are yours and are **labelled as stated rather than
measured**. `--history` takes a CSV of
`instrument_return,market_return,sector_return` rows for the estimation window.

The verdict may be `no_identified_catalyst`, and often should be.

### `prices` — daily bars

```bash
python ask.py prices XNAS:NVDA --days 30
```

Needs a live feed. Offline it says so rather than returning an empty series.

### `thesis` — compose, then red-team

```bash
python ask.py thesis MYX:1155 \
  --breaker "NIM falls below 2.0%" --breaker "CASA below 25%" \
  --evidence "a1_fundamentals=CASA fell to 24%" --stance accumulate
```

**Two to four breakers, written before entry**, are required — a thesis without
falsifiable breakers cannot be constructed.

### `risk` — concentration, heat, drawdown

```bash
python ask.py risk \
  --position MYX:1155:0.22:bank:MY \
  --position XNAS:NVDA:0.18:tech:US
```

Format: `MIC:CODE:weight:sector:country[:risk_to_stop[:currency]]`

The currency defaults to the **market's**, not the country field. Omit it unless
you need to override.

### `size` — a stance into lots, or a refusal

```bash
# MYR market
python ask.py size MYX:1155 --portfolio 200000 \
  --price 6.20 --stop 5.60 --adv 900000

# foreign market — --fx is required
python ask.py size XNAS:NVDA --portfolio 500000 --lot 1 \
  --price 180 --stop 165 --adv 30000000000 --fx 4.20
```

| Flag | Currency |
|---|---|
| `--portfolio` | **MYR** — the book |
| `--price`, `--adv` | the **market's** currency |
| `--fx` | MYR per 1 unit of the market's currency |

Without `--fx` on a non-MYR market the command **refuses** and says why.

"No position" is a frequent and correct outcome. Asking again with a smaller
portfolio just refuses again.

### `learn` — the curriculum

```bash
python ask.py learn --syllabus
python ask.py learn kelly
python ask.py learn kelly --mastered share --mastered compounding
```

Asking for a concept whose prerequisites you have not recorded **exits
non-zero** and names what has to come first.

### `fitness` — can the system score itself yet?

```bash
python ask.py fitness
```

Usually the answer is no, and it names which terms are missing. It will not emit
a headline score from a partial set.

### `graph` — paths, impact, review

```bash
python ask.py graph --path CO:XKLS:1155 CO:XNAS:NVDA --asof 2026-08-28
python ask.py graph --report
```

Expect a refusal or a hub-free path — never a path through `Malaysia`.

### `backend` — which model is actually answering

```bash
python ask.py backend
```

```
backend   EchoBackend
reason    echo (no ANTHROPIC_API_KEY): deterministic stub, NOT a model
          — narrative output is placeholder text
```

Worth one command. A system that quietly ran on `EchoBackend` for a week is
indistinguishable from one that worked.

---

## `predict.py` — the forward record

```bash
make due        # predictions whose horizon has elapsed
make status     # the calibration table
```

Calibration needs `min_graded_for_calibration = 30` graded calls before it means
anything rather than measuring luck. Lowering it does not make you calibrated
sooner.

---

## The MCP server

```bash
make mcp        # stdio transport
make mcp-check  # selftest
```

Twelve tools: `market_info`, `get_prices`, `why_did_it_move`, `fit_factor_model`,
`compose_thesis`, `check_portfolio_risk`, `size_position`, `plan_question`,
`explain_concept`, `log_prediction`, `calibration_status`, `explain_path`.

Client setup is in `docs/15-MCP-SETUP.md`.

---

## Infrastructure

```bash
make up       # docker compose up -d
make down
make health   # docker compose ps
```

---

## Adding things

| Adding | Requires |
|---|---|
| an agent | an entry in `agents/registry.yaml` **plus an eval suite with negative cases** — the loader refuses otherwise |
| a market | an adapter class plus one `_ADAPTERS` entry; the ABC requires `regulator`, `currency`, `tier`, fees, lots, ticks |
| a market **spelling** | one entry in `ALIASES` — never a second adapter |
| a knowledge store | an entry under `knowledge:` with `created_by` and `managed` |
| a task class | an entry in `ROUTING`; an unrouted class raises rather than defaulting |
| a graph edge kind | the enum, an `EDGE_DECAY` entry, and an `EDGE_INVERSE` entry if it has one |

---

## Turning on a real model

The only step that needs a key, and it is deliberately the last one.

```bash
export ANTHROPIC_API_KEY=...
python ask.py backend      # confirm it is no longer EchoBackend
```

Everything above this line works without it. The narrative output is the only
part that changes — every number, every cap, every refusal is computed the same
way either side of that key.

`docs/13` records one improvement waiting to be adopted when the key is wired:
the error taxonomy from `nousresearch/hermes-agent` separates retryable failures
from permanent auth errors, and context-overflow (which must compress, not
retry) from a generic 400. `core/llm/backends.py` currently has a flat
`RETRY_STATUS` set.
