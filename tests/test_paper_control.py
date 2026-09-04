"""The passive control: greedy equal lots, cheapest first, monthly."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from engines.paper.control import control_units, rebalance_due
from engines.paper.rules import Fundable, phase_for
from engines.paper.settings import PaperSettings
from engines.paper.store import CONTROL, TargetRow

S = PaperSettings(start_date=date(2026, 3, 2))


def _fund(iid, lot_usd, lot=100):
    lot_usd = Decimal(lot_usd)
    return Fundable(
        iid,
        "MYR" if lot == 100 else "USD",
        lot,
        lot_usd / lot * 4,
        date(2026, 3, 13),
        lot_usd,
        lot_usd / 1000,
        int(Decimal(250) // lot_usd),
        Decimal("0.012"),
    )


FUNDS = [
    _fund("MYX:1155", "262.5"),
    _fund("MYX:5347", 340),
    _fund("MYX:5183", 104),
    _fund("MYX:5225", "199.5"),
    _fund("MYX:8869", 199),
    _fund("MYX:3182", "50.75"),
    _fund("XNAS:NVDA", 224, 1),
    _fund("XNAS:AAPL", 325, 1),
    _fund("XNAS:MSFT", 497, 1),
]


def test_ramp_fills_the_cheapest_lots_under_the_40_percent_ceiling():
    units = control_units(FUNDS, Decimal(1000), phase_for(date(2026, 3, 16), S), S)
    assert units == {"MYX:3182": 100, "MYX:5183": 100, "MYX:8869": 100}  # 353.75 of 400


def test_full_holds_one_lot_of_each_fundable_name():
    units = control_units(FUNDS, Decimal(1000), phase_for(date(2026, 4, 13), S), S)
    assert units == {
        "MYX:3182": 100,
        "MYX:5183": 100,
        "MYX:8869": 100,
        "MYX:5225": 100,
        "XNAS:NVDA": 1,
    }


def test_observe_and_pre_invest_nothing():
    assert control_units(FUNDS, Decimal(1000), phase_for(date(2026, 3, 3), S), S) == {}
    assert control_units(FUNDS, Decimal(1000), phase_for(date(2026, 3, 1), S), S) == {}


def test_a_second_lot_stays_under_the_per_name_cap():
    only = [_fund("MYX:3182", "50.75")]
    units = control_units(only, Decimal(1000), phase_for(date(2026, 4, 13), S), S)
    assert units == {"MYX:3182": 400}  # four lots = 203, the fifth would be 253.75 > 250


def test_rebalance_is_due_on_a_new_month_or_a_new_phase(paper_env):
    store = paper_env.store
    ramp = phase_for(date(2026, 3, 16), S)
    assert rebalance_due(store, date(2026, 3, 16), ramp)
    assert not rebalance_due(store, date(2026, 3, 16), phase_for(date(2026, 3, 3), S))
    store.record_targets(
        [
            TargetRow(
                CONTROL,
                date(2026, 3, 16),
                datetime(2026, 3, 16, tzinfo=UTC),
                "MYX:3182",
                Decimal("0.05"),
                "control_rebalance",
                "ramp",
                target_units=100,
            )
        ]
    )
    assert not rebalance_due(store, date(2026, 3, 30), ramp)
    assert rebalance_due(store, date(2026, 4, 1), ramp)  # new month
    assert rebalance_due(store, date(2026, 3, 30), phase_for(date(2026, 4, 13), S))  # new phase
