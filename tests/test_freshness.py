"""How old a macro series may be, and the rail that keeps the table complete.

The rule these support fired for the first time on the repository's own fact
book: sixteen DBnomics series 432 to 493 days old and a Malaysian CPI reading
1982, none of it visible anywhere, because the sweeps that fetched them all
reported `ok`.
"""

from __future__ import annotations

from datetime import date

from knowledge.sources.freshness import (
    DAILY,
    MAX_AGE_DAYS,
    MONTHLY,
    age_label,
    is_stale,
    max_age_days,
)

TODAY = date(2026, 9, 6)


def test_every_series_a_collector_stores_has_a_declared_cadence():
    """The rail. A series added to a collector without a limit here would be
    read as current forever, which is the defect this file exists to stop."""
    from knowledge.sources.dbnomics import SERIES as DBN
    from knowledge.sources.fred import SERIES as FRED

    declared = set(MAX_AGE_DAYS)
    collected = {s.series_id for s in DBN} | set(FRED)
    collected |= {"BNM:OPR", "DOSM:CPI_HEADLINE", "DOSM:CPI_YOY"}
    assert collected <= declared, f"no cadence declared for {sorted(collected - declared)}"


def test_a_daily_series_a_week_old_is_fine_and_one_a_year_old_is_not():
    assert not is_stale("DGS10", date(2026, 9, 1), TODAY)
    assert is_stale("DGS10", date(2026, 8, 1), TODAY)
    assert max_age_days("DGS10") == DAILY


def test_a_monthly_series_two_months_behind_is_normal():
    """A monthly statistic is dated to the first of its month and published
    weeks later, so its newest point is routinely two months old at rest."""
    assert not is_stale("CPIAUCSL", date(2026, 7, 1), TODAY)
    assert max_age_days("CPIAUCSL") == MONTHLY
    assert is_stale("CPIAUCSL", date(2025, 7, 1), TODAY)


def test_the_h10_exchange_rates_get_the_week_their_release_takes():
    """DEXMAUS and DTWEXBGS carry daily observations in a Monday release, so a
    nine-day-old newest point is the release schedule, not a stopped feed."""
    assert not is_stale("DEXMAUS", date(2026, 8, 28), TODAY)
    assert not is_stale("DTWEXBGS", date(2026, 8, 28), TODAY)
    assert is_stale("DEXMAUS", date(2026, 8, 1), TODAY)


def test_an_unknown_series_is_not_judged():
    assert max_age_days("SOMETHING:NEW") is None
    assert not is_stale("SOMETHING:NEW", date(2001, 1, 1), TODAY)
    assert age_label("SOMETHING:NEW", date(2026, 9, 3), TODAY) == "3d"


def test_the_label_reads_as_stale_at_a_glance():
    assert age_label("DGS10", date(2026, 9, 3), TODAY) == "3d"
    assert age_label("DOSM:CPI_HEADLINE", date(1982, 12, 1), TODAY) == "15985d STALE (>70d)"
