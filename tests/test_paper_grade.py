"""Paper predictions grade at their date, against the control, never early."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from engines.paper.book import decide, mark
from engines.paper.grade import grade_due, realised_vs_control
from engines.paper.store import ALL_CASH, CASH, CONTROL, DECIDED


def _at(d: date):
    return datetime(d.year, d.month, d.day, 22, 30, tzinfo=UTC)


def test_grading_waits_for_the_horizon_then_measures_excess_over_the_control(paper_env):
    env = paper_env
    d16 = env.week(3)
    mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(2, 4),
        slot="us_close",
        now=_at(env.week(2, 4)),
    )
    d = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={"MYX:5183": Decimal("0.11"), "MYX:8869": Decimal("0.21")},
        thesis="t",
        horizon=5,
        learning=env.learning,
        now=_at(d16),
    )
    assert not d.refused and all(p.grade_on == d16 + timedelta(days=7) for p in d.predictions)
    day = d16
    while day <= d16 + timedelta(days=9):
        if day.weekday() < 5:
            mark(env.store, env.cfg, env.feed, env.fx, day=day, slot="us_close", now=_at(day))
        day += timedelta(days=1)
    assert (
        grade_due(
            env.store, env.cfg, env.feed, env.fx, day=d16 + timedelta(days=6), learning=env.learning
        )
        == []
    )
    graded = grade_due(
        env.store, env.cfg, env.feed, env.fx, day=d16 + timedelta(days=9), learning=env.learning
    )
    assert sorted(g.instrument_id for g in graded) == ["MYX:5183", "MYX:8869"]
    ctl = env.store.marks(CONTROL)
    for g in graded:
        assert g.note == "" or "exited" in g.note
        assert (
            abs(
                g.benchmark
                - float(
                    ctl[-1].equity_usd
                    / [m for m in ctl if m.day <= d16 + timedelta(days=1)][-1].equity_usd
                    - 1
                )
            )
            < 1e-9
        )
    assert len(env.learning.pending()) == 0 and len(env.learning.graded()) == 2
    both = realised_vs_control(env.store)
    assert set(both) == {DECIDED, CONTROL}


def test_an_observe_week_prediction_grades_from_the_decision_day_close(paper_env):
    env = paper_env
    d = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(1, 1),
        weights={"XNAS:NVDA": Decimal("0.23")},
        thesis="t",
        horizon=1,
        learning=env.learning,
        now=_at(env.week(1, 1)),
    )
    assert not d.refused
    for k in range(0, 4):
        day = env.week(1, 1) + timedelta(days=k)
        if day.weekday() < 5:
            mark(env.store, env.cfg, env.feed, env.fx, day=day, slot="us_close", now=_at(day))
    graded = grade_due(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(1, 4),
        learning=env.learning,
        dry_run=True,
    )
    assert len(graded) == 1 and "decision-day close" in graded[0].note
    assert len(env.learning.pending()) == 1  # dry run wrote nothing


def test_an_all_cash_night_leaves_a_row_a_prediction_and_a_grade(paper_env):
    """The 2026-09-08 hole: `decide` printed "logged and will be graded" over an
    empty write, so an all-cash night was indistinguishable from never asking."""
    env = paper_env
    d16 = env.week(3)
    mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(2, 4),
        slot="us_close",
        now=_at(env.week(2, 4)),
    )
    d = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={},
        thesis="nothing clears the evidence bar tonight",
        horizon=5,
        learning=env.learning,
        now=_at(d16),
    )
    assert not d.refused and d.all_cash
    rows = env.store.targets_on(DECIDED, d16)
    assert [(t.instrument_id, t.reason, t.weight) for t in rows] == [(CASH, ALL_CASH, Decimal(0))]
    assert rows[0].prediction_id and "allcash" in rows[0].prediction_id
    # never pending: `apply_pending` would ask a market for the price of CASH
    assert [t.instrument_id for t in env.store.pending_targets(DECIDED)] == []
    assert [a.status for _, a in env.store.applications(DECIDED)] == [ALL_CASH]

    (p,) = env.learning.pending()
    assert p.instrument_id == CASH and p.direction == 1 and p.context["all_cash"] is True

    day = d16
    while day <= d16 + timedelta(days=9):
        if day.weekday() < 5:
            mark(env.store, env.cfg, env.feed, env.fx, day=day, slot="us_close", now=_at(day))
        day += timedelta(days=1)
    assert (
        grade_due(
            env.store, env.cfg, env.feed, env.fx, day=d16 + timedelta(days=6), learning=env.learning
        )
        == []
    )
    (g,) = grade_due(
        env.store, env.cfg, env.feed, env.fx, day=d16 + timedelta(days=9), learning=env.learning
    )
    # Cash held its ground exactly; the control moved. Right iff the control lost.
    assert g.instrument_id == CASH and g.realised == 0.0
    ctl = env.store.marks(CONTROL)
    assert abs(g.benchmark - float(ctl[-1].equity_usd / ctl[0].equity_usd - 1)) < 1e-9
    assert g.correct is (g.benchmark < 0)
    assert env.learning.pending() == []


def test_a_second_decision_on_an_all_cash_day_needs_supersede(paper_env):
    env = paper_env
    d16 = env.week(3)
    mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(2, 4),
        slot="us_close",
        now=_at(env.week(2, 4)),
    )
    args = dict(thesis="t", horizon=5, learning=env.learning, now=_at(d16))
    first = decide(env.store, env.cfg, env.feed, env.fx, day=d16, weights={}, **args)
    assert first.all_cash
    again = decide(env.store, env.cfg, env.feed, env.fx, day=d16, weights={}, **args)
    assert again.refused and again.refusals[0].code == "duplicate"
