"""The passive control book: equal lots of whatever is fundable, monthly.

Same money, same phases, same cash floor, same lots, fees, FX and slippage as
the decided book - and no judgement. It exists so the record can separate
what the deciding added from what the market gave. It has no stops, no halt
and no turnover cap, because a passive book that reacts is not a control.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engines.paper.rules import Fundable, Phase
from engines.paper.settings import PaperSettings
from engines.paper.store import PaperStore


def control_units(
    fundable: list[Fundable], equity: Decimal, phase: Phase, settings: PaperSettings
) -> dict[str, int]:
    """Greedy round-robin, cheapest lot first: one lot per name per round while
    the name stays under the per-name cap and the total under the phase ceiling."""
    if not phase.invests:
        return {}
    budget = phase.max_invested * equity
    cap = settings.max_weight_per_name * equity
    names = sorted((f for f in fundable if f.fundable), key=lambda f: f.lot_usd)
    units: dict[str, int] = {f.instrument_id: 0 for f in names}
    spent: dict[str, Decimal] = {f.instrument_id: Decimal(0) for f in names}
    total = Decimal(0)
    progress = True
    while progress:
        progress = False
        for f in names:
            if total + f.lot_usd <= budget and spent[f.instrument_id] + f.lot_usd <= cap:
                units[f.instrument_id] += f.lot
                spent[f.instrument_id] += f.lot_usd
                total += f.lot_usd
                progress = True
    return {iid: u for iid, u in units.items() if u > 0}


def rebalance_due(store: PaperStore, day: date, phase: Phase) -> bool:
    """First mark of a new calendar month, or of a new phase; never in observe."""
    if not phase.invests:
        return False
    last = store.last_control_rebalance()
    if last is None:
        return True
    if (last.year, last.month) != (day.year, day.month):
        return True
    rows = store.targets_on("control", last, reason="control_rebalance")
    return bool(rows) and rows[0].phase != phase.name
