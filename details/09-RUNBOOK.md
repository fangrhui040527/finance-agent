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
make test         # 1,848 tests, ~70s
make verify       # 14 sections, PASS/FAIL
make stress       # 158 held, exits with the finding count
make trace        # end-to-end, writes debug/<run-id>/
make mcp-check    # MCP handshake against itself
make doctor       # preflight: what this installation can actually do
make web          # the twelve screens on http://127.0.0.1:8765
make lint         # ruff check + format check     make typecheck  # pyright
make cov          # tests with the coverage floor (92%)
```

## Added 2026-08-31

```bash
python ask.py news gdelt --hours 24     # one source through the feed registry
python ask.py size XNAS:NVDA ... --fetch-fx   # BNM rate, dated, instead of --fx
python predict.py reflect H-20260831-0042      # grade a cohort by its hypothesis
python predict.py reflect H-... --second-opinion   # + advisory model read (spends)
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

> Until 2026-08-30 filling these in changed nothing: neither live surface copied
> them into `AgentContext`, so the gate was closed on every article no matter
> what the file said. Both surfaces read them now.

---

### `doctor` — what this installation can actually do

```bash
python ask.py doctor              # every check, including the network ones
python ask.py doctor --offline    # skip anything that needs a network
```

Reports each dependency, key and feed as ready or not, and exits non-zero only
when something **critical** is missing. A fresh checkout reports several
non-critical checks as unavailable and still works: "nothing configured yet" is
a valid state, not a broken one.

### `sweep` — fetch every enabled source and KEEP what arrives

```bash
python ask.py sweep                       # every source in [sources] enabled, every name
python ask.py sweep --slot bursa_close    # the sources that run after Bursa closes, Bursa names
python ask.py sweep --slot us_close       # ... after the US close, Nasdaq names
python ask.py sweep --slot us_preopen     # macro prints, the earnings calendar, rating changes
python ask.py sweep --slot weekly         # statements, estimates, transcripts, CPI
python ask.py sweep --source gdelt        # one source, repeatable, whatever the slot
python ask.py sweep --no-graph            # store only; leave the graph alone
```

Two kinds of source, one run. A **news** source (GDELT, Google News per
company, a Yahoo ticker feed, a Malaysian RSS) produces articles into
`data/corpus.db`, cleaned (`knowledge/news/clean.py`), language-filtered,
junk-dropped, entity-linked on word boundaries, feature-scored and quality-scored,
with the escalation gate evaluated. A **structured** source (Finnhub, FMP, Alpha
Vantage, FRED, EDGAR, BNM's OPR, DOSM's CPI) produces observations, events,
series and documents into `data/facts.db` — append-only and vintaged, so a
restated figure is a new row and a backtest sees what was knowable. The register
of every source, its slot and the key it needs is `knowledge/sources/catalog.py`;
`ask.py sources` prints it. A keyed source whose key is absent is **skipped**,
with the variable named, and the job stays green.

### `sources` — the catalogue, and one live probe of each

```bash
python ask.py sources                     # every source: kind, slots, key, enabled
python ask.py sources --probe             # fetch each once, store nothing, say what came back
python ask.py sources --probe --source thestar_business --source edge_malaysia
```

The probe exists because the development environment has no route to any data
host: the first real answer from a source comes from a GitHub Actions runner
(`.github/workflows/sources-probe.yml`). A Malaysian RSS candidate is enabled in
`config.toml` only after its probe row shows dated items; an index page answers
with the feed URLs it advertises.

### `facts` and `macro` — reading the fact book

```bash
python ask.py facts XNAS:AAPL              # latest figure per concept, events, scheduled, documents
python ask.py facts MYX:1155 --days 90
python ask.py macro                        # every recorded series, latest point and 20-obs change
python ask.py macro DGS10 --points 10      # one series' recent points, with vintages
```

Both print from `data/facts.db` and nothing else. An empty answer names the
sources that would fill it - a blank is an empty store, not a quiet company.
The same formatters back the `fact_snapshot` and `macro_context` MCP tools.

### `pack` — the deterministic half of the nightly feedback

```bash
FINPLANET_OFFLINE=1 python ask.py pack --date 2026-09-04 --write   # knowledge/feedback/<date>.pack.md
```

Moves for every name against its market proxy from the cached bars, the
decomposition (verdict and unexplained share; beta estimated on the 120
sessions before the day, sector beta fixed at zero and said so), the day's
digest, the fact book per name, the macro series, and the last three feedback
pages. The nightly routine (docs/20) reasons over this file and copies its
numbers; it never re-derives them. A name whose bars are absent is a `NO DATA`
row, never a typed leg.

### `paper` — the USD 1,000 paper book

One subcommand, six actions. `docs/22-PAPER-BOOK.md` is the full account; this
is the operator's card.

```
ask.py paper init   [--start YYYY-MM-DD]            open both books (once; append-only, no reset)
ask.py paper status [--date D] [--json]             equity, positions, caps, the FUNDABLE table, cost drag
ask.py paper decide --weights "MYX:5183=0.20,XNAS:NVDA=0.22" --thesis "..." [--horizon 21] [--confidence 0.55] [--dry-run] [--supersede]
ask.py paper mark   [--slot bursa_close|us_close|manual|all]   apply pending targets at the next cached open, then mark
ask.py paper pack   [--write] [--out knowledge/paper]          the deterministic half of the nightly paper journal
ask.py paper grade  [--dry-run]                     grade paper predictions whose date has come, against the control
```

Exit codes: `0` done; `2` refused or nothing to do (a refusal lists every cap
breached and writes nothing); `3` a leg could not be priced from the cache -
the rest was still marked, and the problem is printed.

Three things to know before the first decision. **The fundable table** on the
status page is the universe: a name whose one board lot or single share costs
more than the per-name cap is not fundable at this equity, and a weight below
one lot is refused. **A decision is the whole target book**: a held name left
out of `--weights` is an exit. **Nothing is priced at decision time**: targets
apply at each market's first cached bar after the decision day, at the open
plus slippage, with the real moomoo fee card and the recorded FX rate; the
`us_close` mark is what the nightly routine reads. Run everything with
`FINPLANET_OFFLINE=1` on a machine with no route to a price host; the cache
`collect.yml` commits is the book's only source.

The book lives in `data/paper.db`, append-only like every store here; `ask.py
doctor` fails if its triggers are missing. To replay on a scratch path without
touching the real book: `ask.py paper init --db /some/where/p.db --start
2026-03-02`, then `mark`, `decide` and `mark` with `--date`.

### `digest` — the day's page, per name

```bash
python ask.py digest                      # today, to stdout
python ask.py digest --date 2026-09-04 --write   # also data/digests/<date>.md and .json
```

One day of the corpus and the fact book as a page: per name the top stories by
quality (syndicated copies folded), the tone (polarity, intensity, uncertainty),
what escalated, recent and upcoming events, a snapshot of the latest figures;
then the macro series and their changes; then the day's collection rows. Derived
and regenerated on every run — the stores are the record, this is the reading of
it — and what the nightly feedback routine reasons over.

`news` prints one source and forgets it, which is right for a person checking a
feed by hand. `sweep` is the scheduled sibling and the one that makes a month of
watching add up to something: it resumes each source from its last **successful**
sweep, writes what it finds to `data/corpus.db`, links the articles into
`data/graph.db`, and records the attempt either way.

Three things it does that the interactive path cannot:

- **Deduplicates across runs.** `FeedAdapter._seen` is a set on the instance and
  dies with the process. A wire story still on the wire tomorrow would otherwise
  be stored again every day it stays there, and a month of watching would report
  a volume of news that is mostly one story counted thirty times.
- **Records a failure as a failure.** A month of refused requests and a genuinely
  quiet month leave the same empty articles table. The `sweeps` table is the
  difference, and it is append-only: a failed month stays a failed month.
- **Does not move the watermark on a failure.** Resuming from a failed sweep
  would skip the window that was never read — which is exactly the window the
  outage happened in.
- **Asks for one company at a time.** A single query naming every company,
  sorted newest-first, is won by whichever name publishes most. Measured on the
  2026-09-03 09:11 sweep: nine names and 250 records gave 34 attributed
  articles, every one US tech, Apple alone taking 20, and all six Bursa names
  nothing. The budget is split per name instead — nine requests where there was
  one — and the starting name rotates daily, because the deadline below cuts the
  tail and a fixed order starves the same names every time.

A run that reads some names and not others is recorded `ok`, with the names that
failed in the sweep's `detail`. Marking the whole sweep failed would move no
watermark and re-read every name tomorrow; but silence would be worse, because a
name failing quietly every day must not read as a name nobody is writing about.
Only when **no** name can be read is the source itself failed.

`SWEEP_DEADLINE_SECONDS` (10 minutes) stops the sweep starting new requests and
records which names it never reached. A name whose request times out costs up to
4.5 minutes on its own, and nine of those would run past the collect job's cap —
which kills the job before **anything** is committed, losing the names that did
succeed.

Exit codes, so a scheduler can act without parsing text:

| code | meaning |
|---|---|
| 0 | every enabled source read (a source without its key is skipped, not failed) |
| 2 | the sweep could not run — bad config, unknown slot, no source runs in the slot, or a source with no adapter |
| 3 | at least one source failed or was degraded (more than half its names unreachable); the failure is in the `sweeps` and `pulls` tables |

#### The sources

| name | what it is | slot | state |
|---|---|---|---|
| `gdelt` | global news index, one request per company | bursa_close, us_close | **enabled** |
| `google_news` | Google News search RSS per company, MY edition for Bursa names, US for Nasdaq | bursa_close, us_close | **enabled** |
| `yahoo_rss` | Yahoo Finance ticker headlines with a summary | bursa_close, us_close | **enabled** |
| `edgar` | SEC filings per US name (8-K, 10-Q, 10-K, 4) | us_close | **enabled** |
| `bnm_opr` | Bank Negara's Overnight Policy Rate | bursa_close | **enabled** |
| `dosm_cpi` | Malaysian headline CPI | weekly | **enabled** |
| `finnhub` | US company news, insiders, calendar, surprises, recommendations, metrics | us_close | **enabled**, needs `FINNHUB_API_KEY` |
| `fmp` | statements, estimates, targets, rating changes, transcripts | weekly, us_preopen | **enabled**, needs `FMP_API_KEY` |
| `alphavantage_news` | articles with per-ticker sentiment, one call per US name a day | us_close | **enabled**, needs `ALPHAVANTAGE_API_KEY` |
| `fred` | Fed funds, yields, curve, CPI, unemployment, VIX, dollar, MYR/USD | us_preopen | **enabled**, needs `FRED_API_KEY` |

| `thestar_business`, `edge_malaysia`, `bernama_business`, `fmt_business`, `nst_business` | Malaysian business RSS | bursa_close | registered, **not enabled** until the probe shows dated items |
| `bursa_announcements` | Bursa company announcements | bursa_close | registered, **not enabled** until the probe shows the endpoint answers |
| `bnm_press` | Bank Negara press releases | bursa_close | registered, **not enabled**: no live feed exists |
| `jin10_flash` | 金十数据 flash news, Chinese; one request per slot (the site's own public endpoint; no free API exists, terms are a gray zone, personal research only) | us_preopen, bursa_close, us_close | **enabled** for the first probe |
| `jin10_calendar` | 金十 economic calendar: scheduled releases and prints as `MACRO:<country>` events | us_preopen, us_close | registered, **not enabled**: on the 2026-09-05 probes `cdn-rili.jin10.com` was gone from DNS and `rili.jin10.com` answered 404 on every documented path; `fred` carries the US release calendar meanwhile. To re-enable: copy the economics JSON request the page at rili.jin10.com makes (browser network tab) into `CALENDAR_URLS`, re-probe, add the name back to `enabled` |
| `dbnomics` | the series behind MacroMicro's charts, keyless: IMF commodity prices (palm oil, aluminium, Brent, LNG), BIS policy rates and NEERs, IMF CPI for MY and CN; each id confirmed by the probe | us_preopen, weekly | **enabled**; answers, but its data is old — see below |
| `twse_openapi` | TWSE OpenAPI (official, keyless) for the `[sources] read_only` Taiwan names: P/E, P/B, yield, monthly revenue, close, volume | bursa_close, weekly | **enabled** for the first probe |
| `finmind` | FinMind for the same names: 24 months of revenue, 8 quarters of statements, foreign net buying; `FINMIND_TOKEN` optional | weekly | **enabled** for the first probe |
| `sec_xbrl` | SEC XBRL company facts for the US names: every reported line with its filing date, Q4 derived from FY, restatements kept; feeds the ratio and quality engines | weekly | **enabled**; first probe pending (Actions cap) |
| `eodhd` | EODHD fundamentals, `EODHD_API_KEY` optional: quarterly and annual statements; 2 names a day on the free plan, US only until the Fundamentals plan (which covers KLSE) | bursa_close, us_close | **enabled**; skipped until the key is set |

Keys reach a workflow as repository secrets of exactly these names (Settings →
Secrets and variables → Actions → Repository secrets). One secret named
`ALL_SECRET` holding every key, in any layout, is also accepted: the first step
of `collect.yml`, `sources-probe.yml` and `free-backend-probe.yml` runs
`.github/scripts/keys_from_blob.py`, which recognises each key by its label or
its shape, masks it, and exports it for the steps that follow. A secret set
under its own name always wins over the blob's copy. `secrets-check.yml` lists
the names a job can see, never the values, when a probe says a key is not set.

**Four sites, their free routes (2026-09-05).** MacroMicro's API is paid only (from
USD 5,000 a year; its free tier has no data access), so `dbnomics` carries the series its
charts are drawn from. Goodinfo bans crawlers and 优分析 keeps its figures behind a paid
membership; `twse_openapi` and `finmind` publish the same numbers, and the names they read
come from `[sources] read_only` - read and cited, never traded. 金十数据 has no free API;
`jin10_flash` reads the public endpoint its own pages use, once per slot, with this
repository's User-Agent, for personal research; `jin10_calendar` is registered but off until
the calendar document's new path is known (the table row says how to find it).

**The domestic-coverage gap is open.** GDELT has returned nothing for any Bursa
name on every run so far, and `bnm_press` was enabled on 2026-09-03 to close
that — then disabled the same day, because its URL could not be established:

| tried | answer |
|---|---|
| `/rss/press-release` | HTTP 404 — the path predates BNM's site redesign |
| `/rss` | an HTML landing page, no autodiscovery tags |
| `/press-release-2020?…getRSS` | **works** — valid RSS, but the 2020 archive, so 0 items in any recent window |
| `/press-release-2026?…getRSS` | HTML, not a feed |

So the endpoint shape is right — `p_p_resource_id=getRSS` on Liferay's asset
publisher — and the 2026 instance id was transcribed from that page's own markup
rather than guessed. It still returns HTML, and the page's `subscribe-action`
div is empty where a feed-enabled page carries the subscribe control: **RSS is
switched off for the current year's portlet instance.** The 2020 instance has it
on, which is why only the archive answers.

Remaining candidates for anyone trying again: the non-year-scoped `/pr` and
`/press-releases-main`, each with its own instance id readable from its HTML.
Weigh it against the return first — see below.

It is left disabled rather than left failing on purpose. A source that fails
every night marks the collect job red every night, and a red job that always
means the same dead URL trains you to stop reading it — which is the one thing
the `sweeps` table exists to prevent.

**And be clear what it would buy.** BNM is a central bank: rate decisions,
banking statistics, policy documents. Even working, it will not name Maybank or
Tenaga. It was never the fix for the missing Bursa **company** coverage — that
needs a Malaysian business news source, which this system does not have and
which is the gap actually worth closing.

Two sources means two watermarks and two `sweeps` rows. A source that fails does
not take the other's articles with it — which is the whole reason a second one
is worth enabling. Note the exit code is still 3 if **either** fails, so a flaky
GDELT will mark the job red on a day `bnm_press` worked; the per-source rows are
where the real story is.

#### What GDELT refuses, measured

Four ways the DOC API says no, all found by scheduled runs on 2026-09-03 and all
recorded in the `sweeps` table rather than guessed at. It reports errors as
**plain text, not JSON**, and one bad phrase fails the whole request:

| What comes back | Why | Where it is handled |
|---|---|---|
| `{}` with no `articles` key | the query matched nothing — the adapter's own fallback is `domainis:reuters.com`, which returns nothing at all | `watchlist_query` supplies the book's names |
| `urlopen error timed out` | it answers in ~38s and the socket timeout was 30 | `GdeltFeed.TIMEOUT = 90` |
| `The specified phrase is too short.` | a quoted phrase below its minimum — `"IHH"` and `"TNB"` are three characters | `MIN_PHRASE_CHARS`, next alias used |
| `Timespan is too short.` | resuming from a watermark 25 minutes old | `MIN_TIMESPAN` is two hours, wider than the boundary on purpose |

The last one never fires on the daily schedule, which resumes from ~24h. It
fires on a manual run after the daily one, or a retry after a failure — both of
which land inside the hour, and both of which are when you least want a failure.

Sustained testing gets throttled: roughly thirty requests in eighty minutes took
it from answering in ~38s, to answering with errors, to refusing the TLS
handshake. Once a day is a different pattern entirely.

The edges it adds are `INFERRED`: a substring match establishes that an article
*mentions* a company, never that the event *affects* it, so they are traversable
and `Edge.citable` refuses them. Nothing pruned — this build carries one
extractor, and pruning would close every curated and sector edge the sweep did
not happen to mention.

### The daily collector — `.github/workflows/collect.yml`

`sweep` was built to be scheduled and for a while nothing scheduled it. This
workflow does, daily at 10:00 UTC (18:00 MYT, about an hour after Bursa closes),
and can be fired by hand from the Actions tab.

It runs on GitHub's runners rather than a desktop for one reason: **news
expires.** The GDELT window is recent-only, so a day nothing collects is a day
that cannot be fetched later, and a laptop collects nothing while it is closed.

Daily, not weekdays. `sweep_silence_hours` is 30 — a daily schedule plus six
hours of slack — so a weekday-only schedule would trip that alert every Saturday
by design rather than by fault.

Three things about it that are deliberate:

- **It reads the exit codes instead of flattening them.** Exit 3 still commits,
  because a failed sweep that leaves no trace is indistinguishable from a quiet
  day — the whole reason the `sweeps` table exists — and then annotates the run
  with a warning naming the degraded source. It does not fail the job: GDELT
  times out on one name most days, and a job that is red most days is a job
  nobody reads, which is the failure the `bnm_press` note above describes.
  Exit 2 (the sweep could not run at all) and a failed FX pull still fail it,
  because then nothing was collected. `ask.py watch` reads the same `sweeps`
  and `pulls` tables, so a degraded source is still reported where the
  monitor looks.
- **It is the only workflow here with `contents: write`.** `ci.yml` is
  `contents: read`. The data commit carries `[skip ci]`, because ten test jobs to
  validate a row of news is waste.
- **`timeout-minutes: 20`**, against a default of 360 that would burn six hours
  of a 2,000-minute monthly allowance on one hung socket. The sweep's own
  10-minute deadline sits inside it so the commit step is always reached.

What it costs: about 150 of those 2,000 minutes a month.

### `fx` — the official rate, written down daily

```bash
python ask.py fx                  # record today's rate
python ask.py fx --show           # what has been recorded
python ask.py fx --currency USD,SGD
```

The command `config.toml` referred to for a while before it existed. It is here
for one reason: `fx_spread_per_side` is the largest unmeasured number in the
system — on a US position the whole fee schedule is about 0.3% a round trip, and
half a percent each way of conversion cost is three times that.

Measuring it used to need two figures at the same moment: the rate the broker
gave, and the official rate right then. Needing both at once is why it never got
measured. Recording the official rate daily removes the timing problem — convert
whenever suits, then read the rate off the app afterwards and compare against the
date.

`data/fx.db` is a **history, not a cache.** `PriceCache` expires daily because
bars are derived data reconstructible from the source; a rate is a fact about a
date, BNM does not serve old rates on demand, and a day not written down is a day
the comparison can never be made. Append-only, and `rate_on` is deliberately
exact rather than nearest-earlier: answering with a neighbouring day's rate would
put an unknown error into the one number it exists to measure.

It runs as its **own step** in the collector, not folded into the sweep. A news
source being throttled says nothing about whether the central bank published a
rate, and one failing must not cost the other its day.

Note this uses `api.bnm.gov.my`, BNM's OpenAPI — a different host from the
website, and the one BNM surface that has worked throughout.

### `watch` — evaluate the monitor rules

```bash
python ask.py watch               # exits 1 if any alert is open
```

Seven rules over the ledger, the corpus and the run log: 24-hour spend, p95
latency, dropped
claims, silence (no MODEL call when something should have run), sweep silence
(no successful SWEEP when one was scheduled - a different question, because
`ask.py sweep` makes no model calls), run errors, and a methodology change. Each alert names the number that fired it and the threshold
it crossed. This is the command to put on a schedule.

### `alerts` — what is open, and what has cleared

```bash
python ask.py alerts              # the log, newest first
```

The alert log is append-only: an alert that clears is not deleted, it gains a
cleared row. Reading when something opened and when it cleared is the point —
an alert that opens and clears nightly is a different problem from one that has
been open for a week.

---

### `capital` — how much may be invested at all

Before any question about *which* stock. Fill `[capital]` in `config.toml`:

```toml
[capital]
liquid_assets = 120000.0            # cash and near-cash you could deploy
essential_monthly_spend = 4500.0    # what the emergency floor multiplies
planned_monthly_contribution = 2000.0
goals = [{ name = "car", amount = 30000, months_away = 18 }]
liabilities = [{ name = "card", balance = 8000, annual_rate = 0.17 }]
```

```bash
python ask.py positions
```

What the broker says you hold, as distinct from what `config.toml` says. Needs
OpenD running and logged in; read-only by construction (docs/19). Prints a
`holdings = [...]` line to paste - it never writes config itself, because a
portfolio changed with no diff is a portfolio changed with no decision.

```
python ask.py capital
```

Liquid assets → emergency floor → near-term goals inside 24 months → debt above
the hurdle → cash buffer → **investable**. The first three steps are `[locked]`
and no flag reduces them. If the floor and the reservations consume everything,
the answer is **investable 0** — which is an answer, not a failure.

`size --from-plan` takes its capital from here. `size --portfolio <number>`
still works and says outright that the three locks were not applied to a figure
you typed.

### `allocate` — split a budget across names YOU nominate

This does not choose the names. It answers the question after choosing them.

```bash
python ask.py allocate --portfolio 500000 \
  --name MYX:1155:10.68:9.90:20000000:bank \
  --name MYX:1023:6.40:5.95:18000000:telco \
  --name MYX:5296:2.10:1.95:9000000:consumer \
  --name MYX:6012:4.55:4.20:12000000:energy \
  --name MYX:4197:7.80:7.20:15000000:plantation \
  --name MYX:1961:22.40:21.00:8000000:reit
```

Each name is `MIC:CODE:PRICE:STOP:ADV:SECTOR`. With `--fetch`, an empty PRICE or
ADV is measured from the feed; the **stop is never derived**, because where the
stop goes is your risk decision and inventing one invents the risk budget.

Two things it refuses to do:

* **Fabricate diversification.** Fewer than five fundable names, or a book that
  would behave as one bet, is a refusal — not a concentrated book that passes
  arithmetic.
* **Hide why a name got nothing.** Every excluded name carries its number: the
  cap that stopped it, the minimum economic position it fell under, or the
  missing FX rate.

**Expect cash left over, and read the note that says why.** Six names at the 8%
single-name cap can hold at most 48% of capital. Six *Malaysian* names stop
earlier still, at the 40% country limit — and the line then reports `country`
as its binding cap, because that is the limit that decided the number.

**On a Bursa-only book there is a floor under the whole exercise.** The
cheapest name that can pay for its own round trip needs MYR 4,705.88 (the 60 bps
cost floor), and at an 8% single-name cap that position only fits inside a
portfolio of **MYR 58,824 or more**. Below that, every name is either over the
cap or under the floor, and `allocate` says so with both numbers.

### `rebalance` — what to change versus what you hold

The book comes from `account.holdings`, which grows from bare ids to tables:

```toml
holdings = [
  { id = "MYX:1155", units = 1000, avg_cost = 9.80, stop = 9.40, sector = "bank" },
]
```

`stop` and `sector` are optional and only this command reads them. Without a
stop the risk-budget cap cannot be computed for that name, and the output says
which cap it lost rather than inventing one.

```bash
python ask.py rebalance --portfolio 800000        # or --from-plan
```

Prices come live from the feed and are snapped to the market's tick. Every line
is `hold`, `add`, `trim`, `exit`, `open` or `stop hit`, with the units, the
value of the trade, its round-trip cost, and the limit that bound it.

Three rules worth knowing before you read the output:

* **A trade worth less than its own round trip comes back as `hold`**, with both
  numbers. A rebalance smaller than its cost is a fee.
* **A holding whose live price is at or below its own stop leads the output** as
  `STOP BREACHED`. That is not a data error — it is your own rule, already
  triggered — and it is reported even when no target could be built.
* **Weights are measured against the whole portfolio, cash included**, which is
  the denominator every limit in `engines/risk/concentration.py` is defined
  over. If the capital you give is smaller than the book is worth, the output
  says the two numbers disagree rather than picking one.

`BEFORE` and `AFTER` are the same book run through `A12PortfolioRisk`, so the
breaches you are carrying and the ones you would still be carrying are on the
same screen.

---

## `ask.py` — the CLI

Twenty subcommands.

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

### `method` — the curated method notes, cited

```bash
python ask.py method kb_craft --concept cash_flow            # the note the teacher cites for a concept
python ask.py method kb_method_valuation --archetype bank     # the valuation method for an archetype
python ask.py method kb_failures --pattern accruals_divergence  # failure cases by structural pattern
python ask.py method kb_method_risk portfolio heat stops      # free text
```

The five human-written stores (`knowledge/method/`, contract in its README)
read through the owning agent's router scope. Each hit is quoted verbatim
with its chunk id and its references' licences; a link-only reference is a
link. **Exits 1** when no note matches (silence is not a lesson) and **2** for
an unknown collection, concept or pattern. Same text as the MCP `method_note`.

### `workup` — the twelve steps over the stored record

```bash
python ask.py workup XNAS:AAPL --archetype software
python ask.py workup MYX:1155 --archetype bank
python ask.py workup XNAS:NVDA --as-at 2026-06-30
```

The twelve steps of docs/04 §2, in order, over what the fact book holds as of
the date. Every step is printed with one of five statuses: `done`, `partial`,
`unavailable` (with the collector that would fill it), `manual` (the business
model, the comprehensibility gate and the cycle stage are yours to write) or
`not_applicable`. The earnings-quality gate reports `clean`, `flag` or
`unavailable`; a flag runs the red team's failure-analogue search on the
pattern the flag implies. Step 12 suggests two to four breakers, each an SQL
query against the facts store with a review date. A workup is not a stance:
compose one with `ask.py thesis` and let the red team at it. Exit 2 on an
unknown market or a bad date.

```bash
python ask.py graph --peers MYX:1155
python ask.py graph --peers XNAS:NVDA --asof 2026-06-30
```

Who the graph says the peers are: a stated rivalry (`competes_with`, with its
curated row quoted) and shared sub-sector siblings, labelled apart because the
second is two hops of classification and reads as speculative. Peers in another
market are listed as excluded. These peers feed `why` (a peer's event scores
0.6 on specificity, an unrelated name's 0.1) and the comparables in `valuation`
and `workup`.

```bash
python ask.py thesis XNAS:AAPL --derive-valuation --archetype software \
    --breaker "gross margin below 40%|gross_margin < 0.40|facts" \
    --breaker "revenue growth below 3% for two quarters|revenue_yoy < 0.03|facts"
```

`--derive-valuation` runs the cost of capital and the scenario DCF on the
stored record and hands the thesis the engine's bear-to-bull range, or its
refusal, before the red team reads it. The model never types a range. The red
team's failure analogues print under their own heading, labelled as
resemblances, never forecasts.

### `ratios` and `valuation` — the analyst arithmetic on the stored lines

```bash
python ask.py ratios XNAS:AAPL                         # 22 ratios + accruals, Beneish, Piotroski, Altman
python ask.py ratios MYX:1155                          # a Bursa name today: NO STATEMENTS STORED, and which source would fill them
python ask.py valuation XNAS:AAPL --archetype software # cost of capital, then bear/base/bull DCF as a range
python ask.py valuation XNAS:NVDA --coc-only           # the discount rate alone, every input labelled
python ask.py valuation XNAS:AAPL --peer XNAS:MSFT --peer XNAS:GOOGL   # adds the P/E in its three contexts
```

Point-in-time: `--as-at YYYY-MM-DD` reads only what was filed by that date.
A ratio whose input is not stored is not a number; the head line says n of N
and the collector that would fill the gap. The DCF is a range or a refusal:
a scenario whose terminal growth exceeds the risk-free rate, or whose
discount rate is below risk-free plus half the premium, is dropped and named.
**Exits 1** on NO STATEMENTS STORED or a refused range. Same text as the MCP
tools `ratio_sheet`, `cost_of_capital` and `valuation_range`.

### `backtest` — would it have worked, and was it worth running?

```bash
# a candidate rule over the cached bars, one currency at a time
FINPLANET_OFFLINE=1 python ask.py backtest --rule momentum_12_1 \
    --instrument XNAS:NVDA --instrument XNAS:AAPL --instrument XNAS:MSFT

# the paper book's own record, once it has one
python ask.py backtest --live

# what has already been tried on this data
python ask.py backtest --trials
```

`engines/backtest/harness.py` calls itself "the gate. Nothing reaches a user
before it clears this" — and until 2026-09-07 nothing ever reached it, because
no code in this repository built the return series it scores.
`engines/backtest/book.py` is that plumbing.

**A rule passes only if it beats all three benchmarks after costs** — the local
index (`0820EA.KL` for Bursa, `SPY` for Nasdaq), an equal-weight version of the
same names, and buy-and-hold on those names — **and** clears a deflated Sharpe
of 0.95. Beat none of them and the harness says the correct product is an index
tracker; it is written to be able to say that.

Four things it will not do:

- **It will not let a rule see the future.** `weights(prices, t)` is handed the
  index of the day being decided and paid `returns[t + 1]`. Look-ahead is
  prevented by the shape of the call, not by remembering.
- **It will not blend currencies.** A joint MYR/USD return series needs a daily
  exchange rate over the whole window and this system holds weeks of BNM rates,
  not years. A mixed book is refused, and the refusal prints the two commands to
  run instead.
- **It will not give a verdict on thin history.** Under 252 shared sessions it
  raises and says how many more it needs. `--live` therefore refuses today: the
  paper book has three marked sessions.
- **It will not let you choose your own multiple-testing correction.**
  `n_trials` is read from `data/trials.db`, an append-only ledger of every rule
  ever run on that universe and window. Try twenty rules and report the winner
  and the deflation knows there were twenty. The ledger's triggers refuse UPDATE
  and DELETE for the obvious reason.

`FINPLANET_OFFLINE=1` serves the price cache whatever day it was fetched, which
is what a backtest wants — the last bar being a day old is irrelevant to fifteen
years of history, and re-fetching every name to learn that is quota spent for
nothing.

The same gate is MCP tool `backtest_gate`, so the model reaches it through the
tools rather than reasoning about returns in conversation — which is the whole
posture of this surface.

Measured on the shipped cache (five years, 2021-09 to 2026-09), all three
reference rules **FAIL** on both sleeves. On the US names, momentum beat the
equal-weight universe and SPY and still lost to simply holding all three
(+55.5% against +66.4% CAGR, with a deeper drawdown). That is the gate working.

#### The fifteen DBnomics series that read stale

`ask.py macro` marks fifteen `DBN:` series 463–494 days past their cadence, and
the `series_stale` alert has carried them since the rule was written. Two things
were settled on 2026-09-07 and are worth not re-investigating:

- **The collector is not the DOSM bug.** It takes `periods[-N:]` — the newest
  observations, not the first. That defect was specific to `dosm_cpi`.
- **The endpoint is healthy.** A runner probe answered
  `dbnomics ok 1.8s — 450 series points in 1 request`. The ids resolve and the
  request shape is right.

So the data at DBnomics genuinely ends in mid-2025. What is *not* settled is
whether that is the publisher's own lag — IMF IFS and BIS aggregates can run a
year behind — or whether these particular series ids have been superseded
upstream while still answering with their last values.

**Do not close this by raising the cadence limits.** That would silence a
correct alert and lose the distinction between "we have not fetched" and "the
publisher has not published". The cheap way to settle it is to open each id at
db.nomics.world and read its last update date; the ids are in
`knowledge/sources/dbnomics.py`.

### `retrieval` — is the search any good?

```bash
python ask.py retrieval                        # the shipped vectors
python ask.py retrieval --embedder hashing     # the pre-2026-09-07 baseline
python ask.py retrieval --depth 20             # a more generous idea of "found"
```

Runs `knowledge/retrieval/data/retrieval_gold.yaml` — 24 questions, each pinned
to the articles in `data/corpus.db` verified to answer it — down all four
retrieval legs separately, and prints recall at 1, 5 and 10 plus MRR for each.

Two things to read first. **Dense lift** is the number of questions where the
vector leg found a relevant article BM25's own top ten did not; on the hashing
projection this system shipped with it was **zero out of twenty-four**, which is
what a vector leg that is really a second lexical search looks like. And the
**semantic** block is where a change is judged: the lexical block is questions
made of tickers and product names, which BM25 has always answered perfectly and
which a change must not break.

Recall is a floor. Only articles verified to answer each question are labelled,
so an unlabelled hit scores as a miss; the comparison between legs is the
finding, not the absolute number.

Run it before and after any change to the embedder, the fusion or the reranker.
Both of the obvious improvements tried on 2026-09-07 — a reranker that also
weighed meaning, and expanding the question with its nearest corpus terms — were
measured, found worse, and are recorded as rejected in the docstrings of
`hybrid.rerank` and `pipeline.default_rewrite`.

#### The vectors behind the search

Three backends sit behind one seam (`knowledge/retrieval/embedding.py`):

| backend | needs | what it knows |
|---|---|---|
| `DistributionalEmbedder` | nothing — **the default** | which words keep company with which, **in this corpus** |
| `HashingEmbedder` | nothing | nothing; a token's hash bucket. The pre-2026-09-07 default, kept as the baseline |
| `ApiEmbedder` | `EMBEDDING_API_KEY` | general language, including words this corpus has never contained |

The keyless default has one honest limit, and `ask.py doctor` now names it: it
can only relate words it has SEEN. A question about a "bendable" phone when
every article says "foldable" gets no help, because "bendable" appears nowhere
in the corpus and the corpus is its only teacher. Three of the sixteen semantic
gold questions fail for exactly this reason and no offline change fixes them.

Setting `EMBEDDING_API_KEY` promotes the seam to a real model — and starts
spending. `EMBEDDING_API_URL` and `EMBEDDING_MODEL` override the endpoint and
model (anything speaking the OpenAI `/v1/embeddings` shape). Vectors are cached
in `data/embeddings.db` keyed by model and text, so re-indexing an unchanged
season of headlines every night costs one request the first time and none after.

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

Thirty-nine tools; `details/10-STATUS-AND-GAPS.md` keeps the count and
`tests/test_docs_promises.py` pins it. The analytical ones: `market_info`,
`get_prices`, `why_did_it_move`, `fit_factor_model`, `compose_thesis`,
`check_portfolio_risk`, `size_position`, `plan_question`, `explain_concept`,
`method_note`, `log_prediction`, `calibration_status`, `explain_path`, the
fact-book readers (`daily_digest`, `fact_snapshot`, `macro_context`,
`news_evidence`), the paper book (`paper_status`, `paper_report`) and the
observability reports.

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
