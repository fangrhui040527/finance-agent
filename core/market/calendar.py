"""Session calendars.

docs/06 section 2.2: session calendars differ and include lunch breaks in several
Asian markets. Aligning to UTC bars with a session_id is the only way daily
returns compare correctly across markets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time


@dataclass(frozen=True)
class TradingSession:
    day: date
    windows: tuple[SessionWindow, ...]
    tz_offset_hours: int
    half_day: bool = False

    @property
    def session_id(self) -> str:
        return self.day.isoformat()

    def open_utc(self) -> datetime:
        w = self.windows[0]
        local = datetime.combine(self.day, w.start)
        return (local - timedelta(hours=self.tz_offset_hours)).replace(tzinfo=UTC)

    def close_utc(self) -> datetime:
        w = self.windows[-1]
        local = datetime.combine(self.day, w.end)
        return (local - timedelta(hours=self.tz_offset_hours)).replace(tzinfo=UTC)


class SessionCalendar:
    def __init__(
        self,
        windows: tuple[SessionWindow, ...],
        tz_offset_hours: int,
        holidays: frozenset[date] = frozenset(),
        half_days: frozenset[date] = frozenset(),
        weekend: tuple[int, ...] = (5, 6),
    ) -> None:
        self.windows = windows
        self.tz_offset_hours = tz_offset_hours
        self.holidays = holidays
        self.half_days = half_days
        self.weekend = weekend

    def is_session(self, d: date) -> bool:
        return d.weekday() not in self.weekend and d not in self.holidays

    def session(self, d: date) -> TradingSession | None:
        if not self.is_session(d):
            return None
        windows = self.windows
        if d in self.half_days:
            windows = (self.windows[0],)
        return TradingSession(d, windows, self.tz_offset_hours, d in self.half_days)

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
