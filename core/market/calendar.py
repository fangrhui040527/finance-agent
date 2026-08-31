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
