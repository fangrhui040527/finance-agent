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

`_already_ran_today` (§ the doubled slot) makes the second collector arrival on
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

Nine of the ten are invisible to a type checker and to a test that only
exercises the happy path. All ten are visible to a test that asks *what would
the wrong answer look like, and would I be able to tell?*

That question is what `stress/run.py` is.
