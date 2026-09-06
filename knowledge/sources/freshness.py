"""How old a macro series is allowed to be before its latest point is a lie.

WHY THIS EXISTS. On 2026-09-06 the fact book's Malaysian CPI read 48.7 dated
1982-12-01 and sixteen DBnomics series carried prints 432 to 493 days old, and
nothing anywhere said so: `ask.py macro` printed them in the same column as a
FRED yield three days old, and the monitor - which has rules for a silent sweep
and a stale paper mark - had none for a series that has stopped moving. A
number's age is part of the number. A reader who cannot see it will treat a
2025 policy rate as today's.

A CADENCE, NOT A CALENDAR. The limit per series is its publication rhythm plus
the lag it publishes with, rounded up generously: the rule should fire on a
source that has stopped, not on an ordinary late month. A daily market series
skips weekends and holidays, so a week is normal. A monthly statistic is
published four to six weeks after the month it describes and dated to the FIRST
of that month, so its newest point is routinely two months old at rest; 70 days
allows that and still catches a series a year behind. A policy rate is set at a
scheduled meeting - Malaysia's MPC sits six times a year - so it may legitimately
not move for a hundred days.

UNKNOWN IDS ARE NOT JUDGED. `max_age_days` returns None for a series id this
file has never heard of, and every caller treats None as "no opinion". A new
series must be added here deliberately; guessing a limit from a prefix that
happens to match would produce confident alerts about a cadence nobody checked.
"""

from __future__ import annotations

from datetime import date

#: A market series priced every business day. A long weekend plus a holiday.
DAILY = 7
#: A daily series PUBLISHED weekly. The Fed's H.10 exchange rates carry daily
#: observations but appear in one Monday release, so the newest point is
#: routinely a week and a half old the day before the next one.
WEEKLY = 12
#: A monthly statistic, dated to the first of its month and published weeks
#: later. Two months of ordinary lag, and a month of slack.
MONTHLY = 70
#: A rate set at a scheduled meeting rather than by a market.
POLICY = 100
#: A quarterly release: one quarter, its lag, and slack.
QUARTERLY = 190

#: series_id -> the age past which its latest point should not be read as current.
MAX_AGE_DAYS: dict[str, int] = {
    # FRED, daily (knowledge/sources/fred.py SERIES)
    "DFF": DAILY,
    "DGS2": DAILY,
    "DGS10": DAILY,
    "T10Y2Y": DAILY,
    "VIXCLS": DAILY,
    "BAMLH0A0HYM2": DAILY,
    # H.10, released weekly (see WEEKLY): daily observations, Monday publication.
    "DTWEXBGS": WEEKLY,
    "DEXMAUS": WEEKLY,
    "SP500": DAILY,
    "NASDAQCOM": DAILY,
    # FRED, monthly
    "CPIAUCSL": MONTHLY,
    "UNRATE": MONTHLY,
    # Bank Negara: the OPR is an MPC decision, not a market price.
    "BNM:OPR": POLICY,
    # DOSM, monthly (knowledge/sources/dosm.py)
    "DOSM:CPI_HEADLINE": MONTHLY,
    "DOSM:CPI_YOY": MONTHLY,
    # DBnomics, all monthly (knowledge/sources/dbnomics.py SERIES). IMF PCPS,
    # BIS WS_CBPOL/WS_EER and IMF CPI/IFS publish with a longer lag than FRED,
    # which is why these sit at MONTHLY rather than DAILY and not lower.
    "DBN:PALM_OIL_USD": MONTHLY,
    "DBN:ALUMINIUM_USD": MONTHLY,
    "DBN:BRENT_USD": MONTHLY,
    "DBN:LNG_ASIA_USD": MONTHLY,
    "DBN:POLICY_RATE_US": POLICY,
    "DBN:POLICY_RATE_EA": POLICY,
    "DBN:POLICY_RATE_JP": POLICY,
    "DBN:POLICY_RATE_CN": POLICY,
    "DBN:POLICY_RATE_MY": POLICY,
    "DBN:CPI_MY": MONTHLY,
    "DBN:CPI_CN": MONTHLY,
    "DBN:NEER_MY": MONTHLY,
    "DBN:NEER_US": MONTHLY,
    "DBN:NEER_CN": MONTHLY,
    "DBN:GOVT_YIELD_MY": MONTHLY,
}


def max_age_days(series_id: str) -> int | None:
    """The limit for this series, or None when this file has no opinion."""
    return MAX_AGE_DAYS.get(series_id)


def age_days(obs_date: date, today: date) -> int:
    """How old the latest observation is. Negative is impossible but harmless."""
    return (today - obs_date).days


def is_stale(series_id: str, obs_date: date, today: date) -> bool:
    """True only when a limit is declared for this series AND it is past."""
    limit = max_age_days(series_id)
    return limit is not None and age_days(obs_date, today) > limit


def age_label(series_id: str, obs_date: date, today: date) -> str:
    """`462d STALE`, `3d`, or `` when the id carries no limit.

    Short on purpose: it is appended to a row in a table a person scans, and a
    stale row has to be readable as stale at a glance, not decoded.
    """
    limit = max_age_days(series_id)
    age = age_days(obs_date, today)
    if limit is None:
        return f"{age}d"
    return f"{age}d STALE (>{limit}d)" if age > limit else f"{age}d"


__all__ = [
    "DAILY",
    "MAX_AGE_DAYS",
    "MONTHLY",
    "POLICY",
    "QUARTERLY",
    "WEEKLY",
    "age_days",
    "age_label",
    "is_stale",
    "max_age_days",
]
