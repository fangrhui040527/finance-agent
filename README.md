# finance-agent

**FinPlanet — Module 5: The Analyst Mind.**
A multi-agent, multi-market equity research system that explains *why* a price moved, analyses a company the way an analyst actually does, and enforces risk limits in code rather than in prompts.

> **Not financial advice.** Candidacy bands, calibrated probabilities, attributions and sizing constraints with evidence chains. No recommendations, no execution.

## The implementation plan

Start at **[`docs/README.md`](docs/README.md)**.

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

## Status

**Everything except P16 is built and tested.** 434 tests, no network and no keys
needed to run any of it. CI runs the suite, `verify.py`, the eval ratchet and the
no-execution grep on every push.

P16 is the paper-trade gate: 3–6 months of elapsed forward time, not unbuilt
work. Its machinery exists and refuses to grade a prediction before its horizon.
P18–P19 wait on P16. **If you are picking this up, start at
[`docs/14-OPERATIONS-RUNBOOK.md`](docs/14-OPERATIONS-RUNBOOK.md).**

```bash
make install && make test    # full suite
make verify                  # end-to-end on mock data, <1s
make due                     # predictions that have reached their horizon
make status                  # the calibration table
make up                      # postgres+timescale · qdrant · neo4j · redis · minio
```

| Phase | Ships | Module |
|---|---|---|
| P0 | Tier router, guardrail chain, provenance ledger, typed answers | `core/` |
| P1 | Instrument identity, session calendars, price adjustment, FX | `core/market/` |
| P2 | Market adapter contract + XKLS + XNAS + XSES + conformance | `markets/` |
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
| P18 | Singapore (XSES), the first T2 market | `markets/xses.py` |

**Not built:** P16's forward record — 3–6 months of elapsed time, not effort;
its tooling is built and its clock starts with `python predict.py log`. P19
depends on that record. P18 is started, not finished: XSES is onboarded, HK/JP/UK/AU
are not. Live feed ingest is deliberately unwired —
`GdeltFeed._fetch_raw` raises rather than returning empty, so the offline build
cannot pretend to have data. Everything downstream of the adapter seam is built
and tested. See [`docs/07-BUILD-ORDER.md`](docs/07-BUILD-ORDER.md) §0.

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
  test fails if any class drifts. All in
  [`docs/05`](docs/05-RISK-AND-GUARDRAILS.md) §3.5.

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
