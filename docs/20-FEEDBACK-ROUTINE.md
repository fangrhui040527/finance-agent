# 20 — The feedback routine

Every night, after the last collection of the day, a scheduled Claude Code
session reads what the collector kept and writes one page: what moved, why,
why *that*, what would change the reading, what to watch, and how much of the
day's collection can be trusted. The page goes to `knowledge/feedback/` (the
contract is that folder's README), is committed to `main`, and is indexed into
`kb_lessons` so the reflection agent reads it back.

## Why a routine and not a workflow

Collection is deterministic and runs on GitHub Actions (`collect.yml`). The
feedback is reasoning — asking *why* until the chain reaches a source — and
that is what the model is for. Two constraints shaped where it runs:

- The Claude Code cloud environment this repository is developed in has **no
  route to any data host**. So the routine cannot fetch; it reads what Actions
  committed. `FINPLANET_OFFLINE=1` makes the price cache answer regardless of
  its date, and `ask.py pack` prepares everything else from the stores.
- The reasoning runs on the owner's Claude plan, not on an API key held by the
  repository. That is the MCP architecture of docs/15 applied to a schedule.

## The schedule

| UTC | MYT | ET | what |
|---|---|---|---|
| 09:20 | 17:20 | 05:20 | `collect.yml` bursa_close: Malaysian press, Bursa names, BNM rate and OPR, prices |
| 12:30 | 20:30 | 08:30 | `collect.yml` us_preopen (weekdays): FRED, calendar, rating changes |
| 21:15 | 05:15+1 | 17:15 | `collect.yml` us_close: US names, filings, insiders, sentiment, prices |
| **22:30** | **06:30+1** | 18:30 | **the feedback routine** — reads the day, writes the page |
| Sun 02:00 | 10:00 | 22:00 Sat | `collect.yml` weekly: statements, estimates, transcripts, CPI |

The routine therefore sees both closes of the calendar day it writes about,
and its page is on `main` before Bursa opens.

### It also dispatches the collections the cron dropped

The four `collect.yml` rows above are GitHub cron, and on this repository GitHub
cron delivers roughly half of what it owes: measured 2026-09-03 to 09-07, nine
scheduled runs where about fourteen were due, every one late — from 14 minutes
to **5h43m** — and two created without ever being given a machine. `slots_missed`
(docs/14) reports that the morning after, which is the right shape for an alert
and the wrong shape for a repair.

So the routine is also the collector's fallback scheduler, because it runs on a
scheduler that has not missed a firing. **Before** it writes anything it asks
`ask.py sweep --due` what the day is still short of and dispatches `collect.yml`
for exactly those slots, then pulls again so the page sees whatever landed.

Three properties, and each of them is load-bearing:

* **The cron stays primary.** A catch-up that fired unconditionally would double
  every collection and spend the Actions minutes that may be causing this.
* **It dispatches, it does not collect.** The routine's session has no route to
  any data host; the runner does.
* **It never guesses.** When the store holds runs that do not record which slot
  they were, `--due` says so and names nothing, because a catch-up that could
  not tell "already ran" from "cannot tell" would double-collect every night
  forever.

Same-day recovery is most of the value: **news is the only thing that expires.**
Prices, filings and macro series are re-fetchable tomorrow; a wire feed serves a
recent window and nothing brings back the hours it has rolled past.

## What one run does

```
git clone / fetch, checkout main, uv sync --frozen
FINPLANET_OFFLINE=1 uv run python ask.py pack --date <yesterday UTC> --write
    -> knowledge/feedback/<date>.pack.md     (the deterministic half)
read the pack; read the last three feedback pages
write knowledge/feedback/<date>.md and <date>.json per the README
uv run python ask.py watch                  (open monitor alerts go in Data quality)
git add knowledge/feedback/<date>.md knowledge/feedback/<date>.json
git commit; git push origin main            (or a branch + PR if main refuses)

# phase 2 - the paper book (docs/22)
FINPLANET_OFFLINE=1 uv run python ask.py paper status --json
FINPLANET_OFFLINE=1 uv run python ask.py paper pack --date <today UTC> --write
    -> knowledge/paper/<date>.pack.md
read it and the last three paper pages; write knowledge/paper/<date>.md and .json
FINPLANET_OFFLINE=1 uv run python ask.py paper decide --weights ... --thesis ... --horizon 21 --confidence ...
    (refused by code if any cap is breached: record a smaller book or all-cash instead)
git add knowledge/paper/<date>.md knowledge/paper/<date>.json data/paper.db data/learning.db
git commit; git push origin main
```

The pack is built by code and is not to be re-derived: returns come from the
cached bars, the decomposition from `engines/attribution`, the stories from the
digest with their `doc_id`s, the figures from the fact book with the day they
became knowable, the collection rows from the sweeps table. The routine's job
is the chain of *why*, the confirm/refute conditions, and the honesty about
what the evidence does not reach.

## The prompt

This is the text the Routine carries. It is kept here so it is versioned with
the code it drives; changing it means changing the Routine too
(`update_trigger`).

```
You are the nightly feedback routine for the finance-agent repository
(fangrhui040527/finance-agent). You write one page about yesterday's market
day for a Malaysian retail investor whose book is six Bursa names and three
Nasdaq names. You do not trade, recommend, or forecast prices; you explain
what moved and why, as far as the collected evidence reaches, and you say
plainly where it stops.

Setup
1. Clone the repository if it is not present; otherwise fetch. Check out
   main. Run `uv sync --frozen` (Python 3.11 or 3.12).

Catch up the collection FIRST, because news expires
2. Run `uv run python ask.py sweep --due`. It prints the slots still owed
   today, one per line, and collects nothing. An empty list is a good day.
   A reason on stderr ("nothing to attribute", "no corpus") is also a stop:
   it is refusing to guess, and guessing would double-collect every night.
   For each slot it names, dispatch `collect.yml` on GitHub with that slot
   as the input — do NOT sweep in this session, which has no route to any
   data host. Never dispatch `all` as a shortcut. Then pull again so the
   page sees whatever landed, and record in Data quality which slots you
   dispatched and whether they arrived in time.

3. DAY = yesterday's date in UTC (the collector's day). Run
   `FINPLANET_OFFLINE=1 uv run python ask.py pack --date DAY --write`.
   It writes knowledge/feedback/DAY.pack.md. If it exits non-zero, read its
   stderr, fix nothing, and write a page whose Data quality section says
   what could not be prepared.
3. Read knowledge/feedback/README.md (the contract), TEMPLATE.md, the pack,
   and the three most recent knowledge/feedback/*.md pages.

Write
4. Write knowledge/feedback/DAY.md following TEMPLATE.md, and DAY.json
   following the README's schema. Rules you are held to:
   - Components before narrative: offer a cause only for the part of a move
     the decomposition left unexplained. If the pack says market-driven,
     say so and offer no company story.
   - A mention is not a cause. Say whether a story is ABOUT what happened to
     the company or merely names it.
   - Every figure is copied from the pack with its id. Estimate nothing.
   - For each cause, ask why it arose and what caused that, and keep going
     until you reach a primary source (a filing, an announcement, a macro
     print with a series id) or write "unknown: no collected evidence
     reaches further". Unknown is a complete answer.
   - For each cause, one observation that would confirm it and one that
     would refute it, checkable by tomorrow's collection.
   - No advice verbs. No "buy", "sell", "should".
   - Carry forward any open question from the previous pages that is still
     open, with the date it was first asked.
5. Run `uv run python ask.py watch`. Put open alerts, and every failed,
   degraded or skipped source from the pack's collection table, in Data
   quality, with what that does to the page's confidence.

Commit
6. `git add knowledge/feedback/DAY.md knowledge/feedback/DAY.json` (do not
   add the .pack.md; it is regenerable). Commit with the message
   "feedback: DAY" and push to main. If the push is refused, push to a
   branch named claude/feedback-DAY and open a pull request titled
   "feedback: DAY" against main.

Paper book (docs/22-PAPER-BOOK.md; the contract is knowledge/paper/README.md)
7. `git pull --rebase origin main` (the collector marked the book at 21:15
   UTC). Run `FINPLANET_OFFLINE=1 uv run python ask.py paper status --json`
   and `... ask.py paper pack --date TODAY --write`, where TODAY is today's
   date in UTC. Read the pack and the last three knowledge/paper/*.md pages.
8. Write knowledge/paper/TODAY.md following knowledge/paper/TEMPLATE.md and
   TODAY.json per the README. Every number is copied from the pack. Each
   mistake is a falsifiable sentence naming a decision date and prediction
   id. No advice verbs.
9. Decide tomorrow's target book from the status page's FUNDABLE table and
   the feedback page's evidence, inside the caps the status page prints:
   `... ask.py paper decide --weights "MYX:5183=0.11,XNAS:NVDA=0.22"
   --thesis "..." --horizon 21 --confidence 0.55`. A held name left out is an
   exit; an all-cash decision is `--weights ""`. If the command is REFUSED,
   copy its refusals into the page and record a smaller book or all-cash;
   never soften a cap to get it accepted. In the observe weeks it is logged
   and graded, not applied; decide anyway.
10. `git add knowledge/paper/TODAY.md knowledge/paper/TODAY.json data/paper.db
    data/learning.db`, commit "paper: TODAY", push to main (fallback branch
    claude/paper-TODAY and a pull request titled "paper: TODAY").

Report
11. End with five lines: the feedback page's path, the names whose unexplained
    share was above 50%, any source that failed, the paper book's equity
    against the control, and the decision recorded (or the refusal). Nothing
    else.
```

## Creating and changing the Routine

The Routine was created from a Claude Code session with the `create_trigger`
tool of the Claude Code Remote server: cron `30 22 * * *`, a fresh session per
firing in the repository's environment, push notifications on. `list_triggers`
shows it; `update_trigger` changes the prompt or the schedule; `delete_trigger`
removes it. Nothing about it lives in GitHub — if it stops firing, the Actions
side keeps collecting and the pages simply stop, which `ask.py watch`'s
sweep-silence rule does not see. Check `knowledge/feedback/` has yesterday's
page; that is the monitor.

The paper book's half of the run, the caps and the phase calendar are in
`docs/22-PAPER-BOOK.md`.

## What the page is not

It is not a prediction log. If the routine is confident enough to state a
falsifiable view, it may log one with `predict.py log`, which fixes the grading
date in advance; the page itself makes no calls. And it is not evidence: the
reflection agent retrieves it as `kb_lessons`, the lowest-trust store, and
nothing it says can outrank a filing or a price.
