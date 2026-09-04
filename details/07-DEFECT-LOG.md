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

## What the families have in common

| Family | Shape |
|---|---|
| identity drift | two producers of a name disagree; nothing can hold an opinion |
| range violation | a value leaves its own domain and still type-checks |
| unit confusion | two numbers meet with nothing naming their units |
| declared-not-read | a field exists, is documented, and no code path reads it |
| docstring-as-spec | the comment promises a guarantee the code does not implement |

Four of the five are invisible to a type checker and to a test that only
exercises the happy path. All five are visible to a test that asks *what would
the wrong answer look like, and would I be able to tell?*

That question is what `stress/run.py` is.
