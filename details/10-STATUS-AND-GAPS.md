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
| **Broker fee schedules** — `moomoo_my` on XNAS **and XKLS**, from the account's real card | complete, **and reachable** via `[account] broker` |
| **MYR unit-of-account boundary** | complete |
| Knowledge graph — schema, store, ids, 6 extractors, reproducible build | complete |
| Event taxonomy, base rates, catalyst attachment | complete |
| Retrieval — hybrid, parent-child chunking, router | complete |
| Backtest harness — walk-forward, costs, metrics, point-in-time | complete |
| MCP server — 40 tools, protocol, selftest | complete |
| **CI budget** — private repository on GitHub Pro: 3,000 included Actions minutes a month, Windows billed double. `ci.yml` runs the full matrix only on pull requests (Windows once, on 3.12), a Linux check on pushes to main, and nothing for pushes that touch only `data/`, `debug/` or the dated journal pages; superseded runs are cancelled. The allowance ran out on 6 September 2026 (286 runs) and every job died in two seconds until the account and repository Actions budgets were set to USD 10; at this month's pattern the trims save about 1,300 minutes | complete |
| Teacher — 30 concepts, enforced prerequisite order | complete |
| **Analyst engines** — `engines/fundamentals/` (22 point-in-time ratios; accruals, Beneish M, Piotroski F, Altman Z, each saying n of N inputs) and `engines/valuation/` (cost of capital from stored inputs and the dated table, bear/base/bull DCF with the terminal share reported and fatal sanity checks, peer comps in three contexts); reachable as `ask.py ratios` / `ask.py valuation` and MCP `ratio_sheet`, `cost_of_capital`, `valuation_range`; a name with no statements says NO STATEMENTS STORED and which source would change that | complete (added 2026-09-06); Bursa names have no free statement source, so for the six they answer honestly with nothing until EODHD's Fundamentals plan |
| **Analyst workup** — `engines/analysis/workup.py`: the twelve steps of docs/04 §2 over the stored record, every step `done`, `partial`, `unavailable` (naming the collector that would fill it), `manual` (business model, the comprehensibility gate, cycle stage) or `not_applicable`; the quality gate calls the red team's failure analogues; peers come from the entity graph (`knowledge/graph/peers.py`, A7 `peers`); suggested breakers are executable queries against the facts store with review dates; `ask.py workup`, `ask.py graph --peers`, MCP `analyst_workup`, `peer_set`; `ask.py thesis --derive-valuation` and `compose_thesis(derive_valuation=true)` hand the thesis the engine's range so the red team's valuation challenge is answered by arithmetic, and the memo renders ANALOGUES | complete (added 2026-09-06); with no statements stored the steps say so and the workup still runs all twelve |
| **Statement collectors** — `sec_xbrl` (SEC XBRL company facts, keyless, filing-date stamped, Q4 derived from FY, restatements kept as rows) and `eodhd` (optional `EODHD_API_KEY`, two names a day on the free plan, US only until the Fundamentals plan) | built, catalogued and enabled; **first runner probe pending** on the Actions cap |
| **Method collections filled** — `knowledge/method/`: 52 own-written notes in the five human stores (`kb_craft` 13 covering all 30 concepts, `kb_method_valuation` 13, `kb_method_technical` 6, `kb_method_risk` 7, `kb_failures` 13 cases with pattern tags) plus the dated cost-of-capital table; loaded by `knowledge/retrieval/method.py` on every surface; the teacher cites its note, the valuation agent its archetype's method, the red team retrieves failure analogues by pattern; `ask.py method`, MCP `method_note` | complete (added 2026-09-06; until then every one of the five stores was registered empty and no agent cited a method) |
| Reflection — grading, lesson proposal, calibration, scoring | complete |
| Tracing — spans, HTML report, anatomy, prompts | complete |
| CLI — 32 subcommands | complete |
| Paper book — `engines/paper/`, `ask.py paper`, `data/paper.db`, marked by `collect.yml`, decided by the Routine, journal in `knowledge/paper/` (docs/22) | complete; the record accrues from 2026-09-08. **An all-cash night now writes a row and a prediction** (added 2026-09-09): until then it wrote nothing, so the book's first night left no evidence that a decision had been taken — see `details/07` §10 |
| **Feedback routine** — `ask.py pack` prepares the night, a scheduled Claude session writes `knowledge/feedback/<date>.md`, indexed as `kb_lessons` | complete; docs/20 |
| Fitness function — refuses a partial score | complete |
| CI — 10 steps, offline, keyless | complete |
| **Collector** — `collect.yml`, four slots a day timed to each market, commits what it saw | complete, **and running** |
| **Source catalogue** — 22 sources (news and structured), slots, keys; `ask.py sources --probe` | complete; 6 Malaysian candidates await their first probe from a runner |
| **Corpus is indexed** — `knowledge/retrieval/index.py`, one router from the registry, `kb_news` filled from the corpus | complete (until 2026-09-04 every surface built `Router({})` and no retrieval ever read an article) |
| **Cleaning layer** — normal form, language allowlist, junk filter, quality score, word-boundary entity linking | complete |
| **Fact book** — `data/facts.db`: observations with `known_at`, events, vintaged series, documents; bridge to the point-in-time FactStore | complete |
| **Digest** — `ask.py digest`, the day's page per name for the person, the agents and the feedback routine | complete |
| **Free-provider backend** — `OpenAICompatibleBackend` over six catalogued free tiers, `SplitBackend` per tier, model and price resolved through the backend into the ledger | complete; docs/21. **Never run against a live provider from this environment** (no route out); the wire format is pinned by tests, the model ids are the catalogue's and rot |

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

### A cache dated by when it was fetched, not by what it held (added 2026-09-09)

`data/price_cache.db` expires at the UTC day boundary because "a daily bar
cannot change until a new session prints". A body fetched DURING a session
breaks that: on 2026-09-08 `XNAS:SPY` came back with an in-progress row whose
open sat above its own high, the bar parser dropped it as corrupt (correctly),
and the cache then served that body for the rest of the day — so the `us_close`
sweep, which would have got the finished bar, never refetched. The US market
proxy ended on 2026-09-04 while the three US names it was measuring had printed
2026-09-08, and no surface said so.

Two fixes, because the fault has two halves. `core/market/cache.is_mid_session`
makes a body that carries a dated row its own parser rejects a cache MISS. And
`knowledge/pack.Move.mis_dated` marks any row whose name printed a session its
proxy did not, naming the day the figures are really about — the same label that
would have caught the Bursa blank row on the 2026-09-07 page. A fallback to an
earlier session is not a fault; a silent one is.

An in-progress row that happens to be self-consistent still parses as a bar and
is still read as that session's close. Nothing sees that yet; `details/07` §11
carries it.

### The death detector was watching the wrong thing (added 2026-09-03)

`silence` counts model calls, and `ask.py sweep` makes none. So the day the
sweep went on a daily timer, the one rule meant to catch "a scheduled job died
quietly" could not see it — and turned on anyway it would have fired every
morning after a run that worked, while staying silent through a sweep dead since
Tuesday if you happened to ask a question yesterday. Wrong in both directions
from one plausible setting.

`sweep_silence` asks the same question of the record the sweep writes. Off by
default, 30 hours in the shipped config, quiet until a source has succeeded
once. One rule covers both a dead scheduler and a week of refused requests,
because a failed sweep is not a successful one and both want the same look.

### `[sources]` described four settings and nothing read any of them (fixed 2026-09-03)

`config.toml` carried `[sources] enabled`, `gdelt_poll_minutes`, `gdelt_languages`
and `gdelt_countries`, each with a paragraph explaining what it controlled.
`core/config.Config` had no field for any of them and `load()` never looked. A
grep for them across the repository returned nothing but the config file itself.

So enabling a source did nothing, narrowing the languages did nothing, and the
comment above `enabled` — "a disabled feed, or one whose key is missing, is
skipped and says so" — described a code path with no caller. The same defect
class as the unreachable waterfall above, and it fails the same way: silently,
producing a system that looks configured.

Now: `Config.sources` is loaded and **validated against the adapter registry**,
the same shape as the broker check — naming a source here does not create one,
and an enabled name with no adapter is refused at load rather than ingesting
nothing every night.

### News was fetched and thrown away (fixed 2026-09-03)

`ask.py news` fetched a source, printed it, and kept nothing; `GdeltExtractor`
could only read a fixture from disk. Nothing wrote an article anywhere. A
scheduled nightly run would therefore have produced a month of scrollback and an
empty disk, and the answer to "what did we see on the 3rd" would have been the
terminal history of whichever machine ran it.

`knowledge/corpus.py` is the store that was missing — append-only, deduplicated
**across runs**, and recording every sweep including the ones that failed.
`ask.py sweep` is the scheduled entry point. See `details/09-RUNBOOK.md`.

The property that took the most care is the last one: a month of refused
requests and a genuinely quiet month leave an identical empty articles table.
Without the `sweeps` table, an outage reads as calm.

### What this system does NOT do

It does not pick stocks. There is no screen, no ranked candidate list, no
"here are five ideas". It evaluates names **you** bring, sizes them, splits a
budget across them, and tells you what changed against what you hold.
`Intent.SCREEN` now refuses and says so; before 2026-08-30 it routed to two
per-instrument agents with no instrument and ran them against nothing.
`docs/01`'s architecture diagram still draws a `Factor library → Ranked
candidates` box: it is marked **unbuilt** there and it is not on the roadmap.

## Recently built — was the largest gap, no longer is

### Frontend — all 12 screens now run

`design/` holds 12 `.dc.html` artboards plus the generators that produce them:
Main, WhyItMoved, Prices, Thesis, Portfolio, Sizing, Predictions, Trace, Learn,
WorldMonitor, Agents, Settings.

These notes originally listed this as the single largest piece of unbuilt scope,
with `ui/render.py` as the only surface. That is no longer true, and the heading
said so for longer than the body did.

**BUILT** (2026-08-31): all twelve screens run at `make web` /
`run web` - `web/` is a FastAPI app on 127.0.0.1:8765 whose endpoints call the
same tool functions the MCP server exposes (response text is parity-tested
byte-identical), with vanilla ES-module screens, no build step, and the design
tokens EXPORTED from `design/_css.txt` minus its font import so the app
renders fully offline. Refusals are first-class cards; the portfolio book
lives in the browser's localStorage only. `ui/render.py` remains the terminal
surface; the artboards remain the visual specification the screens follow.

## Not built

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

### Bursa statements and the cost-of-capital table

The analyst engines run on whatever statement lines the fact book holds. For
the three Nasdaq names `sec_xbrl` fills them without a key once the runner can
start it. For the six Bursa names **no free source carries the statements**:
`eodhd` answers them only on its paid Fundamentals plan, so `ask.py ratios
MYX:1155` says NO STATEMENTS STORED until the operator either subscribes or
supplies the lines another way. Registering the free EODHD plan and adding
`EODHD_API_KEY` covers two US names a day, nothing on Bursa.

`knowledge/method/data/cost_of_capital.yaml` carries the two Damodaran premiums
that were readable from here (mature-market and US); the Malaysia, Taiwan,
China and Japan country rows, every industry beta and every synthetic-rating
spread are `null` because the source pages were not reachable from the
sandbox. Each null shows up on the surface as "not transcribed", never as a
zero. Transcribing them is a person with the July 2026 pages open, one row at
a time, keeping the row label. The Malaysian 10-year yield id in
`dbnomics.SERIES` answered on the 2026-09-06 probe; Taiwan is not in the IMF
tables, so no free id exists and Taiwanese names use the US ten-year with the
approximation stated.

### Bursa coverage: the half of the book the corpus barely holds

The book is six Bursa names and three Nasdaq names. The corpus is 1,968
articles linked to the three US names and **62 to all six Malaysian ones**;
`MYX:5183` (Petronas Chemicals) held **zero, all time**, and 857 of 2,671
articles (32%) link to no instrument at all.

Three causes, at different stages of fix:

1. **The query asked for one name.** Both per-name sources took the first alias
   in entities.yaml, so PCHEM and TNB — the forms the Malaysian press prints —
   were never searched, though the linker has always known them. Google News:
   fixed in [#60](https://github.com/fangrhui040527/finance-agent/pull/60).
   GDELT: fixed here (`gdelt.search_query`). `TNB` and `IHH` remain unaskable on
   GDELT alone, whose API refuses a phrase under five characters.
2. **Four of the five Malaysian outlets are disabled.** thestar, edge and nst
   answered 404 on 2026-09-04 and bernama dated nothing, so `fmt_business`
   carries the whole Malaysian press by itself. One 404 on one guessed path is
   not proof a publisher has no feed, and nothing here can reach a Malaysian
   host to say otherwise. `.github/workflows/bursa-feeds-probe.yml` asks the
   publishers directly — autodiscovery tags off their own pages, conventional
   paths beside them, and a verdict per URL on whether its items carry DATES.
   **This is the largest single lever on Bursa coverage and it is not yet
   pulled: run the probe and enable whatever answers.**
3. **GDELT reaches three names a run.** By design (`names_per_run=3`), because
   84 name-failures over 30 sweeps were HTTP 429 and a refusal costs three
   retries and up to a 90s read. Not a defect; it does mean each Bursa name
   comes round every second or third run.

### The macro series that have stopped

All fifteen DBnomics ids are **ended upstream**, confirmed by the 2026-09-06
runner probe: IMF/PCPS and BIS/WS_CBPOL stop at 2025-06, BIS/WS_EER and IMF/IFS
at 2025-05, IMF/CPI at 2025-07, with every sibling code inside each dataset
stopping at the same period. The codes are right; the datasets stopped being
ingested, so there is nothing to re-point at.

They are marked rather than deleted (`knowledge/sources/freshness.ENDED`). Every
macro row reads `466d ENDED 2025-06`, `ask.py macro` prints which upstream
stopped and when under the table, the monitor no longer opens a nightly alert
about them, and a WACC on a Malaysian name says out loud that its risk-free rate
comes from a stopped series. The collector keeps fetching them so the
`series_resumed` rule can contradict the verdict if any of them restarts.

**The open decision is where to buy macro data.** Commodity prices, policy rates
and effective exchange rates are inputs a book of a petrochemical, an aluminium
smelter and five Malaysian names actually moves on, and right now the newest
reading of any of them is from mid-2025. FRED covers the US side and is live;
nothing free that has been found covers the rest.

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
