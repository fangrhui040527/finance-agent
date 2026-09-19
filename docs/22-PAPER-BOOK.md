# 22 — The paper book

A USD 1,000 hypothetical ledger, opened 2026-09-08, marked from the cached daily
bars at both closes, with target weights recorded nightly inside caps the code
enforces, and a passive equal-lot control beside it. It exists to produce the
one thing the rest of this repository cannot: thirteen weeks of forward,
graded, costed record — the P16 gate's evidence (docs/07).

## 1. What it is, and is not

It is arithmetic over prices the collector already cached. `engines/paper/`
records target weights, applies them to a make-believe account at the next
cached open with the real moomoo fee card, the recorded BNM rate and a stated
slippage, and marks the result to market. It sends nothing anywhere, imports
nothing from `core/broker`, and has no field for a gateway;
`tests/test_paper_boundary.py` fails if that import ever appears.

It chooses weights among the nine names *you* listed in `[account] watchlist`.
It does not search for names. It is not a recommendation: the OUTPUT rail
still refuses advice verbs, and the journal is held to "bands, never verbs".

## 2. The honest answer on "+10% in three months"

- **+10% in one quarter is about 46% annualised.** Long-run equity returns are
  7–10% a *year*. Under the conservative caps (at most 80% invested, 25% per
  name) the held names would need to rise 12–13% net of costs in sixty
  sessions. Some names do that in some quarters; no process can promise it.
- **Costs bite at USD 1,000.** On the real moomoo cards a round trip costs
  0.9–1.2% of the ticket on most names, about 2% on Petronas Chemicals and
  over 4% on Genting (a flat RM 3 platform fee on a RM 203 lot), plus 1% of FX
  spread on every ringgit leg. The turnover cap exists for this reason, and
  `ask.py paper status` prints cost drag to date.
- **Sixty sessions cannot prove skill.** The standard error of a quarterly
  return at this sample size is several percent; a +10% quarter would be
  indistinguishable from luck. The quarter is a **calibration** period. Its
  deliverable is the record — hit rate and Brier score of the logged
  predictions, drawdown, cost drag, decided minus control — not the return.
- **The hypothesis under test**, logged with `log_hypothesis`: *the decided
  book beats the passive control by at least two percentage points over
  thirteen weeks with a maximum drawdown of 8% or less.* Testable, and honest.

## 3. Accounts, currency, marks

Two books in one file (`data/paper.db`, append-only like every store here):
`decided` and `control`, each opened with USD 1,000. Base currency USD. Every
MYR leg converts at the BNM reference rate the collector records daily
(`data/fx.db`), as of the day, falling back to `[account] fx_myr_per_usd` and
saying so (`fx_source`). Entries buy ringgit at `rate × (1 − 0.5%)`, exits
sell it at `rate × (1 + 0.5%)`; marks use the mid and say so.

Each position is marked at its own last cached close on or before the mark
day, so a Bursa name and a Nasdaq name in the same mark can carry different
close dates; the mark records both. A leg whose close is more than three
weekdays old is flagged stale; a leg with no bar at all is carried at its
last stored close, flagged, and `mark` exits 3.

A mark's day is the **session** of the bars it was marked from — the last
cached bar for the slot's market (`bursa_close` XKLS, `us_close` XNAS) on or
before the day asked for — never the wall clock. A catch-up run on a Saturday
marks Friday's session; a run on a holiday marks the session before it. A
slot marks a book at most once per session day: a later run of the same
session replaces the earlier mark in place, because a later fetch is closer
to the close, and that replacement is the one UPDATE the ledger's guard
permits. A day the calendar calls no session, with no cached bar to mark
from, is refused (exit 2). Marks written before this rule (2026-09-19) sat on
weekends; `mark` re-dates them onto their session from the cache on its next
run and says what it moved. The calendars carry weekends only until the
holiday tables land, so a Labor Day `us_close` mark stays on the Monday.

## 4. Position changes

A decision recorded on day D applies at each market's **first cached bar
after D** — Bursa at its 09:00 MYT open, Nasdaq at its 09:30 ET open — at the
bar's open plus slippage (10 bps XKLS, 5 bps XNAS), rounded to the tick
against the book. Whole US shares, 100-share Bursa lots: `units = floor(w × E
/ lot_usd) × lot`, where E is the equity the decider saw. Exits first (stops,
then reductions), then entries by descending weight. An entry that would
breach the cash floor shrinks one lot at a time; at zero it is skipped and the
ledger says why. A target with no bar within five weekdays expires unapplied.
Nothing is ever priced at decision time: a daily-bar system does not know that
price.

Fees are per leg from `markets/brokers.py`: an entry pays the symmetric legs,
an exit pays everything (SEC and FINRA land on the exit), and a test pins
`entry + exit == round_trip`.

## 5. Caps and the phase calendar

Enforced by code at decision time and again at application; a decision that
breaches any cap is refused with every breach named, and nothing is written.
`HARD_BOUNDS` in `core/config.py` lets `config.toml` tighten these and never
loosen them.

| cap | conservative profile |
|---|---|
| per name | ≤ 25% of equity |
| invested | ≤ 40% in weeks 3–6, ≤ 80% (a 20% cash floor) from week 7 |
| names | ≥ 4 once invested weight exceeds 40%; below it `ceil(invested / 25%)` — the arithmetic minimum, because four whole lots do not fit under USD 400 |
| stop | close ≤ average cost × 0.92 in the name's currency → exit at the next open |
| halt | drawdown from peak ≥ 8% → no target may raise a weight; reductions and stops still run |
| turnover | consideration changed over five weekdays ≤ 50% of equity; stops and reductions count but are never blocked |

The calendar from `[paper] start_date`: weeks 1–2 **observe** (decisions are
recorded and graded, never applied; both books hold cash), weeks 3–6 **ramp**,
week 7 on **full**. Week 14 adds "P16 review due" to the status page; the caps
do not change.

### The fundable set

`ask.py paper status` prints, for every watchlist name, one lot in USD at the
last close, its share of equity, how many lots the per-name cap allows, and
the round-trip cost. A name whose lot exceeds the cap is *not fundable at this
equity*; a weight below one lot is refused. At USD 1,000 and September 2026
prices five of the nine names are holdable (Petronas Chemicals, IHH, Press
Metal, Genting, NVIDIA) and four are not (Maybank, Tenaga, Apple, Microsoft).
The levers for the other four are a larger paper account or a looser per-name
cap, not fractional shares.

## 6. The control book

Same money, phases, cash floor, lots, fees, FX and slippage; no judgement, no
stops, no halt, no turnover cap. On the first mark of each calendar month (and
of each new phase) it re-targets a greedy equal-lot spread of the fundable set,
cheapest lot first, one lot per name per round while the name stays under the
per-name cap and the total under the phase ceiling. Its targets apply at the
next bar like everything else.

## 7. Predictions and grading

Every raised weight is logged as a `+1` prediction and every exit to zero as
`−1`, agent `paper`, in the same log `predict.py` uses, with the horizon's
grading date fixed at decision time. `ask.py paper grade` scores each one when
its date comes: realised is the name's USD return from the price the book got
(or the decision-day close in the observe weeks), benchmark is the control
book's return over the same days, and the queue refuses anything early. The
thirty graded calls that `min_graded_for_calibration` asks for arrive around
week ten to thirteen at three decisions a week — which is why the review sits
at week thirteen.

## 8. The nightly loop

| UTC | what |
|---|---|
| 09:20 | `collect.yml bursa_close`: prices cached, then `ask.py paper mark --slot bursa_close` and `paper grade`; committed with the sweep |
| 21:15 | `collect.yml us_close`: the same for the US close |
| 22:30 | the Routine (docs/20): pulls, writes the feedback page, then `paper status`, `paper pack`, the paper journal page, `paper decide`, commits and pushes |

Two writers share `data/paper.db` through git in disjoint windows; the
Routine pulls before it decides and pushes after. `decide` refuses a second
decision for the same day, so a retried run is safe.

### An all-cash night is a decision

Holding nothing writes one target row — instrument `CASH`, reason `all_cash`,
weight zero, resolved the moment it is written so no market is ever asked to
price it — and one prediction. Until 2026-09-09 it wrote nothing at all, and
the book could not tell a night decided all-cash from a night nobody asked
about; `decide` still printed "this is logged and will be graded" over the
empty write. The first night of the book, 2026-09-08, was such a night.

It is graded like any other prediction, against the control rather than
against a price: realised is the deciding book's own return over the horizon,
benchmark is the control's over the same days, and holding nothing was right
exactly when the control lost ground. That is the claim an all-cash night
makes — *nothing in the fundable universe beats cash over the horizon* — and
it is the only one it makes. A second decision the same day still needs
`--supersede`.

## 9. Reading the status page and the pack

The status page is the decider's whole view: equity, cash, peak, drawdown and
halt; the control's equity; positions with their close dates; pending
targets; each cap's value against its limit, drift included; the fundable
table; turnover used and headroom; the FX quote and its source; cost drag.
The pack (`ask.py paper pack`) adds the day's position changes with fees, both
books' marks and day returns, the attribution of each held name's move
through `engines/attribution`, and the predictions due and graded. The journal
page (`knowledge/paper/README.md`) copies numbers from the pack and estimates
nothing.

## 10. Verification

Offline and keyless: `tests/test_paper_*.py` cover the append-only triggers,
FX as-of and spread arithmetic, per-leg fees against the round trip, tick and
lot rounding, every refusal message, next-bar application, cash to the cent
in both currencies, stops, the halt, the control's greedy fill, grading, and a
thirteen-week scripted replay on a synthetic feed asserting `cash + positions
== equity` after every mark, fees summing to cost drag, turnover under the
cap, and no entry while halted. `stress/run.py` calls both MCP tools on an
empty ledger; `ask.py doctor` fails if the store loses its triggers.

## 11. The week-13 review

On or after 2026-12-07: read `paper_report`, the calibration table and the
journal's lessons. Three outcomes are honest — continue paper with the same
caps, continue with tighter ones, or stop. Real money is not one of them; that
question belongs to docs/14 and to a record longer than one quarter.
