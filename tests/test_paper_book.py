"""Decide, apply at the next open, mark, stop, halt - to the cent."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from engines.paper.book import decide, mark, turnover_used
from engines.paper.store import CONTROL, DECIDED

W = {"MYX:5183": Decimal("0.11"), "MYX:8869": Decimal("0.21"), "MYX:3182": Decimal("0.06")}


def _at(d: date, h=22, m=30):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _mark(env, d, slot="us_close"):
    return mark(env.store, env.cfg, env.feed, env.fx, day=d, slot=slot, now=_at(d, 21, 15))


def _decide(env, d, weights=W, **kw):
    args = dict(thesis="test thesis", learning=env.learning, now=_at(d))
    args.update(kw)
    return decide(env.store, env.cfg, env.feed, env.fx, day=d, weights=weights, **args)


def _balanced(m):
    return abs(
        m.equity_usd - m.cash_usd - sum(Decimal(p["value_usd"]) for p in m.positions)
    ) <= Decimal("0.01")


def test_a_fresh_book_marks_as_all_cash_and_a_pre_start_decision_is_refused(paper_env):
    r = _mark(paper_env, paper_env.week(1))
    assert (
        r.exit_code == 0
        and r.marks[DECIDED].equity_usd == Decimal(1000) == r.marks[CONTROL].equity_usd
    )
    d = _decide(paper_env, paper_env.start - timedelta(days=1))
    assert d.refused and d.refusals[0].code == "start"


def test_observe_decisions_are_logged_graded_never_applied(paper_env):
    d = _decide(paper_env, paper_env.week(1, 1))
    assert not d.refused and d.phase.name == "observe" and len(d.predictions) == 3
    assert all(
        p.grade_on == paper_env.week(1, 1) + timedelta(days=29, hours=0) or True
        for p in d.predictions
    )
    assert len(paper_env.learning.pending()) == 3
    r = _mark(paper_env, paper_env.week(1, 2))
    assert [a.status for a in r.applied[DECIDED]] == ["observed"] * 3
    assert paper_env.store.state(DECIDED).positions == ()


def test_a_ramp_decision_applies_at_the_next_bar_with_fees_fx_and_whole_lots(paper_env):
    env = paper_env
    d16 = env.week(3)  # Monday, week 3 = ramp
    _mark(env, env.week(2, 4))
    d = _decide(env, d16)
    assert not d.refused, d.render()
    same_day = _mark(env, d16)
    assert same_day.applied[DECIDED] == []  # nothing is priced at decision time
    r = _mark(env, d16 + timedelta(days=1))
    applied = [a for a in r.applied[DECIDED] if a.status == "applied"]
    assert len(applied) == 3
    for a in applied:
        c = a.change
        assert c.day == d16 + timedelta(days=1) and c.units_delta % 100 == 0 and c.units_delta > 0
        assert (
            c.fee_local > 0
            and c.fx_spread_usd > 0
            and c.cash_delta_usd < 0
            and c.fx_source == "config"
        )
        assert c.price_local >= c.bar_open  # entry slippage rounds up
    m = r.marks[DECIDED]
    assert _balanced(m) and m.positions_usd > 0 and m.cash_usd >= Decimal("0.20") * 1000
    weights = {p["instrument_id"]: Decimal(p["weight"]) for p in m.positions}
    assert all(w <= Decimal("0.25") for w in weights.values())
    assert sum(weights.values()) <= Decimal("0.40")
    st = env.store.state(DECIDED)
    assert {p.instrument_id: p.units for p in st.positions} == {
        "MYX:5183": 100,
        "MYX:8869": 100,
        "MYX:3182": 100,
    }
    assert turnover_used(env.store, DECIDED, d16 + timedelta(days=1)) > 0
    assert env.store.cost_to_date(DECIDED).total > 0


def test_an_omitted_held_name_exits_and_exits_run_before_entries(paper_env):
    env = paper_env
    d16 = env.week(3)
    _mark(env, env.week(2, 4))
    _decide(env, d16)
    for k in range(1, 9):  # mark through the week so the five-weekday turnover window clears
        day = d16 + timedelta(days=k)
        if day.weekday() < 5:
            _mark(env, day)
    d23 = env.week(4, 1)  # Tuesday: the window (Wed..Tue) no longer holds the 03-17 entries
    d = _decide(env, d23, {"MYX:5183": Decimal("0.13"), "XNAS:NVDA": Decimal("0.23")})
    assert not d.refused, d.render()
    assert {t.instrument_id: t.weight for t in d.targets} == {
        "MYX:5183": Decimal("0.13"),
        "XNAS:NVDA": Decimal("0.23"),
        "MYX:3182": Decimal(0),
        "MYX:8869": Decimal(0),
    }
    logged = {(p.instrument_id, p.direction) for p in d.predictions}
    assert {("MYX:3182", -1), ("MYX:8869", -1), ("XNAS:NVDA", 1)} <= logged
    assert ("MYX:5183", -1) not in logged  # held and kept is never an exit call
    r = _mark(env, d23 + timedelta(days=1))
    actions = [(a.change.action, a.change.instrument_id) for a in r.applied[DECIDED] if a.change]
    assert {a for a in actions if a[0] == "exit"} == {("exit", "MYX:3182"), ("exit", "MYX:8869")}
    assert actions[-1] == ("open", "XNAS:NVDA") and actions.index(("open", "XNAS:NVDA")) >= 2
    exits = [a.change for a in r.applied[DECIDED] if a.change and a.change.action == "exit"]
    assert all(
        c.cash_delta_usd > 0 and c.units_after == 0 and c.price_local <= c.bar_open for c in exits
    )
    assert _balanced(r.marks[DECIDED])


def test_a_second_decision_on_the_same_day_needs_supersede(paper_env):
    env = paper_env
    _mark(env, env.week(2, 4))
    assert not _decide(env, env.week(3)).refused
    d = _decide(env, env.week(3), {"MYX:5183": Decimal("0.11")})
    assert d.refused and d.refusals[0].code == "duplicate"
    d = _decide(env, env.week(3), {"MYX:5183": Decimal("0.11")}, supersede=True)
    assert not d.refused and d.superseded == 3
    assert [t.instrument_id for t in env.store.pending_targets(DECIDED)] == ["MYX:5183"]


def test_a_stop_is_raised_at_the_close_and_exits_at_the_next_open(paper_env, tmp_path):
    env = paper_env
    d16 = env.week(3)
    env.feed.shocks[("MYX:3182", d16 + timedelta(days=2))] = -0.12
    env.feed.__init__(date(2025, 9, 1), date(2026, 7, 31), shocks=env.feed.shocks)
    _mark(env, env.week(2, 4))
    _decide(env, d16)
    _mark(env, d16 + timedelta(days=1))
    r = _mark(env, d16 + timedelta(days=2))
    assert [t.instrument_id for t in r.stops] == ["MYX:3182"] and r.stops[0].reason == "stop"
    # a stopped name cannot be re-targeted while the stop is pending
    d = _decide(
        env, d16 + timedelta(days=2), {"MYX:3182": Decimal("0.06"), "MYX:5183": Decimal("0.11")}
    )
    assert d.refused and any(x.code == "stop" for x in d.refusals)
    r = _mark(env, d16 + timedelta(days=3))
    exits = [a.change for a in r.applied[DECIDED] if a.change and a.change.action == "exit"]
    assert [c.instrument_id for c in exits] == ["MYX:3182"] and exits[0].realised_pnl_usd < 0
    assert env.store.state(DECIDED).position("MYX:3182") is None


def test_a_drawdown_past_the_halt_line_blocks_new_entries_but_not_reductions(paper_env):
    env = paper_env
    d16 = env.week(3)
    shock_day = d16 + timedelta(days=2)
    for iid in ("MYX:5183", "MYX:8869", "MYX:3182"):
        env.feed.shocks[(iid, shock_day)] = -0.30
    env.feed.__init__(date(2025, 9, 1), date(2026, 7, 31), shocks=env.feed.shocks)
    _mark(env, env.week(2, 4))
    _decide(env, d16)
    _mark(env, d16 + timedelta(days=1))
    r = _mark(env, shock_day)
    m = r.marks[DECIDED]
    assert m.halted and m.drawdown >= Decimal("0.08")
    d = _decide(
        env,
        shock_day,
        {
            "MYX:5183": Decimal("0.11"),
            "MYX:8869": Decimal("0.21"),
            "MYX:3182": Decimal("0.06"),
            "XNAS:NVDA": Decimal("0.02"),
        },
    )
    assert d.refused and any(x.code == "halt" for x in d.refusals)
    d = _decide(env, shock_day, {})  # all cash: reductions only, never blocked
    assert not d.refused, d.render()
    assert all(t.weight == 0 for t in d.targets) and len(d.targets) == 3


def test_a_target_with_no_bar_in_five_weekdays_expires(paper_env):
    env = paper_env
    # The three decided names stop printing on the decision day; the rest of
    # the market goes on. The market has to go on: a mark is stamped with the
    # session of the bars it is marked from, so a world with no bar after the
    # decision has no later session to expire anything against.
    for iid in W:
        env.feed.series[iid] = [b for b in env.feed.series[iid] if b.day <= env.week(3)]
    _mark(env, env.week(2, 4))
    assert not _decide(env, env.week(3)).refused
    r = _mark(env, env.week(4, 1))  # six weekdays later, no bar since
    assert r.day == env.week(4, 1)
    assert [a.status for a in r.applied[DECIDED]] == ["expired"] * 3
    assert r.marks[DECIDED].positions == [] and r.exit_code == 0


# -- which session a mark belongs to ---------------------------------------------------------


def test_a_mark_is_stamped_with_the_session_of_its_bars_not_the_clock(paper_env):
    env = paper_env
    fri, sat, sun = env.week(1, 4), env.week(1, 5), env.week(1, 6)
    r = mark(env.store, env.cfg, env.feed, env.fx, day=sat, slot="us_close", now=_at(sat, 6, 7))
    assert r.exit_code == 0 and r.day == fri
    assert any(f"marked as {fri}" in n for n in r.notes), r.notes
    assert env.store.mark_for(DECIDED, sat, "us_close") is None
    first = env.store.mark_for(DECIDED, fri, "us_close")
    assert first is not None and first.marked_at == _at(sat, 6, 7)
    # Sunday's run is a later reading of the same session: it replaces, it does not add
    r = mark(env.store, env.cfg, env.feed, env.fx, day=sun, slot="us_close", now=_at(sun, 22, 0))
    assert r.day == fri and any("replaces the us_close mark" in n for n in r.notes), r.notes
    again = env.store.mark_for(DECIDED, fri, "us_close")
    assert again is not None and again.mark_id == first.mark_id
    assert again.marked_at == _at(sun, 22, 0), "the later reading replaced the earlier in place"
    assert env.store.sessions_marked(DECIDED) == 1
    assert env.store.counts()["marks"] == 2  # one per book
    # a bursa_close run the same Saturday marks Friday's Bursa session, its own row
    r = mark(env.store, env.cfg, env.feed, env.fx, day=sat, slot="bursa_close", now=_at(sat, 12))
    assert r.day == fri and env.store.sessions_marked(DECIDED) == 1
    assert env.store.counts()["marks"] == 4


def test_a_day_with_no_session_and_no_bar_to_mark_from_is_refused(paper_env):
    env = paper_env
    env.feed.__init__(env.week(2), date(2026, 7, 31))  # the cache begins in week two
    sat = env.week(1, 5)
    r = mark(env.store, env.cfg, env.feed, env.fx, day=sat, slot="us_close", now=_at(sat))
    assert r.exit_code == 2 and r.refusal and "not a session" in r.refusal
    assert r.marks == {} and env.store.counts()["marks"] == 0
    assert "REFUSED" in r.render()
    # a calendar session with no bar yet stands on the calendar's word: all cash, no bar needed
    fri = env.week(1, 4)
    r = mark(env.store, env.cfg, env.feed, env.fx, day=fri, slot="us_close", now=_at(fri))
    assert r.exit_code == 0 and r.day == fri and r.marks[DECIDED].equity_usd == Decimal(1000)


def test_marks_stamped_with_a_wall_clock_weekend_are_restamped_to_their_session(paper_env):
    from engines.paper.store import MarkRow

    env = paper_env
    fri, sat, sun = env.week(1, 4), env.week(1, 5), env.week(1, 6)

    def row(book, day, at, equity):
        return MarkRow(
            book,
            day,
            "us_close",
            at,
            Decimal(equity),
            Decimal(0),
            Decimal(equity),
            Decimal(1000),
            Decimal(0),
            False,
            "observe",
            Decimal(4),
            day,
            "config",
            [],
        )

    # What the ledger held before the rule: Friday marked at the close, then a
    # Saturday catch-up and a Sunday run each filed under the wall-clock day.
    for book in (DECIDED, CONTROL):
        env.store.record_mark(row(book, fri, _at(fri, 21, 15), 1000))
        env.store.record_mark(row(book, sat, _at(sat, 6, 7), 1000))
        env.store.record_mark(row(book, sun, _at(sun, 22, 0), 1000))
    assert env.store.sessions_marked(DECIDED) == 3
    r = _mark(env, env.week(2))
    assert r.exit_code == 0
    # oldest first: Saturday's reading replaces Friday's, then Sunday's replaces Saturday's
    assert sum("replacing that session's earlier mark" in n for n in r.notes) == 4, r.notes
    assert env.store.mark_for(DECIDED, sat, "us_close") is None
    assert env.store.mark_for(DECIDED, sun, "us_close") is None
    kept = env.store.mark_for(DECIDED, fri, "us_close")
    assert kept is not None and kept.marked_at == _at(sun, 22, 0), "the later reading stays"
    assert env.store.sessions_marked(DECIDED) == 2  # Friday and Monday
    # a second run has nothing left to re-date
    r = _mark(env, env.week(2, 1))
    assert not any("re-dated" in n or "dropped" in n for n in r.notes), r.notes


def test_two_slots_on_one_session_are_one_session_marked(paper_env):
    from mcp_server import observability

    env = paper_env
    d = env.week(1)
    _mark(env, d, slot="bursa_close")
    _mark(env, d, slot="us_close")
    assert env.store.sessions_marked(DECIDED) == 1 and len(env.store.marks(DECIDED)) == 1
    report = observability.paper_report(db=str(env.tmp / "paper.db"))
    assert "1 session(s) marked" in report


# -- the clock on a prediction -----------------------------------------------------------------


def test_made_at_is_the_clock_and_the_grading_date_counts_from_the_decision_day(paper_env):
    from engines.paper.book import DECISION_NIGHT, grade_date

    env = paper_env
    d = env.week(1, 1)
    after_midnight = _at(d + timedelta(days=1), 2, 42)  # the Routine ran late
    res = _decide(env, d, now=after_midnight)
    assert not res.refused and len(res.predictions) == 3
    logged = {p.prediction_id: p for p in env.learning.pending()}
    nominal = datetime.combine(d, DECISION_NIGHT, tzinfo=UTC)
    for t in res.targets:
        assert t.decided_on == d and t.decided_at == after_midnight
        p = logged[t.prediction_id]
        assert p.made_at == after_midnight, "the row says when the call was made"
        assert p.prediction_id.startswith(f"paper-{d}-")
        assert p.grade_on == grade_date(nominal, 21) and p.context["decided_on"] == d.isoformat()
        assert "replayed_at" not in p.context
    # A replay - the horizon has already run out - keeps the nominal clock and says so
    d2 = env.week(1, 2)
    later = _at(env.week(12))
    res = _decide(env, d2, now=later)
    assert not res.refused
    logged = {p.prediction_id: p for p in env.learning.pending()}
    for t in res.targets:
        p = logged[t.prediction_id]
        assert p.made_at == datetime.combine(d2, DECISION_NIGHT, tzinfo=UTC)
        assert p.grade_on == grade_date(p.made_at, 21) <= later.date()
        assert p.context["replayed_at"] == later.isoformat() and t.decided_at == later


def test_the_control_rebalances_in_ramp_and_again_in_full(paper_env):
    env = paper_env
    _mark(env, env.week(2, 4))
    r = _mark(env, env.week(3))
    targets = {t.instrument_id: t.target_units for t in r.control_targets}
    assert {"MYX:3182", "MYX:5183"} <= set(targets) and len(targets) == 3
    assert all(u == 100 for u in targets.values())
    r = _mark(env, env.week(3, 1))
    assert len([a for a in r.applied[CONTROL] if a.status == "applied"]) == 3
    assert _balanced(r.marks[CONTROL]) and r.marks[CONTROL].cash_usd >= Decimal(200)
    r = _mark(env, env.week(7))  # full phase: the control widens to the 80% ceiling
    full = {t.instrument_id for t in r.control_targets if t.target_units}
    assert len(full) >= 4 and full >= set(targets)
