"""The paper book trades only what could have been traded, once, at a price printed after the decision.

Every test here fails on the code before 2026-10-08:

  * a target decided before a Bursa holiday filled on Yahoo's holiday filler
    row, at the previous close, dated a day nothing traded;
  * offline, the price chain served the first feed that had EVER cached a
    symbol, however stale, ahead of a fresher body behind it;
  * pending targets for one name each bought the whole difference against the
    same holding, so three weekend decisions tripled a position;
  * a stop and a decision exit for one name recorded a second, zero-unit exit
    with a platform fee;
  * a decision recorded after the next session had opened filled at that open;
  * re-marking an earlier session valued later fills at earlier closes;
  * a replaced reading's spike stayed in the peak, and its stop still sold;
  * a session whose bar came back with a blank close re-marked the session
    before it, at exit 0.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from core.market.prices import Bar, PriceSeries
from engines.paper.book import decide, mark
from engines.paper.pricing import first_bar_after, last_close
from engines.paper.store import DECIDED, TargetRow

W = {"MYX:5183": Decimal("0.11"), "MYX:8869": Decimal("0.21"), "MYX:3182": Decimal("0.06")}


def _at(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _mark(env, d, slot="bursa_close", now=None):
    return mark(env.store, env.cfg, env.feed, env.fx, day=d, slot=slot, now=now or _at(d, 10, 11))


def _decide(env, d, weights=W, now=None, **kw):
    return decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d,
        weights=weights,
        thesis="test thesis",
        learning=env.learning,
        now=now or _at(d, 22, 50),
        **kw,
    )


def _units(env, iid):
    pos = env.store.state(DECIDED).position(iid)
    return pos.units if pos else 0


# --- real sessions only -----------------------------------------------------------------------


def test_a_fill_skips_bursa_holidays_and_the_last_close_is_the_last_session(paper_env):
    feed = paper_env.feed
    thu = date(2026, 3, 19)  # Fri 03-20 and Mon 03-23 are XKLS holidays; the feed has rows for both
    assert any(b.day == date(2026, 3, 20) for b in feed.series["MYX:8869"])
    bar = first_bar_after(feed, "MYX:8869", thu, date(2026, 3, 27))
    assert bar is not None and bar.day == date(2026, 3, 24)
    assert last_close(feed, "MYX:8869", date(2026, 3, 23))[1] == thu


def test_a_carried_filler_row_is_not_a_session_even_off_the_holiday_table(paper_env):
    feed = paper_env.feed
    rows = feed.series["MYX:8869"]
    i = next(k for k, b in enumerate(rows) if b.day == date(2026, 7, 10))  # a Friday, no holiday
    prev = rows[i - 1]
    rows[i] = Bar(rows[i].day, prev.close, prev.close, prev.close, prev.close, 0)
    bar = first_bar_after(feed, "MYX:8869", date(2026, 7, 9), date(2026, 7, 17))
    assert bar is not None and bar.day == date(2026, 7, 13)


# --- offline, the freshest cached body wins ---------------------------------------------------


class _Cached:
    def __init__(self, name, last, fetched):
        self.name = name
        self.last = last
        self.fetched = fetched

    def fetch(self, instrument_id, start=None, end=None):
        days = [self.last - timedelta(days=k) for k in range(5, -1, -1)]
        bars = [Bar(d, 10.0, 10.5, 9.5, 10.0, 1e6) for d in days if end is None or d <= end]
        return PriceSeries(instrument_id, bars)

    def fetched_at(self, instrument_id):
        return self.fetched


def test_offline_the_chain_serves_the_freshest_body_not_the_first(monkeypatch):
    from core.market.feed import ChainedFeed

    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    d = date(2026, 10, 2)
    stooq = _Cached("stooq", d, datetime(2026, 10, 2, 22, 0, tzinfo=UTC))
    yahoo = _Cached("yahoo", d + timedelta(days=3), datetime(2026, 10, 5, 22, 0, tzinfo=UTC))
    chain = ChainedFeed([stooq, yahoo])
    assert chain.fetch("XNAS:NVDA").raw()[-1].day == d + timedelta(days=3)
    assert chain.source_used == "yahoo"
    assert chain.fetched_at("XNAS:NVDA") == yahoo.fetched


# --- one live target per name ------------------------------------------------------------------


def test_three_weekend_decisions_buy_one_position_not_three(paper_env):
    env = paper_env
    _mark(env, env.week(4, 3))  # Thursday 03-26
    for k in (4, 5, 6):  # Friday, Saturday, Sunday: the same book each night
        d = _decide(env, env.week(4, k))
        assert not d.refused, d.render()
    pending = env.store.pending_targets(DECIDED)
    assert len(pending) == 3 and {t.decided_on for t in pending} == {env.week(4, 6)}
    r = _mark(env, env.week(5, 0))  # Monday 03-30
    assert _units(env, "MYX:8869") == 100, "one lot for 21%, not one per decision"
    weights = {p["instrument_id"]: Decimal(p["weight"]) for p in r.marks[DECIDED].positions}
    assert max(weights.values()) <= Decimal("0.25") and sum(weights.values()) <= Decimal("0.40")
    superseded = [a for _, a in env.store.applications(DECIDED) if a.status == "superseded"]
    assert len(superseded) == 6


def test_an_all_cash_night_withdraws_the_entries_still_waiting(paper_env):
    env = paper_env
    _mark(env, env.week(4, 3))
    _decide(env, env.week(4, 4))  # Friday: buy three names at Monday's open
    _decide(env, env.week(4, 5), weights={})  # Saturday: all cash
    _mark(env, env.week(5, 0))
    assert env.store.state(DECIDED).positions == ()


def test_targets_already_pending_together_are_applied_once(paper_env):
    """The same rule at the point of use, for rows already in a ledger."""
    env = paper_env
    _mark(env, env.week(4, 3))
    fri, sat = env.week(4, 4), env.week(4, 5)
    for d in (fri, sat):
        env.store.record_targets(
            [
                TargetRow(
                    DECIDED, d, _at(d, 22), "MYX:8869", Decimal("0.21"), "decision", "ramp", "t"
                )
            ]
        )
    r = _mark(env, env.week(5, 0))
    assert _units(env, "MYX:8869") == 100
    assert sorted(a.status for a in r.applied[DECIDED]) == ["applied", "superseded"]


def test_a_stop_and_a_decision_exit_make_one_exit_and_no_zero_unit_trade(paper_env):
    env = paper_env
    mon, tue, wed = env.week(5, 0), env.week(5, 1), env.week(5, 2)  # 03-30 .. 04-01
    _mark(env, env.week(4, 4))
    _decide(env, env.week(4, 4), weights={"MYX:8869": Decimal("0.21")})
    _mark(env, mon)
    assert _units(env, "MYX:8869") == 100
    env.store.record_targets(
        [
            TargetRow(
                DECIDED,
                tue,
                _at(tue, 10, 11),
                "MYX:8869",
                Decimal(0),
                "stop",
                "ramp",
                "stop",
                target_units=0,
            ),
            TargetRow(
                DECIDED, tue, _at(tue, 22, 50), "MYX:8869", Decimal(0), "decision", "ramp", "x"
            ),
        ]
    )
    r = _mark(env, wed)
    changes = [a.change for a in r.applied[DECIDED] if a.change]
    assert len(changes) == 1 and changes[0].units_delta == -100
    assert all(c.units_delta != 0 for c in env.store.changes(DECIDED))
    statuses = {a.target.reason: a.status for a in r.applied[DECIDED]}
    assert statuses == {"stop": "applied", "decision": "superseded"}


# --- no look-ahead fills -----------------------------------------------------------------------


def test_a_decision_recorded_after_the_next_open_fills_at_the_session_after(paper_env):
    env = paper_env
    tue = env.week(4, 1)  # 03-24
    _mark(env, tue)
    late = _at(tue + timedelta(days=1), 2, 42)  # Wednesday 02:42Z: Bursa opened at 01:00Z
    d = _decide(env, tue, weights={"MYX:8869": Decimal("0.21")}, now=late)
    assert not d.refused, d.render()
    wed = _mark(env, tue + timedelta(days=1))
    assert not [a for a in wed.applied[DECIDED] if a.change], "Wednesday's open printed first"
    thu = _mark(env, tue + timedelta(days=2))
    (c,) = [a.change for a in thu.applied[DECIDED] if a.change]
    assert c.day == tue + timedelta(days=2)


# --- marks are a function of their session -----------------------------------------------------


def test_re_marking_an_earlier_session_values_that_sessions_holdings(paper_env):
    env = paper_env
    _mark(env, env.week(4, 3))
    _decide(env, env.week(4, 3), weights={"MYX:8869": Decimal("0.21")})
    _mark(env, env.week(4, 4))  # Friday: the fill
    assert _units(env, "MYX:8869") == 100
    again = _mark(env, env.week(4, 3), now=_at(env.week(4, 4), 23))  # Thursday, re-marked later
    m = again.marks[DECIDED]
    assert m.positions == [] and m.equity_usd == m.cash_usd == Decimal(1000)


def test_a_replaced_reading_leaves_no_peak_and_no_stop_behind(paper_env):
    env = paper_env
    _mark(env, env.week(4, 4))
    _decide(env, env.week(4, 4), weights={"MYX:8869": Decimal("0.21")})
    _mark(env, env.week(5, 0))  # Monday 03-30: the fill
    assert _units(env, "MYX:8869") == 100
    day = env.week(5, 1)
    clean = env.feed.series["MYX:8869"]
    i = next(k for k, b in enumerate(clean) if b.day == day)
    real = clean[i]

    clean[i] = Bar(day, real.open, real.high, real.low * 0.8, real.close * 0.85, real.volume)
    dip = _mark(env, day, now=_at(day, 8))  # a mid-session reading, 15% down
    assert dip.stops and dip.stops[0].instrument_id == "MYX:8869"

    clean[i] = Bar(day, real.open, real.high * 1.3, real.low, real.close * 1.3, real.volume)
    spike = _mark(env, day, now=_at(day, 9))
    assert spike.marks[DECIDED].peak_usd > Decimal(1000)

    clean[i] = real
    settled = _mark(env, day, now=_at(day, 10, 11))
    m = settled.marks[DECIDED]
    earlier = env.store.latest_mark(DECIDED, on_or_before=env.week(5, 0))
    assert m.peak_usd == max(Decimal(1000), earlier.equity_usd, m.equity_usd)
    assert not [t for t in env.store.pending_targets(DECIDED) if t.reason == "stop"]
    withdrawn = [a for _, a in env.store.applications(DECIDED) if a.status == "withdrawn"]
    assert len(withdrawn) == 1


def test_a_closed_session_with_a_blank_close_is_a_problem_not_a_re_mark(paper_env):
    env = paper_env
    thu, fri = env.week(4, 3), env.week(4, 4)
    _mark(env, thu)
    before = env.store.mark_for(DECIDED, thu, "bursa_close")
    for iid, rows in env.feed.series.items():
        if iid.startswith("MYX:"):
            env.feed.series[iid] = [b for b in rows if b.day != fri]  # the vendor served no close
    r = _mark(env, fri, now=_at(fri, 15, 59))
    assert r.exit_code == 3 and any("no usable bar" in p for p in r.problems)
    assert env.store.mark_for(DECIDED, fri, "bursa_close") is None
    assert env.store.mark_for(DECIDED, thu, "bursa_close").marked_at == before.marked_at


def test_the_after_midnight_us_close_run_does_not_re_mark_the_session_before(paper_env):
    """2026-10-08 01:00Z: the 10-07 US rows had blank closes, and the run is
    dated the next UTC day, so checking only that day saw nothing owed."""
    env = paper_env
    tue, wed = env.week(4, 1), env.week(4, 2)
    mark(env.store, env.cfg, env.feed, env.fx, day=tue, slot="us_close", now=_at(tue, 22, 47))
    for iid, rows in env.feed.series.items():
        if iid.startswith("XNAS:"):  # the 03-25 close came back blank; nothing later exists yet
            env.feed.series[iid] = [b for b in rows if b.day < wed]
    r = mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=wed + timedelta(days=1),
        slot="us_close",
        now=_at(wed + timedelta(days=1), 1, 0),
    )
    assert r.exit_code == 3 and any(str(wed) in p for p in r.problems)


def test_a_session_still_trading_is_not_owed_its_bar(paper_env):
    env = paper_env
    fri = env.week(4, 4)
    for iid, rows in env.feed.series.items():
        if iid.startswith("MYX:"):
            env.feed.series[iid] = [b for b in rows if b.day != fri]
    r = _mark(env, fri, now=_at(fri, 5))  # 13:00 MYT: Bursa is still trading
    assert r.exit_code == 0 and r.day == env.week(4, 3)
