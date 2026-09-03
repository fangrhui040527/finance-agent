"""Fees that depend on SHARE COUNT, not only on trade value.

`FeeLeg.charge` took a consideration and nothing else, so every schedule in the
repository is a pure function of trade value. That covers eleven venues and
misses the shape a retail broker actually charges: a flat fee per order, and a
rate per share.

It matters because the cost floor is derived from the schedule. `markets/xnas.py`
models a zero-commission US account, which yields a 5 bps floor and a USD 1.00
minimum economic position. A moomoo Malaysia account pays 0.03% plus a flat
USD 0.99 per order, so a USD 100 position pays roughly 204 bps round trip. Sized
against the wrong schedule, the engine funds positions that cannot pay for their
own round trip - and it does so silently, because a wrong floor is still a number.

The first test here is the one that matters most: adding the shape must not move
a single existing venue by a cent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from markets.contract import FeeLeg, FeeSchedule
from markets.registry import get, supported

D = Decimal

#: Measured before the per-share shape existed. Any drift is a regression.
BASELINE: dict[str, list[str]] = {
    "XASX": ["20", "20.0000", "200.0000"],
    "XETR": ["16.036000", "20.360000", "203.600000"],
    "XHKG": ["206.1700000", "225.7000000", "721.0000000"],
    "XKLS": ["18.6000", "46.0000", "460.0000"],
    "XKRX": ["2001.5000", "2015.0000", "2150.0000"],
    "XLON": ["23.000", "72.0000", "702.0000"],
    "XNAS": ["0.0556000", "0.5560000", "5.5600000"],
    "XNSE": ["42.2114000", "62.1140000", "261.1400000"],
    "XSES": ["20.800000", "28.000000", "240.000000"],
    "XTAI": ["43.000", "70.000", "585.000000"],
    "XTKS": ["3000", "3000", "3000"],
}
SIZES = [D(1000), D(10000), D(100000)]


@pytest.mark.parametrize("mic", supported())
def test_no_existing_venue_moves_by_a_cent(mic: str):
    """The regression guard. Eleven schedules ship today and every sizing
    decision in the system flows from them."""
    rt = get(mic).fee_schedule.round_trip
    assert [str(rt(v)) for v in SIZES] == BASELINE[mic]


@pytest.mark.parametrize("mic", supported())
def test_a_value_only_schedule_still_needs_no_price(mic: str):
    """None of the shipped venues charges per share, so none may start
    demanding a price to be costed."""
    assert get(mic).fee_schedule.round_trip(D(10000)) >= 0


# --- the new shape ---------------------------------------------------------------


def test_a_flat_leg_is_charged_once_per_order_whatever_the_size():
    flat = FeeSchedule((FeeLeg("platform", flat=D("0.99")),))
    assert flat.one_side(D(100)) == D("0.99")
    assert flat.one_side(D(100000)) == D("0.99")
    assert flat.round_trip(D(100)) == D("1.98")


def test_a_flat_leg_is_what_makes_a_small_order_uneconomic():
    """USD 0.99 a side on a USD 100 position is 198 bps before anything else.
    This is the arithmetic the zero-commission model hides."""
    flat = FeeSchedule((FeeLeg("platform", flat=D("0.99")),))
    assert flat.round_trip_bps(D(100)) == D(198)
    assert flat.round_trip_bps(D(10000)) < D(20)


def test_a_per_share_leg_charges_by_share_count_not_by_value():
    sched = FeeSchedule((FeeLeg("settlement", per_share=D("0.003")),))
    # 100 shares at USD 10 and 10 shares at USD 100 are the same consideration
    # and NOT the same fee. That is the whole point of the shape.
    assert sched.one_side(D(1000), price=D(10)) == D("0.3")
    assert sched.one_side(D(1000), price=D(100)) == D("0.03")


def test_a_per_share_leg_without_a_price_is_refused_not_guessed():
    """Returning the value-only part would understate the cost, and an
    understated floor funds a position that cannot pay its own spread."""
    sched = FeeSchedule((FeeLeg("settlement", per_share=D("0.003")),))
    with pytest.raises(ValueError, match="price"):
        sched.round_trip(D(1000))


def test_a_per_share_cap_is_honoured():
    sched = FeeSchedule((FeeLeg("settlement", per_share=D("0.003"), cap=D("1.00")),))
    assert sched.one_side(D(10000), price=D(1)) == D("1.00")


def test_a_sell_only_leg_is_charged_once_over_the_round_trip():
    """The US SEC fee and the trading activity fee are levied on sales only."""
    sched = FeeSchedule((FeeLeg("sec", rate=D("0.0000206"), per_side=False),))
    assert sched.round_trip(D(100000)) == sched.one_side(D(100000))
