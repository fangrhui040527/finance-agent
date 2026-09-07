"""Thresholds that fire on their own, and an append-only history of when.

`mcp_server/observability.py` answers questions when asked. This answers the
question nobody asked because nobody was watching: it evaluates a small set
of rules against the ledger and the traces, and records every state CHANGE so
a scheduled job can run it every hour and tell you only when something moved.

Design decisions worth stating, because each is a way this could have been
useless:

  * **State changes, not repetitions.** An alert that fires every hour for a
    week is noise a person learns to ignore, which is worse than silence. The
    log records `opened` and `resolved`; a rule that is still tripped writes
    nothing new.
  * **Append-only, like every other record here.** You cannot rewrite when an
    alert opened. Triggers, not convention.
  * **Silence is a rule.** "Nothing has run for N hours" is a condition worth
    alerting on: a daemon that dies quietly looks exactly like a quiet week,
    and the whole point of this module is telling those apart. Off by default
    because a personal tool is allowed to sit idle.
  * **Every alert says what to look at next.** A threshold with no next step
    is a number that makes you anxious rather than informed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import _enable_wal

WARN = "warn"

#: Where the nightly feedback pages live. Read at CALL time, and overridable
#: per call through `evaluate(feedback_root=...)`, for the reason that file's
#: docstring gives: a test that cannot point it somewhere of its own reads the
#: repository's own tracked pages, whose questions age.
FEEDBACK_DIR = "knowledge/feedback"
ALERT = "alert"

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_events (
    rule      TEXT NOT NULL,
    at        TEXT NOT NULL,
    state     TEXT NOT NULL,          -- opened | resolved
    severity  TEXT NOT NULL,
    title     TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS alert_events_rule_at ON alert_events(rule, at);

CREATE TRIGGER IF NOT EXISTS alert_events_no_update
BEFORE UPDATE ON alert_events
BEGIN SELECT RAISE(ABORT, 'when an alert opened is not editable'); END;
CREATE TRIGGER IF NOT EXISTS alert_events_no_delete
BEFORE DELETE ON alert_events
BEGIN SELECT RAISE(ABORT, 'alert history is append-only: a log you can prune proves nothing'); END;
"""


@dataclass(frozen=True)
class Alert:
    """One tripped rule, with the number that tripped it and the next step."""

    rule: str
    severity: str
    title: str
    detail: str
    next_step: str
    evidence: dict = field(default_factory=dict)

    def render(self) -> str:
        mark = "ALERT" if self.severity == ALERT else " warn"
        return f"[{mark}] {self.title}\n         {self.detail}\n         next: {self.next_step}"


class AlertLog:
    """Append-only history. Current state of a rule is its latest event."""

    def __init__(self, path: str | Path = "data/alerts.db") -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p))
        self.db.row_factory = sqlite3.Row
        _enable_wal(self.db, str(p), timeout_ms=5000)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def open_rules(self) -> dict[str, sqlite3.Row]:
        """rule -> its latest event, for rules whose latest event is `opened`."""
        rows = self.db.execute("SELECT * FROM alert_events ORDER BY at, rowid").fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for r in rows:
            latest[r["rule"]] = r
        return {k: v for k, v in latest.items() if v["state"] == "opened"}

    def record(self, alert: Alert, state: str, at: datetime | None = None) -> None:
        at = at or datetime.now(UTC)
        self.db.execute(
            "INSERT INTO alert_events (rule, at, state, severity, title, detail, evidence_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                alert.rule,
                at.isoformat(),
                state,
                alert.severity,
                alert.title,
                alert.detail,
                json.dumps(alert.evidence, default=str, sort_keys=True),
            ),
        )
        self.db.commit()

    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM alert_events ORDER BY at DESC, rowid DESC LIMIT ?", (int(limit),)
        ).fetchall()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> AlertLog:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------


def evaluate(
    cfg,
    db: str = "",
    debug_root: str = "debug",
    now: datetime | None = None,
    feedback_root: str = "",
) -> list[Alert]:
    """Every rule, against the ledger, the traces and the nightly pages. Pure:
    writes nothing. `debug_root` and `feedback_root` are parameters rather than
    settings for the same reason: a test that cannot point them somewhere of
    its own reads the repository's own tracked directories, which age."""
    from core.provenance.ledger import ProvenanceLedger

    now = now or datetime.now(UTC)
    out: list[Alert] = []
    ledger_path = db or cfg.provenance_db

    with ProvenanceLedger(ledger_path) as led:
        day_ago = now - timedelta(days=1)
        spend = led.cost_since(day_ago)
        budget = Decimal(str(cfg.daily_budget_myr))
        fraction = Decimal(str(cfg.alert_spend_fraction))
        if budget > 0 and spend >= budget * fraction:
            out.append(
                Alert(
                    rule="spend_24h",
                    severity=ALERT if spend >= budget else WARN,
                    title=f"24h spend RM {spend:.4f} is {spend / budget:.0%} of the RM {budget:.2f} budget",
                    detail=f"the alert threshold is {fraction:.0%}",
                    next_step="operating_report to see which agent and model spent it; "
                    "FINPLANET_MODEL=haiku pins every tier to the cheapest model, and "
                    "FINPLANET_EFFORT=low buys the same answers with less thinking",
                    evidence={"spend_myr": str(spend), "budget_myr": str(budget)},
                )
            )

        latencies = led.latencies_between(day_ago, now)
        if latencies:
            ordered = sorted(latencies)
            p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
            limit = float(cfg.alert_p95_latency_ms)
            if limit > 0 and p95 >= limit:
                out.append(
                    Alert(
                        rule="latency_p95",
                        severity=WARN,
                        title=f"p95 latency {p95:.0f} ms over the last 24h, threshold {limit:.0f} ms",
                        detail=f"{len(latencies)} call(s) measured",
                        next_step="run_anatomy on the newest run to see which span is slow",
                        evidence={"p95_ms": p95, "calls": len(latencies)},
                    )
                )

        claims = led.claims_between(day_ago, now)
        if claims:
            dropped = [c for c in claims if not c["survived"]]
            rate = Decimal(len(dropped)) / Decimal(len(claims))
            limit_rate = Decimal(str(cfg.alert_dropped_claim_rate))
            if limit_rate > 0 and rate >= limit_rate:
                out.append(
                    Alert(
                        rule="dropped_claims",
                        severity=WARN,
                        title=f"{rate:.0%} of claims dropped for want of a citation "
                        f"({len(dropped)} of {len(claims)})",
                        detail="a dropped claim is the system declining to say what it "
                        "could not support - a rising rate means the evidence is thinning",
                        next_step="operating_report names the most recent drop reasons",
                        evidence={"dropped": len(dropped), "checked": len(claims)},
                    )
                )

        silence_hours = int(cfg.alert_silence_hours)
        if silence_hours > 0:
            recent = led.calls_between(now - timedelta(hours=silence_hours), now)
            ever = list(led.calls(limit=1))
            if ever and not recent:
                out.append(
                    Alert(
                        rule="silence",
                        severity=ALERT,
                        title=f"no model calls in {silence_hours}h, but this ledger has run before",
                        detail="a job that dies quietly looks exactly like a quiet week",
                        next_step="system_health, then check whatever schedules the run",
                        evidence={"silence_hours": silence_hours},
                    )
                )

    out.extend(_sweep_rules(cfg, now))
    out.extend(_series_rules(cfg, now))
    out.extend(_slot_rules(cfg, now))
    out.extend(_question_rules(now, feedback_root))
    out.extend(_paper_rules(cfg, now))
    out.extend(_trace_rules(debug_root, now))
    return out


#: The longest gap each collection slot can legitimately leave between two
#: firings, from the cron in `.github/workflows/collect.yml`. A source is only
#: silent if it has missed its OWN cadence: `bursa_close` and `us_close` run
#: every day, `us_preopen` only on weekdays - so a Friday-to-Monday gap of three
#: days is the schedule working - and `weekly` fires once on a Sunday.
SLOT_MAX_GAP_HOURS: dict[str, int] = {
    "bursa_close": 24,
    "us_close": 24,
    "us_preopen": 72,
    "weekly": 168,
}

#: What `alert_sweep_silence_hours` has always meant: the allowance for a source
#: that runs DAILY. Everything less frequent is that number plus the extra time
#: its own slot leaves, so the configured grace carries through unchanged.
DAILY_SLOT_HOURS = 24


def sweep_allowance_hours(name: str, configured: int) -> int:
    """How long this source may be silent before that means something is wrong.

    A single threshold across every source was wrong in a way that guaranteed
    false alarms on a fixed schedule. `fred` runs at `us_preopen`, weekdays
    only, so every Saturday, Sunday and Monday morning it was over a 30-hour
    line by simply not being a weekday - the alert fired on 2026-09-07 saying
    "no successful sweep in 30h: fred (54h ago)" while the collector was
    running perfectly. Three sources are worse: `dosm_cpi`, `finmind` and
    `sec_xbrl` run only on the weekly slot, so a 30-hour rule calls them dead
    six days out of every seven.

    An alert that is guaranteed to be wrong on a timetable is worse than no
    alert, because it teaches the person reading the list to skim past a real
    one. The slots each source runs in are already in the catalogue; this reads
    them rather than asking an operator to keep a second list in step.
    """
    try:
        from knowledge.sources.catalog import CATALOG

        slots = CATALOG[name].slots
    except (ImportError, KeyError):
        return configured
    gaps = [SLOT_MAX_GAP_HOURS[s] for s in slots if s in SLOT_MAX_GAP_HOURS]
    if not gaps:
        return configured
    # The most frequent slot sets the expectation: a source in both a daily and
    # a weekly slot should still report every day.
    return configured + (min(gaps) - DAILY_SLOT_HOURS)


def _sweep_rules(cfg, now: datetime) -> list[Alert]:
    """Whether the scheduled sweep is still running, and still working.

    `silence` above watches the MODEL ledger, and `ask.py sweep` makes no model
    calls at all - it fetches, stores and links. So the moment a sweep goes on a
    timer, the existing death detector is watching the wrong thing: the ledger
    can be silent for a week while the sweep runs perfectly every morning, and
    it can be busy with interactive questions while the sweep has been dead
    since Tuesday. This rule watches the record the sweep actually writes.

    ONE RULE FOR BOTH FAILURE MODES, because a failed sweep is not a successful
    one. A sweep whose scheduler died and a sweep the network has refused for a
    week both stop producing `ok` rows, and both want the same look from a
    person. Splitting them would mean two alerts that can never both be right.

    Quiet until a source has succeeded ONCE, the same discipline `silence` uses
    on the ledger: a corpus that has never been filled is a system nobody has
    turned on yet, not a system that has stopped.
    """
    hours = int(getattr(cfg, "alert_sweep_silence_hours", 0))
    if hours <= 0:
        return []

    from knowledge.corpus import Corpus

    path = str(getattr(cfg, "corpus_db", "data/corpus.db"))
    if not Path(path).exists():
        return []

    stale: list[tuple[str, datetime, int]] = []
    with Corpus(path) as corpus:
        for name in getattr(cfg, "sources", ()):
            last = corpus.last_success(name)
            if last is None:
                continue
            allowance = sweep_allowance_hours(name, hours)
            if last < now - timedelta(hours=allowance):
                stale.append((name, last, allowance))
    if not stale:
        return []

    worst = min(age for _, age, _ in stale)
    named = ", ".join(
        f"{n} ({(now - t).total_seconds() / 3600:.0f}h ago, allowed {a}h)"
        for n, t, a in sorted(stale)
    )
    return [
        Alert(
            rule="sweep_silence",
            severity=ALERT,
            title=f"past its own cadence: {named}",
            detail="the sweep has succeeded before, so this is a stop, not a system "
            "nobody turned on. A dead scheduler and a refused network look the same "
            "from here and want the same look",
            next_step="run `ask.py sweep` by hand - it prints the reason - then check "
            "whatever schedules it and the network policy the feed needs",
            evidence={
                "silence_hours": hours,
                "sources": [n for n, _, _ in sorted(stale)],
                "allowances": {n: a for n, _, a in sorted(stale)},
                "oldest_success": worst.isoformat(),
            },
        )
    ]


#: Which slots the cron in `.github/workflows/collect.yml` owes on a given
#: weekday, 0 = Monday. `us_preopen` runs `1-5` (Mon-Fri) and `weekly` fires on
#: Sunday; the other two run every day. Kept beside SLOT_MAX_GAP_HOURS because
#: they read the same cron and must not drift apart.
SLOT_WEEKDAYS: dict[str, frozenset[int]] = {
    "bursa_close": frozenset(range(7)),
    "us_preopen": frozenset(range(5)),
    "us_close": frozenset(range(7)),
    "weekly": frozenset({6}),
}

#: A run may land in the next UTC day and still be the previous day's slot -
#: 21:15 delayed by three hours is 00:15 tomorrow - so one firing short across
#: the whole window is lateness, not loss. Two is a fault.
SLOT_SHORTFALL_MIN = 2


def slots_due(start: datetime, end: datetime) -> dict[str, int]:
    """How many firings the cron owes each slot over [start, end).

    Counted by whole UTC days, the same unit the cron is written in.
    """
    due: dict[str, int] = dict.fromkeys(SLOT_WEEKDAYS, 0)
    day = start.date()
    while day < end.date():
        for slot, weekdays in SLOT_WEEKDAYS.items():
            if day.weekday() in weekdays:
                due[slot] += 1
        day += timedelta(days=1)
    return {s: n for s, n in due.items() if n}


def slots_outstanding(corpus_path: str, now: datetime) -> tuple[list[str], str]:
    """Which of TODAY's slots are still owed, for a catch-up to fire.

    Deliberately a DIFFERENT question from `slots_missed`. That rule judges
    whole finished days, because a day still in progress cannot be short of
    anything - a slot that has not come round yet is not a slot missed. This one
    asks what is outstanding while the day is still running, which is the only
    moment a replacement run is worth firing: news expires, and a collection
    recovered the same evening is worth most of one that happened on time.

    Returns the slot names and, when the answer is empty for a reason worth
    printing, why. Three ways it says nothing:

      * a `--slot all` run has already happened today and covered everything;
      * the store has never recorded a slot AND something ran today, so the run
        cannot be attributed and firing again would be guessing;
      * there is genuinely nothing owed.

    A store that has never recorded a slot and saw NO run today does report the
    day's slots: "I cannot tell you which one" and "nothing ran at all" are
    different answers, and only the second is silence worth acting on.
    """
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    due = [s for s, weekdays in SLOT_WEEKDAYS.items() if day_start.weekday() in weekdays]
    if not Path(corpus_path).exists():
        return [], f"no corpus at {corpus_path}"

    from knowledge.corpus import Corpus

    with Corpus(corpus_path) as corpus:
        ran = corpus.slot_runs(day_start, now)
        total = corpus.run_count(day_start, now)
        ever = corpus.first_slot_row()

    if ran.get("all"):
        return [], "a run covering every slot has already happened today"
    outstanding = [s for s in due if not ran.get(s)]
    if outstanding and ever is None and total:
        return [], (
            f"{total} run(s) today, none recording which slot - nothing to attribute. "
            "the next sweep on a build that records slots settles this"
        )
    return outstanding, ""


def _slot_rules(cfg, now: datetime) -> list[Alert]:
    """Whether the collector fired as often as its own cron says it should.

    `sweep_silence` above watches for a collector that has STOPPED, and it is
    the wrong instrument for a collector that is merely unreliable. It reads
    the newest success per source against an allowance, so ANY run - including
    one fired by hand - resets it for every source at once.

    2026-09-07 is the case it was built from. At 14:20 UTC neither the 09:20 nor
    the 12:30 slot had produced a run, a sweep fired by hand at 08:50 had already
    moved every source's newest success, and the alert list was clean: a day that
    had so far lost two of its three collections read as perfectly healthy. The
    09:20 slot did eventually arrive - at 15:03, five hours and forty-three
    minutes late - which is the point rather than a reprieve. Lateness on that
    scale is indistinguishable from loss while you are waiting, and a rule that
    can only see a stopped collector cannot tell you either way.

    Counting by whole days is what makes a late run count as the day's run: a
    slot delayed most of a day still lands on the day it was owed.

    So this rule counts instead of timing: the cron owes a known number of
    firings over the window, and the sweeps table records what arrived.

    THE TOTAL IS THE SIGNAL, not any one slot. The failure this exists to catch
    spreads itself thin - a bad day loses one firing from each of three
    different slots, so a per-slot threshold sees three ones and reports
    nothing. Summed, that day is three missing collections out of four owed,
    which is the number worth waking someone for. Per-slot counts still go in
    the title, because they say WHICH part of the day is being dropped.

    A MANUAL RUN DOES NOT COUNT AS A SCHEDULED ONE. `--slot all` collects every
    source, so the data is not lost - and it is named in the alert for exactly
    that reason. But it took a person noticing, which is the thing being
    reported: a scheduler papered over by hand every morning is still broken,
    and letting the repair silence the alarm is how it stays broken.

    WHY THIS MATTERS MORE THAN A LATE RUN. News expires. GDELT and the wire
    feeds serve a recent window only and the free tiers are per-day, so a slot
    that never fires is a few hours of headlines that cannot be fetched later
    at any price. Prices, filings and macro series are all re-fetchable; the
    corpus is not.

    Quiet over any part of the window that predates the first recorded slot:
    rows written before the slot column existed carry none, and counting them
    as misses would raise an alert about a period nobody can now investigate.
    """
    days = int(getattr(cfg, "alert_slot_window_days", 0))
    if days <= 0:
        return []

    from knowledge.corpus import Corpus

    path = str(getattr(cfg, "corpus_db", "data/corpus.db"))
    if not Path(path).exists():
        return []

    # WHOLE UTC DAYS, both ends, because the comparison is against a count of
    # days the cron owes. `end` is midnight this morning: today is still in
    # progress, and a slot that has not come round yet is not a slot missed.
    end = datetime(now.year, now.month, now.day, tzinfo=UTC)
    start = end - timedelta(days=days)
    with Corpus(path) as corpus:
        first = corpus.first_slot_row()
        if first is None:
            return []
        # Only judge days this store can answer for. Round up to the next
        # midnight so a partial first day is never owed a full day's firings.
        if first > start:
            start = datetime(first.year, first.month, first.day, tzinfo=UTC) + timedelta(days=1)
        if end - start < timedelta(days=1):
            return []
        ran = corpus.slot_runs(start, end)

    due = slots_due(start, end)
    manual = ran.get("all", 0)
    # Capped per slot: a slot that fired twice in a day does not pay for
    # another slot that never fired at all.
    arrived = {slot: min(ran.get(slot, 0), n) for slot, n in due.items()}
    short = sum(due.values()) - sum(arrived.values())
    if short < SLOT_SHORTFALL_MIN:
        return []

    window = (end - start).days
    named = ", ".join(f"{slot} {arrived[slot]} of {due[slot]}" for slot in sorted(due))
    hand = f"; {manual} run(s) by hand collected the data anyway" if manual else ""
    return [
        Alert(
            rule="slots_missed",
            severity=ALERT,
            title=f"the collector missed {short} of {sum(due.values())} scheduled "
            f"runs in {window}d: {named}{hand}",
            detail="the cron owes a fixed number of firings and the sweeps table says how "
            "many arrived. sweep_silence cannot see this - one run by hand resets it "
            "for every source - so a day that loses two of its three collections reads "
            "as healthy there. A manual sweep saves the data but not the schedule, so "
            "it is named here rather than counted. News is the loss that cannot be "
            "recovered: the wire feeds serve a recent window only",
            next_step="check the runner's own history - a run created but never given a "
            "machine is out of minutes, no run at all is a dropped schedule - then "
            "`ask.py sweep --slot <name>` fills what is still fetchable",
            evidence={
                "window_days": window,
                "since": start.isoformat(),
                "until": end.isoformat(),
                "due": due,
                "arrived": arrived,
                "missed": short,
                "manual_runs": manual,
            },
        )
    ]


def _series_rules(cfg, now: datetime) -> list[Alert]:
    """A macro series whose newest point is past its own publication cadence.

    The sweep can succeed every night on a source whose upstream stopped
    publishing a year ago: the request answers, the rows are stored, and the
    fact book's "latest" quietly ages. That is what happened to sixteen
    DBnomics series and to Malaysian CPI, which read 1982 while the sweeps
    beside it reported `ok`. `sweep_silence` cannot see it - the sweep is not
    silent - so the age of the data, not the health of the fetch, is what this
    rule reads.

    One alert for all of them, worst first: fifteen separate alerts about the
    same dead upstream is fifteen alerts nobody finishes reading. Series with
    no declared cadence in knowledge/sources/freshness.py are not judged.
    """
    from knowledge.facts import FactBook
    from knowledge.sources.freshness import age_days, max_age_days

    path = str(getattr(cfg, "facts_db", "data/facts.db"))
    if not Path(path).exists():
        return []

    today = now.date()
    stale: list[tuple[int, int, str, str]] = []  # age, limit, series_id, obs_date
    with FactBook(path) as book:
        for sid in book.series_ids():
            limit = max_age_days(sid)
            if limit is None:
                continue
            pts = book.series(sid)
            if not pts:
                continue
            newest = pts[-1].obs_date
            age = age_days(newest, today)
            if age > limit:
                stale.append((age, limit, sid, newest.isoformat()))
    if not stale:
        return []

    stale.sort(reverse=True)
    named = ", ".join(f"{sid} ({age}d, limit {limit})" for age, limit, sid, _ in stale[:6])
    more = f" and {len(stale) - 6} more" if len(stale) > 6 else ""
    return [
        Alert(
            rule="series_stale",
            severity=ALERT,
            title=f"{len(stale)} macro series past their cadence: {named}{more}",
            detail="the sweep may be succeeding on every one of these - a stopped upstream "
            "and a healthy fetch look identical from the sweep table. Any reading that "
            "treats these as current is reading a figure from another year",
            next_step="check the upstream for each id (DBnomics and the IMF/BIS datasets "
            "behind it, or DOSM), then either point the adapter at a live series or take "
            "the id out of its SERIES table; `ask.py macro` marks each row STALE meanwhile",
            evidence={
                "stale": [
                    {"series_id": sid, "newest": newest, "age_days": age, "limit_days": limit}
                    for age, limit, sid, newest in stale
                ],
            },
        )
    ]


#: How long an open question may stand on the nightly pages before it is a
#: finding rather than a question. Three weeks: long enough that a slow answer
#: - a quarterly filing, a source that publishes monthly - is not an alert, and
#: short enough that "the six Bursa names have no fact-book coverage" cannot be
#: carried forward for a season without anyone deciding anything.
OPEN_QUESTION_DAYS = 21


def _question_rules(now: datetime, directory: str = "") -> list[Alert]:
    """A question the nightly pages have carried for longer than they should.

    The pages ask the right questions and then carry them, correctly, with the
    date first asked. What nothing did was notice how long one had stood: a
    question three weeks old and one asked last night were the same prose in
    the same list. An old one is usually not a hard question - it is a source
    that was never wired, and it belongs in front of a person.
    """
    from knowledge.feedback_questions import open_questions

    directory = directory or FEEDBACK_DIR
    if not Path(directory).is_dir():
        return []
    old = [q for q in open_questions(directory) if q.age_days > OPEN_QUESTION_DAYS]
    if not old:
        return []
    worst = old[0]
    return [
        Alert(
            rule="open_question_stale",
            severity=WARN,
            title=f"{len(old)} question(s) carried over {OPEN_QUESTION_DAYS} days; "
            f"oldest {worst.age_days}d: {worst.text[:110]}",
            detail="the nightly pages carry an open question forward with the date it "
            "was first asked. One still open after three weeks is rarely a hard "
            "question - it is usually a source nobody wired",
            next_step="`ask.py pack --questions` lists them oldest first; answer it, "
            "or write on tonight's page why it cannot be answered and stop carrying it",
            evidence={
                "questions": [
                    {
                        "text": q.text,
                        "first_asked": q.first_asked.isoformat(),
                        "age_days": q.age_days,
                        "nights": q.nights,
                    }
                    for q in old[:10]
                ]
            },
        )
    ]


def _paper_rules(cfg, now: datetime) -> list[Alert]:
    """The paper book (docs/22): drawdown at the halt line, a mark gone stale,
    a cap breached by drift. Quiet until the book has been marked once - a
    book nobody opened is not a book that stopped."""
    settings = getattr(cfg, "paper", None)
    path = str(getattr(settings, "database", "data/paper.db"))
    if not Path(path).exists():
        return []

    from engines.paper.book import current_weights, settings_of
    from engines.paper.pricing import weekdays_between
    from engines.paper.store import DECIDED, PaperStore

    out: list[Alert] = []
    with PaperStore(path) as store:
        if not store.has_books():
            return []
        m = store.latest_mark(DECIDED)
        if m is None:
            return []
        caps = settings_of(cfg, store)
        line = Decimal(str(getattr(cfg, "alert_paper_drawdown", 0) or 0))
        if line > 0 and m.drawdown >= line:
            out.append(
                Alert(
                    rule="paper_drawdown",
                    severity=ALERT,
                    title=f"paper book drawdown {m.drawdown:.2%} from peak USD {m.peak_usd:,.2f}",
                    detail=f"equity USD {m.equity_usd:,.2f} at the {m.day} mark; the halt line is "
                    f"{caps.drawdown_halt:.0%} and no target may raise a weight while it holds",
                    next_step="`ask.py paper status` for the positions and the fundable set; "
                    "the journal in knowledge/paper/ should say what the book learned",
                    evidence={"drawdown": str(m.drawdown), "day": m.day.isoformat()},
                )
            )
        age = weekdays_between(m.day, now.date())
        if age > 2:
            out.append(
                Alert(
                    rule="paper_stale",
                    severity=WARN,
                    title=f"paper book last marked {m.day}, {age} weekdays ago",
                    detail="collect.yml marks it at the bursa_close and us_close slots; a stale "
                    "mark means the workflow or the price cache stopped",
                    next_step="run `ask.py paper mark --slot manual` by hand and read its problems",
                    evidence={"last_mark": m.day.isoformat(), "weekdays": age},
                )
            )
        weights = current_weights(m)
        drifted = [k for k, w in weights.items() if w > caps.max_weight_per_name]
        cash_share = (m.cash_usd / m.equity_usd) if m.equity_usd > 0 else Decimal(1)
        if drifted or cash_share < caps.cash_floor:
            what = []
            if drifted:
                what.append(f"{', '.join(drifted)} above {caps.max_weight_per_name:.0%}")
            if cash_share < caps.cash_floor:
                what.append(f"cash {cash_share:.1%} below the {caps.cash_floor:.0%} floor")
            out.append(
                Alert(
                    rule="paper_cap_breach",
                    severity=ALERT,
                    title="paper book outside its caps by drift: " + "; ".join(what),
                    detail="prices moved the book past a cap no decision may cross; the next "
                    "decision must bring it back or reduce",
                    next_step="`ask.py paper status` shows each cap's value and limit",
                    evidence={"drifted": drifted, "cash_share": str(cash_share)},
                )
            )
    return out


#: How recent the newest traced run must be for a method change between it and
#: the one before to still be news. `methodology_changed` is a TRANSITION alert:
#: it means something about the system moved, which is worth a look on the day
#: and worth nothing a week later. It compares the two newest runs in `debug/`,
#: so once tracing stops it compares the same pair forever and can never clear
#: itself - which is how "the method changed between 20260904T054544-e3b02a and
#: 20260904T061546-eefefe" (a tool being added, entirely expected after a
#: deploy) was still sitting open three days later. An alert that cannot resolve
#: trains the person reading the list to skim past a real one.
METHOD_CHANGE_WINDOW_DAYS = 3


def _run_started(name: str) -> datetime | None:
    """The timestamp in a run id, e.g. `20260904T054544-e3b02a`."""
    try:
        return datetime.strptime(name.split("-")[0], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except (ValueError, IndexError):
        return None


def _trace_rules(debug_root: str, now: datetime | None = None) -> list[Alert]:
    """Errors in the newest run, and whether the methodology moved under us."""
    from mcp_server.observability import _events, _manifest, _runs

    runs = _runs(debug_root)
    if not runs:
        return []
    out: list[Alert] = []
    newest = runs[0]

    errors = [e for e in _events(newest) if e.get("kind") == "error" or e.get("error")]
    if errors:
        first = str(errors[0].get("error", ""))[:120]
        out.append(
            Alert(
                rule="run_errors",
                severity=ALERT,
                title=f"{len(errors)} error(s) in the newest traced run {newest.name}",
                detail=f"first: {first}",
                next_step=f"run_anatomy('{newest.name}'), then debug/{newest.name}/ for the text",
                evidence={"run_id": newest.name, "errors": len(errors)},
            )
        )

    started = _run_started(newest.name)
    fresh = started is None or (now or datetime.now(UTC)) - started <= timedelta(
        days=METHOD_CHANGE_WINDOW_DAYS
    )

    mine = _manifest(newest) if fresh else None
    if mine:
        prev = next((r for r in runs[1:] if _manifest(r)), None)
        if prev is not None:
            theirs = _manifest(prev)
            if theirs.get("manifest_hash") != mine.get("manifest_hash"):
                from mcp_server.observability import _diff_manifests

                changes = _diff_manifests(theirs, mine)
                out.append(
                    Alert(
                        rule="methodology_changed",
                        severity=WARN,
                        title=f"the method changed between {prev.name} and {newest.name}",
                        detail="; ".join(changes[:4]),
                        next_step="expected after a deploy; unexpected means output moved "
                        "for a reason that is not the data",
                        evidence={"from": prev.name, "to": newest.name, "changes": changes},
                    )
                )
    return out


# --------------------------------------------------------------------------
# the check a scheduler runs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    opened: list[Alert]
    still_open: list[Alert]
    resolved: list[str]
    checked_at: datetime

    @property
    def any_open(self) -> bool:
        return bool(self.opened or self.still_open)

    def render(self) -> str:
        stamp = self.checked_at.strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"MONITOR  {stamp}"]
        if self.opened:
            lines.append("")
            lines.append(f"  {len(self.opened)} NEW:")
            lines += ["  " + a.render() for a in self.opened]
        if self.still_open:
            lines.append("")
            lines.append(f"  {len(self.still_open)} still open:")
            lines += [f"    {a.title}" for a in self.still_open]
        if self.resolved:
            lines.append("")
            lines.append(f"  {len(self.resolved)} resolved: {', '.join(self.resolved)}")
        if not self.any_open and not self.resolved:
            lines.append("  nothing tripped. This covers the rules that exist, over their")
            lines.append("  own windows - it is not a statement that everything is well.")
        return "\n".join(lines)


def check(
    cfg=None,
    db: str = "",
    alerts_db: str = "data/alerts.db",
    debug_root: str = "debug",
    now: datetime | None = None,
    feedback_root: str = "",
) -> CheckResult:
    """Evaluate, diff against the last known state, and append only the changes."""
    from core.config import load as load_config

    cfg = cfg or load_config()
    now = now or datetime.now(UTC)
    firing = {
        a.rule: a
        for a in evaluate(cfg, db=db, debug_root=debug_root, now=now, feedback_root=feedback_root)
    }

    opened: list[Alert] = []
    still: list[Alert] = []
    resolved: list[str] = []
    with AlertLog(alerts_db) as log:
        already = log.open_rules()
        for rule, alert in firing.items():
            if rule in already:
                still.append(alert)
            else:
                log.record(alert, "opened", at=now)
                opened.append(alert)
        for rule, row in already.items():
            if rule not in firing:
                log.record(
                    Alert(
                        rule=rule,
                        severity=row["severity"],
                        title=row["title"],
                        detail="",
                        next_step="",
                    ),
                    "resolved",
                    at=now,
                )
                resolved.append(rule)
    return CheckResult(opened=opened, still_open=still, resolved=resolved, checked_at=now)
