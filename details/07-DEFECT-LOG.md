# 07 — Defect log

Every silent-wrong-answer defect found while building this system, with the
magnitude of each.

**None of these crashed.** Every one produced a finite, plausible,
correctly-typed number that would have survived every downstream check and been
acted on. That is the failure mode this repository exists to convert into a
loud one.

They are grouped by family, because the families repeat.

---

## 1. Identity drift — two producers disagreeing about a name

### 1.1 `MYX` / `XKLS` — every Bursa position sized against half the real floor

Instrument ids throughout the repository say `MYX:1155`. The adapter MIC is
`XKLS`. Nothing mapped between them, so `cost_floor_bps("MYX")` missed the table
and returned the **30 bps default instead of Bursa's 60**.

Magnitude: a factor of two on every Bursa position ever sized. Nothing crashed.

Fix: one canonical resolver, `markets.registry.resolve_mic`, used everywhere an
id becomes a market.

### 1.2 `XJPX` / `XTKS` — the same drift, caught before it cost anything

`docs/06` wrote `XJPX` (the Japan Exchange Group operator MIC) while
`core/market/feed.py` already wrote `XTKS` (the segment where shares trade).
Mapped in `ALIASES` before Tokyo was registered.

### 1.3 `display_names()` keyed by the raw form

`entities.yaml` writes `MYX:1155`; ids resolve to `XKLS:1155`. The display-name
lookup was keyed on the raw spelling, so it **silently never matched**. Every
company in a path would have been labelled with its stock code:
`1155 --competes_with--> 1023`.

### 1.4 Six drifted agent ids

Recorded in docs/05. Same shape: an id written two ways, no crash, and the
allowlist check quietly passing something it should have refused.

---

## 2. A cap that inverts the thing it bounds

### 2.1 `liquidity_cap` accepted a negative ADV

A negative cap is the **smallest** of the five, so it wins `CapSet.binding()`
every time and carries a negative target size downstream.

Magnitude: a cap that inverts its own constraint. Refused at the source now.

### 2.2 `cost_floor_value` returns its ceiling when no size works

With a flat-bps cost model there is no fixed minimum, so the bisection can never
come down and returns its MYR 100,000,000 ceiling — which reads as a *position
requirement* rather than as the impossibility it is.

Fix: `ask.py` computes the asymptote first and says "no position can pay its own
spread here" instead.

---

## 3. Numbers outside their own range

Found by `stress/run.py`. All four looked like answers.

| Function | Input | Wrong output | Why it is dangerous |
|---|---|---|---|
| `hhi` | weights `[-0.5, 1.5]` | 2.5 | HHI is bounded `[0,1]` and compared against a 0.18 limit. 2.5 reads as extreme concentration, not as bad data. |
| `effective_number_of_bets` | correlation 2.0 | **0.67 bets from 2 positions** | the range is `[1, n]`. This is the number the entire eggs-in-one-basket rule rests on. |
| `decompose` | NaN return | `unexplained_share = nan` | renders as "nan% unexplained" — a confident finding, arriving through the *data* rather than the model. |
| `liquidity_cap` | negative ADV | negative cap | see §2.1 |

Three of the four would have shown a **more alarming** reading than the truth.
A system whose outputs are numbers a human acts on cannot tell a bad number from
a bad input unless it checks at the boundary.

---

## 4. The unit of account — four defects behind one missing word

Adding ten foreign markets introduced eight currencies into a system where no
number outside `Money` carried one. `BASE_CURRENCY = "MYR"` had been in
`core/contracts/money.py` from the start; what was missing is that everything
else passed bare `Decimal`s.

### 4.1 `CapSet.binding()` took a `min()` across two currencies

`risk`, `kelly` and `concentration` derive from the portfolio and are MYR.
`liquidity` derives from local turnover and `cost_floor` from a local fee
schedule — both native. `min()` compared them as bare numbers.

> A thinly-traded US name with USD 300k of daily value gives a **USD 15,000**
> liquidity cap. Against an **MYR 40,000** concentration cap, `min` picks
> 15,000 — and the system deploys roughly **MYR 63,000 against a limit that had
> just computed 40,000**. A 58% overshoot, reported as compliant.

### 4.2 `size()` divided an MYR cap by a native price

`units = value / price`, with nothing naming either side.

> On a USD 180 stock, an MYR 40,000 cap bought **222 shares** — USD 39,960, or
> **MYR 167,832 of a MYR 500,000 book**. A **33.6% position from an 8% limit**,
> labelled `bound by concentration`.

The same arithmetic on a JPY name buys a **thirty-eighth** of the intended size,
so the error is not even consistently in one direction. It is consistently the
exchange rate.

### 4.3 The post-trade concentration check was fed the same wrong number

`new_weight = final_value / port_value` put a native numerator over an MYR
denominator. The last line of defence — the check that runs on the portfolio
*after* the trade — saw a weight off by the same factor and **passed the
breach**.

### 4.4 `currency` was copied from the `country` field

In `ask.py::_parse_position` and in the MCP `check_portfolio_risk` tool:

```python
country = parts[4], currency = parts[4]  # "MY" is not "MYR"
```

`check()` counts anything that is not the base currency as foreign exposure.

> A book of **nothing but Bursa stocks** reported **100% foreign-currency
> exposure** and breached the 50% limit.

A false refusal from a field nobody was reading — in the same line that produced
a false *pass* for anything genuinely foreign.

### The fix

A declared boundary, not a conversion. `CapSet.currency` names the one currency
all five caps are in; `market_currency(mic)` reads it off the adapter; sizing
happens entirely in the market's currency; only the result crosses back to MYR;
crossing without an explicit dated rate raises `CurrencyMismatch`.

Full mechanism in `05-MARKETS-AND-MONEY.md`.

---

## 5. A field declared and never read

### 5.1 `FeeLeg.per_side`

`FeeSchedule.round_trip` doubled **every** leg. UK Stamp Duty Reserve Tax is
charged on the buy leg only, ~50 bps.

Magnitude: London's cost floor was **50 bps too high**. Doubling reads as
prudence and is not — an overstated floor refuses positions that would have
cleared the real one.

Taiwan and Korea levy their transaction tax sell-side only; India's stamp duty
is buy-side.

### 5.2 Latency was measured and thrown away

`record_call` took a latency and did nothing with it, so it reached the trace
only when tracing was on. `p95_latency` is a term in the fitness function.

Fix: a `latency_ms` column, a migration, and `latencies_between(start, end)`
that excludes zeros rather than averaging them in.

---

## 6. Claims that could not be made, or could be made too easily

### 6.1 The graph could not emit any claim, in any configuration

`path_to_citations` did not exist. The graph could traverse; everything it found
died at the citation check. This was the phase-1 blocker.

### 6.2 `bidirectional=True` minted the reverse edge with the same kind

"A supplies B" produced "B supplies A". Fixed with `EDGE_INVERSE`.

### 6.3 `verify_claim` kept a claim if **any** citation verified

Right for redundant support, wrong for a conjunctive chain. "A supplies B, and B
is exposed to C, therefore A is exposed to C" is not three-quarters true when
one link fails.

Fix: `Claim.all_citations_required`.

### 6.4 `EDGE_DECAY` unguarded dict access

A `KeyError` mid-query on any edge kind added without a decay entry.

### 6.5 `money.convert()` accepted a negative rate

USD 100 → **MYR −415**. A zero rate destroyed the amount outright. Both
silently, because the result is finite, plausible and correctly typed.

### 6.6 `currency` was bounded by length, not alphabet

`"123"` was a valid currency code — carried, formatted and summed like one.

---

## 7. Docstrings that described work nobody had done

Three cases where the comment was the specification and the code was empty:

- `store.py` claimed tier-scoped re-extraction with nothing implementing it.
- `traverse` was incomplete without saying so.
- `code.py` claimed to answer "which agent has no eval" — a state the registry
  ratchet already refuses, so the query could never return a row.

Each was either implemented or the claim removed. A docstring that promises a
guarantee is worse than no docstring, because it is read as a check.

---

## 8. Test defects — three premises that were simply wrong

Worth recording, because a test asserting a false premise is a defect that
*hides* defects.

| Test premise | Reality |
|---|---|
| "Hong Kong sets one board lot" | HK sets lots **per instrument** — 0001 is 500, 0700 is 100 |
| "India is uniquely T+1" | XNAS has been T+1 since **May 2024** |
| a traversal that "misses" a path | the construction did not actually miss; rebuilt with a genuine two-route case |

And one that passed for the wrong reason:

> A test helper mapped `__` → `/`, so a fixture named `__pycache__/skip.py`
> became the **absolute path** `/pycache`. It passed locally **only because the
> shell ran as root**, silently creating a directory at `/`. CI, running
> unprivileged, raised `PermissionError`. Verified afterwards by running the
> suite as a non-root user.

---

## 9. A default that was a fabrication

`knowledge/feeds/rss.py` dated an item with no `<pubDate>` as `rec.fetched_at`,
and `_parse` skipped an item only when its date was *older* than the window —
so an undated item was never filtered out, and then arrived stamped with the
moment it was fetched.

Read as a line of code it looks like a sensible fallback. Read as a claim it
says: *this was published now*, asserted about an item that said nothing of the
kind. Found on 2026-09-04 probing BNM, whose 2020 press-release archive is valid
RSS with **zero `<pubDate>` elements in 23,228 bytes**. Enabled, a nightly sweep
would have entered six-year-old central bank releases at the NEWEST end of every
window, on a schedule, indistinguishable from real news — and the registry note
recording the attempt said the archive would yield "0 items in any recent
window", which was the opposite of true for exactly this reason.

Now: an undated item is dropped and counted, and a feed that dates **none** of
its items raises rather than returning anything, because a source that places
nothing in time cannot be windowed at all. `_to_article` keeps a guard behind
the guard, so if the drop is ever removed the fabrication fails loudly instead
of resuming silently.

Same family as §4's currency defect and the broker's refusal to substitute zero
for a missing figure: the bug is not the missing value, it is the plausible one
put in its place.

## 10. A decision that was taken and left no record

`ask.py paper decide` on an all-cash night wrote NOTHING. No name raised and no
name held is zero target rows, so the book kept no row saying a decision had
been taken at all — while the command printed *"observe phase: this is logged
and will be graded"* over an empty write. Found on 2026-09-08, the paper book's
first night, by checking the stores against the page that claimed the decision
was recorded: `paper.db` targets 0, `learning.db` predictions 0, neither file
changed in git.

Magnitude: the observe phase exists to grade decisions, and **every all-cash
night in it was unfalsifiable**. Not one wrong number — the absence of any.
Worse than a wrong number, because a book with no losing record is exactly the
shape of a flattering one.

The question underneath it is whether holding nothing is a prediction at all.
It is: *nothing in the fundable universe beats cash over the horizon*, and the
control book is the counterfactual that settles it. So an all-cash night now
writes one row (`CASH`, reason `all_cash`, resolved on the spot so no market is
ever asked to price it) and one prediction graded against the control instead of
against a price — right exactly when the control lost ground over the same days.

## 11. A price cache that served half a session as a whole one

The cache expires at the UTC day boundary, on the stated reasoning that *"a
daily bar cannot change until a new session prints"*. That is false for a body
fetched **during** a session. On 2026-09-08 `XNAS:SPY` was fetched mid-session
and Yahoo answered with an in-progress row whose open (772.01) was carried over
from the previous day and sat **above its own high** (769.70). The bar parser
was right to drop it. The cache then reported a hit for the rest of that UTC
day, so the 22:37 `us_close` sweep — which would have got the finished bar —
never refetched.

Magnitude: the US market proxy silently ended on **2026-09-04** while the three
US names it was measuring had printed 2026-09-08. Every US decomposition on the
2026-09-08 pack is two sessions stale, and nothing in the pack said so. Same
shape as the Bursa proxy blank row on the 2026-09-07 page (`knowledge/feedback/2026-09-07.md`),
reached by a different route: there the proxy did not print, here it printed and
was fetched too early.

Fix, in two places because the fault has two halves:

* **The cache** refuses to serve a body that carries a dated ROW its own parser
  will not accept as a BAR (`core/market/cache.is_mid_session`). That is what an
  in-progress session looks like on the wire, and the next process fetches
  again. A body that simply ends on the last session it saw still hits, so a
  quiet market costs no quota.
* **The pack** compares each name's last printed session against the last one it
  shares with its proxy, and marks the row `MIS-DATED` with the day it is really
  about (`knowledge/pack.Move.mis_dated`). A fallback to an earlier session is
  not a fault — markets close — but a *silent* one is, and the two cases have
  different cures.

Still not caught: an in-progress row that happens to be self-consistent parses
as a bar and is read as that session's close. Recorded here because nothing in
the system can currently see it.

### The "unstable cached history", settled on 2026-09-11

An open question carried since 2026-09-08 said the cached price HISTORY changed
between fetches - 2026-09-07 present in one, absent in the next - with the
mechanism unknown. Read against the shipped cache, it is three different things
and only one of them is a defect:

| symbol | tail | verdict |
|---|---|---|
| `SPY` | 09-04, **09-08**, 09-09, 09-10 | 2026-09-07 was **Labor Day**. Not a gap. |
| `1155.KL` | 09-04, **09-07**, 09-08, 09-09, 09-10 | Bursa was open. Not a gap. |
| `0820EA.KL` | 09-08, `2026-09-09,,,,,`, 09-10 | a dated row with NO VALUES |

So the history was never unstable. One fetch read a Bursa symbol and the next a
US one, across two market calendars, on a week where the US had a holiday
Malaysia did not. **The question is closed.**

What the reading did turn up is real, and it is two things:

* **The mid-session defect recurred on 2026-09-10**, after §11 was written and
  while the fix was still unmerged. `SPY` came back `open 764.08 > high 758.55`,
  volume 3.59m against 32.77m the session before, and its open was carried
  verbatim from 09-09. `is_mid_session` returns True on that exact body, which
  is the fix confirmed against a case it was not written from.
* **A dated row with empty fields** is a THIRD shape, distinct from a missing
  row and from an inverted one. The parser drops it, correctly, leaving a
  permanent hole at 09-09 in the proxy's history while the names it measures
  have a bar for that day - which `pack.Move.mis_dated` is what flags. It must
  NOT be made a cache miss: no amount of refetching fills a session the upstream
  never recorded, so a miss there spends the day's quota to change nothing. A
  test pins both halves.

## 12. Fifteen macro series that were stale in a way nobody could fix

The `series_stale` rule fired every night on the same fifteen DBnomics ids —
palm oil, aluminium, Brent, Asian LNG, five policy rates, two CPIs, three
effective exchange rates and the Malaysian government yield — each 436 to 497
days old with a *current* `fetched_at` beside it. The rule was right to fire and
its next step ("point the adapter at a live series, or take the id out") was
unanswerable from here: the development environment has no route to
db.nomics.world, so nothing could tell a retired code from a stopped dataset.

`.github/workflows/dbnomics-probe.yml` asked the API on 2026-09-06. Verdict on
all fifteen: **FROZEN**. Our codes are correct; the datasets behind them stopped
being ingested — IMF/PCPS and BIS/WS_CBPOL at **2025-06**, BIS/WS_EER and
IMF/IFS at **2025-05**, IMF/CPI at **2025-07** — and every sibling code inside
each dataset stops at the same period (siblings outside our list answered HTTP
400). There is no live code to move to. Re-sourcing is a decision about where to
buy macro data, not a bug to fix in the collector.

Two defects, then, and only the first one is DBnomics':

* **The surfaces could not say "ended".** They had one word, STALE, for two
  different facts: *the next print is late* and *there is no next print*. A
  reader holding a 466-day-old Malaysian policy rate needs to know which. Worse,
  `engines/valuation/cost_of_capital.py` discounts every Malaysian name off
  `DBN:GOVT_YIELD_MY` — so a stopped series was setting the ringgit risk-free
  rate in a live WACC, dated but unqualified.
* **The monitor could not close a finding nobody could act on.** An alert that
  reopens nightly with an impossible next step trains a reader to skim the list,
  which costs the alerts beside it.

Fix: `knowledge/sources/freshness.ENDED` records the probe's verdict once — last
period, upstream, verdict, the day it was probed. From it: the macro row reads
`466d ENDED 2025-06` instead of an age; `macro_context` prints the reason under
the table, grouped by dataset (five upstreams, not fifteen lines); the WACC's
`rf_source` names the stopped series and the caveat says the rate rests on it;
and `series_stale` stops judging them.

What keeps that honest is that **the collector still fetches all fifteen**. One
request covers the whole list, so the cost is a request the sweep was making
anyway, and if any of them prints past its recorded last period the new
`series_resumed` rule opens a WARN asking for the `ENDED` entry to be deleted.
Deleting the ids instead would have left nothing able to notice a restart — the
marking would have been unfalsifiable, which is the failure mode of every
"known issue" list that outlives the issue.

## 13. A company the corpus could recognise and never asked for

`MYX:5183` (Petronas Chemicals) holds **zero** articles out of 2,671. Its five
Bursa neighbours hold 1 to 25; the three Nasdaq names hold 1,968 between them.

Not a linker fault. `entity_index` has carried `PCHEM` — the form the Malaysian
press actually prints — since the alias table was written, so an article naming
PCHEM would have been attributed correctly the moment it arrived. None arrived,
because the per-name GDELT path asked for `terms[0]`: the FIRST alias in
entities.yaml, and only that one.

    query=f'"{terms[0]}"'      ->   "Petronas Chemicals"

So the corpus could recognise a name it never asked for. The same defect in the
Google News path is fixed in [#60](https://github.com/fangrhui040527/finance-agent/pull/60);
this is the other half of it.

The reason the one-alias rule existed at all is real and is preserved.
`watchlist_query` — the COMBINED query, every company in one request — is capped
at one phrase per company because 21 phrases across nine companies timed out
three times at 30s on the 2026-09-03 runner, and because the DOC API charges for
query breadth. But the per-name path asks about **one company at a time**: its
own two or three forms cost query width and no extra request, so the cap that
protects the combined query has nothing to do there. `search_terms` /
`search_query` answer the per-name question and `watchlist_terms` still answers
the combined one; a test asserts the two do not merge.

What is NOT relaxed is `MIN_PHRASE_CHARS`. GDELT refuses a quoted phrase under
five characters with a plain-text error that fails the whole request, so `TNB`
and `IHH` still cannot be asked for here however much the press uses them — an
API's constraint, not a judgement, which is why the Google News path applies no
such floor.

Still open, and larger than this fix: four of the five registered Malaysian
outlets are DISABLED on one 404 each from 2026-09-04.
`.github/workflows/bursa-feeds-probe.yml` reads the RSS autodiscovery tags off
the publishers' own pages and reports which URLs serve items **with dates** —
because a 404 on one guessed path is not evidence a publisher has no feed, and
nothing in this environment can reach a Malaysian host to tell the difference.

## 14. A no-op run that still committed

`_already_ran` (§ the doubled slot) makes the second collector arrival on
a slot a no-op: the cron and the nightly catch-up both fire, whoever is first
collects, the second costs seconds instead of 370 requests. It was not a no-op
in one place. The digest was re-rendered with a later `Generated` line over
identical figures, so three files changed and the workflow pushed them:

    -Generated 2026-09-08 22:38 UTC · slot `all` · 2084 articles, 4465 observations, 3968 events
    +Generated 2026-09-08 23:23 UTC · slot `all` · 2084 articles, 4465 observations, 3968 events

That is the whole content of commit `c8f3ede`, and of `8387e38` the night
before. The cost is not the bytes. `git log data/digests` is the cheapest record
anyone has of when the collection actually moved, and a timestamp that advances
over unchanged data makes that record lie.

`write_digest` now compares the render against the file with only the timestamp
masked, and writes nothing when that is the only difference. The slot and the
three counts share that line and ARE compared: a different slot writing the same
figures is a fact about the collection; a re-render at a later minute is not.
The publication rail still runs before the comparison — a digest is checked
before it is compared, never waved through for resembling one that passed.

Not changed, deliberately: the second arrival still fetches prices and marks the
paper book. §11 is the reason — the first arrival can land mid-session and cache
a partial bar, so the later run is the one that gets the finished close. And the
duplicate `marks` rows it leaves are read correctly: `marks()` and `latest_mark`
both take the last row per day, so the equity series never double-counts.

## 15. A skip that was invisible to the rule that watches for silence

On 2026-09-11 `sweep_silence` opened on **fmp**: *"past its own cadence: fmp (90h
ago, allowed 78h)"*. The collector had dispatched fmp on time every weekday.

fmp is per-instrument, and `us_preopen` is a macro slot that carries no
per-instrument work, so the right thing happened: it was skipped, with a reason.

    facts.db  pulls   2026-09-10T16:37  fmp  skipped  no name in the book trades in slot 'us_preopen'
                      2026-09-09T16:50  fmp  skipped  ...
                      2026-09-08T16:48  fmp  skipped  ...
    corpus.db sweeps  2026-09-07T08:49  fmp  ok       <- nothing after this

`sweep_silence` reads `corpus.last_success` and nothing else. The skip was
written to the PULLS table and not the SWEEPS table, so a source being
dispatched on schedule read as a source that had stopped, and on the fourth day
the monitor said so.

The inconsistency was visible in the same function. The `KeyMissing` branch
eight lines below has always written BOTH rows, which is why the 2026-09-04
"fmp needs FMP_API_KEY" skip appears in both tables while the 09-08 one appears
in neither-but-pulls. Two skips, two spellings, one of them invisible to the
rule that exists to notice absence.

A skip is a dispatch that had nothing to do. That is precisely what the silence
rule needs to see - it asks whether the collector STOPPED, not whether the
source returned rows - so the no-instruments skip now records a sweep the same
way, with the reason in the detail.

Note what this does NOT change: fmp still returns **HTTP 402** on its earnings
endpoint (3,957 fetched, 0 kept) whenever it does run. Whether the free tier
earns its requests is a purchasing question, recorded here and left open.

## 16. A filter that ran after the limit, and hid the thinnest names best

`Corpus.articles()` takes a `limit` and an `instrument`. It applied the limit in
SQL and the instrument filter in Python, afterwards. So the question it actually
answered was not *"the newest `limit` articles about this company"* but *"of the
newest `limit` articles about ANYTHING, which mention this company"*.

With a corpus that is 97% US wire copy, that is a different question with a
different answer. Measured on the 2026-09-11 corpus at the default limit of 500:

| instrument | held | returned |
|---|---|---|
| MYX:5347 Tenaga | 3 | **0** |
| MYX:8869 Press Metal | 4 | **0** |
| MYX:1023 CIMB | 1 | **0** |
| MYX:1295 Public Bank | 2 | **0** |
| MYX:1155 Maybank | 28 | **1** |
| MYX:3182 Genting | 17 | 1 |
| XNAS:NVDA | 1,188 | 230 |

Six of the nine Bursa names held articles and returned none of them. The error
is not uniform - it is worst exactly where coverage is thinnest, because a name
with few articles is the one whose articles fall outside a recency window. A
reader would have seen `NOTHING COLLECTED` and concluded the collector had a
gap, when the collector had the story and the reader could not ask for it.

The fix is one clause moved into SQL, matching the id between its own JSON
quotes so `MYX:115` cannot be answered by `MYX:1155`.

**Latent, not live.** No production caller passed `instrument=` when this was
found - the retrieval index reads the corpus unfiltered and was never affected.
It is recorded because the API was wrong for anyone who used it next, and
because the failure mode is silent: an empty list is indistinguishable from an
empty store.

**What this was NOT.** Two things were checked first and cleared, so nobody
re-investigates them:

  * **GDELT's 23% link rate is not a linking defect.** 210 articles were fetched
    for a named instrument and stored with no link to it, which looked like
    discarded provenance. Reading them settles it: they are titles like *"Xiaomi
    SkyNomad N70 Pro"* and *"Copper Just Soared to an All-Time High"*, returned
    against a query for NVDA. The linker is right to decline them, and linking
    on fetch provenance would have injected ~200 false attributions into the
    evidence the paper book reasons from. GDELT is a broad feed; the open
    question is its VALUE, not its correctness — and that question cannot be
    answered yet, because `gdelt.search_query` only landed 2026-09-10 05:50 and
    the post-fix sample is one day.
  * **457 of the 947 unlinked articles are historical.** GDELT ran untargeted
    until 2026-09-06 and per-instrument from 2026-09-07. The unlinked mass is
    debris from a regime that has already been replaced, not a live fault.

## 17. Asking for the ticker worked, and the ticker is what spam quotes

#60 made the collector ask Google News for every alias a company is printed
under, so `"PCHEM"` was searched for the first time on 2026-09-11. It worked:
`MYX:5183` went from **0 articles, all time** to 2 on the first run, and Bursa
coverage overall went 70 -> 98 in that single sweep.

Both PCHEM articles were these:

```
$PCHEM (5183.MY)$
$PCHEM (5183.MY)$ OMG!!! My mom got FREE RM188 here wowww, some of the users got RM88 and RM100
```

A cashtag is how a retail social platform tags a user post, and Google News
carries them as if they were reporting. So the alias fix cleared the
`name_coverage` alert on Petronas Chemicals **while the company still had no
reporting at all**. The rule counted arrivals; two arrived. Nothing was broken -
the collector asked the right question and the internet answered with rubbish -
which is why only reading the rows found it.

**The publisher is not the filter.** Blocking moomoo.com is the obvious move and
measuring says it is wrong: 11 of its 19 articles are real syndicated journalism,
including *"Foreigners Dump Banks While Locals Gobble Up Maybank"* - precisely
the Bursa reporting this book is short of. #65 measured the same thing from the
other side: the whole domain removed 23 rows, the tag prefix removed 9.

**IT TOOK THREE FIXES AT THREE SEAMS, and that is the lesson.** One shape of junk
reached the book by three independent paths, and closing one said nothing about
the others:

| seam | what it decides | fixed by |
|---|---|---|
| `retrieval.indexable` | can it RANK | #65 |
| `monitor.name_coverage` | does the name look covered | #66 |
| `clean.is_junk` -> `quality_score` | does it score as NEWS, for the digest | this |

#65's own docstring recorded the second one as still open rather than assuming
it had been covered, which is why #66 exists. The third survived both: the daily
digest reads the corpus directly, not through the index, so a ticker-tag post
still scored 0.65 against `digest.MIN_QUALITY` of 0.4 and remained eligible for
the page a person actually reads. Nine were stored by 2026-09-14 and they were
still arriving - `$SanDisk (SNDK.US)$ $Apple (AAPL.US)$ ...` on 09-12, so the
shape is not a Malaysian quirk either.

`clean.TICKER_TAG` is now the single copy and `retrieval.index` imports it. Two
regexes for one concept drift, and the divergence would be silent: one seam
ranking what the other had already decided was not a story. A test asserts the
two are the same object.

## 18. A vector leg that was never asked to prove it, and was subtracting

`knowledge/retrieval/hybrid.py` shipped BM25 fused with a dense leg by
reciprocal rank fusion, as docs/02 §3 and docs/09 §6 specify. The argument is a
good one: finance questions are half semantic ("margin compression risk") and
half exact-token ("MYR", "Q3 FY25", "0011.KL"), and fusing the two should beat
either alone. It was built to that argument, and then it was shipped on that
argument, and for the whole life of the module *no number was ever asked to
defend it*. §7 of this log is docstrings that described work nobody had done.
This is the neighbouring failure: work that was done, described accurately, and
never checked against the thing it was for.

Measured on 2026-09-14 — the 23 labelled questions, 22 of which have an
answer still reachable in the corpus, over 3,481 indexed chunks:

| leg | r@10 | MRR | semantic r@10 | what it is |
|---|---|---|---|---|
| `bm25` | **47.8%** | 0.360 | 25.0% | exact-token search alone |
| `dense` | 39.1% | 0.337 | 12.5% | vector search alone |
| `fused` | 43.5% | 0.346 | 18.8% | the two by RRF — **what shipped** |
| `reranked` | 43.5% | 0.406 | 18.8% | fused, then reordered — what a reader got |

Two findings, and only the first is an inference-free reading of the table.

**Dense lift was zero.** Not low — zero, on all 23 questions, for both keyless
backends: the `HashingEmbedder` this system launched with and the corpus-fitted
`DistributionalEmbedder` that replaced it. Dense lift counts questions where the
vector leg found a relevant article BM25's own top ten did not, so zero is the
direct statement that the leg contributed no document exact-token search had
not already found. It was not a semantic index. It was a second lexical search
with a slower inner loop.

**So the fusion landed between its own legs rather than above them.** 43.5%
against BM25's 47.8%. That is what rank fusion does when one leg is a duplicate
of the other: it still spends ranks on the duplicate's opinions, and those ranks
come out of the other leg's real hits. The component added to make semantic
retrieval better was making semantic retrieval worse — 18.8% against BM25's
25.0% on exactly the sixteen questions it existed to serve.

The fusion is removed, in two steps by two hands. #68 switched it off behind a
`Collection.FUSE_DENSE = False` class flag, keeping the RRF code gated so a
future embedder could flip it back, and re-aimed the failing test from "the
dense leg always helps" at "we only SHIP a leg that helps" — the right first
move, and the sharper statement of the property. This removes the flag and the
code under it, because a gate is a poor resting place: with nothing to fuse, the
RRF path was exercised by no shipped call, `evaluate.py`'s `fused` column
silently re-measured `bm25` (verified on main at `d3a1902` — 30.4/47.8/47.8,
0.360, identical to `bm25` in every cell), and that column's own guard
assertion, `fused.recall_at_10 >= bm25.recall_at_10`, had become BM25 compared
against itself. Fifteen lines of rank fusion are cheaper to rewrite than to keep
honest unused. What survives from the gated version is its best assertion: the
dense leg must still be MEASURED every run, because deleting the fusion is only
defensible while the number that would justify rebuilding it still exists.

`Collection.search` is now BM25 plus the hard filters, and `rerank` orders what
it returns. After:

| leg | r@10 | MRR | semantic r@10 | semantic MRR |
|---|---|---|---|---|
| `reranked` before | 43.5% | 0.406 | 18.8% | 0.146 |
| `reranked` after | **47.8%** | **0.422** | **25.0%** | **0.169** |

**Read the recall move as one question, because that is what it is.** Twenty-
three questions: 10 answered within the top ten before, 11 after; on the
semantic block, 3 before and 4 after. A single question moving is not evidence
of a 4.3-point improvement and should not be quoted as one. The evidence that
carries weight here is the zero and the ordering — a leg that added nothing on
any question, fused to a result strictly below one of its own inputs. That the
shipped path also came out ahead on both recall and MRR is a consequence worth
recording and too small to be the argument.

**This is a retraction of a measurement, not of the design.** No hosted
embedding model has ever been scored here. The docs may well be right that a
real one beats BM25 alone; what was tested is the two backends that run without
a key, and those two have now had three attempts between them — the fusion, a
meaning-weighted reranker, and query expansion, the latter two rejected on
2026-09-07 — with nothing to show on any. So the apparatus stays whole and
wired to nothing: `Collection.dense`, all three backends, the `dense` leg in the
evaluator, `ask.py retrieval --embedder api` and `.github/workflows/embedding-
probe.yml`. Dense lift is now a door rather than a promise. Move it off zero and
the fusion is worth rebuilding; until then production never embeds anything, and
a test asserts that by handing `search` an embedder that raises if touched.

## 19. A term the model had, the output did not, and a test added by hand

`engines/attribution/decompose.py` builds the expected return from the fitted
market model and subtracts it to get the abnormal return:

```python
expected = fit.coefficients[0] + c_mkt + c_sec + c_sty
ar = realised_local - expected
```

`coefficients[0]` is α, the intercept. It is correct that it is there — that is
the textbook market-model abnormal return, and the whole significance test rests
on `ar` being the residual. The defect is twenty lines below, in `contribs`,
which listed market, sector, style, currency and idiosyncratic and **not α**.

So every decomposition this system has ever printed summed to the realised
return *minus the fitted drift*, and no line of output named the difference.
The residual is labelled `idiosyncratic`, which is a plausible enough name to
absorb a missing term without anyone asking what happened to it.

**The test knew.** `test_components_sum_back_to_the_realised_return` asserted
exactly this invariant, and passed, because its body read:

```python
total = sum(c.contribution for c in m.components if c.component is not Component.CURRENCY)
assert total + fit.coefficients[0] == pytest.approx(m.total_return_local, abs=1e-9)
```

It added the missing term itself. Somebody hit the gap, compensated for it in
the assertion, and left the test's name saying the components sum back to the
return. That is the family this belongs to: not a missing test, a test whose
compensation was the only place the defect was recorded.

Fixed by reporting the term: `Component.DRIFT` carries `coefficients[0]`, the
six contributions sum to `total_return_base` exactly, and the test no longer
adds anything by hand.

**What it cost, measured on the nine names of the book at 2026-09-14.** The
drift enters the `share_of_total` denominator, so every unexplained share falls
a little — Genting 68% → 62%, NVIDIA 76% → 73%, Apple 52% → 38% (the largest,
because Apple's residual was small and its drift was not). Those are corrections
rather than changes: the denominator had been missing a real term.

**And a second finding the fix produced, which corrects the journal page that
reported the defect.** `knowledge/feedback/2026-09-13.md` said the per-name
drifts were "economically legible" — Petronas Chemicals drifting up after a 19%
run, Genting drifting down in a downtrend. Carrying `Fit.intercept_se` says
otherwise. On 120-session windows all nine names' drifts are **0.1 to 1.3
standard errors from zero** and not one clears 1.96. The pattern was read into
noise. The estimation note now prints the standard-error count and the words
*not distinguishable from zero*, so the next reader is told rather than left to
find a story in a fitted intercept.

## 20. Three spellings of one state, and the source that decayed inside the gap

GDELT's article yield fell from 83–166 per run to **zero** between 2026-09-12 and
2026-09-14. Nothing alerted. The sweeps table said `ok` on every run, including
the ones that returned nothing.

`_news_per_instrument` computes `_mostly_failed` and the caller sets
`result.status = DEGRADED`. That status reached two places and not the third:

| surface | what it said | who reads it |
|---|---|---|
| console | `DEGRADED: 2 of 3 names could not be read` | a person watching a manual run |
| exit code | `3` | the collector step |
| **sweeps table** | **`ok`** — hardcoded at the `record_sweep` call | **every monitor rule, and every later question** |

Only the third is durable. The collector runs unattended and commits with
`[skip ci]`, so the console line went to a log nobody opens and the exit code to
a step that tolerates 3. §15 is the same family one seam over — a state recorded
two ways, one of which the watching rule cannot read.

Replaying `_mostly_failed` over the recorded details on 2026-09-15: **23 of 70
per-name sweeps were degraded by the code's own rule and stored as `ok`** — every
single one GDELT, spread across all twelve days the corpus has existed. The
durable record has never once said that source was degraded.

**Why the hardcoded `ok` was written, and what was right about it.**
`test_most_names_unreachable_is_degraded_and_exits_3_but_still_stores` asserted
it deliberately: *"articles were stored, so the row says ok"*. The premise is
correct and the conclusion does not follow. Storing articles means THE WINDOW WAS
READ, which is a fact about the watermark — `last_success` keys on `status = OK`
and is what a sweep resumes from. Writing `ok` spent the status column on the
watermark and left nothing to carry the health of the run.

Both facts fit, once they stop sharing one column: the status now records what
happened, and `last_success` accepts `OK` **or** `DEGRADED`, because a degraded
run did read the names it reached and holding the watermark back would re-fetch
that window every run without ever reaching the names that failed. `sweep_silence`
is unchanged and should be — a half-reachable source is not a silent one. Whether
half is enough is a different alarm reading the same column, and it could not be
written while every run was recorded as `ok`.

**The second half: the cause was collected and thrown away.** `_fetch_each` has
always returned `(name, reason)` pairs. `_sweep_note` did:

```python
parts.append("failed: " + ", ".join(t for t, _ in failed))
```

The reason went on the floor at the join. So the nightly page read `failed:
NVIDIA, Apple` — two companies having a quiet day — when what GDELT actually
answered was `HTTP Error 429: Too Many Requests`. A symptom with its cause
removed reads like bad luck. Notes now group by reason rather than by name,
because the reason is the finding and the names are how many it happened to:

```
failed: HTTP Error 429: Too Many Requests (NVIDIA, Apple)
```

Bounded by `REASON_CHARS` and `MAX_REASONS` so a source whose every name fails
differently cannot write a note as long as its book; the surplus is counted
(`+N more`), never silently dropped.

**The 429 itself is not a code defect and is not fixed here.** A live probe on a
runner (`sources-probe`, 2026-09-15) returned `GDELT fetch failed: HTTP Error
429: Too Many Requests`, and the same run shows `GDELT_USER_AGENT` and
`SEC_USER_AGENT` both empty. The plumbing is complete — `collect.yml` passes
both, `adapter.py` reads `GDELT_USER_AGENT`, `edgar.py` reads `SEC_USER_AGENT`
then falls back to it — so both sources are identifying themselves with the
generic default that carries no contact address, which is what GDELT and the SEC
each ask not to be sent. Setting the two repository secrets is the cheap test.
That it is a one-line configuration fix is the point of this entry rather than an
aside: the throttle was visible in the source's own reply from the first run, and
twelve days of it were recorded as `ok`.

## 21. A guard that read the calendar while the cron read the clock

§14's guard asked "has this slot run since 00:00 UTC". The cron it guards has
never fired on time here — 14 minutes to 6h34m late across September — and on
2026-09-22 Monday's 21:15 `us_close` arrived at **00:06:24 UTC on Tuesday**,
2h51m late, an ordinary night. The guard saw a fresh day with no `us_close` in
it. The catch-up had already collected Monday's close at 22:38, so the second
sweep was the doubled collection §14 exists to prevent, filed under Tuesday
(`da1298c`, "the 2026-09-22 us_close sweep").

It did not stop at the corpus. The paper mark that runs after the sweep, on a
build that still stamped the wall clock, wrote a `2026-09-22 us_close` mark at
00:09:01 holding Monday's closes (fx dated 2026-09-21), thirteen hours before
Tuesday's session opened; and because 2026-09-22 is the first day of the ramp,
that mark's phase was `ramp` and the control book's rebalance fired from it —
three targets, decided at 00:09:01, sized against Monday's closes rather than
Tuesday's. The decision day is the right one and the fills land at the first
bar after it either way; the sizing is the part the 09-22 paper page records.

And the fault runs forward. Tuesday's own 21:15 firing finds a `us_close` run
"today" and exits 0 as a repeat, and `--due` at 22:33 — which read the same
midnight — owes nothing: one close collected twice, the next not at all, with
every rule reporting a day that worked.

The fix keys both questions to the **firing**. `core.monitor.SLOT_TIMES` holds
the cron's four times beside `SLOT_WEEKDAYS`; `slot_window_start` returns the
slot's most recent scheduled firing at or before now. The guard skips a slot
only when a run is recorded since that firing (never more than 24 hours back,
so a `weekly` fired by hand on a Wednesday is judged on the day); `--due`
names a slot only once its firing today has come round and nothing has landed
since. The 00:06 arrival now sees the 22:38 run and stops, and Tuesday's 21:15
sees nothing since 21:15 and collects. `restamp_marks` gained the second shape
of the weekend rule: a mark on a session day taken before that session
**opened** cannot carry its bars and is re-dated to the last cached bar before
it, later reading stays — the open and not the close, because a mark taken
mid-session from a provisional bar is that day's mark and a later run replaces
it. collect.yml gained a `force` dispatch input, the override the guard's own
docstring promised and the form could not pass.

The price cache had the same midnight in it. The 00:08 collector refetched
every book name and each market's proxy, and `PriceCache.get` served a row for
the rest of the UTC day it was fetched on (`fetched_on == today`) unless its
last row failed to parse. The three US names and `^KLSE` came back from Yahoo
with Monday's row blank in the close column — the parser drops it, so those
rows read as mid-session and would have been refetched at 09:25. The Bursa
names came back complete through Monday: `5183.KL`, fetched-on Tuesday, last
bar Monday, and after Bursa shut at 09:00 UTC that row would have been served
to the bursa_close sweep at 09:25, to the paper mark behind it, and to every
read until Wednesday — Tuesday's Bursa closes never fetched on Tuesday. The
row was today's; the session was not. `get` now takes the market's MIC from
the feed and refuses a body pulled before that market's most recent session
close (`calendar.last_session_close`): the Bursa row pulled at 00:08 is stale
at 09:01 and served until then; a US row pulled at 00:08 is fresh until 20:00,
because no Nasdaq session shut in between. A caller that cannot name the
market keeps the day rule, which is the looser answer, never the fresher one.

A review of that fix the next morning found two more faults in it. The catch-up
still owed only firings since 00:00 UTC. On 2026-09-23 the routine itself ran
at 02:27 UTC, four hours late, and Tuesday's 21:15 firing, which main's guard
had skipped, was invisible to it: `slots_outstanding` returned nothing, the
same silence this section describes, arriving by a different door. It now owes
any firing of the last 24 hours with no run of that slot, or of `all`, since
it; at 22:33 that window holds exactly the day's own firings. And the
real-clock test of `sweep --due` recorded its run five minutes back, which
between 09:20 and 09:25 UTC lands before the `bursa_close` firing and fails a
correct command, the trap its own docstring describes at 00:02. It now records
the run at or after the firing.

The same morning's rehearsal of tonight's pipeline on this branch found the
third shape of the fault in the feedback pack. The paper book's fundable table
has labelled a price pulled before its market shut since 2026-09-16; the
pack's moves table never asked. Built for 2026-09-22 from `main`'s cache, it
decomposed NVIDIA, Apple and Microsoft from quotes pulled at 14:05 UTC, 35
minutes into the session, exactly as it would a close, which is also the
shape of the 1.12pp Apple discrepancy carried as an open question since
2026-09-17. `measure` now runs both legs through `price_state` and a
provisional leg marks the row and gets its own block under the table, beside
STALE NAMES and MIS-DATED; a leg whose fetch time is unknown is not labelled.
The 2026-09-22 pack now marks six rows STALE and three PROVISIONAL, which is
every row it has.

The paper book's fundable table had the mirror gap. It labelled a price pulled
mid-session but printed an older session's close under the heading "the last
close" with no date: the first ramp decision was sized on six Bursa prices from
Monday, eight hours after Tuesday's session shut, and the table could not say
so. `fundables` now compares each close with the latest session its market has
finished by the end of the day (or by now, when that is earlier) and the row says
`[the 2026-09-21 close; XKLS has since closed 2026-09-22]`, with a footnote
beside the provisional one.

Two open questions the nightly pages had carried since 2026-09-16 and
2026-09-18 turned out to be collector faults, not facts about the market.
FRED lists "FOMC Press Release" (release 101) on every day of its calendar,
weekends included; the collector stored each as a `macro_release`, so every
page's watch list carried an FOMC date on every row from 2026-09-07 and the
one real decision, the +0.25 in `DFF` on 09-17, could not be told from the
thirty that were not. A release listed on five or more days of the fortnight
is now a daily table: noted on the pull, not stored, and collapsed to one
line where rows already stored are read. And `DCOILBRENTEU` sat under the
DAILY freshness limit although EIA publishes its daily spot prices in one
weekly release: the monitor raised *past its cadence (8d, limit 7)* on
2026-09-23 with nothing wrong. It is WEEKLY now, like the H.10 rates.

Not changed: the control book's three 2026-09-22 targets. They are the record
of what the machine did, on the correct decision day; the page says how they
were sized.

## 22. A verdict that read as a search nobody ran

Every significant idiosyncratic row in the feedback pack has read
`no_identified_catalyst` - *significant idiosyncratic move; no catalyst matched
yet*. Three pages built on that sentence as if it reported a search. None had
been run. The sentence is a literal in `decompose`, which never looks at a
candidate; the pack calls `decompose` and nothing else. On 2026-09-18 Tenaga
carried the verdict on the one session the corpus held five dated rows about
the cause, and the page could only ask why the matcher had missed them. It had
not missed them. It had never been called.

The same sentence said "significant" of every residual past the 1.5 sigma at
which the cause hunt starts, while the `Significance` object beside it applies
1.96, a 5% test. Re-measured on 2026-09-23 from the current cache: Press Metal
on 2026-09-21 was 2.01 sigma and the page was right to call it the one
significant name. Tenaga on 2026-09-18 was 1.92 and Petronas Chemicals'
-6.71% residual on 2026-09-17 was 1.60. Neither clears the test, and both pages
called them significant. (The bars may have been refetched since those pages
were built, so these are today's figures, not a correction of the pages' own
numbers.)

`catalyst.attach`, the matcher's own verdict, had the adjacent fault: an empty
candidate list and a list weighed and rejected shared one reason, and that
reason ends *no-news moves of this size have historically tended to REVERSE*.
The reversal finding is about moves whose news was looked at and found
wanting. A name the collector held nothing about has not been looked at.

Now: `decompose` states the sigma and whether it clears 5%, and says no
candidate has been weighed; `attach` gives an empty list its own reason
(*an empty evidence set is not a rejection*) and gives a rejection the count
and the best score it rejected; the pack adds that it runs no matcher, so the
verdict means a company-specific cause is warranted, not that none exists. The
pack line also ends the reason with a full stop. It used to run into the next
field: *no catalyst matched yet Beta 1.04*.

Two faults of the same age surfaced while testing this, both in the
`qa/phase1` suite, which CI does not run. Its forward-record round trip logged
`--grade-on 2026-09-21` as a literal, and `log` refuses a grading date that is
not in the future, so the test had failed on its own setup since that morning.
`tests/test_learning_store.py` already warned about this exact trap in a
comment. And its stress assertion expected two standing notes when one had
been closed on 2026-09-08 by b738dab (config database paths are confined to
the project); it had been red for two weeks. The round trip also showed that
`predict.py log` printed a refusal (past date, impossible confidence, reused
id) as a Python traceback; it prints `refused: <reason>` and exits 1, as
`grade` already did.

## 23. A queued run started from the commit its trigger carried

The first two collections on the merged code, dispatched three seconds apart on
2026-09-23 (`us_close` to recover Tuesday's skipped close, then `bursa_close`),
were queued one behind the other by the workflow's concurrency group, as
intended. The second then checked out `dc6e822`, the commit its dispatch event
carried, not the branch as it stood when the first had finished and pushed
`8948539`. It collected for two minutes and failed at the commit step: eight
stores and digests conflicted, and binary SQLite files do not merge.

The commit step's failure was PR #76's change working: before it, the step
ended in `|| true` and would have reported success with the collection
dropped. The fault was upstream of it: `actions/checkout` without `ref`
checks out the event's SHA. The same applies to a scheduled run that queues
behind a dispatched one. The checkout now names the branch
(`ref: ${{ github.ref_name }}`), so a queued run starts from what the run
before it pushed, and `tests/test_collect_workflow.py` fails if that `ref` is
removed.

The group is not a queue, and the first version of this entry and of the runbook
said it was. GitHub holds one waiting run per concurrency group; a third arrival
cancels the waiting one (a review bot caught the claim on the fix's own pull
request). `cancel-in-progress: false` protects only the run already working. A
cancelled run records nothing, so its slot reads as missed and `sweep --due`
owes it for 24 hours; the runbook now says to dispatch no more than one slot
while another runs and another waits.

## What the families have in common

| Family | Shape |
|---|---|
| identity drift | two producers of a name disagree; nothing can hold an opinion |
| range violation | a value leaves its own domain and still type-checks |
| unit confusion | two numbers meet with nothing naming their units |
| declared-not-read | a field exists, is documented, and no code path reads it |
| docstring-as-spec | the comment promises a guarantee the code does not implement |
| no-record | the decision is taken, nothing is written, and the log reads as if it were never asked |
| stale-but-fresh | derived data is dated by when it was fetched rather than by what it contains |
| one-word-two-facts | a label collapses two conditions that ask the reader for different things |
| ask-vs-recognise | the system can identify something it never requests, so it never arrives |
| no-op-that-writes | a run that decided to do nothing still leaves a trace, and the trace reads as work |
| two-spellings-of-one-state | the same outcome is recorded two ways, and one of them the watching rule cannot read |
| cause-dropped-at-the-join | the failure's reason is collected and discarded while formatting, so the record names the symptom and not the cause |
| limit-before-filter | a bound is applied before the predicate, so the rarest rows are the ones that vanish |
| satisfied-by-noise | a rule counts arrivals, so junk that arrives clears the alarm the gap raised |
| never-asked-to-prove-it | a component is built to a sound argument, works exactly as described, and no measurement is ever asked whether it helps — so nobody notices it subtracting |
| compensated-in-the-test | the assertion adds the term the code forgot, so the invariant passes and the defect is recorded nowhere else |

Thirteen of the sixteen are invisible to a type checker and to a test that
only exercises the happy path. A fourteenth, `limit-before-filter`, is invisible to a
test whose fixture is smaller than the limit it is testing, which is why the
corpus tests missed it for as long as they had two articles in them. Those
fourteen are all visible to the same question: *what would the wrong answer look
like, and would I be able to tell?*

`cause-dropped-at-the-join`, the newest of them, is worth singling out for how
cheaply it hid: the question above was asked and answered correctly at the point
where the failure was CAUGHT — `_fetch_each` kept the reason — and then the
answer was discarded one function later while formatting a string. A diagnosis
is only as good as the last hand it passes through.

That question is what `stress/run.py` is, and it does not reach the last two.
`never-asked-to-prove-it` has no wrong answer to look for. The code is correct,
the types check, the happy path passes, the adversarial path passes, and the
component returns precisely what it promises — it simply does not help, and
nothing in the shape of the code can say so. The only instrument that finds it
is a labelled set of real questions with the right answers written down by a
human, run against the whole path, comparing each part against the system
without it. `knowledge/retrieval/data/retrieval_gold.yaml` is twenty-three such
questions and it took a year to acquire one of them. That is the cost of being
able to detect this family at all, and §18 is what one of them bought.

`compensated-in-the-test` evades it from the opposite side. There WAS a test
aimed straight at the defect - `test_components_sum_back_to_the_realised_return`
asserted the exact invariant §19 restores - and it passed, because the assertion
had been amended to add the term the code forgot. No instrument helps here: the
labelled set, the stress runner and the type checker all read a green suite and
a named invariant, and the only written record of the defect was the line of
arithmetic put there to work around it. The question that finds this one is not
asked by any test. It is asked by the person writing one: *am I fixing the code,
or the assertion?*
