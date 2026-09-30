"""The index book: a passive, equal-weight basket of a whole market index.

The control answers "what would equal lots of the watchlist have done". With
USD 1,000 and 100-share Bursa lots it can hold three or four names, which says
more about lot sizes than about the market. The index book answers the wider
question - what would the market itself have done - by holding every fundable
name of a published index (engines/paper/data/fbm100.yaml) at equal value.

What it shares with the control: the phases and their ceilings, the cash
floor, whole lots, fees, FX spread and slippage, the monthly-or-new-phase
rebalance, and no stops, no halt, no turnover cap. What it does not share is
the money. Equal weight across a hundred names in whole lots needs each name's
share of the ramp ceiling to buy at least one lot of the dearest member, so the
book opens with a notional (INDEX_NOTIONAL_USD) large enough for that. The
notional is a scale, not money anyone has: every comparison with the decided
book is in percent, and the page says so. It also means the per-trade minimums
weigh less here than on USD 1,000, which the page states as the index's own
cost drag rather than hiding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from engines.paper.rules import Fundable, Phase
from engines.paper.settings import PaperSettings
from engines.paper.store import INDEX, PaperStore, TargetRow
from engines.paper.universe import MemberCheck, Universe

#: USD. The ramp puts 40% of it in 100 names, USD 4,000 each: above one lot of
#: the dearest FBM100 member (Nestle, about RM 9,000 a lot), so every member can
#: hold at least one. Frozen in the book's row when it opens.
INDEX_NOTIONAL_USD = Decimal(1_000_000)

REASON = "index_rebalance"

#: Share of the phase ceiling held back when sizing. The lots are sized at the
#: mid, and each entry then pays the FX spread (0.5% a side at the default),
#: fees and slippage on top; filled in order, the last few entries would find
#: the cash floor already reached and be refused. A hundred entries make that
#: the rule rather than the exception: in a full-scale replay the step from
#: ramp to full left Nestle at its ramp size, refused at the floor. Sizing 1%
#: inside the ceiling pays for the costs up front, so every member is filled.
COST_HEADROOM = Decimal("0.01")


def index_units(
    fundable: list[Fundable], equity: Decimal, phase: Phase, settings: PaperSettings
) -> dict[str, int]:
    """Equal value per name, in whole lots, nearest the target without passing the ceiling.

    Each fundable name's target is the phase ceiling, less COST_HEADROOM,
    divided by the number of fundable names. Floor to whole lots first; then, while the ceiling allows,
    add one lot to the name furthest below its target, as long as that lot
    brings it closer to the target than it was. Deterministic: ties go to the
    lower instrument id.
    """
    if not phase.invests:
        return {}
    names = sorted((f for f in fundable if f.fundable), key=lambda f: f.instrument_id)
    if not names or equity <= 0:
        return {}
    budget = phase.max_invested * equity * (1 - COST_HEADROOM)
    cap = settings.max_weight_per_name * equity
    target = min(budget / len(names), cap)
    lots = {f.instrument_id: int((target / f.lot_usd).to_integral_value(ROUND_DOWN)) for f in names}
    spent = sum((f.lot_usd * lots[f.instrument_id] for f in names), Decimal(0))
    while True:
        best: tuple[Decimal, str] | None = None
        for f in names:
            held = f.lot_usd * lots[f.instrument_id]
            short = target - held
            # One more lot must land nearer the target than the name is now,
            # fit the ceiling, and keep the name under the per-name cap.
            if short <= 0 or f.lot_usd - short >= short:
                continue
            if spent + f.lot_usd > budget or held + f.lot_usd > cap:
                continue
            key = (short / target, f.instrument_id)
            if best is None or key[0] > best[0]:
                best = key
        if best is None:
            break
        f = next(x for x in names if x.instrument_id == best[1])
        lots[f.instrument_id] += 1
        spent += f.lot_usd
    return {f.instrument_id: lots[f.instrument_id] * f.lot for f in names if lots[f.instrument_id]}


@dataclass
class IndexRebalance:
    """What one rebalance wrote, and what it left out and why."""

    targets: list[TargetRow] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)
    unfundable: list[str] = field(default_factory=list)
    excluded: list[MemberCheck] = field(default_factory=list)
    kept_unpriced: list[str] = field(default_factory=list)

    def summary(self) -> str:
        buy = sum(1 for t in self.targets if (t.target_units or 0) > 0)
        parts = [f"index rebalance: {len(self.targets)} target(s), {buy} name(s) to hold"]
        if self.excluded:
            parts.append(
                f"{len(self.excluded)} left out, name not the listed company: "
                + ", ".join(f"{c.member.instrument_id} ({c.listed})" for c in self.excluded)
            )
        if self.unpriced:
            parts.append(f"{len(self.unpriced)} without a cached price: {', '.join(self.unpriced)}")
        if self.unfundable:
            parts.append(
                f"{len(self.unfundable)} with a lot above the per-name cap: "
                + ", ".join(self.unfundable)
            )
        if self.kept_unpriced:
            parts.append(
                f"{len(self.kept_unpriced)} held but unpriced, left as held: "
                + ", ".join(self.kept_unpriced)
            )
        return "; ".join(parts)


def rebalance_targets(
    store: PaperStore,
    funds: list[Fundable],
    checks: list[MemberCheck],
    universe: Universe,
    *,
    equity: Decimal,
    day: date,
    now: datetime,
    phase: Phase,
    settings: PaperSettings,
) -> IndexRebalance:
    """Targets for every member to hold and every held name to leave.

    A held name with no price today keeps its units: a missing bar is not a
    reason for a benchmark to sell. A held name that has left the universe, or
    whose code turned out to be another company, is targeted to zero.
    """
    out = IndexRebalance()
    out.excluded = [c for c in checks if c.excluded]
    excluded = {c.member.instrument_id for c in out.excluded}
    members = set(universe.ids)
    eligible = [f for f in funds if f.instrument_id in members and f.instrument_id not in excluded]
    out.unpriced = [f.instrument_id for f in eligible if f.error]
    out.unfundable = [f.instrument_id for f in eligible if not f.error and not f.fundable]
    units = index_units(eligible, equity, phase, settings)
    by_id = {f.instrument_id: f for f in funds}
    held = {p.instrument_id: p.units for p in store.state(INDEX).positions}
    for iid in sorted(set(units) | set(held)):
        f = by_id.get(iid)
        if iid in held and iid in members and iid not in excluded and (f is None or f.error):
            out.kept_unpriced.append(iid)
            continue
        u = units.get(iid, 0)
        w = (
            (f.lot_usd / f.lot * u / equity).quantize(Decimal("0.0001"))
            if f is not None and not f.error and f.lot and equity > 0
            else Decimal(0)
        )
        if u:
            why = "equal-value index member"
        elif iid not in members:
            why = "no longer in the index universe"
        elif iid in excluded:
            why = "its code lists another company"
        elif f is not None and not f.fundable:
            why = "one lot is above the per-name cap"
        else:
            why = "equal value rounds to no whole lot"
        out.targets.append(
            TargetRow(
                INDEX,
                day,
                now,
                iid,
                w,
                REASON,
                phase.name,
                f"{why}, {phase.name} phase, {universe.describe()}",
                target_units=u,
            )
        )
    return out
