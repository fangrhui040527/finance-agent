"""Every registered market adapter, held to the same contract.

The eleven adapters were covered only through markets.registry's generic
conformance loop, so a venue-specific fee schedule or calendar could be wrong
without any test naming that venue. This file parametrises over supported()
and names each one in its test id.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from markets.contract import AccountingStandard, KnownAtStrategy, MarketAdapter
from markets.registry import get, supported

MICS = supported()
CONSIDERATIONS = [
    Decimal("1"),
    Decimal("500"),
    Decimal("10000"),
    Decimal("250000"),
    Decimal("5000000"),
]
PRICES = [
    Decimal("0.05"),
    Decimal("0.5"),
    Decimal("1"),
    Decimal("9.99"),
    Decimal("10"),
    Decimal("100"),
    Decimal("12345.67"),
]


@pytest.fixture(params=MICS, ids=MICS)
def adapter(request) -> MarketAdapter:
    return get(request.param)


def test_registry_has_the_eleven_markets():
    assert len(MICS) >= 11, MICS


def test_identity_fields_are_populated(adapter: MarketAdapter):
    assert adapter.mic and adapter.mic == adapter.mic.upper()
    assert len(adapter.country) == 2
    assert len(adapter.currency) == 3 and adapter.currency == adapter.currency.upper()
    assert adapter.tier in (1, 2, 3)
    assert isinstance(adapter.accounting_standard, AccountingStandard)
    assert isinstance(adapter.known_at_strategy, KnownAtStrategy)
    assert adapter.local_index
    assert adapter.regulator
    assert adapter.settlement_days >= 0


def test_fee_round_trip_is_non_negative_and_monotone(adapter: MarketAdapter):
    fees = adapter.fee_schedule
    costs = [fees.round_trip(c) for c in CONSIDERATIONS]
    assert all(c >= 0 for c in costs), costs
    assert costs == sorted(costs), (
        f"{adapter.mic}: round trip not monotone in consideration: {costs}"
    )


def test_fee_round_trip_is_at_least_one_side(adapter: MarketAdapter):
    fees = adapter.fee_schedule
    for c in CONSIDERATIONS:
        assert fees.round_trip(c) >= fees.one_side(c)


def test_fee_bps_is_zero_for_non_positive_consideration(adapter: MarketAdapter):
    assert adapter.fee_schedule.round_trip_bps(Decimal(0)) == 0
    assert adapter.fee_schedule.round_trip_bps(Decimal("-1")) == 0


def test_lot_size_is_positive_and_rounding_never_exceeds_units(adapter: MarketAdapter):
    lot = adapter.lot_size(f"{adapter.mic}:TEST")
    assert isinstance(lot, int) and lot >= 1
    for units in (0, 1, lot - 1, lot, lot + 1, 7 * lot + 3):
        rounded = adapter.lot_round_down(units, f"{adapter.mic}:TEST")
        assert 0 <= rounded <= units and rounded % lot == 0


def test_tick_size_is_positive_and_non_decreasing_in_price(adapter: MarketAdapter):
    ticks = [adapter.tick_size(p) for p in PRICES]
    assert all(t > 0 for t in ticks), ticks
    assert ticks == sorted(ticks), f"{adapter.mic}: tick size not non-decreasing: {ticks}"


def test_withholding_is_a_fraction(adapter: MarketAdapter):
    for income in ("dividend", "interest"):
        for holder in ("MY", "US", "GB", adapter.country):
            w = adapter.withholding(income, holder)
            assert Decimal(0) <= w <= Decimal(1), (income, holder, w)


def test_calendar_produces_sessions_in_an_ordinary_fortnight(adapter: MarketAdapter):
    start = date(2026, 3, 2)  # a Monday with no globally shared holiday
    sessions = adapter.calendar.sessions_between(start, start + timedelta(days=13))
    assert 5 <= len(sessions) <= 10, f"{adapter.mic}: {len(sessions)} sessions in 14 days"
    for s in sessions:
        assert s.open_utc() < s.close_utc()


def test_weekend_is_never_a_session(adapter: MarketAdapter):
    sat, sun = date(2026, 3, 7), date(2026, 3, 8)
    assert not adapter.calendar.is_session(sat)
    assert not adapter.calendar.is_session(sun)


def test_factor_model_support_follows_tier(adapter: MarketAdapter):
    assert adapter.supports_factor_model() == (adapter.tier <= 2)
