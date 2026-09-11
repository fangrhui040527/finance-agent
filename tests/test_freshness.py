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


# --- a stopped upstream, which is not a late one ---------------------------------------------


def test_every_ended_series_still_declares_a_cadence():
    """The rail behind the rail. ENDED suppresses the staleness judgement; if
    the entry is ever deleted because the upstream came back, the series must
    fall straight back onto a declared limit rather than into "not judged"."""
    from knowledge.sources.freshness import ENDED

    assert set(ENDED) <= set(MAX_AGE_DAYS), (
        f"no cadence for {sorted(set(ENDED) - set(MAX_AGE_DAYS))}"
    )


def test_every_ended_id_is_a_series_a_collector_actually_stores():
    """An ENDED entry for an id nothing fetches is a claim nothing can check."""
    from knowledge.sources.dbnomics import SERIES as DBN
    from knowledge.sources.fred import SERIES as FRED
    from knowledge.sources.freshness import ENDED

    collected = {s.series_id for s in DBN} | set(FRED) | {"BNM:OPR"}
    assert set(ENDED) <= collected, f"nothing collects {sorted(set(ENDED) - collected)}"


def test_a_stopped_upstream_reads_ended_not_stale():
    """The whole point: STALE tells a reader to go and chase the upstream, and
    that upstream has already been chased and found stopped."""
    label = age_label("DBN:POLICY_RATE_MY", date(2025, 6, 1), date(2026, 9, 10))
    assert label == "466d ENDED 2025-06"
    assert "STALE" not in label


def test_the_probe_verdict_matches_what_the_collector_last_stored():
    """The recorded last period is the API's answer. If a later run stores a
    newer point these dates are wrong, which `has_resumed` is there to catch."""
    from knowledge.sources.freshness import ENDED

    assert ENDED["DBN:GOVT_YIELD_MY"].last_period == date(2025, 5, 1)
    assert ENDED["DBN:CPI_MY"].last_period == date(2025, 7, 1)
    assert ENDED["DBN:BRENT_USD"].upstream == "IMF/PCPS"
    assert {e.verdict for e in ENDED.values()} == {"FROZEN"}


def test_a_series_that_starts_printing_again_stops_being_called_dead():
    """Marking an upstream dead is the one judgement here that could hide a
    live number, so it is checked against the observation every time."""
    from knowledge.sources.freshness import ended_note, has_resumed

    sid, today = "DBN:NEER_MY", date(2026, 9, 10)
    assert not has_resumed(sid, date(2025, 5, 1))
    assert has_resumed(sid, date(2026, 8, 1))
    assert age_label(sid, date(2026, 8, 1), today) == "40d"
    assert ended_note(sid, date(2026, 8, 1)) == ""
    assert "BIS/WS_EER stopped at 2025-05" in ended_note(sid, date(2025, 5, 1))


def test_a_live_series_is_never_called_ended():
    from knowledge.sources.freshness import ended, ended_note

    assert ended("DGS10") is None
    assert ended_note("DGS10") == ""
    assert age_label("DGS10", date(2026, 8, 1), TODAY) == "36d STALE (>7d)"
