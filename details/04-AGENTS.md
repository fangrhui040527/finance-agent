# 04 — The sixteen agents

Generated from `agents/registry.yaml` via `core/registry/loader.load()`. The
registry is the single source of truth: adding an agent is an entry here plus an
adapter class, never a change to the orchestrator.

---

## The full table

| Agent | Layer | Tier hint | Knowledge | Tools |
|---|---|---|---|---|
| `a0_supervisor` | orchestration | cheap | — | `plan`, `budget`, `route`, `refuse` |
| `a1_fundamentals` | evidence | balanced | `kb_filings` | `retrieve`, `get_statement`, `dupont`, `accrual_ratio`, `restatement_diff` |
| `a2_valuation` | evidence | balanced | `kb_method_valuation` | `retrieve`, `multiple_vs_history`, `reverse_dcf`, `peer_multiples` |
| `a3_price_technical` | evidence | balanced | `kb_method_technical`, `price_bars` | `retrieve`, `ohlcv`, `atr`, `drawdown`, `base_rate` |
| `a4_news_narrative` | evidence | cheap | `kb_news` | `retrieve`, `search_news`, `extract_features`, `source_reliability`, **`llm_complete`** |
| `a5_catalyst_events` | evidence | balanced | `kb_events`, `event_base_rates` | `retrieve`, `events_in_window`, `base_rate`, `blackout_check` |
| `a6_macro_regime` | evidence | balanced | `kb_macro` | `series`, `regime_label`, `country_stress` |
| `a7_sector_technology` | evidence | balanced | `kb_sector`, `kb_supply_chain` | `retrieve`, `traverse`, `peers`, `sector_primer` |
| `a8_ownership_flow` | evidence | balanced | `kb_ownership` | `insider_activity`, `ownership_change`, `short_interest_trend` |
| `a9_attribution` | synthesis | reason | `factor_returns`, `price_bars`, `event_base_rates` | `decompose`, `abnormal_return`, `candidate_causes`, `long_horizon_decompose` |
| `a10_thesis` | synthesis | reason | — | `compose`, `check_coverage`, **`llm_complete`** |
| `a11_red_team` | synthesis | reason | `kb_failures`, `kb_news`, `kb_filings` | `retrieve`, `find_disconfirming`, `check_crowding`, **`llm_complete`** |
| `a12_portfolio_risk` | portfolio | balanced | `kb_method_risk`, `holdings` | `concentration_check`, `heat`, `effective_bets`, `drawdown_state`, `stress` |
| `a13_sizing` | portfolio | balanced | `ledger`, `holdings`, `goals` | `investable_capital`, `risk_budget_cap`, `kelly_cap`, `concentration_cap`, `liquidity_cap`, `cost_floor`, `vol_target_scalar`, `lot_round` |
| `a14_teacher` | learning | balanced | `kb_craft` | `retrieve`, `explain`, `next_concept`, `quiz` |
| `a15_reflection` | learning | reason | `kb_lessons`, `outcomes` | `grade_queue`, `propose_lesson`, `calibrate`, `curate`, **`llm_complete`** |

## `llm_complete` is granted to exactly four agents

**a4** triages prose, **a10** writes the thesis, **a11** argues against it,
**a15** judges lessons. Those are the four whose documented job is a language
task. The other twelve compute.

This matters more than it looks. An allowlist that names every capability for
every agent has stopped being a control — it is a list. Twelve of sixteen agents
here cannot call a model at all, which means twelve of sixteen cannot hallucinate
a number into a finding.

> **The bug this grant caused.** `llm_complete` went missing from the registry
> entirely. No agent held it, so the registry-derived allowlist denied
> `InferenceClient.complete()` for *every* registered agent. `verify.py` and the
> unit tests never caught it because they pass hand-written allowlists with
> invented agent ids like `{"a4": {"llm_complete"}}` — the seam was exercised
> and the grant was not. Only `trace_run.py`, which uses the real registry,
> found it.

## The twenty knowledge stores

`created_by: human` means read-only to every agent, forever.

| Store | Created by | Agent-managed |
|---|---|---|
| `kb_lessons` | agent | yes |
| `event_base_rates` | agent | yes |
| `outcomes` | agent | yes |
| `kb_craft`, `kb_method_valuation`, `kb_method_technical`, `kb_method_risk`, `kb_failures`, `kb_filings`, `kb_news`, `kb_events`, `kb_macro`, `kb_sector`, `kb_supply_chain`, `kb_ownership`, `factor_returns`, `price_bars`, `holdings`, `ledger`, `goals` | human | **no** |

Three of twenty are agent-managed. `kb_lessons` is what the reflection loop
writes; `event_base_rates` is what accumulates from graded outcomes; `outcomes`
is the graded forward record itself. **Seventeen of twenty** — everything the
system reasons *from* — are human-authored and the system cannot edit them.

This is why deterministic graph extraction sidesteps the write ban: it is a
**build step**, not an agent action. `kb_sector` and `kb_supply_chain` are
`created_by: human, managed: false`; the extractors read the YAML and write a
database, and no agent writes either.

## Tier routing

Callers declare a `TaskClass`; the tier is derived.

| Tier | Model | Input / output USD per Mtok | Used for |
|---|---|---|---|
| `REASON` | `claude-opus-5` | 5.00 / 25.00 | thesis synthesis, red team, hard attribution, deep reflection |
| `BALANCED` | `claude-sonnet-5` | 2.00 / 10.00 | fundamentals, valuation, catalyst, macro, sector, flow, risk, ad-hoc, daily brief, breaker sweep |
| `CHEAP` | `claude-haiku-4-5` | 1.00 / 5.00 | intent routing, news triage, entity tagging |
| `EMBED` | `text-embedding-3-small` | 0.05 / 0 | retrieval |
| `LOCAL` | `finbert-local` | 0 / 0 | on-device classification |

Cached input bills at roughly 10% of base input, handled in `cost_usd()`.

Twenty-two task classes route into five tiers. `route()` raises
`TierRoutingError` for an unregistered class rather than falling back to a
default — a silent fallback to `BALANCED` is how a cheap task starts costing
four times what the budget assumed.

## The agent seam

Every agent inherits `agents/base.Agent` and every tool call passes
`_guard_tool(name)`, which checks the name against the registry-derived
allowlist for *that agent id*. The check is not "is this a known tool" but "is
this tool granted to this agent".

```python
a0.retrieve("kb_filings", "x")  # raises — the supervisor has no retrieve
```

`verify.py` §9 asserts exactly that, along with the supervisor's refusals:

- refuses to place an order
- refuses a point price forecast
- routes "why did maybank fall today" to the documented playbook, with a cost
  estimate

## The drawdown ladder

`agents/portfolio/agents.py::DRAWDOWN_TIERS` — risk appetite governed
mechanically, because the moment it is discretionary it gets overridden at
exactly the wrong time.

| Drawdown | Risk multiplier | Rule |
|---|---|---|
| 10% | 0.75 | review, do not react |
| 15% | 0.50 | no new positions outside existing themes |
| 20% | 0.25 | new positions require a written post-mortem first |
| 25% | **0.00** | no new risk. Close the laptop, write the review, wait a full month. |

## The teaching order

`a14_teacher` holds 30 concepts in a prerequisite order the code enforces.
Asking for `kelly` before mastering `share`, `compounding`, `volatility`,
`trend_vs_noise`, `factor_decomposition`, `base_rate`, `expected_value` and
`position_sizing` returns a `prerequisite` finding and exits non-zero.

A curriculum that lets you skip to the interesting part is a reading list.
