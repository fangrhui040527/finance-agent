"""A graded call's name leg is total return net of withholding, like its benchmark.

Each test fails on the code before 2026-10-10:

  * the name leg was the price return alone while the benchmark, the control
    book's equity, is credited dividends on the ex-date from 2026-10-10, so
    every grade was biased against the name by the control's net yield.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from core.market.prices import ActionKind, CorporateAction
from engines.paper import grade as G

BOUNDARY = date(2026, 10, 12)


class _StoreDouble:
    def __init__(self, since):
        self.since = since

    def credited_from(self, book):
        return self.since


class _Feed:
    def __init__(self, actions, source: str | None = "yahoo"):
        self.series = SimpleNamespace(
            actions_source=source,
            dividends=lambda: [a for a in actions if a.kind is ActionKind.DIVIDEND],
        )

    def fetch(self, iid, end=None):
        return self.series


class _FxDouble:
    def asof(self, day):
        return SimpleNamespace(rate=Decimal("4"))


def _Store(since) -> Any:
    return _StoreDouble(since)


def _Fx() -> Any:
    return _FxDouble()


def _div(day, amount):
    return CorporateAction(day, ActionKind.DIVIDEND, amount=amount)


def test_a_bursa_dividend_in_the_window_is_added_at_the_ex_date_rate():
    feed = _Feed([_div(date(2026, 10, 14), 0.30), _div(date(2026, 11, 20), 0.50)])
    paid, note = G._net_dividends_usd(
        _Store(BOUNDARY), feed, _Fx(), "MYX:1155", after=date(2026, 10, 13), up_to=date(2026, 11, 3)
    )
    assert paid == Decimal("0.075") and note == ""  # RM0.30 at 4.0, 0% withheld


def test_a_us_dividend_is_net_of_thirty_percent():
    feed = _Feed([_div(date(2026, 10, 15), 1.00)])
    paid, _ = G._net_dividends_usd(
        _Store(BOUNDARY),
        feed,
        _Fx(),
        "XNAS:AAPL",
        after=date(2026, 10, 13),
        up_to=date(2026, 11, 3),
    )
    assert paid == Decimal("0.70")


def test_nothing_before_the_control_boundary_and_nothing_without_one():
    feed = _Feed([_div(date(2026, 10, 9), 0.30)])
    args = dict(after=date(2026, 10, 1), up_to=date(2026, 11, 3))
    assert G._net_dividends_usd(_Store(BOUNDARY), feed, _Fx(), "MYX:1155", **args)[0] == 0
    assert G._net_dividends_usd(_Store(None), feed, _Fx(), "MYX:1155", **args)[0] == 0


def test_a_name_with_no_dividend_data_says_so():
    paid, note = G._net_dividends_usd(
        _Store(BOUNDARY),
        _Feed([], source=None),
        _Fx(),
        "MYX:1155",
        after=date(2026, 10, 13),
        up_to=date(2026, 11, 3),
    )
    assert paid == 0 and "NO DIVIDEND DATA for MYX:1155" in note
