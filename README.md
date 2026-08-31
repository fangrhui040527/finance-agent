# finance-agent

**FinPlanet — Module 5: The Analyst Mind.**
A multi-agent, multi-market equity research system that explains *why* a price moved, analyses a company the way an analyst actually does, and enforces risk limits in code rather than in prompts.

> **Not financial advice.** Candidacy bands, calibrated probabilities, attributions and sizing constraints with evidence chains. No recommendations, no execution.

## Start here

**New to this? Open [`docs/user-guide.html`](docs/user-guide.html) in a browser.**
Install, every command, and the weekly-to-quarterly cadence for using it.

Then [`docs/pipeline.html`](docs/pipeline.html) to see the data flow end to end,
[`docs/world-sources.html`](docs/world-sources.html) for where knowledge can come
in from worldwide and what each source costs, and
[`docs/14-OPERATIONS-RUNBOOK.md`](docs/14-OPERATIONS-RUNBOOK.md) for what to
monitor and what should make you stop.

## The implementation plan

Full design detail starts at **[`docs/README.md`](docs/README.md)**.

| Doc | Covers |
|---|---|
| [01 System architecture](docs/01-SYSTEM-ARCHITECTURE.md) | Layers, agent org chart, request lifecycle, deployment, data model, tier routing |
| [02 Agents and their RAG](docs/02-AGENTS-AND-RAG.md) | 16 agents, 9 vector collections, per-agent retrieval configs and evals |
| [03 Why did it move?](docs/03-WHY-IT-MOVED.md) | Factor decomposition, catalyst scoring, 5-year return decomposition |
| [04 The analyst method](docs/04-ANALYST-METHOD.md) | The 12-step company workup, valuation by sector archetype, the trader's layer |
| [05 Risk and guardrails](docs/05-RISK-AND-GUARDRAILS.md) | Capital waterfall, five sizing caps, concentration, drawdown governance, guardrail chain |
| [06 Data and knowledge sources](docs/06-DATA-AND-KNOWLEDGE-SOURCES.md) | Market coverage ladder, adapter contract, ingestion, KB construction |
| [07 Build order](docs/07-BUILD-ORDER.md) | Phases, definitions of done, what to cut |
| [08 Cost breakdown](docs/08-COST-BREAKDOWN.md) | Three tiers, token math, break-even analysis, cost controls |
| [09 Research sources](docs/09-RESEARCH-SOURCES.md) | Every source behind every design decision |
| [10 Repository references](docs/10-REPOSITORY-REFERENCES.md) | Every GitHub repo used, verified licences, obligation map |
| [11 Template audit](docs/11-TEMPLATE-AUDIT.md) | What `awesome-llm-apps` actually contains, measured; what to adopt and what to drop |
| [12 Code reference map](docs/12-CODE-REFERENCE-MAP.md) | Exact file:line pointers into each upstream repo, pinned to commit SHAs |
| [13 The self-learning loop](docs/13-SELF-LEARNING-LOOP.md) | Hermes-agent studied; the assembled A15 loop and its provenance gate |
| [14 Operations runbook](docs/14-OPERATIONS-RUNBOOK.md) | **What to do next, what to monitor, and what should make you stop** |
| [15 MCP setup](docs/15-MCP-SETUP.md) | **Run it as an MCP server so the reasoning is your Claude session** |
| [16 Tracing and anatomy](docs/16-TRACING-AND-ANATOMY.md) | **Every prompt, every guardrail decision, every dropped claim — `make trace`** |
| [17 Knowledge graph](docs/17-KNOWLEDGE-GRAPH.md) | **Multi-hop exposure end to end: confidence, validity, the citation seam, the build, the review surface, and the codebase graph** |

## Status

**Everything except P16 is built and tested - including the twelve-screen web
app** (`make web`, 127.0.0.1 only). 1,400+ tests, no network and no keys needed
to run any of them. CI runs lint (ruff), types (pyright), the suite with a 92%
coverage floor, `verify.py`, stress, the eval ratchet, preflight doctor and the
no-execution grep on ubuntu AND windows, 3.11 and 3.12, from a committed
uv.lock.

Runtime deps: `pydantic`, `pyyaml`, plus `anthropic` (model seam; EchoBackend
keeps everything offline) and `fastapi`/`uvicorn` (web). Engines and tests
import none of the last three. Live model testing runs under
`FINPLANET_CHEAP=1`, which resolves every tier to the cheapest model and says
so on every surface.

P16 is the paper-trade gate: 3–6 months of elapsed forward time, not unbuilt
work. Its machinery exists and refuses to grade a prediction before its horizon.
P18–P19 wait on P16. **If you are picking this up, start at
[`docs/14-OPERATIONS-RUNBOOK.md`](docs/14-OPERATIONS-RUNBOOK.md).**

```bash
make install && make test    # full suite   (Windows: run install && run test)
make config                  # settings, and the bounds they cannot cross
make verify                  # end-to-end on mock data, <1s
make stress                  # adversarial: volume, NaN, thresholds, concurrency, live seams, MCP
make trace                   # full traced system run -> debug/<run_id>/
make mcp-check               # MCP handshake selftest, no client needed
make doctor                  # preflight: what this installation can actually do
make web                     # the twelve screens on http://127.0.0.1:8765
make graph                   # build the knowledge graph -> data/graph.db
make graph-report            # hubs, orphans, review queue, surprising links
make codegraph               # the repo as a graph -> data/codegraph.db
make mcp                     # serve MCP on stdio -> docs/15-MCP-SETUP.md
python ask.py backend                        # which model is actually answering
python ask.py why MYX:1155 --move -0.09 --market -0.08
python ask.py why XNAS:NVDA --fetch --against XNAS:SPY --days 5
python ask.py prices XNAS:NVDA --days 30     # live daily bars
python ask.py thesis MYX:1155 --breaker "NIM below 2%|nim < 0.02|kb_filings" ...
python ask.py risk --position MYX:1155:0.22:bank:MY
python ask.py size MYX:1155 --portfolio 200000 --price 6.20 --stop 5.60 --adv 900000
python ask.py learn --syllabus
python predict.py log MYX:1155 +1 63d 0.62 "NIM recovers"
make due                     # predictions that have reached their horizon
make status                  # the calibration table
make up                      # postgres+timescale · qdrant · neo4j · redis · minio
```

| Phase | Ships | Module |
|---|---|---|
| P0 | Tier router, guardrail chain, provenance ledger, typed answers | `core/` |
| P1 | Instrument identity, session calendars, price adjustment, FX | `core/market/` |
| P2 | Market adapter contract + 11 markets + conformance | `markets/` |
| P3.5 | Point-in-time `known_at` store, survivorship-safe universes | `core/market/pointintime.py` |
| P4 | Attribution: robust regression, decomposition, long-horizon | `engines/attribution/` |
| P5 | News corpus: five-dimension features, wire dedup, escalation gate | `knowledge/news/` |
| P6 | Event taxonomy, base-rate table, six-factor catalyst scoring | `engines/events/` |
| P3 | Parent-child chunking, hybrid BM25+dense+RRF, grader, scoped router | `knowledge/` |
| P10 | Concentration: HHI, effective bets, correlation clusters | `engines/risk/` |
| P11 | Waterfall, five caps, unconstructable-if-breached decisions | `engines/sizing/` |
| P12 | Purged walk-forward, cost model, deflated Sharpe, 3 benchmarks | `engines/backtest/` |
| P7 | A1–A8 evidence agents, each owning one collection | `agents/evidence/` |
| P8 | A9 attribution, A10 thesis, A11 red team, A0 supervisor | `agents/synthesis/`, `agents/supervisor.py` |
| P9 | Entity graph, per-hop decay, path-required impact claims | `knowledge/graph/` |
| P13 | Deferred outcome queue, inverted lesson gate, calibration | `agents/learning/reflection.py` |
| P14 | 30-concept curriculum with an enforced prerequisite graph | `agents/learning/teacher.py` |
| P15 | Decomposition bars, annotated chart, thesis memo, daily brief | `ui/render.py` |
| P17 | Capability registry and the eval ratchet, 16 suites | `core/registry/`, `evals/` |
| P16 tooling | Durable prediction log + CLI — the clock the gate needs | `agents/learning/store.py`, `predict.py` |
| Entrypoint | `ask why` / `ask plan` — decomposition and routing from the shell | `ask.py` |
| P18 | T2 markets: SG HK JP UK AU IN TW KR DE | `markets/` — 11 adapters |
| P18 | Hong Kong (XHKG), the second — per-issuer board lots, uncapped stamp | `markets/xhkg.py` |
| Model | Anthropic Messages backend behind the one `Backend` seam | `core/llm/backends.py` |
| Prices | Stooq daily bars, validated at the seam | `core/market/feed.py` |
| Entrypoints | `thesis` · `risk` · `size` · `learn` · `prices` · `backend` | `ask.py` |
| MCP | 11 tools over stdio — the engines decide, your Claude narrates | `mcp_server/` |
| Trace | Every prompt, rail decision and dropped claim; 4 reports per run | `core/trace/`, `trace_run.py` |

**Not built:** P16's forward record — 3–6 months of elapsed time, not effort;
its tooling is built and its clock starts with `python predict.py log`. P19
depends on that record. The **frontend** is designed but unimplemented: 12
artboards in `design/`, against 273 lines of terminal rendering in `ui/render.py`.

**Two live sources are wired**, both free and keyless: GDELT for news
(`knowledge/feeds/adapter.py`) and Stooq for daily bars (`core/market/feed.py`).
Filings, ownership and macro have no ingest yet — supply those numbers or the
agents that need them report a gap. A real model backend exists at
`core/llm/backends.py`; `python ask.py backend` says whether a model or the
deterministic stub is answering, because the two are otherwise
indistinguishable. See [`docs/07-BUILD-ORDER.md`](docs/07-BUILD-ORDER.md) §0.

### What building it found

- **The 30 bps cost floor is unreachable on Bursa** — a round trip is ~46 bps
  before the RM 8 minimum. The floor is now per-market, and the minimum economic
  Bursa position is **~RM 4,700**. A single lot at RM 6.20 costs 284 bps to trade.
- **The plan's own Kelly worked example trips its own sanity ceiling** —
  p=0.56, b=1.8 gives f\*=31.6%, over the "30% edge means the model is broken"
  rule. The ceiling wins.
- **Market impact dwarfs fees at the liquidity cap** — a fill at 5% of ADV costs
  ~224 bps of impact against 23 bps of Bursa fees. The cost floor is a lower
  bound on cost, not an estimate of it.
- **A rejected catalyst was rendering as the cause** — the renderer branched on
  list emptiness instead of the verdict, so a story scoring 0.11 appeared under
  the move as though it explained it.
- **Six agent ids drifted from the registry.** Nothing crashed. A10 simply
  reported two evidence gaps that were in fact covered, docked its own
  confidence by 0.24 on **every** thesis, and the red team raised a coverage
  challenge on every thesis forever — which is the same as never raising one.
  The registry is now load-bearing: the tool allowlist is derived from it, and a
  test fails if any class drifts.
- **Hong Kong is the most expensive market here, not the cheapest.** Uncapped
  both-sided stamp duty plus 0.25% retail brokerage puts HK's asymptotic
  round-trip cost at **~72 bps**, against Bursa's 46 and XNAS's 0.6. The minimum
  economic HK position is **~HKD 28,000**, six times Bursa's. Ranking markets by
  how developed they are gets the cost ranking backwards.
- **`MYX` never resolved to `XKLS`.** Every instrument id in this repo is written
  `MYX:1155`; the adapter MIC is `XKLS`; nothing mapped between them. So
  `cost_floor_bps("MYX")` missed its table and returned the 30 bps **default**
  instead of Bursa's 60 — every Bursa position sized against half the real floor,
  and nothing crashed. The same drift class as the six agent ids. One resolver in
  `markets/registry.py` now owns it, and a test fails if any legal spelling
  reaches a different adapter or a different floor.
- **A cost model with no fixed minimum has no floor to find.** `cost_floor_value`
  bisects to RM 100,000,000 when the asymptotic cost already exceeds the floor —
  a number that reads as a position requirement rather than the impossibility it
  is. What makes small positions uneconomic is the RM 8 *minimum*, not the rate.
- **`Learner.mastered` accepted concepts that do not exist**, then raised a bare
  `KeyError` from `level` on the next call — and a mis-typed prerequisite reads
  as unmet forever, so the learner is sent back to a concept they already did.
- **Four more from stress testing, none of which crashed.** A NaN return reached
  a verdict as `nan% unexplained`; `liquidity_cap` could go negative and so win
  `binding()` every time — a cap that inverts what it bounds; HHI could exceed
  its own [0,1] range; and an invalid correlation matrix gave **0.67 effective
  bets from two positions**. Each produced something that looked like an answer.
  All in [`docs/05`](docs/05-RISK-AND-GUARDRAILS.md) §3.5.

### What P0 enforces

| Guarantee | Where | Test |
|---|---|---|
| Callers cannot pick a model tier — it derives from `TaskClass` | `core/llm/tiers.py` | `test_tier_routing.py` |
| No order-placement code exists anywhere in the repo | `core/guardrails/policy.py` + repo grep | `test_no_execution_anywhere.py` |
| All five rails run on every request; no bypass path | `core/guardrails/chain.py` | `test_guardrail_chain.py` |
| A claim without a verified verbatim citation is dropped individually | `core/contracts/answer.py` | `test_answer_contract.py` |
| The ledger is append-only — UPDATE and DELETE abort | `core/provenance/ledger.py` | `test_provenance.py` |
| Human-authored knowledge is never agent-editable | `core/contracts/provenance_marker.py` | `test_provenance.py` |
| Budget exhaustion raises; it never downgrades silently | `core/llm/client.py` | `test_inference_client.py` |
| Telemetry sits beside content, never inside it | `core/provenance/sidecar.py` | `test_sidecar.py` |
| Nothing registers without an eval suite carrying negative cases | `core/registry/loader.py` | `test_registry.py` |
| Agent identity and tools cannot drift from the registry | `core/registry/loader.py` | `test_evidence_agents.py` |
| A significant move with no catalyst is never given one | `engines/attribution/` + `ui/render.py` | `test_render.py` |
| A prediction cannot be graded before its stated horizon | `agents/learning/reflection.py` | `test_learning.py` |
| A concept cannot be taught before its prerequisites | `agents/learning/teacher.py` | `test_learning.py` |
| A logged prediction can never be edited or deleted | `agents/learning/store.py` | `test_learning_store.py` |
| Every supported market has an explicit cost floor | `engines/sizing/caps.py` | `test_market_foundation.py` |
| A non-finite input never reaches a verdict | `engines/attribution/decompose.py` | `test_attribution.py` |
| No cap can go negative and win `binding()` | `engines/sizing/caps.py` | `test_risk_sizing.py` |
| Effective bets never leaves `[1, n]` | `engines/risk/concentration.py` | `test_risk_sizing.py` |
| A broken feed never returns an empty series | `core/market/feed.py` | `test_price_feed.py` |
| An unmapped market raises rather than guessing a symbol | `core/market/feed.py` | `test_price_feed.py` |
| A truncated model answer is never returned as a whole one | `core/llm/backends.py` | `test_anthropic_backend.py` |
| A missing API key fails at construction, not mid-plan | `core/llm/backends.py` | `test_anthropic_backend.py` |
| Model retries are bounded and classified, never unbounded | `core/llm/backends.py` | `test_anthropic_backend.py` |
| Every legal id prefix reaches one adapter and one cost floor | `markets/registry.py` | `test_market_aliases.py` |
| Mastery cannot be claimed for a concept that does not exist | `agents/learning/teacher.py` | `test_ask_cli_agents.py` |
