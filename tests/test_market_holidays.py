"""A calendar with no holidays in it is a weekday filter.

On 2026-09-16, a Wednesday and Malaysia Day, Bursa Malaysia was shut. Every
adapter in markets/ was built with `holidays=frozenset()`, so `is_session`
said True, the feedback page found the day by its blank bars, and any session
count spanning it came out one too many. The tables now live in
markets/holidays.py and reach the adapters through markets.registry.get; this
file pins that they do, and that they are current.

The expiry test is meant to fail. A holiday table that has run out does not
error - the calendar quietly goes back to being the weekday filter it was on
2026-09-16 - so the only way a stale table announces itself is a test that
reads the date. When it fails, extend the table from the exchange's own
notice; do not move the date.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfoNotFoundError

import pytest

from core.market.calendar import (
    UNKNOWN,
    SessionCalendar,
    SessionWindow,
    TradingSession,
    price_state,
)
from markets import holidays
from markets.holidays import EARLY_CLOSES, EXPIRES, HOLIDAYS, closures
from markets.registry import get
from markets.xkls import XKLS
from markets.xnas import XNAS

MALAYSIA_DAY = date(2026, 9, 16)
THANKSGIVING = date(2026, 11, 26)


def _session(cal: SessionCalendar, d: date) -> TradingSession:
    s = cal.session(d)
    assert s is not None, f"{d} is not a session on this calendar"
    return s


# -- the tables have not run out ----------------------------------------------------------


@pytest.mark.parametrize("mic", sorted(EXPIRES))
def test_the_holiday_table_has_not_expired(mic):
    """Failing loudly is the point. Past EXPIRES the calendar is not wrong yet,
    it is unverified, and for a holiday table that is the same thing: the next
    closure it does not know about is a day it will call a session."""
    assert date.today() <= EXPIRES[mic], (
        f"the {mic} holiday table in markets/holidays.py vouches for nothing after "
        f"{EXPIRES[mic]}; extend it from the exchange's own notice, then move EXPIRES"
    )


def test_every_market_with_closures_names_an_expiry():
    assert set(HOLIDAYS) <= set(EXPIRES)
    assert set(EARLY_CLOSES) <= set(EXPIRES)


# -- the tables say what the exchanges published --------------------------------------------


def test_bursa_has_sixteen_closures_in_2026_and_only_new_year_in_2027():
    closed = HOLIDAYS["XKLS"]
    assert len({d for d in closed if d.year == 2026}) == 16
    assert {d for d in closed if d.year == 2027} == {date(2027, 1, 1)}
    assert MALAYSIA_DAY in closed
    assert "XKLS" not in EARLY_CLOSES  # no half day since 26 January 2024


def test_nasdaq_has_ten_closures_a_year_and_the_one_o_clock_early_closes():
    closed = HOLIDAYS["XNAS"]
    assert len({d for d in closed if d.year == 2026}) == 10
    assert len({d for d in closed if d.year == 2027}) == 10
    assert THANKSGIVING in closed
    assert EARLY_CLOSES["XNAS"] == {
        date(2026, 11, 27): time(13, 0),
        date(2026, 12, 24): time(13, 0),
        date(2027, 11, 26): time(13, 0),
    }


@pytest.mark.parametrize("mic", sorted(HOLIDAYS))
def test_a_closure_is_a_weekday_and_an_early_close_is_not_also_a_closure(mic):
    """An exchange does not announce a Saturday as a holiday; a weekend date in
    the table is a typo, and the observed-holiday rules exist to move it."""
    for d in HOLIDAYS[mic]:
        assert d.weekday() < 5, f"{mic}: {d} is a {d:%A}"
    for d in EARLY_CLOSES.get(mic, {}):
        assert d.weekday() < 5, f"{mic}: {d} is a {d:%A}"
        assert d not in HOLIDAYS[mic], f"{mic}: {d} is both shut and shutting early"


def test_a_market_without_a_table_gets_an_empty_one_not_an_error():
    """Nine of the eleven adapters have no table yet; refusing to build them
    would take their fee schedules down with the calendar. The gap stays
    visible: `expires` is None only where nothing was ever entered."""
    none = closures("XSES")
    assert none.holidays == frozenset() and dict(none.early_closes) == {} and none.expires is None
    assert closures("XKLS").expires == EXPIRES["XKLS"]


# -- the registry hands them to the adapters --------------------------------------------------


def test_the_registry_builds_bursa_with_its_closures():
    cal = get("XKLS").calendar
    assert not cal.is_session(MALAYSIA_DAY)
    assert cal.holidays == HOLIDAYS["XKLS"]
    assert get("MYX").calendar is cal  # the alias reaches the same table


def test_the_registry_builds_nasdaq_with_its_closures_and_early_closes():
    cal = get("XNAS").calendar
    assert not cal.is_session(THANKSGIVING)
    assert cal.early_closes == EARLY_CLOSES["XNAS"]


def test_an_adapter_built_bare_still_has_no_holidays():
    """The classes default to none so a test fixture can build one without a
    year's worth of dates; the table is the registry's to supply. This is the
    behaviour every caller got for a year, which is why it is pinned as the
    fixture case and not the market's."""
    assert XKLS().calendar.is_session(MALAYSIA_DAY)
    assert XNAS().calendar.is_session(THANKSGIVING)
    assert not XKLS(holidays=frozenset({MALAYSIA_DAY})).calendar.is_session(MALAYSIA_DAY)


# -- what the calendar now says ---------------------------------------------------------------


def test_malaysia_day_is_not_a_bursa_session_and_the_week_has_four():
    cal = get("XKLS").calendar
    assert not cal.is_session(MALAYSIA_DAY)
    assert cal.count_sessions(date(2026, 9, 14), date(2026, 9, 18)) == 4
    assert cal.shift(date(2026, 9, 15), 1) == date(2026, 9, 17)


def test_thanksgiving_is_not_a_nasdaq_session_and_the_friday_after_ends_at_one():
    cal = get("XNAS").calendar
    assert not cal.is_session(THANKSGIVING)
    friday = _session(cal, date(2026, 11, 27))
    assert friday.half_day
    assert friday.close_utc() == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 EST
    assert friday.open_utc() == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)


def test_the_nasdaq_close_follows_new_york_daylight_saving():
    """A fixed UTC-5 put the modelled close at 21:00Z all year; New York is
    UTC-4 from 8 March to 1 November 2026, so the September close is 20:00Z."""
    cal = get("XNAS").calendar
    assert _session(cal, date(2026, 9, 17)).close_utc() == datetime(2026, 9, 17, 20, 0, tzinfo=UTC)
    assert _session(cal, date(2026, 11, 20)).close_utc() == datetime(
        2026, 11, 20, 21, 0, tzinfo=UTC
    )


def test_the_bursa_close_is_the_auction_not_the_end_of_continuous_trading():
    """Continuous trading ends 16:45 MYT; the closing auction and trading-at-last
    run to 17:00, and the close a bar carries is the auction's."""
    session = _session(get("XKLS").calendar, date(2026, 9, 17))
    assert session.close_utc() == datetime(2026, 9, 17, 9, 0, tzinfo=UTC)


def test_a_bar_dated_a_holiday_is_unknown_whenever_it_was_pulled():
    """There was no session for it to be the close of. Yahoo emitted a blank row
    for Bursa on Malaysia Day; had it filled one in, the clock could not have
    settled it."""
    for at in (
        datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
        datetime(2026, 9, 16, 23, 31, tzinfo=UTC),
        datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    ):
        assert price_state("XKLS", MALAYSIA_DAY, at) == UNKNOWN
        assert price_state("MYX", MALAYSIA_DAY, at) == UNKNOWN
    assert price_state("XNAS", THANKSGIVING, datetime(2026, 11, 27, tzinfo=UTC)) == UNKNOWN


# -- the calendar's two shapes of a short day -------------------------------------------------

LUNCH_BREAK = (SessionWindow(time(9, 0), time(12, 30)), SessionWindow(time(14, 30), time(17, 0)))
ONE_WINDOW = (SessionWindow(time(9, 30), time(16, 0)),)


def test_an_early_close_cuts_a_single_window_where_a_half_day_could_not():
    """Dropping the only window leaves nothing; naming the time the day ends
    leaves the morning and marks the session short."""
    day = date(2026, 11, 27)
    cal = SessionCalendar(ONE_WINDOW, -5, early_closes={day: time(13, 0)})
    s = _session(cal, day)
    assert s.windows == (SessionWindow(time(9, 30), time(13, 0)),) and s.half_day
    assert _session(cal, date(2026, 11, 30)).windows == ONE_WINDOW


def test_an_early_close_on_a_lunch_break_market_keeps_whole_windows_before_it():
    cal = SessionCalendar(
        LUNCH_BREAK, 8, early_closes={date(2026, 5, 4): time(12, 0), date(2026, 5, 5): time(15, 0)}
    )
    assert _session(cal, date(2026, 5, 4)).windows == (SessionWindow(time(9, 0), time(12, 0)),)
    assert _session(cal, date(2026, 5, 5)).windows == (
        LUNCH_BREAK[0],
        SessionWindow(time(14, 30), time(15, 0)),
    )


def test_a_half_day_still_drops_the_afternoon_when_no_time_is_given():
    """The lunch-break markets keep their existing shape of the fact."""
    cal = SessionCalendar(LUNCH_BREAK, 8, half_days=frozenset({date(2026, 5, 4)}))
    s = _session(cal, date(2026, 5, 4))
    assert s.windows == (LUNCH_BREAK[0],) and s.half_day
    assert s.close_utc() == datetime(2026, 5, 4, 4, 30, tzinfo=UTC)


def test_an_early_close_before_the_open_is_refused():
    """A day that ends before it starts is a holiday, and belongs in the other table."""
    with pytest.raises(ValueError, match="belongs in holidays"):
        SessionCalendar(ONE_WINDOW, -5, early_closes={date(2026, 11, 27): time(9, 0)})


# -- the clock --------------------------------------------------------------------------------


def test_a_named_zone_or_an_offset_but_not_neither():
    with pytest.raises(ValueError, match="zone name or a UTC offset"):
        SessionCalendar(ONE_WINDOW)
    assert SessionCalendar(ONE_WINDOW, tz="America/New_York").tz_offset_hours is None
    assert SessionCalendar(ONE_WINDOW, 8).tz is None


def test_an_unresolvable_zone_falls_back_to_the_offset_and_says_so():
    """Python ships no zone database on Windows. tzdata is a direct dependency
    now, but an environment installed without it must not lose its fee schedules
    over a calendar - it runs an hour out under daylight saving, and is told once."""
    with pytest.warns(RuntimeWarning, match="not in this machine's zone database"):
        cal = SessionCalendar(ONE_WINDOW, -5, tz="Nowhere/Imaginary")
    assert _session(cal, date(2026, 9, 17)).close_utc() == datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
    with pytest.raises(ZoneInfoNotFoundError):  # no offset to fall back on: the error is the answer
        SessionCalendar(ONE_WINDOW, tz="Nowhere/Imaginary")


def test_the_loader_is_pure_data():
    """No I/O behind the table: the registry may build an adapter anywhere,
    including offline and in a test that has no network."""
    src = inspect.getsource(holidays)
    for word in ("open(", "urllib", "requests", "sqlite", "Path("):
        assert word not in src, word
