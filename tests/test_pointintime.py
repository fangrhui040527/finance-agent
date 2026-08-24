"""P3.5: the phase that makes every downstream result honest."""
from datetime import date
from decimal import Decimal as D

import pytest

from core.market.pointintime import (
    Fact, FactStore, LookaheadError, UniverseSnapshots, assert_no_lookahead,
)
from markets.contract import AccountingStandard as AS

PE = date(2025, 12, 31)


def f(known, value, restated=False):
    return Fact("1155.KL", "revenue", PE, known, D(value), "MYR", AS.IFRS,
                "doc", is_restatement=restated)


def store():
    s = FactStore()
    s.add(f(date(2026, 2, 20), "4200"))
    s.add(f(date(2026, 8, 1), "4350", restated=True))
    return s


def test_a_fact_cannot_predate_the_period_it_describes():
    with pytest.raises(ValueError, match="cannot be public before"):
        Fact("X", "revenue", date(2025, 12, 31), date(2025, 6, 1), D(1), "MYR", AS.IFRS, "d")


def test_point_in_time_returns_the_first_reported_figure():
    assert store().as_known_at("1155.KL", "revenue", date(2026, 3, 1)).value == D("4200")


def test_restatement_is_visible_only_after_it_lands():
    assert store().as_known_at("1155.KL", "revenue", date(2026, 9, 1)).value == D("4350")


def test_nothing_is_knowable_before_the_filing():
    assert store().as_known_at("1155.KL", "revenue", date(2026, 1, 15)) is None


def test_naive_query_would_have_used_a_future_number():
    """The bug this store exists to prevent, made explicit."""
    s = store()
    naive = s.latest_restated("1155.KL", "revenue", PE).value
    honest = s.as_known_at("1155.KL", "revenue", date(2026, 3, 1)).value
    assert naive != honest and naive > honest


def test_restatement_diff_surfaces_the_gap():
    first, latest = store().restatement_diff("1155.KL", "revenue", PE)
    assert latest.value - first.value == D("150")


def test_append_only_a_restatement_never_overwrites():
    s = store()
    assert len(s._facts[("1155.KL", "revenue")]) == 2


def test_lookahead_guard_raises():
    with pytest.raises(LookaheadError):
        assert_no_lookahead(f(date(2026, 8, 1), "4350"), date(2026, 3, 1))


def test_universe_snapshots_are_built_forward_only():
    u = UniverseSnapshots()
    u.record(date(2026, 1, 1), ["A", "B"])
    with pytest.raises(ValueError, match="built forward"):
        u.record(date(2025, 1, 1), ["A"])


def test_snapshot_retains_names_that_have_since_died():
    u = UniverseSnapshots()
    u.record(date(2020, 1, 1), ["ALIVE", "BANKRUPT"])
    u.record(date(2026, 1, 1), ["ALIVE"])
    assert "BANKRUPT" in u.asof(date(2020, 6, 1))
    assert u.survivorship_safe(date(2020, 6, 1), still_listed_today={"ALIVE"})


def test_series_as_known_at_picks_best_version_per_period():
    s = FactStore()
    s.add(Fact("X", "eps", date(2025, 3, 31), date(2025, 5, 1), D("1.0"), "MYR", AS.IFRS, "a"))
    s.add(Fact("X", "eps", date(2025, 3, 31), date(2025, 9, 1), D("1.1"), "MYR", AS.IFRS, "b"))
    s.add(Fact("X", "eps", date(2025, 6, 30), date(2025, 8, 1), D("1.2"), "MYR", AS.IFRS, "c"))
    early = s.series_as_known_at("X", "eps", date(2025, 8, 15))
    assert [x.value for x in early] == [D("1.0"), D("1.2")]
