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

```bash
# log a view. instrument, direction, horizon, confidence, statement
python predict.py log MYX:1155 +1 63d 0.62 "NIM stabilises above 2.25%"

python predict.py due        # what has reached its horizon
python predict.py grade 2026-08-25-myx1155-8c2c --return 0.031 --benchmark 0.048
python predict.py status     # the calibration table

make due                     # the same two you will run most
make status
```

Everything lands in `data/learning.db` and survives restarts. The file is
gitignored: your prediction log is yours and never leaves the machine.

Rules that make this worth doing, all enforced in code:

- **The horizon is set before the outcome.** `Prediction` refuses a `grade_on`
  that is not in the future. A horizon chosen after the fact is not a horizon.
- **Grading early is refused.** `OutcomeQueue.grade` raises if you call it before
  `grade_on`. A 63-day call scored on day 4 is noise wearing a track record's
  clothes.
- **Correctness is measured against a benchmark**, not against zero. Being up 6%
  in a month the index rose 8% is being wrong, and `predict grade` will say so.
- **Nothing can be edited or deleted.** SQLite triggers refuse an `UPDATE` on a
  logged prediction and a `DELETE` on any of them. A log you can revise is a
  memory, and a log missing its losers produces confident, wrong calibration.

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

Point them at, in order: `docs/user-guide.html` (install and cadence), this
document, then `README.md`, then
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

---

## Added in the 2026-08-31 hardening pass

- `python ask.py doctor [--offline]` - preflight with named impact per check;
  CI runs it offline on every push.
- `FINPLANET_LOG=INFO|DEBUG` - stderr logging for every entrypoint (a healthy
  run stays silent at the default WARNING).
- `FINPLANET_CHEAP=1` - every Messages tier resolves to the cheapest model,
  billed at its own rate; disclosed by `ask.py backend`, the doctor, and the
  web Overview. This is the standing rule for live testing.
- `debug/` traces are pruned automatically (newest 20 kept, 14-day cap) -
  retention is a privacy control, the traces hold verbatim prompts.
- docker-compose binds loopback only and refuses to start without passwords
  in `.env` (no more shipped defaults).
- The web app: `make web` / `run web`, thirteen screens on 127.0.0.1:8765.

---

## Alerting: rules that fire without being asked

`ask.py watch` evaluates a small set of rules against the ledger and the
traces, and records every state CHANGE to `data/alerts.db` (append-only, like
every other record here). A rule that stays tripped writes nothing new - an
alert repeating hourly is noise a person learns to ignore, which is worse
than silence.

Exit codes are the interface, so a scheduler can act without parsing text:

| code | meaning |
|---|---|
| 0 | nothing open |
| 1 | at least one rule is open |
| 2 | the check itself could not run - the failure a monitor exists to catch |

The rules, all thresholds in `config.toml [monitor]` and bounded in code:

| rule | fires when |
|---|---|
| `spend_24h` | 24h spend crosses `spend_fraction` of the daily budget |
| `latency_p95` | p95 latency over 24h exceeds `p95_latency_ms` |
| `dropped_claims` | claims dropped for want of a citation exceed `dropped_claim_rate` |
| `silence` | no model calls in `silence_hours`, on a ledger that HAS run before (0 = off) |
| `sweep_silence` | no successful sweep in `sweep_silence_hours`, for a source that HAS succeeded before (0 = off) |
| `slots_missed` | the collector fired fewer times than its own cron owes over `slot_window_days` whole days (0 = off) |
| `series_stale` | a macro series' newest observation is past the cadence declared for it in `knowledge/sources/freshness.py` |
| `series_resumed` | a series recorded as ENDED in that same file has printed past the period its upstream stopped at |
| `open_question_stale` | a question the nightly pages carry has stood for more than 21 days |
| `run_errors` | the newest traced run contains an error event |
| `methodology_changed` | the manifest hash moved between the last two runs |

`series_stale` reads the AGE OF THE DATA, not the health of the fetch, and it
exists because the two came apart: on 2026-09-06 sixteen DBnomics series were
432 to 493 days old and Malaysian CPI read 1982, while every sweep beside them
reported `ok`. A stopped upstream and a working one are identical in the sweep
table. The cadence per series - daily, weekly for the Fed's H.10 release,
monthly, or a policy rate's meeting schedule - is declared in
`knowledge/sources/freshness.py`; a series with no entry there is not judged,
and `ask.py macro` marks each row with its age so a stale figure is labelled
where it is read, not only where it is alerted.

A series whose upstream has **stopped** is a different fact from a late one, and
`series_stale` does not report it. The 2026-09-06 probe found the datasets
behind fifteen DBnomics ids frozen with no live sibling code to move to; that
verdict lives in `freshness.ENDED`, the rows read `466d ENDED 2025-06`, and
`ask.py macro` names each stopped upstream under the table. An alert that
reopens nightly with the next step "buy macro data somewhere else" is not a
change worth reporting, so the marking is what carries it. `series_resumed` is
the check that keeps the marking honest: it fires when one of those ids prints
again, and asks for the `ENDED` entry to be deleted so the cadence rule takes it
back. That is why the collector still fetches all fifteen frozen series.

`open_question_stale` reads the pages the nightly routine writes. Each carries
its open questions forward with the date first asked - and until 2026-09-06
nothing in the code read them, so a question that had stood for a fortnight was
the same prose in the same list as one asked yesterday. `ask.py pack
--questions` lists them oldest first, with how many nights each has been
carried, and flags any asked under a name that the page forgot to carry. A
question still open after three weeks is rarely a hard question: it is usually
a source nobody wired. Answer it, or write on tonight's page why it cannot be
answered and stop carrying it - a question is open exactly while the writer
keeps carrying it.

`silence_hours` is off by default because a personal tool is allowed to sit
idle. **Turn it on the moment anything runs on a timer**: a job that dies
quietly looks exactly like a quiet week, and telling those two apart is the
whole point.

### Scheduling it

Windows Task Scheduler, hourly:

```
schtasks /create /tn "finplanet-watch" /sc hourly /st 00:05 ^
  /tr "cmd /c cd /d C:\path\finance-agent && .venv\Scripts\python.exe ask.py watch >> data\watch.log 2>&1"
```

`cmd /c cd /d ...` is not optional. `schtasks` has no "Start in" flag without
an XML definition, and `ask.py watch` resolves `data/alerts.db` relative to the
working directory. Started elsewhere it does not fail - it creates a second,
empty alert store beside wherever the scheduler happened to be, and reports a
quiet system because it is reading a file nothing writes.

cron, hourly:

```
5 * * * * cd /path/finance-agent && .venv/bin/python ask.py watch >> data/watch.log 2>&1
```

### The nightly sweep

`ask.py watch` tells you the system is healthy. `ask.py sweep` is what gives it
something to be healthy about: it fetches every enabled source and keeps what
arrives, so the corpus and the graph grow while nobody is looking.

Windows Task Scheduler, daily at 06:10:

```
schtasks /create /tn "finplanet-sweep" /sc daily /st 06:10 ^
  /tr "cmd /c cd /d C:\path\finance-agent && .venv\Scripts\python.exe ask.py sweep >> data\sweep.log 2>&1"
```

cron, daily at 06:10:

```
10 6 * * * cd /path/finance-agent && .venv/bin/python ask.py sweep >> data/sweep.log 2>&1
```

`cd /d` is not optional here for the same reason it is not optional above:
`ask.py sweep` resolves `data/corpus.db` relative to the working directory.
Started elsewhere it does not fail — it writes a second, empty corpus beside
wherever the scheduler happened to be, and every night's watermark is missing,
so every night refetches the same window.

**The rule that watches this is `sweep_silence`, not `silence`.** Reaching for
`silence_hours` here is the obvious move and it is the wrong one: it counts
MODEL calls, and `ask.py sweep` makes none. Turned on for a sweep-only schedule
it fires every single morning after a run that worked perfectly — and it stays
silent through a sweep that has been dead since Tuesday, as long as you asked
the system a question yesterday. Two ways of being wrong, in opposite
directions, from one plausible setting.

`sweep_silence_hours` asks the same question of the record the sweep itself
writes. It ships at 30 — a daily schedule plus six hours of slack, so one late
run is not an alert and a missed day is — and it stays quiet until a source has
succeeded once, because a corpus nobody has filled yet is a system nobody turned
on rather than one that stopped.

Leave `silence_hours` at 0 unless something SCHEDULED also calls a model.

**And `sweep_silence` cannot see a collector that is merely unreliable.** It
reads the newest success per source, so ANY run resets it for every source at
once — including one you fire by hand. On 2026-09-07, checked at 14:20 UTC,
neither the 09:20 nor the 12:30 slot had produced a run, a manual sweep at 08:50
had already reset the clock, and `ask.py watch` was clean: a day that had lost
two of its three collections read as perfectly healthy.

The 09:20 slot did arrive — at 15:03, **five hours and forty-three minutes
late**. That is the point rather than a reprieve: while you are waiting, lateness
on that scale is indistinguishable from loss, and the wire feeds serve a recent
window whether or not the runner eventually turns up. It is also why this rule
counts by whole days — a slot delayed most of a day still lands on the day it
was owed, so a late collection is not reported as a missing one.

`slots_missed` counts instead of timing. The cron owes a known number of firings
— `bursa_close` and `us_close` daily, `us_preopen` Mon–Fri, `weekly` on Sunday —
and the sweeps table now records which slot each run was, so the gap between owed
and arrived is the alert.

**The total is the signal, not any one slot.** This failure spreads itself thin:
a bad day loses one firing from each of three different slots, so a per-slot
threshold sees three ones and reports nothing. Summed, that day is three missing
collections out of four owed. Per-slot counts still appear in the alert, because
they say which part of the day is being dropped, and each slot is capped at what
it was owed so three `bursa_close` runs cannot pay for a `us_close` that never
fired. Four rules keep it honest:

* **Whole UTC days, both ends.** Anchored to the clock instead of midnight, a
  five-day check reported one missing firing on every daily slot for a collector
  that had missed nothing: the oldest day's runs fell before the start while the
  day itself was still owed. Today is excluded — a slot that has not come round
  yet is not a slot missed.
* **A shortfall of one is not a finding.** A 21:15 run delayed three hours lands
  on the next UTC day; `SLOT_SHORTFALL_MIN` is 2 so that is never read as a
  fault.
* **It judges only days the store can answer for.** Rows written before the slot
  column existed carry `''`, and the corpus is append-only, so the window starts
  at the first recorded slot rather than counting that history as misses.

* **A run by hand saves the data, not the schedule.** `--slot all` collects every
  source, so nothing is lost, and the alert says so — but it is never counted as
  a scheduled firing. A person firing the collector every morning because the
  timer stopped is the fault being reported; letting the repair silence the
  alarm is how it stays broken.

### The catch-up: the Routine dispatches what the cron dropped

`slots_missed` reports the failure the morning after. `ask.py sweep --due` is
the other half — it asks what is still owed **today**, while a replacement run
is still worth firing:

```
$ ask.py sweep --due
us_preopen
us_close
```

One slot per line on stdout, reasons on stderr, exit 0 always, and it collects
nothing. That is the contract a loop depends on:

```
for slot in $(ask.py sweep --due); do ask.py sweep --slot "$slot"; done
```

It is a **different question** from `slots_missed`, which judges whole finished
days — a slot that has not come round yet is not a slot missed, but it is very
much still owed. Three ways it declines to name anything: a `--slot all` run has
already covered the day; the store has never recorded a slot *and* something ran
today, so the run cannot be attributed and firing again would be guessing; or
nothing is owed. A store that has recorded no slot and saw **no** run today does
report the day's schedule — "I cannot tell which one ran" and "nothing ran at
all" are different answers, and only the second is worth acting on.

**The nightly Routine fires this at 22:30 UTC**, after the last slot of the day.
That is deliberate: the Routine runs on a scheduler that has never missed a
firing, while GitHub's cron on this repository has delivered somewhere near half
of what it owes. The cron stays primary, and the Routine only dispatches
`collect.yml` for the slots the day is actually short of.

#### One slot, one collection a day

`--due` cannot tell a **dropped** cron from a **very late** one. On 2026-09-07 it
called `us_close` owed at 22:36, 78 minutes past due; the catch-up collected it
at 22:38; and the cron itself then arrived at **23:31, 2h17m late** and swept the
same slot again. Two full sweeps, 370 requests, for one slot.

Waiting longer does not fix it. This repository's cron has been observed between
14 minutes and 5h43m late, so a grace window wide enough to be safe would push
every catch-up past the Routine's own fire and into the next day — and news
collected tomorrow is not news. The dispatcher genuinely cannot know, at the
moment it must decide, which of the two it is looking at.

So the guard is at the **collector**, where the question is settled rather than
predicted: `run_sweep` refuses a named slot that already has a run recorded for
the current UTC day. Whoever arrives first collects; the second arrival — cron or
catch-up, in either order — exits **0** in seconds having contacted nothing. Exit
0 and not 2, because a slot that already ran is a no-op, not a fault, and a
scheduler told otherwise would raise an alarm about a day that worked.

Three ways past it, each an explicit act by a person: `--slot all` (the recovery
hammer, never guarded), a named `--source`, or `--force`. And a store whose rows
carry no `slot` at all is **not** read as having run — that column landed on
2026-09-07, and treating older rows as prior runs would refuse every slot on any
store written before that build.

Same-day recovery is most of the value: **news is the only thing that expires.**
Prices, filings and macro series are re-fetchable tomorrow; a wire feed serves a
recent window and nothing brings back the hours it has rolled past.

Two shapes of failure look different in the run history and want different
fixes: a run **created and never given a machine**
(seconds long, no log) is the Actions minutes cap; **no run at all** is GitHub
dropping the schedule under load. News is the only loss that cannot be
recovered — the wire feeds serve a recent window only.

### Is a source covering the name it is asked for?

An article count is not coverage. A per-name source is asked for one company at
a time, and what comes back may be about that company or about nothing in
particular - and only the first number was ever visible. Measured on this
corpus on 2026-09-06: GDELT had returned 575 articles across nine companies and
457 of them named no book company at all. "575 collected" and "118 about the
book" are different facts, and the sweep row said only the first.

Every article now records the instrument that FETCHED it - provenance, not
attribution: GDELT is asked a phrase and answers from a full-text index this
corpus never sees, so a story it returned for "Apple" may be about a brothel
sale, and calling that Apple's evidence would be the fetch talking. Only a
source keyed by TICKER (Yahoo's per-symbol feed) may also assert the name.

```
ask.py sources --coverage           # every source/name pair, worst share first
ask.py sources --coverage --days 7  # just the last week
```

Each sweep row also carries `named the company: N of M` with the three worst
names. A source whose share stays near zero for a name is not covering it, and
that is a reason to drop the name from that source rather than to read its
volume as coverage.

#### And a headline naming nobody is not indexed at all

Measured 2026-09-08, and the diagnosis above needed one correction. GDELT's DOC
API answers in `artlist` mode: a headline, a URL, metadata, and **no article
text**. All 667 GDELT rows in the corpus have `body == title`, about 73
characters each. So GDELT's precision was never the defect and cannot be judged
from this store — it matched the company deep inside a page the corpus does not
hold. The defect is that **a headline was being stored and counted as an
article**.

Such a row is unusable in both directions: it cannot be retrieved for a name it
does not mention, and it cannot be cited for a claim it does not make. What it
can do is take one of the ten places in every result list. 539 of them — **32%
of the corpus** — were doing exactly that, and the search had been getting
worse as the collector worked:

```
                        1,397 chunks     1,673 chunks     filtered
  recall@10                 66.7%            58.3%          66.7%
  plain-English             50.0%            37.5%          50.0%
```

`knowledge/retrieval/index.indexable` drops a row that has **no text beyond a
headline AND no linked company**. Two exclusions are deliberate: a headline that
*does* name a company stays (Google News returns no body at all and 88% of its
rows are linked — a headline is a real, citable claim), and a row with a real
body that names nothing stays (121 of them, still able to answer a macro or
sector question). The rule is not "thin" and not "unlinked"; it is the one
combination that provably answers nothing.

Cost, counted rather than waved away: one labelled answer — an article about
Google escaping an ad-tech breakup — is dropped, because it names nothing in the
book and so could not have been evidence for it either. The label is kept and the
loss is inside the numbers above.

The corpus still **stores** these rows; it is append-only and a record of what
the collector saw. Only the index refuses them.

### Measuring a real embedding model against the one that ships

The default `DistributionalEmbedder` is fitted on the corpus's own word company,
so it only relates words it has SEEN. That is precisely why the plain-English
half of the labelled set fails: ask about a "bendable" phone when every article
says "foldable" and it reaches nothing. Whether a real model fixes that is a
measurement, and until 2026-09-08 it was an unrunnable one — `ApiEmbedder` had
been at the seam since it was written and `--embedder` offered only
`default`, `distributional` and `hashing`. The comparison the command exists to
make could not be selected.

```
ask.py retrieval --embedder api      # needs EMBEDDING_API_KEY; spends requests
```

It **refuses** without a key rather than falling back to the default and
reporting the default's score under the API's name.

**The measurement has to run on a runner.** This development environment has no
route to any embedding host — `openrouter.ai` answers `CONNECT tunnel failed,
403` through the sandbox proxy — so `.github/workflows/embedding-probe.yml`
exists to make it on a machine that can. It scores the same 24 questions over
the same committed corpus with `hashing`, `default` and `api` in turn, so the
embedder is the only variable, and prints all three tables to the run summary.

It probes ONE embedding before spending a corpus of them. That is not caution
for its own sake: OpenRouter answers **HTTP 200 with an empty `data` array** for
a model that cannot serve the requested encoding, so "it did not error" is not
the same as "it returned a vector". The step asserts a vector came back and says
how wide it is.

#### The provider is three environment variables

`EMBEDDING_API_KEY`, `EMBEDDING_API_URL`, `EMBEDDING_MODEL`. The client POSTs
`{"model", "input"}` and reads `data[].embedding`, which is the OpenAI schema
every candidate speaks, so switching provider needs no code. Three free routes,
assessed 2026-09-08:

| route | model | terms |
|---|---|---|
| **OpenRouter** *(wired)* | `nvidia/nemotron-3-embed-1b:free` | zero cost per token; low daily rate limit on free models |
| Cloudflare Workers AI | BGE family | 10,000 Neurons/day, no credit card; URL carries the account id |
| Hugging Face router | any served embedding model | OpenAI-compatible, but $0.10/month of credits rather than a free model |

**The trap, and why this client is safe from it.** The OpenAI SDK sends
`encoding_format=base64` by default, and OpenRouter returns 200-with-nothing for
a model that cannot serve it — a silent, invisible failure that fills an index
with zero vectors. This client is raw `urllib`, never sets the field, and RAISES
when the response holds fewer vectors than it asked for. Both halves are pinned
by test.

Vectors cache in `data/embeddings.db` by *(model, text)*, so the first index is
the only expensive one and switching model never reads back the old model's
vectors.

### When a registered feed URL rots

A 404 from a news feed used to be reported as "404" and nothing else: the
autodiscovery reader only ever saw a body, and a 404 has none. On 2026-09-04
and again on 2026-09-06 The Star, The Edge and the New Straits Times all
answered 404 to the paths in `knowledge/feeds/registry.py`, which left the six
Malaysian business feeds this book most needs dead with no way, from an
environment that cannot browse, to find where they had gone.

A 404 or 410 now costs one extra request to the SITE ROOT, and the error names
whatever feeds that page advertises:

```
thestar_business fetch failed: HTTP Error 404: Not Found - but
https://www.thestar.com.my/ advertises feeds at: https://www.thestar.com.my/rss/News/Business
```

Nothing follows the discovered URL automatically. A feed URL is a decision
about what the system ingests and belongs in the registry where a person put
it; the probe reports, a person edits, and only then does a candidate become
enabled.

Two numbers to read afterwards, both from `ask.py sweep`'s own last line: how
many articles the corpus holds, and how many of the sweeps failed. A failure
count that climbs is the signal; an article count that stops climbing while the
failure count does not is a source that has gone quiet without erroring, which
is worth a look at the source itself.

Then read it from anywhere: `ask.py alerts` on the command line, the
`open_alerts` MCP tool in a Claude session, or `GET /api/alerts` in the web
app. An empty history means no rule has been EVALUATED - not that none would
fire.

## Reading a capped sweep row (2026-09-08)

GDELT asks about three names a run, not the whole book. A sweep row for it now
ends with, for example:

```
read but empty: IHH; deferred to a later run: Genting, Press Metal, Petronas Chemicals
```

Those two clauses are different facts and the distinction is the reason the
note exists:

* **read but empty** — the name was asked about and the world had nothing.
  That is information.
* **deferred to a later run** — the name was not asked about. It is not a gap
  in coverage unless it repeats: `_window` advances by three each day, so a
  six-name list is fully covered every two days and a nine-name list every
  three.

**When to worry.** The same name appearing under *deferred* on every run for
more than the cycle length means the rotation has stopped advancing — check
that the clock passed to `run_sweep` is real and not pinned. A name under
*failed* repeatedly is the ordinary GDELT 429 and is what the cap exists to
reduce; if it persists across a whole cycle for every name, GDELT is refusing
the whole book and `ask.py sources --probe gdelt` from a runner will say so.

**Do not raise the cap to "catch up".** The refusals scale with requests per
run, so a higher cap collects less, not more. That was the state before it:
nine names asked, a mean of 3.5 refused.

## `name_coverage`: one name going quiet inside a working collector (2026-09-08)

The two collector rules watch the COLLECTOR. This one watches the BOOK.

`sweep_silence` asks whether the collector stopped. `slots_missed` asks whether
it fired as often as its cron says. Both were green every day for a week while
**Petronas Chemicals held zero articles out of 1,673** — it was searched only as
"Petronas Chemicals" and never as "PCHEM", the form the Malaysian press prints,
so every run succeeded and collected nothing about it. A per-name defect inside
a successful sweep is invisible to a rule that asks whether the sweep ran.

**It compares rather than thresholds.** A name with no news is only evidence
when OTHER names have news. If the whole book is empty the collector is down,
which is `sweep_silence`'s alert — firing both would be two alerts about one
fault, so this rule stays quiet unless something else succeeded.

**When it opens**, the cause is usually a name the press uses and the collector
does not search. Work it in this order:

1. `knowledge/graph/data/entities.yaml` — is the short form there? The ticker,
   the initials, the name a headline would actually print. Every alias listed
   is searched (the first four); a missing one is a collection gap now, not
   just a linking one.
2. `ask.py sources --coverage` — what each source returned for that name, and
   what share of it named the company. A source returning rows that never name
   the company is a different fault from a source returning nothing.
3. Only then consider that the name may genuinely have had a quiet fortnight.
   `name_coverage_days = 14` is set so that is credible and a quiet month is
   not.

**Do not silence it by shortening the window.** The window is what makes a
genuine quiet spell distinguishable from a broken query.
