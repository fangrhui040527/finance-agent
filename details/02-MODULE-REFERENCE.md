# 02 — Module reference

Every package and every module, what it owns, and the symbol worth knowing.

169 Python files, 29,286 lines. Test files (43 files, 9,478 lines) are covered
in `08-VERIFICATION.md`.

---

## `core/` — 29 files, 3,565 lines

The contracts and the cross-cutting machinery. Nothing here knows about
equities specifically; it knows about money, provenance, guardrails and models.

### `core/contracts/`

| Module | Owns |
|---|---|
| `money.py` | `Money(amount, currency, fx_asof)`, `BASE_CURRENCY = "MYR"`. `convert()` refuses a non-positive rate; same-currency conversion is an identity that ignores the rate. `currency` must be three **letters** — the length bound alone accepted `"123"`. |
| `answer.py` | `Answer`, `Claim`, `Citation`, `verify_claim`. Verbatim substring matching, `MIN_QUOTE_CHARS = 8`. `Claim.all_citations_required` distinguishes a conjunctive chain (all citations must hold) from redundant support (any one will do). |
| `provenance_marker.py` | the marker every stored artefact carries: who made it, from what, when. |

### `core/guardrails/`

| Module | Owns |
|---|---|
| `chain.py` | the five rails, in order: `INPUT → RETRIEVAL → TOOL → OUTPUT → PUBLICATION`. |
| `policy.py` | `PolicyEngine.enforce` — **raises**, does not return a warning. |
| `defaults.py` | the default policy set, including the tool allowlist derived from the registry. |

The rail order is not decoration. `PUBLICATION` runs last so that a claim which
passed `OUTPUT` can still be withheld from a surface — the two are different
questions.

### `core/llm/`

| Module | Owns |
|---|---|
| `tiers.py` | `TaskClass` (22 classes) → `Tier` (`REASON`/`BALANCED`/`CHEAP`/`EMBED`/`LOCAL`). `route()` raises `TierRoutingError` for an unregistered class. `PRICING_USD` and `MODEL_IDS` per tier. `cost_usd()` bills cached input at ~10%. |
| `client.py` | `InferenceClient.complete()` — the single seam every model call goes through. Records to the ledger before returning. |
| `backends.py` | `EchoBackend` (deterministic stub, no key) and the Anthropic backend. `backend_from_env()` says which is answering and why. |

**Callers never pick a tier.** They declare what the call is *for*; the tier is
derived. This is why the pricing table can change without touching an agent.

### `core/provenance/`

| Module | Owns |
|---|---|
| `ledger.py` | append-only SQLite. Four `RAISE(ABORT, 'provenance ledger is append-only')` triggers on UPDATE and DELETE. WAL enabled via `_enable_wal` with `busy_timeout` set **first**. Records agent, task class, tier, tokens, cost, run id, `latency_ms`. |
| `fitness.py` | the docs/01 §10 fitness function. Emits **no headline score unless every term is available** — groundedness, citation validity, refusal precision, attribution accuracy, forecast calibration, p95 latency, cost per query. |
| `sidecar.py` | the per-artefact provenance sidecar. |

`fitness.py` refusing to emit a partial score is the point. A fitness number
computed from the four terms you happen to have is a number that improves when
you delete the hard terms.

### `core/market/`

| Module | Owns |
|---|---|
| `instrument.py` | instrument id parsing and validation. |
| `prices.py` | `Bars`, ATR, and `FxStore` — **explicit rates only**, `rate_asof(base, quote, d)` with bisect lookup and an inverse-pair fallback. There is no implicit global rate lookup anywhere. |
| `pointintime.py` | as-of reads. A backtest that can see tomorrow is not a backtest. |
| `calendar.py` | trading days, holidays. |
| `feed.py` | the price feed abstraction. |

### `core/registry/`

`loader.py` — loads `agents/registry.yaml` and **refuses at load time** if any
capability lacks an eval suite, or any suite lacks negative cases. It also owns
`FORBIDDEN_TOOLS`, the list no agent may hold.

### `core/trace/`

`tracer.py` (spans, `emit`) and `report.py` (the HTML artefact). Produces
`debug/<run-id>/` with `trace.jsonl`, `report.html`, `anatomy.md`,
`session.log`, `summary.json` and `prompts/`.

### `core/config.py`

Loads `config.toml`, preferring `config.local.toml` when present. Validates
instrument ids at load, so a typo in `watchlist` fails loudly rather than
silently never matching.

---

## `agents/` — 14 files, 2,485 lines

| Module | Agents |
|---|---|
| `base.py` | `Agent`, `AgentContext`, `Finding`. `_guard_tool` is the seam every tool call passes. |
| `supervisor.py` | `A0Supervisor` — `plan`, `budget`, `route`, `refuse`. Owns `Intent`. |
| `evidence/agents.py` | `A1`–`A8` |
| `synthesis/agents.py` | `A9Attribution`, `A10Thesis`, `A11RedTeam`; `Stance`, `Breaker` |
| `portfolio/agents.py` | `A12PortfolioRisk`, `A13Sizing`; `DRAWDOWN_TIERS` |
| `learning/teacher.py` | `A14Teacher`, `Learner` — 30 concepts in a prerequisite order the code enforces |
| `learning/reflection.py` | `A15Reflection` — grades the queue, proposes lessons, calibrates |
| `learning/scoring.py` | `recency × relevance × evidence`, multiplicative. Half-life 90 days, decaying from `last_confirmed` rather than `created_at` |
| `learning/store.py` | the lesson store |

See `04-AGENTS.md` for the full table.

---

## `engines/` — 18 files, 2,026 lines

Pure computation. No model calls, no I/O.

| Package | Modules | Owns |
|---|---|---|
| `attribution/` | `decompose.py`, `regression.py` | the "why did it move" decomposition; `unexplained_share`; refuses non-finite inputs with `attribution_unavailable` |
| `risk/` | `concentration.py` | `hhi`, `effective_number_of_bets`, `correlation_clusters`, `check`, `Position`, `Limits`, `Breach` |
| `sizing/` | `caps.py`, `decision.py`, `waterfall.py` | the five caps, `CapSet.binding()`, `size()`, the investable-capital waterfall |
| `events/` | `taxonomy.py`, `catalyst.py` | `EventType`, `BaseRateTable`, `SurpriseBucket`; catalyst attachment and scoring |
| `backtest/` | `harness.py`, `costs.py`, `metrics.py`, `splitter.py` | walk-forward splitting, cost modelling, metrics |

**`concentration.py` validates its inputs rather than trusting them.** A
correlation of 2.0 once produced *0.67 effective bets from two positions*, which
is outside the mathematically possible `[1, n]`. Both the matrix and the weights
are now checked, and the result clamped.

---

## `knowledge/` — 28 files, 3,864 lines

| Package | Owns |
|---|---|
| `graph/` | the entity graph — see `06-KNOWLEDGE-GRAPH.md`. 14 modules including 7 extractors. |
| `retrieval/` | `hybrid.py` (lexical + dense), `pipeline.py` (`Router`) |
| `chunking/` | `parent_child.py` — retrieve the child, return the parent |
| `news/` | `features.py` — `LexiconExtractor`, `near_duplicate_hash`, `should_escalate` |
| `feeds/` | `adapter.py` — `FixtureFeed`, `link_entities`, the offline-safe feed contract |

**A disabled feed says so.** It never returns an empty list that a caller could
read as a quiet news day. That distinction is the difference between "no news"
and "the news source is broken", and only one of them is safe to act on.

---

## `markets/` — 14 files, 1,333 lines

`contract.py` (the `MarketAdapter` ABC, `FeeSchedule`, `FeeLeg`), `registry.py`
(the adapter map, the alias map, `resolve_mic`, `mic_of`, `market_currency`),
and 11 adapter classes. See `05-MARKETS-AND-MONEY.md`.

---

## `mcp_server/` — 4 files, 1,196 lines

A hand-written MCP server with no SDK dependency.

| Module | Owns |
|---|---|
| `protocol.py` | `Server` — `initialize`, `tools/list`, `tools/call`; JSON-RPC framing |
| `tools.py` | 12 exposed tools |
| `server.py` | stdio transport plus `--selftest`, which runs the whole handshake against itself |

Exposed tools: `market_info`, `get_prices`, `why_did_it_move`, `fit_factor_model`,
`compose_thesis`, `check_portfolio_risk`, `size_position`, `plan_question`,
`explain_concept`, `log_prediction`, `calibration_status`, `explain_path`.

There is no `place_order`, and `tests/test_no_execution_anywhere.py` is what
keeps it that way.

---

## `ui/` — 2 files, 273 lines

`render.py` — terminal rendering only. The 12 designed screens in `design/`
are **not implemented**; see `10-STATUS-AND-GAPS.md`.

---

## `stress/` — 3 files, 1,283 lines

`run.py` — ten adversarial sections. `harness.py` — `held`, `finding`, `note`,
`expect_raises`, `expect_no_crash`, `section`, `report`. Exits with the finding
count, so CI fails on a new finding.

---

## Top-level entry points

| File | Purpose |
|---|---|
| `ask.py` | the CLI — 10 subcommands |
| `verify.py` | 14 sections proving the guarantees on mock data |
| `stress/run.py` | the adversarial suite |
| `trace_run.py` | the only end-to-end run using the **real** registry-derived allowlist |
| `predict.py` | the forward prediction log — `due`, `status` |

`trace_run.py` being the only one with the real allowlist is not an accident of
convenience — it is how a missing `llm_complete` grant survived every unit test.
The tests passed hand-written allowlists with invented agent ids.
