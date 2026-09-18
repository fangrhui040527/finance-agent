"""Exchange closures by MIC: the days a market does not open, and the days it
shuts early.

Pure data and one loader; no I/O. `markets.registry.get` reads this when it
builds an adapter, so `get("XKLS").calendar` knows Malaysia Day and nothing
else in the repository has to. Before this module every adapter was built with
`holidays=frozenset()`, so on 2026-09-16 - a Wednesday, Malaysia Day, Bursa
shut - `is_session` said True, a session count over any window spanning the
day came out one too many, and `price_state` would have called a bar dated
that day settled the moment the clock passed the modelled close, for a session
that never happened. The feedback page found it by the blank bars.

WHY THE TABLES ARE TYPED IN. No vendor here supplies either exchange's
calendar, and neither is derivable. Bursa follows the Malaysian federal public
holidays with replacement days that are decided late - the 23 March 2026 Hari
Raya replacement was confirmed only on 18 March 2026 - and the NYSE observed-
holiday rules carry exceptions of their own. A table naming its source and
the last date it vouches for is the honest form.

EXPIRES. Each table carries the last date it is good for. tests/
test_market_holidays.py asserts today is not past it, so the suite fails when
a table runs out rather than the calendar quietly reverting to the weekday
filter it was on 2026-09-16. Extending a table means reading the exchange's
own notice, not the previous year plus one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time

# --- Bursa Malaysia (XKLS) ----------------------------------------------------------------
#
# Source: Bursa Malaysia's media notifications of trading holidays, as carried
# by The Star and Bernama. Sixteen closures in 2026. Bursa has had no half day
# since 26 January 2024, when the Chinese New Year eve half session was
# dropped, so it has no early-close table. 2027 is unpublished: only New
# Year's Day is entered, and the table expires with 2026 - re-check
# bursamalaysia.com in November-December 2026.
_XKLS_CLOSED = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 2, 2),  # Federal Territory Day (1 Feb, a Sunday) observed
        date(2026, 2, 17),  # Chinese New Year
        date(2026, 2, 18),  # Chinese New Year, second day
        date(2026, 3, 20),  # Hari Raya Aidilfitri
        date(2026, 3, 23),  # Hari Raya Aidilfitri, second day (21 Mar, a Saturday) observed
        date(2026, 5, 1),  # Labour Day
        date(2026, 5, 27),  # Hari Raya Haji
        date(2026, 6, 1),  # Agong's birthday, the first Monday of June
        date(2026, 6, 2),  # Wesak Day (31 May, a Sunday) observed, displaced by the above
        date(2026, 6, 17),  # Awal Muharram
        date(2026, 8, 25),  # Prophet Muhammad's birthday
        date(2026, 8, 31),  # National Day
        date(2026, 9, 16),  # Malaysia Day - the one the feedback page caught
        date(2026, 11, 9),  # Deepavali (8 Nov, a Sunday) observed
        date(2026, 12, 25),  # Christmas Day
        date(2027, 1, 1),  # New Year's Day; the rest of 2027 is not yet published
    }
)
_XKLS_EXPIRES = date(2026, 12, 31)

# --- Nasdaq (XNAS) ------------------------------------------------------------------------
#
# Source: the ICE / NYSE Group holiday press release of 23 December 2025 and
# nasdaq.com/holidayandtradinghours. NYSE and Nasdaq keep the same calendar,
# so an XNYS adapter, if one is ever added, shares this table. The early
# closes are 13:00 New York; the calendar turns that into UTC through the
# named zone, so the day after Thanksgiving (standard time) ends 18:00Z.
_XNAS_CLOSED = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (4 Jul, a Saturday) observed
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving Day
        date(2026, 12, 25),  # Christmas Day
        date(2027, 1, 1),  # New Year's Day
        date(2027, 1, 18),  # Martin Luther King Jr. Day
        date(2027, 2, 15),  # Presidents' Day
        date(2027, 3, 26),  # Good Friday
        date(2027, 5, 31),  # Memorial Day
        date(2027, 6, 18),  # Juneteenth (19 Jun, a Saturday) observed
        date(2027, 7, 5),  # Independence Day (4 Jul, a Sunday) observed
        date(2027, 9, 6),  # Labor Day
        date(2027, 11, 25),  # Thanksgiving Day
        date(2027, 12, 24),  # Christmas Day (25 Dec, a Saturday) observed
    }
)
_XNAS_EARLY = {
    date(2026, 11, 27): time(13, 0),  # the day after Thanksgiving
    date(2026, 12, 24): time(13, 0),  # Christmas Eve
    date(2027, 11, 26): time(13, 0),  # the day after Thanksgiving; Christmas Eve 2027 is a closure
}
_XNAS_EXPIRES = date(2027, 12, 31)

# --- the tables, by MIC -------------------------------------------------------------------

#: Days the market does not open. A MIC absent here has no table, and its
#: adapter is a weekday filter; that is a gap to fill, not a fact about the market.
HOLIDAYS: dict[str, frozenset[date]] = {
    "XKLS": _XKLS_CLOSED,
    "XNAS": _XNAS_CLOSED,
}

#: Days the market opens and shuts early, and the local time it shuts.
EARLY_CLOSES: dict[str, dict[date, time]] = {
    "XNAS": _XNAS_EARLY,
}

#: The last date each table vouches for. Past it the calendar is not wrong
#: yet - it is unverified, which for a holiday table is the same thing.
EXPIRES: dict[str, date] = {
    "XKLS": _XKLS_EXPIRES,
    "XNAS": _XNAS_EXPIRES,
}


@dataclass(frozen=True)
class Closures:
    """What one market's calendar is handed: the days it is shut, the days it
    shuts early, and the last date the table vouches for (None: no table)."""

    holidays: frozenset[date] = frozenset()
    early_closes: Mapping[date, time] = field(default_factory=dict)
    expires: date | None = None


def closures(mic: str) -> Closures:
    """The table for a MIC, or an empty one for a market that has none yet.

    Empty rather than an error because nine of the eleven adapters have no
    table, and refusing to build them would take the fee schedules and lot
    sizes down with the calendar. The gap is visible: `Closures.expires` is
    None only where nothing was ever entered.
    """
    return Closures(
        holidays=HOLIDAYS.get(mic, frozenset()),
        early_closes=dict(EARLY_CLOSES.get(mic, {})),
        expires=EXPIRES.get(mic),
    )
