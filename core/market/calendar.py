"""Session calendars.

docs/06 section 2.2: session calendars differ and include lunch breaks in several
Asian markets. Aligning to UTC bars with a session_id is the only way daily
returns compare correctly across markets.

A calendar given no closures is a weekday filter, not a calendar. On 2026-09-16,
Malaysia Day, Bursa Malaysia was shut and every adapter here still called it a
session, because none had ever been handed a holiday. The tables now live in
`markets/holidays.py` and `markets.registry.get` passes them in; an adapter
built bare, as the tests do, still has none, which is right for a fixture.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time


@dataclass(frozen=True)
class TradingSession:
    day: date
    windows: tuple[SessionWindow, ...]
    #: The market's own clock. A named zone where the market keeps daylight
    #: saving, a fixed offset where it does not; the calendar decides which.
    zone: tzinfo
    half_day: bool = False

    @property
    def session_id(self) -> str:
        return self.day.isoformat()

    def open_utc(self) -> datetime:
        return self._at(self.windows[0].start)

    def close_utc(self) -> datetime:
        """When the day's last window shuts. An early close is already in the
        windows this session was built with, so it needs no second look here,
        and `price_state` reads this alone."""
        return self._at(self.windows[-1].end)

    def _at(self, t: time) -> datetime:
        return datetime.combine(self.day, t, tzinfo=self.zone).astimezone(UTC)


def _zone(tz: str | None, offset_hours: int | None) -> tzinfo:
    """The zone a market's clock runs on.

    A named zone is the only way to get a market that keeps daylight saving
    right. New York is UTC-4 from the second Sunday of March to the first
    Sunday of November and UTC-5 otherwise, so a fixed -5 put the modelled
    Nasdaq close at 21:00Z for eight months of the year when the real one was
    20:00Z, and a settled bar fetched in that hour read as provisional. The
    integer offset stays for the markets that never shift and as the fallback.

    The fallback exists because Python ships no zone database on Windows: it
    comes from the `tzdata` package, which this project depends on directly
    (it used to arrive only behind the optional broker extra, and the Windows
    CI runner had none). An environment installed some other way may still
    lack it, and a calendar that raised there would take the fee
    schedules and lot sizes down with it. There the calendar runs on the offset
    and says so once, because a close an hour late is the defect `price_state`
    exists to catch and a silent one is indistinguishable from the right answer.
    """
    if tz is not None:
        try:
            return ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            if offset_hours is None:
                raise
            warnings.warn(
                f"{tz!r} is not in this machine's zone database; the session calendar "
                f"runs on a fixed UTC{offset_hours:+d} and is an hour out under daylight "
                f"saving. On Windows install the tzdata package; elsewhere check the name.",
                RuntimeWarning,
                stacklevel=3,
            )
    if offset_hours is None:
        raise ValueError("a session calendar needs a zone name or a UTC offset")
    return timezone(timedelta(hours=offset_hours))


def _cut_at(windows: tuple[SessionWindow, ...], at: time) -> tuple[SessionWindow, ...]:
    """The day's windows up to `at`: whole windows that end before it, and the
    one it falls inside cut short. A 13:00 close on a 09:30-16:00 market is one
    window ending 13:00; a 12:00 close on a lunch-break market is the morning."""
    out = []
    for w in windows:
        if w.start >= at:
            break
        out.append(w if w.end <= at else SessionWindow(w.start, at))
    return tuple(out)


class SessionCalendar:
    def __init__(
        self,
        windows: tuple[SessionWindow, ...],
        tz_offset_hours: int | None = None,
        holidays: frozenset[date] = frozenset(),
        half_days: frozenset[date] = frozenset(),
        weekend: tuple[int, ...] = (5, 6),
        *,
        tz: str | None = None,
        early_closes: Mapping[date, time] | None = None,
    ) -> None:
        """`half_days` and `early_closes` are two shapes of the same fact.

        A date in `half_days` keeps the first window only, which is what a half
        day means on a lunch-break market: the morning trades, the afternoon
        does not. A single-window market cannot be described that way - drop
        Nasdaq's one window and nothing is left - so `early_closes` names the
        time the day ends instead, and the windows are cut there. Either form
        marks the session `half_day`.
        """
        self.windows = windows
        self.tz = tz
        self.tz_offset_hours = tz_offset_hours
        self.zone = _zone(tz, tz_offset_hours)
        self.holidays = holidays
        self.half_days = half_days
        self.early_closes: dict[date, time] = dict(early_closes or {})
        self.weekend = weekend
        for d, at in self.early_closes.items():
            if at <= windows[0].start:
                raise ValueError(
                    f"early close {at} on {d} is not after the open {windows[0].start}; "
                    f"a day that ends before it starts is a holiday, and belongs in holidays"
                )

    def is_session(self, d: date) -> bool:
        return d.weekday() not in self.weekend and d not in self.holidays

    def session(self, d: date) -> TradingSession | None:
        if not self.is_session(d):
            return None
        early = self.early_closes.get(d)
        if early is not None:
            windows = _cut_at(self.windows, early)
        elif d in self.half_days:
            windows = (self.windows[0],)
        else:
            windows = self.windows
        return TradingSession(d, windows, self.zone, early is not None or d in self.half_days)

    def sessions_between(self, start: date, end: date) -> list[TradingSession]:
        out, cur = [], start
        while cur <= end:
            s = self.session(cur)
            if s:
                out.append(s)
            cur += timedelta(days=1)
        return out

    def count_sessions(self, start: date, end: date) -> int:
        return len(self.sessions_between(start, end))

    def shift(self, d: date, n: int) -> date | None:
        """n sessions forward (n>0) or back (n<0) from d."""
        step = 1 if n >= 0 else -1
        remaining, cur = abs(n), d
        guard = 0
        while remaining and guard < 10_000:
            cur += timedelta(days=step)
            guard += 1
            if self.is_session(cur):
                remaining -= 1
        return cur if not remaining else None


# -- is a bar a close yet? ------------------------------------------------------------------

#: `price_state` answers. Three, because two would lie: a caller that prints
#: "settled" where the fetch instant is unknown is guessing, and one that prints
#: "provisional" there cries wolf on every row cached before the column existed.
SETTLED = "settled"
PROVISIONAL = "provisional"
UNKNOWN = "unknown"


def last_session_close(mic: str, now: datetime) -> datetime | None:
    """When `mic` last finished a session at or before `now`, in UTC.

    None when the MIC has no adapter, or no session closed in the fortnight
    before `now` - both read by a caller as "cannot judge", never as fresh.
    The companion of `price_state`: that one asks whether a bar was pulled
    after ITS session shut; this one asks whether a session has shut since a
    body was pulled at all, which is the question a cache has to answer
    before serving that body again.
    """
    try:
        from markets.registry import get as adapter_for

        calendar = adapter_for(mic).calendar
    except (KeyError, ValueError):
        return None
    at = now if now.tzinfo else now.replace(tzinfo=UTC)
    day = at.date()
    for _ in range(14):
        session = calendar.session(day)
        if session is not None and session.close_utc() <= at:
            return session.close_utc()
        day -= timedelta(days=1)
    return None


def price_state(mic: str, day: date, fetched_at: datetime | None) -> str:
    """Whether a bar dated `day` is a settled close or the session so far.

    A price is a close only once the market it came from has shut, and the bar
    carries a date and nothing else - so the question is answered against WHEN
    THE BODY WAS FETCHED, never when it is read. A row dated today, pulled at
    17:13 UTC while Nasdaq trades until 20:00, is an intraday quote wearing a
    close's shape: the same five columns, a plausible price, and it sits inside
    the day's eventual high-low range, so nothing about it reads as unfinished.

    This is the other half of `cache.is_mid_session`, which catches the case
    where the vendor's in-progress row does not PARSE - a blank close, as Yahoo
    returns for Bursa mid-session. When the vendor fills every column with the
    session so far, the parser takes it and only the clock can tell.

    `day` is the bar's own day, so a row from a previous session is settled
    whatever time it was fetched: the market had already shut when it printed.
    A bar dated a day the market never opened is `unknown`: there was no
    session for it to be the close of, whenever it was pulled.
    """
    if fetched_at is None:
        return UNKNOWN
    try:
        from markets.registry import get as adapter_for

        calendar = adapter_for(mic).calendar
    except (KeyError, ValueError):
        return UNKNOWN
    session = calendar.session(day)
    if session is None:
        return UNKNOWN  # a bar on a day this calendar does not call a session
    at = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)
    return SETTLED if at >= session.close_utc() else PROVISIONAL
