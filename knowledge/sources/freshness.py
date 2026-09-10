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

A STOPPED UPSTREAM IS NOT A LATE ONE. Staleness assumes the next print is
coming. For fifteen of these it is not: the 2026-09-06 runner probe read the
DBnomics API directly and found the whole DATASET frozen behind each id - every
sibling code stops at the same period, so there is no replacement code to point
at and nothing anyone here can fix. `ENDED` records that verdict with the last
period each upstream published, and it changes what the surfaces say rather
than what they hide: a row reads `466d ENDED 2025-06` instead of `466d STALE`,
the monitor stops opening an alert nobody can close, and `macro_context` prints
the reason under the table. The collector keeps fetching them, which is what
makes the claim falsifiable - an observation newer than the recorded last
period means the upstream restarted, and `has_resumed` says so loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Ended:
    """An upstream that has stopped publishing, and the evidence for saying so.

    `last_period` is the newest observation the API itself returned, not the
    newest row we happen to hold - the two agree only while the fetch works,
    and it is the API's answer that makes this a verdict rather than a guess.
    `checked_on` dates that answer so a reader can see how old the verdict is,
    and `verdict` is the probe's own word (see .github/scripts/dbnomics_probe.py):
    FROZEN means every sibling code in the dataset stops at the same period, so
    there is no live code to point the adapter at.
    """

    last_period: date
    upstream: str
    verdict: str
    checked_on: date


#: The 2026-09-06 runner probe: IMF/PCPS, BIS/WS_CBPOL, BIS/WS_EER, IMF/CPI and
#: IMF/IFS each stop dead at one period, and every sibling code inside them
#: stops there too (siblings outside our list answered HTTP 400 or the same
#: last period). So these are not our codes going out of date - the datasets
#: behind them stopped being ingested, and re-sourcing them is a decision about
#: where to buy macro data, not a bug to fix in the collector.
_FROZEN = "FROZEN"
_PROBED = date(2026, 9, 6)
ENDED: dict[str, Ended] = {
    sid: Ended(last, upstream, _FROZEN, _PROBED)
    for sids, last, upstream in (
        (
            ("DBN:PALM_OIL_USD", "DBN:ALUMINIUM_USD", "DBN:BRENT_USD", "DBN:LNG_ASIA_USD"),
            date(2025, 6, 1),
            "IMF/PCPS",
        ),
        (
            (
                "DBN:POLICY_RATE_US",
                "DBN:POLICY_RATE_EA",
                "DBN:POLICY_RATE_JP",
                "DBN:POLICY_RATE_CN",
                "DBN:POLICY_RATE_MY",
            ),
            date(2025, 6, 1),
            "BIS/WS_CBPOL",
        ),
        (("DBN:CPI_MY", "DBN:CPI_CN"), date(2025, 7, 1), "IMF/CPI"),
        (
            ("DBN:NEER_MY", "DBN:NEER_US", "DBN:NEER_CN"),
            date(2025, 5, 1),
            "BIS/WS_EER",
        ),
        (("DBN:GOVT_YIELD_MY",), date(2025, 5, 1), "IMF/IFS"),
    )
    for sid in sids
}


def ended(series_id: str) -> Ended | None:
    """The stopped-upstream verdict for this series, or None if it still runs."""
    return ENDED.get(series_id)


def has_resumed(series_id: str, obs_date: date) -> bool:
    """True when a series marked ended has printed past the period it stopped at.

    The one way this file can be wrong in the direction that matters. Marking a
    live series dead would hide a moving number behind the word ENDED, so every
    caller that trusts `ENDED` asks this first, and the monitor turns a True
    here into an alert to delete the entry.
    """
    e = ENDED.get(series_id)
    return e is not None and obs_date > e.last_period


def ended_note(series_id: str, obs_date: date | None = None) -> str:
    """One line for a person: what stopped, when, and how we know.

    Empty when the series is live, and empty when `obs_date` is given and has
    printed past the recorded last period - a caller holding an observation
    should never be handed a note calling its series dead.
    """
    e = ENDED.get(series_id)
    if e is None or (obs_date is not None and has_resumed(series_id, obs_date)):
        return ""
    return (
        f"{series_id}: {e.upstream} stopped at {e.last_period:%Y-%m} "
        f"({e.verdict}, probed {e.checked_on}); no live sibling code to point at"
    )


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
    """`466d ENDED 2025-06`, `462d STALE (>70d)`, or `3d`.

    Short on purpose: it is appended to a row in a table a person scans, and a
    row that will never refresh has to be readable as such at a glance, not
    decoded. ENDED is checked before STALE because it is the more specific and
    more useful thing to say: STALE asks the reader to go and chase an upstream
    that has already been chased and found stopped.
    """
    e = ENDED.get(series_id)
    age = age_days(obs_date, today)
    if e is not None and not has_resumed(series_id, obs_date):
        return f"{age}d ENDED {e.last_period:%Y-%m}"
    limit = max_age_days(series_id)
    if limit is None:
        return f"{age}d"
    return f"{age}d STALE (>{limit}d)" if age > limit else f"{age}d"


__all__ = [
    "DAILY",
    "ENDED",
    "MAX_AGE_DAYS",
    "MONTHLY",
    "POLICY",
    "QUARTERLY",
    "WEEKLY",
    "Ended",
    "age_days",
    "age_label",
    "ended",
    "ended_note",
    "has_resumed",
    "is_stale",
    "max_age_days",
]
