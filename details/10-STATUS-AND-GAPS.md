# 10 — Status and gaps

What is built, what is not, and what each gap is actually blocked on.

The distinction that matters: some gaps are **work**, some are **blocked on a
key**, some are **blocked on elapsed time**, and some are **blocked on human
judgement that no code can supply**. Conflating them makes the fourth kind
invisible.

---

## Built and verified

| Area | State |
|---|---|
| Contracts — `Money`, `Answer`, `Claim`, `Citation`, provenance markers | complete, tested |
| Guardrail chain — 5 rails, `PolicyEngine.enforce` raises | complete |
| Provenance ledger — append-only, SQLite triggers, WAL, latency | complete |
| Tier routing — 26 task classes → 5 tiers, cost accounting | complete |
| 16 agents, registry-derived allowlist, eval ratchet | complete |
| Attribution engine — decomposition, unexplained share, non-finite refusal | complete |
| Risk engine — HHI, effective bets, correlation clusters, 4 concentration measures | complete |
| Sizing — 5 caps, binding cap, lot rounding, cost floor, `NoPosition` | complete |
| Investable-capital waterfall — emergency floor, goals, debt hurdle | complete |
| 11 market adapters, fee schedules, alias map | complete |
| **MYR unit-of-account boundary** | complete |
| Knowledge graph — schema, store, ids, 6 extractors, reproducible build | complete |
| Event taxonomy, base rates, catalyst attachment | complete |
| Retrieval — hybrid, parent-child chunking, router | complete |
| Backtest harness — walk-forward, costs, metrics, point-in-time | complete |
| MCP server — 12 tools, protocol, selftest | complete |
| Teacher — 30 concepts, enforced prerequisite order | complete |
| Reflection — grading, lesson proposal, calibration, scoring | complete |
| Tracing — spans, HTML report, anatomy, prompts | complete |
| CLI — 10 subcommands | complete |
| Fitness function — refuses a partial score | complete |
| CI — 10 steps, offline, keyless | complete |

---

## Not built

### Frontend — 12 screens designed, none implemented

`design/` holds 12 `.dc.html` artboards plus the generators that produce them:
Main, WhyItMoved, Prices, Thesis, Portfolio, Sizing, Predictions, Trace, Learn,
WorldMonitor, Agents, Settings.

`ui/render.py` is **273 lines of terminal output**. No screen exists as running
code.

The design README says so plainly: *"These are the specification, not the
product."* Shared CSS tokens are lifted verbatim from `docs/user-guide.html` so
the design cannot drift from the documentation's palette.

**Blocked on:** nothing but work. This is the largest single piece of unbuilt
scope in the repository.

### 32 keyless feed adapters

`docs/world-sources.html` registers **52 sources** across 8 tables, **33 of them
marked "no key"**. Exactly **one is enabled** in `config.toml` — `gdelt`, which
is free, keyless, worldwide and covers 100+ languages.

That leaves 32 sources that need no key and have no adapter. Each would be a
class implementing the same offline-safe contract as
`knowledge/feeds/adapter.py`: a disabled or failing feed must say so rather than
return an empty list a caller could read as a quiet news day.

**Blocked on:** scoping. Which sources are worth the maintenance is a judgement
call, not a coding one, and wiring all 32 because they happen to be free would
produce a queue nobody reads — the same failure the `watchlist` gate exists to
prevent.

### An FX rate source

`core/market/prices.FxStore` exists and works — explicit dated rates,
`rate_asof()` with bisect lookup and an inverse-pair fallback. **Nothing
populates it.** The rate is supplied per call today.

**Blocked on:** a source decision. `config.toml` carries
`fx_myr_per_usd = 4.15` for cost estimates only and says so.

### A semantic graph tier

The graph is `tier="deterministic"` only. The store already supports a second
tier and re-extraction replaces only its own tier, so a model tier can arrive
without wiping the deterministic one.

**Blocked on:** an API key, and on the judgement that model-proposed edges are
worth reviewing. Model edges would be `INFERRED` and therefore non-citable by
construction.

---

## Blocked on an API key

These are the user's to test. Everything below is wired and unexercised.

| Item | What happens today |
|---|---|
| A real model backend | `EchoBackend` — deterministic stub. `ask.py backend` says so. |
| Narrative output from a4, a10, a11, a15 | placeholder text |
| Live cost accounting against real token counts | the ledger records; the numbers are from the stub |
| Error taxonomy adoption (`docs/13`) | `backends.py` has a flat `RETRY_STATUS`; the hermes-agent taxonomy separating retryable from permanent auth, and context-overflow from a generic 400, is documented and not adopted |

Every number, cap and refusal is computed identically either side of that key.

---

## Blocked on elapsed time

No amount of code shortens these.

| Item | Requires |
|---|---|
| **P16 paper-trade gate** | 3–6 months of forward-tested, logged, calibration-checked results before a signal moves money (docs/05 §9) |
| **P19 classifier** | enough graded outcomes to train on |
| `forecast_calibration` term in the fitness function | ≥30 graded predictions (`min_graded_for_calibration`) |
| Kelly cap | ≥50 resolved decisions. Below that the win rate and payoff ratio are indistinguishable from noise, and a noisy Kelly is worse than none because it is confidently wrong in both directions. |

---

## Blocked on human work

The category most easily mistaken for a coding task.

### Verifying the curated graph data

`knowledge/graph/data/supply_chain.yaml` rows and the Bursa stock codes in
`entities.yaml` were **written by hand and not checked against primary
sources**. Each row carries a `source_doc_id`; nothing verifies the document says
what the row claims.

This is the one place where the system's core promise — a claim carries where it
came from — is currently backed by an unverified assertion. It is 14 nodes and
18 edges, so the work is bounded, and it needs a person with the filings open.

### `attribution_accuracy`

Needs roughly **200 labelled moves** — a human deciding, after the fact, what
actually caused each one.

### `refusal_precision`

Needs a labelled set of questions that *should* have been refused, and ones that
should not.

### `holdings` and `watchlist`

Both empty in `config.toml`. Until they are filled, the escalation gate never
passes anything and a background sweep surfaces nothing.

---

## The two standing stress notes

Known, bounded, not findings:

1. **Config accepts a relative traversing database path.**
   `'../../../../tmp/pwned.db'` is stored as given. Low risk — the operator owns
   the file — but the path is never normalised or confined to the project.
2. **Injected text is echoed in thesis output.** It is quoted *as evidence*,
   which is correct — but the model sees it.

Neither is fixed, both are named, and `stress/run.py` re-reports them every run
so they cannot quietly become news.

---

## Honest summary

The **analysis engine is complete and verified**. Every number a user would act
on — the decomposition, the caps, the concentration measures, the cost floors,
the graph paths — is computed, bounded, tested and traceable, offline, with no
key.

What is missing falls into three groups:

1. **A face.** 12 screens designed, none built. The largest piece of work.
2. **Inputs.** One feed of fifty-two; no FX source; an empty book.
3. **Time and judgement.** The paper-trade gate, the calibration set, the
   labelled moves, and a person checking 18 supply-chain rows against filings.

None of the second or third group is a coding problem, and the system is
deliberately built so that not having them produces a refusal rather than a
confident guess.
