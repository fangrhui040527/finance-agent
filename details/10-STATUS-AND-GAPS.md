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
| Investable-capital waterfall — emergency floor, goals, debt hurdle | complete, **and reachable since 2026-08-30** — see the note below |
| 11 market adapters, fee schedules, alias map | complete |
| **Broker account feed** — moomoo, read-only, `ask.py positions` | built, **never run against a live gateway** |
| **Broker fee schedules** — `moomoo_my` on XNAS, per-share and per-order legs, broker-aware cost floor | complete, **and reachable** via `[account] broker` |
| **MYR unit-of-account boundary** | complete |
| Knowledge graph — schema, store, ids, 6 extractors, reproducible build | complete |
| Event taxonomy, base rates, catalyst attachment | complete |
| Retrieval — hybrid, parent-child chunking, router | complete |
| Backtest harness — walk-forward, costs, metrics, point-in-time | complete |
| MCP server — 27 tools, protocol, selftest | complete |
| Teacher — 30 concepts, enforced prerequisite order | complete |
| Reflection — grading, lesson proposal, calibration, scoring | complete |
| Tracing — spans, HTML report, anatomy, prompts | complete |
| CLI — 18 subcommands | complete |
| Fitness function — refuses a partial score | complete |
| CI — 10 steps, offline, keyless | complete |

---

### The waterfall was complete, correct, and unreachable (fixed 2026-08-30)

`engines/sizing/waterfall.py` computed liquid assets → emergency floor →
near-term goals → debt above the hurdle → cash buffer → investable, with three
*locked* steps, property tests, and an explicit refusal to raid the floor.
`A13Sizing.investable_capital()` wrapped it. **Nothing outside the tests called
either.** A grep for `waterfall|investable` across `ask.py`, `mcp_server/` and
`web/` returned one hit: the help text of a `--portfolio` flag.

The row above said "complete", which was true of the engine and wrong about the
system. The consequence was not cosmetic: `size --portfolio <number>` made the
user type the waterfall's **output** as its **input**, so the emergency floor,
the near-term goals and the debt hurdle were bypassed by construction on every
real invocation — silently, while `docs/05` said "before any question about
which stock, there is a question about how much money is allowed to be in
stocks at all."

Now: `[capital]` in `config.toml`, `ask.py capital`, `size --from-plan`, the
`investable_capital` MCP tool and `GET /api/capital` all run the one path. A
typed `--portfolio` still works and now says in as many words which three locks
did not apply to it.

**The lesson for this table:** a row here describes what a USER can reach, not
what exists in `engines/`. Two other rows were audited against that standard at
the same time — the MCP tool count and the CLI subcommand count were simply
stale, which is a different and much smaller kind of wrong.

### The venue schedule is not the account's schedule (added 2026-09-03)

Every `markets/<mic>.py` answers "what does this exchange charge everyone".
`markets/xnas.py` answers it with a ZERO-COMMISSION US retail account — a real
account shape, and the reason its floor is 5 bps and its minimum economic
position USD 1.00. For an account that pays commission it is simply the wrong
schedule, and sizing against it funds US positions that cannot pay for their own
round trip. Silently: a wrong floor is still a number.

`markets/brokers.py` now carries `moomoo_my` on XNAS. It moves the minimum
economic position from USD 1.00 to about **USD 1,511 at a USD 100 share price**,
and because two legs are charged per SHARE that figure is a function of price,
rising to about USD 2,620 at USD 10 a share. Two things fell out of building it:
`ask.py size` and `ask.py allocate` (through `mcp_server/tools.py`) are wired;
**`web/api.py`, `engines/sizing/rebalance.py` and `trace_run.py` are NOT** and
still price on the venue's terms. That is a real half-wiring and is recorded here
rather than described as done.

Two constants in that file — the SEC fee rate and the FINRA trading-activity fee
— are regulator pass-throughs that were NOT verified against a primary source.
They are pinned by test so a drift is visible, and they are the reason a cost
floor from this schedule should not be trusted to the basis point yet.

### What this system does NOT do

It does not pick stocks. There is no screen, no ranked candidate list, no
"here are five ideas". It evaluates names **you** bring, sizes them, splits a
budget across them, and tells you what changed against what you hold.
`Intent.SCREEN` now refuses and says so; before 2026-08-30 it routed to two
per-instrument agents with no instrument and ran them against nothing.
`docs/01`'s architecture diagram still draws a `Factor library → Ranked
candidates` box: it is marked **unbuilt** there and it is not on the roadmap.

## Not built

### Frontend — 12 screens designed, none implemented

`design/` holds 12 `.dc.html` artboards plus the generators that produce them:
Main, WhyItMoved, Prices, Thesis, Portfolio, Sizing, Predictions, Trace, Learn,
WorldMonitor, Agents, Settings.

**BUILT** (2026-08-31): all twelve screens run at `make web` /
`run web` - `web/` is a FastAPI app on 127.0.0.1:8765 whose endpoints call the
same tool functions the MCP server exposes (response text is parity-tested
byte-identical), with vanilla ES-module screens, no build step, and the design
tokens EXPORTED from `design/_css.txt` minus its font import so the app
renders fully offline. Refusals are first-class cards; the portfolio book
lives in the browser's localStorage only. `ui/render.py` remains the terminal
surface; the artboards remain the visual specification the screens follow.

### 32 keyless feed adapters

`docs/world-sources.html` registers **52 sources** across 8 tables, **33 of them
marked "no key"**. Exactly **one is enabled** in `config.toml` — `gdelt`, which
is free, keyless, worldwide and covers 100+ languages.

A generic `knowledge/feeds/rss.RssFeed` (2026-08-31) now covers any RSS/Atom
source as ONE LINE in `knowledge/feeds/registry.py`; the scoping judgement
below still stands for which lines are worth adding.

That leaves 32 sources that need no key and have no adapter. Each would be a
class implementing the same offline-safe contract as
`knowledge/feeds/adapter.py`: a disabled or failing feed must say so rather than
return an empty list a caller could read as a quiet news day.

**Blocked on:** scoping. Which sources are worth the maintenance is a judgement
call, not a coding one, and wiring all 32 because they happen to be free would
produce a queue nobody reads — the same failure the `watchlist` gate exists to
prevent.

### An FX rate source

**BUILT** (2026-08-31): `core/market/fx.BnmFxFeed` fills the store from Bank
Negara Malaysia's public API — keyless, official, MYR-native, with per-100
units honoured (JPY/IDR/KRW) and every rate dated. Sizing still takes an
explicit rate per call; the store is the source an operator populates when
they want dated rates instead of the planning constant.

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
