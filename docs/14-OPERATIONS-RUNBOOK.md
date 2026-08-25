# 14 — Operations runbook

The build is finished. This document is what to do with it, what to watch, and
what should make you stop.

Everything below assumes the system is **not** yet making decisions you act on.
That is the correct state. The whole point of §2 is to earn the right to leave it.

---

## 1. The first hour, before anything else

```bash
make install          # uv venv + editable install
make test             # 404 tests, no network, no keys
make verify           # the whole pipeline on mock data, under a second
```

If those three pass, the system is intact. If `make verify` ever needs a network
connection or an API key, something has leaked across the adapter seam and that
is a defect, not a configuration problem.

**Do not turn on live data yet.** Wiring a feed before §2 is set up means the
first three months of outcomes are ungraded, and ungraded months cannot be
recovered — you cannot go back and ask what you believed in March.

---

## 2. The single thing that matters most: start the outcome clock

Everything else in this repository is machinery for producing a view. **P16 is
the machinery for finding out whether the views are any good**, and it is the
only part that cannot be compressed by working harder. Three months of forward
outcomes takes three months.

So the first real task is not analysis. It is **starting the clock**.

```python
from agents.learning.reflection import Horizon, OutcomeQueue, Prediction

queue = OutcomeQueue()
queue.enqueue(Prediction(
    prediction_id="2026-08-25-maybank-nim",
    instrument_id="MYX:1155",
    agent="a10_thesis",
    made_at=now,
    horizon=Horizon.D63,           # fixed NOW, never revised later
    statement="NIM stabilises above 2.25% and the multiple re-rates",
    direction=+1,
    confidence=0.62,               # your honest number, not a flattering one
    grade_on=date(2026, 11, 26),   # computed from the horizon, in the future
))
```

Rules that make this worth doing, all enforced in code:

- **The horizon is set before the outcome.** `Prediction` refuses a `grade_on`
  that is not in the future. A horizon chosen after the fact is not a horizon.
- **Grading early is refused.** `OutcomeQueue.grade` raises if you call it before
  `grade_on`. A 63-day call scored on day 4 is noise wearing a track record's
  clothes.
- **Correctness is measured against a benchmark**, not against zero. Being up 6%
  in a month the index rose 8% is being wrong.

Log **every** view, including the ones you do not act on and the ones you later
feel embarrassed by. A prediction log with the losers quietly missing is worse
than no log, because it produces confident, wrong calibration.

**Minimum useful volume: 30–50 graded predictions.** Below that, the calibration
table is measuring luck. At roughly two or three logged views a week, that is the
three-to-six months P16 asks for.

---

## 3. What to actually do, in order

### Month 1 — run it read-only

Ask it questions you already know the answer to. You are testing the *system*,
not the market.

| Ask | What a healthy answer looks like |
|---|---|
| "Why did X fall today?" on a broad down-day | `market_driven`, no cause hunted |
| "Why did X fall today?" after real news | a named catalyst with its score and the unexplained share |
| "Why did X move?" on an ordinary day | `not_significant` — no story at all |
| "Should I buy X?" with thin evidence | gaps named, stance `no_view` |
| "What will X be worth in December?" | refused, with the reason |

**The refusals are the product.** If it never refuses, something is broken —
check the guardrail chain before you trust a single answer.

### Month 2 — wire one live feed, not five

`GdeltFeed._fetch_raw` is the only method a live source needs. Subclass
`FeedAdapter`, implement it, and every downstream rule — dedup, entity linking,
feature extraction, freshness gating — applies automatically.

Start with **one** free source and run it beside the fixture feed for two weeks.
Compare: does the live feed produce duplicates the dedup misses? Entities it
cannot link? Items the freshness SLA should have dropped?

### Month 3 — build the base rates that make catalysts mean anything

The catalyst scorer needs `BaseRateTable` populated for **your** markets. An
empty table means every candidate scores on prior alone, and the six-factor score
collapses to "was there news that day", which is exactly the failure mode the
design exists to prevent.

Target: **at least 30 observations per `event_type × market × cap_band × surprise`
cell** you actually use. `BaseRate.thin` flags the cells that are not there yet,
and the agents surface that caveat — do not suppress it.

### Month 4+ — only now, small real positions

Not because the system has proven itself; four months is not proof. Because
paper trading stops teaching you anything once you stop feeling it. Size so that
being completely wrong is boring.

The floor from `docs/05 §3.5`: on Bursa the **minimum economic position is about
RM 4,700**. Below that, costs eat the thesis regardless of how good it is.

---

## 4. What to monitor

### 4.1 Weekly — five minutes

| Signal | Where | Healthy | Act when |
|---|---|---|---|
| Pending predictions | `queue.pending_count()` | growing steadily | flat for 2 weeks — you stopped logging |
| Overdue gradings | `queue.due(today)` | empty after your review | anything sits >1 week |
| Portfolio heat | A12 `concentration` finding | under 6% | any breach finding appears |
| Effective bets | A12 `effective_bets` | ≥ 5 | below 5 while HHI looks fine — the dangerous case |
| Drawdown tier | A12 `drawdown` | scalar 1.0 | scalar < 1.0 — the tier is mechanical, do not override it |
| Breakers due | daily brief | reviewed on date | a breaker passes its date unreviewed |

### 4.2 Monthly — thirty minutes

| Signal | Where | Healthy | Act when |
|---|---|---|---|
| **Brier score** | `A15Reflection.calibration()` | flat or falling | rising 3 months running |
| **Overconfident bands** | `Calibration.overconfident_bands()` | empty | any band with n≥5 stating >10pp above realised |
| Unexplained share | A9 findings | varies | consistently <20% — the model is fitting noise, not explaining it |
| `no_identified_catalyst` rate | A9 verdicts | 20–40% of significant moves | near 0% — it is inventing causes |
| Refusal rate | A0 plans | some | zero — the guardrails are not firing |
| Lessons written | `LessonStore.active()` | 0–2 per quarter | more than ~1/month — the gate is too loose |
| Cost per answer | provenance ledger, MYR | within `docs/08` tier | 2× the plan for 2 weeks |
| Thin base rates | `BaseRate.thin` count | falling | rising — coverage is decaying |

### 4.3 The four numbers that decide whether to keep going

After roughly 50 graded predictions:

1. **Brier score vs. the base rate.** Beating a naive "always predict the base
   rate" forecaster is the minimum bar. Not beating it after 50 calls means the
   evidence layer is not adding information.
2. **Calibration in the 60–80% band.** This is where real decisions live. If you
   say 70% and realise 45%, the system is confidently wrong and position sizing
   built on it is dangerous.
3. **Deflated Sharpe, not Sharpe.** `engines/backtest/metrics.py`. If you tried
   20 variants, a 0.37 Sharpe deflates to roughly zero. Always report the trial
   count honestly; lying to yourself here is the single most common way people
   lose money with a backtest.
4. **Realised cost vs. modelled cost.** If fills come in materially worse than
   `engines/backtest/costs.py` predicts, every backtest above is optimistic and
   needs rerunning before it means anything.

---

## 5. What should make you stop

Stop means: no new positions, review before continuing. Not "try harder".

- **Drawdown ≥ 25%.** `DRAWDOWN_TIERS` sets the risk scalar to zero. This is
  mechanical and not overridable by conviction — that is the entire point of
  putting it in code rather than in a prompt.
- **Calibration inverted.** High-confidence calls resolving worse than
  low-confidence ones means the confidence signal is actively misleading, which
  is worse than having none.
- **Three consecutive months of rising Brier.** Something in the world changed,
  or something in the pipeline broke quietly. Find out which before trading it.
- **You stopped logging predictions.** The moment the log lapses, everything
  downstream — calibration, lessons, the reliability curve — is measuring a past
  that no longer describes you.
- **You overrode a cap.** Once. Any cap. The caps only work if they are not
  negotiable, and the first override is always the reasonable-seeming one.

---

## 6. Failure modes specific to this system

Six real defects surfaced during the build (`docs/05 §3.5`). Five were caught by
tests. The sixth is the one to internalise, because it was silent:

> Six agent ids drifted from the registry. Nothing crashed. A10 reported two
> evidence gaps that were in fact covered, docked its own confidence by 0.24 on
> **every** thesis, and the red team raised a coverage challenge every single
> time — which carries exactly as much information as never raising one.

**The lesson generalises: in a system whose job is to express uncertainty, a bug
does not look like a crash. It looks like a slightly-too-humble answer, forever.**

So the things to watch for are the ones that look like modesty:

| Looks like | Might actually be |
|---|---|
| Consistently low confidence | phantom evidence gaps |
| The red team always objecting | a challenge that fires unconditionally |
| Every move "explained" | catalyst scoring with no base rates behind it |
| No refusals | a guardrail rail silently skipped |
| Lessons accumulating fast | the inverted gate stopped inverting |

Run `make verify` after any change to the agent layer. It walks all fourteen
sections and takes under a second — it exists precisely to catch this class of
silent degradation.

---

## 7. If you hand this to someone else

Point them at, in order: this document, then `README.md`, then
`docs/03-WHY-IT-MOVED.md` (the differentiator) and
`docs/05-RISK-AND-GUARDRAILS.md` (the part that protects them).

Tell them the three rules that are not negotiable:

1. **No execution code, ever.** Enforced three ways, and the repo-wide grep has
   a rot check on its own exemption list. It is a one-way door by design.
2. **Nothing registers without an eval suite carrying negative cases.** A suite
   where every case expects an answer measures enthusiasm, not skill.
3. **A claim without a citation is dropped, not softened.** Per claim, not per
   answer — a good paragraph does not get to carry one invented sentence.

---

## 8. Honest limitations

- **No live track record exists.** Nine of ten done-ness criteria are met; the
  tenth is a reliability curve that only time produces. Nothing here has been
  tested against a market.
- **Base rates ship empty.** Until you populate them, catalyst scores lean on
  priors and are weaker than the design intends.
- **Two markets.** XKLS and XNAS. A third is one adapter class plus a registry
  entry, and the conformance tests tell you when it is right.
- **The teacher's curriculum is 30 concepts, not a course.** It enforces order
  and names misconceptions. It does not replace reading.
- **Costs are planning estimates** at RM 4.15/USD, not measured bills. The
  provenance ledger records actuals from day one — compare monthly.
