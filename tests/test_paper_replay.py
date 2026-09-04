"""Thirteen weeks, scripted, on a synthetic world: the ledger's invariants hold every day."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from engines.paper.book import decide, mark, turnover_used
from engines.paper.grade import grade_due
from engines.paper.pricing import lot_size
from engines.paper.store import CONTROL, DECIDED

# The script builds exposure the way the caps allow it: a step is repeated on
# the next decision day so the five-weekday turnover window can clear before
# the next increase, and every weight sits comfortably above one lot.
R1 = {"MYX:8869": "0.23", "MYX:3182": "0.07"}
R2 = {"MYX:8869": "0.23", "MYX:5183": "0.13"}
R3 = {"MYX:5183": "0.13", "MYX:3182": "0.07"}
RAMP = [R1, R1, R2, R2, R3, R3, {}, {}]
A = {"MYX:8869": "0.23", "MYX:5183": "0.13"}
B = {"MYX:8869": "0.23", "MYX:5183": "0.13", "MYX:3182": "0.07", "XNAS:NVDA": "0.245"}
C = {"MYX:5183": "0.13", "XNAS:NVDA": "0.245"}
FULL = [A, A, A, B, B, B, C, C, {}, {}]
BREACH = {"XNAS:AAPL": "0.30"}  # per-name and lot: must be refused in every phase


def _at(d: date, h=22, m=30):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def test_thirteen_weeks_hold_every_invariant(paper_env):
    env = paper_env
    env.feed.shocks[("MYX:3182", env.week(5, 2))] = -0.11  # one engineered stop
    for iid in ("MYX:5183", "MYX:8869", "MYX:5225", "MYX:3182", "XNAS:NVDA"):
        env.feed.shocks[(iid, env.week(9, 3))] = -0.30  # one engineered halt (stops too)
    env.feed.__init__(date(2025, 9, 1), date(2026, 7, 31), shocks=env.feed.shocks)
    store, cfg, feed, fx = env.store, env.cfg, env.feed, env.fx
    caps = cfg.paper

    day = env.start - timedelta(days=3)
    end = env.start + timedelta(weeks=13)
    i_ramp = i_full = 0
    refused_breaches = 0
    halted_days: set[date] = set()
    while day <= end:
        if day.weekday() < 5:
            r = mark(store, cfg, feed, fx, day=day, slot="us_close", now=_at(day, 21, 15))
            assert r.exit_code == 0, r.problems
            for book in (DECIDED, CONTROL):
                m = r.marks[book]
                held = sum(Decimal(p["value_usd"]) for p in m.positions)
                assert abs(m.equity_usd - m.cash_usd - held) <= Decimal("0.01"), (book, day)
                assert m.cash_usd >= 0
                for p in m.positions:
                    assert p["units"] % lot_size(p["instrument_id"]) == 0
                    assert p["units"] > 0
            if r.marks[DECIDED].halted:
                halted_days.add(day)
            # no entry may be applied on a day whose previous mark was halted
            prev = store.mark_before(DECIDED, day)
            if prev is not None and prev.halted:
                assert not any(
                    a.change is not None and a.change.units_delta > 0 for a in r.applied[DECIDED]
                ), day
            grade_due(store, cfg, feed, fx, day=day, learning=env.learning)
            if day.weekday() in (0, 3) and day >= env.week(3):
                phase = r.marks[DECIDED].phase
                d = decide(
                    store,
                    cfg,
                    feed,
                    fx,
                    day=day,
                    weights={k: Decimal(v) for k, v in BREACH.items()},
                    thesis="breach",
                    learning=env.learning,
                    now=_at(day),
                )
                assert d.refused
                refused_breaches += 1
                if phase == "ramp":
                    w, i_ramp = RAMP[i_ramp % len(RAMP)], i_ramp + 1
                else:
                    w, i_full = FULL[i_full % len(FULL)], i_full + 1
                decide(
                    store,
                    cfg,
                    feed,
                    fx,
                    day=day,
                    weights={k: Decimal(v) for k, v in w.items()},
                    thesis=f"scripted {phase}",
                    learning=env.learning,
                    now=_at(day),
                )
        day += timedelta(days=1)

    marks = store.marks(DECIDED)
    assert len(marks) >= 60 and refused_breaches >= 20
    changes = store.changes(DECIDED)
    assert changes, "the script never touched the book"
    cost = store.cost_to_date(DECIDED)
    assert cost.fees_usd == sum((c.fee_usd for c in changes), Decimal(0))
    assert cost.fx_spread_usd == sum((c.fx_spread_usd for c in changes), Decimal(0))
    # the turnover cap held for entries on every five-weekday window
    for m in marks:
        entries = sum(
            (c.consideration_usd for c in changes if c.day == m.day and c.units_delta > 0),
            Decimal(0),
        )
        if entries > 0:
            seen = store.mark_before(DECIDED, m.day)
            cap = caps.weekly_turnover_cap * (seen.equity_usd if seen else Decimal(1000))
            assert turnover_used(store, DECIDED, m.day) <= cap + Decimal("0.01"), m.day
    assert any(c.action == "exit" for c in changes), "the engineered stop never fired"
    assert halted_days, "the engineered drawdown never halted the book"
    stops = [t for t in store.targets(DECIDED) if t.reason == "stop"]
    assert stops
    # every raised or exited weight was logged, and the grader worked through them
    assert len(env.learning.graded()) > 0
    assert store.marks(CONTROL)[-1].positions, "the control never invested"
    ctl_changes = store.changes(CONTROL)
    assert all(c.units_delta % lot_size(c.instrument_id) == 0 for c in ctl_changes)
