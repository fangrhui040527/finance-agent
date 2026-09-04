"""Per-leg fees against the real cards, ticks against the book, next-bar lookups."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from engines.paper.pricing import (
    ENTRY,
    EXIT,
    currency_of,
    first_bar_after,
    last_close,
    leg_fee,
    leg_price,
    lot_size,
    round_trip_pct,
    tick_for,
    weekdays_back,
    weekdays_between,
)
from markets.brokers import schedule_for


@pytest.mark.parametrize(
    "mic, price, units",
    [
        ("XKLS", Decimal("4.16"), 100),
        ("XKLS", Decimal("2.03"), 100),
        ("XNAS", Decimal("224.41"), 1),
    ],
)
def test_entry_plus_exit_equals_the_round_trip_on_the_moomoo_cards(mic, price, units):
    consideration = price * units
    entry = leg_fee(mic, "moomoo_my", consideration, price, ENTRY)
    exit_ = leg_fee(mic, "moomoo_my", consideration, price, EXIT)
    assert entry + exit_ == schedule_for(mic, "moomoo_my").round_trip(consideration, price)
    assert entry > 0 and exit_ >= entry  # the exit carries the one-way legs


def test_round_trip_share_is_larger_for_the_smallest_lot():
    genting = round_trip_pct("XKLS", "moomoo_my", Decimal("203"), Decimal("2.03"))
    tenaga = round_trip_pct("XKLS", "moomoo_my", Decimal("1364"), Decimal("13.64"))
    assert genting > Decimal("0.03") > tenaga  # the flat RM 3 platform fee on a RM 203 lot


def test_leg_price_rounds_against_the_book():
    tick = Decimal("0.01")
    assert leg_price(Decimal("4.16"), ENTRY, 10, tick) == Decimal("4.17")  # 4.16416 rounds UP
    assert leg_price(Decimal("4.16"), EXIT, 10, tick) == Decimal("4.15")  # 4.15584 rounds DOWN
    assert leg_price(Decimal("224.41"), ENTRY, 5, tick) == Decimal("224.53")
    assert leg_price(Decimal("224.41"), ENTRY, 0, tick) == Decimal("224.41")


def test_lots_ticks_and_currencies_come_from_the_market_adapters():
    assert lot_size("MYX:5183") == 100 and lot_size("XNAS:NVDA") == 1
    assert currency_of("MYX:5183") == "MYR" and currency_of("XNAS:NVDA") == "USD"
    assert tick_for("MYX:5183", Decimal("4.16")) == Decimal("0.01")
    assert tick_for("MYX:1155", Decimal("10.54")) == Decimal("0.02")
    assert tick_for("XNAS:NVDA", Decimal("224.41")) == Decimal("0.01")


def test_weekday_arithmetic():
    mon, fri, nxt = date(2026, 3, 16), date(2026, 3, 20), date(2026, 3, 23)
    assert weekdays_between(mon, fri) == 4 and weekdays_between(fri, nxt) == 1
    assert weekdays_between(date(2026, 3, 14), mon) == 1  # Saturday to Monday
    assert weekdays_back(fri, 5) == date(2026, 3, 15)  # (Sun, Fri] holds Mon..Fri


def test_last_close_and_first_bar_after_read_the_feed(paper_env):
    feed = paper_env.feed
    close, day = last_close(feed, "MYX:5183", date(2026, 3, 15))  # a Sunday
    assert day == date(2026, 3, 13)
    bar = first_bar_after(feed, "MYX:5183", date(2026, 3, 13), date(2026, 3, 20))
    assert bar is not None and bar.day == date(2026, 3, 16)
    assert first_bar_after(feed, "MYX:5183", date(2026, 3, 13), date(2026, 3, 13)) is None
    assert first_bar_after(feed, "MYX:5183", date(2026, 7, 31), date(2026, 8, 31)) is None
