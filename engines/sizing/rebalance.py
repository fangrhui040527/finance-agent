"""What to change, versus what is already held.

This is the allocation question (`engines/sizing/allocate.py`) asked against a
book that exists, so the answer is a set of DIFFERENCES rather than a set of
targets. Three things make the difference honest:

  * **Every delta carries the cost of acting on it.** A trade worth less than
    its own round trip is not a rebalance, it is a fee. Those are reported as
    `hold` with the reason, not as small trades the user is left to price.
  * **A book that cannot be valued is not silently valued at zero.** A holding
    with no `units` in `config.toml` is named and excluded from equity; a name
    with no price refuses the whole answer rather than dropping a position out
    of a weight calculation that must sum to one.
  * **It reports the shape before and after**, as positions, so the caller can
    run `A12PortfolioRisk` over both. The engine stays pure and offline: prices
    are passed in, never fetched here.

The vocabulary is deliberately `hold / add / trim / exit / open` - what changes
and by how much, never whether to do it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from core.config import Holding
from core.contracts.money import BASE_CURRENCY
from engines.risk.concentration import Limits, Position
from engines.sizing.allocate import Allocation, Candidate, allocate
from engines.sizing.caps import cost_floor_value
from markets.brokers import cost_at


@dataclass(frozen=True)
class Delta:
    """One name, and what would have to change about it."""

    instrument_id: str
    action: str  # hold | add | trim | exit | open
    units_now: int
    units_target: int
    value_now: Decimal
    value_target: Decimal
    cost: Decimal  # round-trip cost of the trade this delta implies
    binding_cap: str
    reason: str = ""

    @property
    def units_delta(self) -> int:
        return self.units_target - self.units_now

    @property
    def trade_value(self) -> Decimal:
        return abs(self.value_target - self.value_now)


@dataclass(frozen=True)
class Rebalance:
    equity: Decimal
    #: The denominator every weight and every limit is measured against: the
    #: whole portfolio, cash included. Defaults to the book's own value.
    capital: Decimal = Decimal(0)
    deltas: tuple[Delta, ...] = ()
    positions_now: tuple[Position, ...] = ()
    positions_target: tuple[Position, ...] = ()
    notes: tuple[str, ...] = ()
    refusal: str = ""
    allocation: Allocation | None = None

    @property
    def traded(self) -> Decimal:
        return sum((d.trade_value for d in self.deltas if d.action != "hold"), Decimal(0))

    @property
    def cost(self) -> Decimal:
        return sum((d.cost for d in self.deltas if d.action != "hold"), Decimal(0))

    def explain(self) -> str:
        if self.refusal:
            body = [f"NO REBALANCE: {self.refusal}", ""]
            body += [f"  {n}" for n in self.notes]
            return "\n".join(body)

        out = [
            f"REBALANCE  book {BASE_CURRENCY} {self.equity:,.2f} over {len(self.deltas)} "
            f"name(s), weighed against {BASE_CURRENCY} {self.capital:,.2f} of capital",
            "",
        ]
        breached = [n for n in self.notes if n.startswith("STOP BREACHED")]
        if breached:
            out += [f"  {n}" for n in breached] + [""]
        out += [
            f"  {'name':<14} {'action':<8} {'units now':>10} {'target':>10} "
            f"{'change':>10} {'trade':>13} {'cost':>9}  bound by",
        ]
        for d in sorted(self.deltas, key=lambda x: (x.action == "hold", x.instrument_id)):
            out.append(
                f"  {d.instrument_id:<14} {d.action:<8} {d.units_now:>10,} "
                f"{d.units_target:>10,} {d.units_delta:>+10,} "
                f"{d.trade_value:>13,.2f} {d.cost:>9,.2f}  {d.binding_cap}"
            )
            if d.reason:
                out.append(f"  {'':<14} {d.reason}")
        out += [
            "",
            f"  {len([d for d in self.deltas if d.action != 'hold'])} trade(s), "
            f"{BASE_CURRENCY} {self.traded:,.2f} turned over, "
            f"{BASE_CURRENCY} {self.cost:,.2f} in round-trip cost",
        ]
        for n in self.notes:
            if not n.startswith("STOP BREACHED"):  # already reported at the top
                out.append(f"\n  {n}")
        out.append(
            "\n  These are differences against a target CAPITAL split, not a set of "
            "positions. Each name still needs its own thesis and at least two "
            "falsifiable breakers before anything is held (docs/04 section 7)."
        )
        return "\n".join(out)


def _adapter(iid: str):
    from markets.registry import get as market_get
    from markets.registry import market_currency, mic_of

    mic = mic_of(iid)
    return mic, market_get(mic), market_currency(mic)


def _candidate(
    iid: str,
    price: Decimal,
    adv: Decimal,
    stop: Decimal | None,
    sector: str,
    broker: str | None = None,
) -> Candidate:
    mic, adapter, currency = _adapter(iid)
    return Candidate(
        instrument_id=iid,
        price=price,
        stop_price=stop,
        adv_20d=adv,
        sector=sector or "unknown",
        country=adapter.country,
        currency=currency,
        lot_size=adapter.lot_size(iid),
        mic=mic,
        # Your broker's terms where it sets them, the venue's otherwise. A
        # rebalance quotes the cost of every change it proposes, so a schedule
        # that is not the one you actually pay understates every one of them.
        round_trip_cost_at=cost_at(mic, broker, price),
    )


def _positions(
    values: Mapping[str, Decimal], meta: Mapping[str, Candidate], capital: Decimal
) -> tuple[Position, ...]:
    """Weights against the WHOLE portfolio, cash included.

    Every limit in `engines/risk/concentration.py` is a fraction of the
    portfolio, and that is the denominator `allocate()` sizes against. Measuring
    the before/after shape against deployed value instead reported the freshly
    built target as breaching the single-name cap at 16.7% and the country limit
    at 100% - a target constructed to satisfy exactly those limits.
    """
    if capital <= 0:
        return ()
    out = []
    for iid, value in values.items():
        if value <= 0:
            continue
        c = meta[iid]
        risk = 0.0
        if c.stop_price is not None and c.price > 0:
            # A live price at or below the stop makes this negative, and a
            # negative risk-to-stop silently SUBTRACTS from portfolio heat - a
            # real book came back at -26.8% heat, which reads as safety. Past
            # the stop there is no distance left to lose to the stop.
            risk = max(0.0, float((value * (c.price - c.stop_price) / c.price) / capital))
        out.append(
            Position(
                instrument_id=iid,
                weight=float(value / capital),
                sector=c.sector,
                country=c.country,
                currency=c.currency,
                risk_to_stop=risk,
            )
        )
    return tuple(out)


def rebalance(
    book: Sequence[Holding],
    nominated: Sequence[Candidate],
    prices: Mapping[str, Decimal],
    advs: Mapping[str, Decimal],
    investable: Decimal | None = None,
    limits: Limits | None = None,
    risk_per_trade: Decimal = Decimal("0.0075"),
    single_name_limit: Decimal = Decimal("0.08"),
    participation: Decimal = Decimal("0.05"),
    broker: str | None = None,
) -> Rebalance:
    """Deltas between the book as held and a target split over the same names.

    `investable` defaults to the book's own market value: with nothing added,
    a rebalance re-splits what is already there. Supplying it means the target
    is computed over that capital instead, and the surfaces say where it came
    from.
    """
    notes: list[str] = []
    held: dict[str, Decimal] = {}  # units actually held
    meta: dict[str, Candidate] = {}

    for h in book:
        if not h.valued:
            notes.append(
                f"{h.id} has no units in config.toml, so it cannot be valued and is not "
                f"part of this rebalance. Add units = <n> to include it."
            )
            continue
        if h.id not in prices:
            return Rebalance(
                Decimal(0),
                refusal=f"{h.id} has no price, and a book with an unpriced name cannot be "
                f"weighed - every weight would be wrong, not just that one",
                notes=tuple(notes),
            )
        held[h.id] = Decimal(h.units or 0)
        meta[h.id] = _candidate(
            h.id, prices[h.id], advs.get(h.id, Decimal(0)), h.stop, h.sector, broker
        )

    for c in nominated:
        meta.setdefault(c.instrument_id, c)
        held.setdefault(c.instrument_id, Decimal(0))

    if not meta:
        return Rebalance(
            Decimal(0),
            refusal="nothing to rebalance: no holdings with units, and no names nominated",
            notes=tuple(notes),
        )

    values_now = {iid: units * meta[iid].price for iid, units in held.items()}
    equity = sum(values_now.values(), Decimal(0))
    capital = investable if investable is not None else equity
    if capital <= 0:
        return Rebalance(
            equity,
            refusal="the book values at zero and no capital was supplied, so there is "
            "nothing to split",
            notes=tuple(notes),
        )

    # A holding whose live price is at or below its own stop is not a data error,
    # which is how the allocator reports it ("stop is at or above entry"). It is
    # the user's own rule saying this position is already closed, and it is the
    # most time-critical line on the screen.
    through_stop = {
        iid: c
        for iid, c in meta.items()
        if c.stop_price is not None and c.price <= c.stop_price and held.get(iid, Decimal(0)) > 0
    }
    for iid, c in through_stop.items():
        notes.append(
            f"STOP BREACHED: {iid} trades at {c.currency} {c.price} against the "
            f"{c.currency} {c.stop_price} stop in config.toml. Your own rule has already "
            f"closed this position, so no target was computed for it below."
        )

    if investable is not None and equity > capital:
        notes.append(
            f"the capital given ({BASE_CURRENCY} {capital:,.2f}) is less than the book is "
            f"worth ({BASE_CURRENCY} {equity:,.2f}), so the weights below exceed 100%. One "
            f"of the two numbers is wrong and this cannot tell which."
        )

    no_stop = [iid for iid, c in meta.items() if c.stop_price is None]
    if no_stop:
        notes.append(
            f"no stop in config.toml for {', '.join(sorted(no_stop))}: the risk-budget cap "
            f"could not be computed for {'them' if len(no_stop) > 1 else 'it'} and did not "
            f"bind. Add stop = <price> to apply it."
        )

    target = allocate(
        capital,
        [c for iid, c in meta.items() if iid not in through_stop],
        limits=limits,
        risk_per_trade=risk_per_trade,
        single_name_limit=single_name_limit,
        participation=participation,
    )
    if target.refusal:
        return Rebalance(
            equity,
            capital,
            refusal=f"a target split over these names is refused, so there is no delta to "
            f"report: {target.refusal}",
            positions_now=_positions(values_now, meta, capital),
            notes=tuple(notes),
            allocation=target,
        )

    by_id = {line.instrument_id: line for line in target.lines}
    for x in target.excluded:
        if held.get(x.instrument_id, Decimal(0)) > 0:
            notes.append(f"{x.instrument_id} is held but not fundable at this capital: {x.reason}")

    deltas: list[Delta] = []
    values_target: dict[str, Decimal] = {}
    for iid, units_now in held.items():
        c = meta[iid]
        line = by_id.get(iid)
        units_target = Decimal(line.units) if line else Decimal(0)
        value_now = values_now[iid]
        value_target = units_target * c.price
        values_target[iid] = value_target
        trade = abs(value_target - value_now)
        cost = Decimal(0)
        if trade > 0 and c.round_trip_cost_at is not None:
            cost = c.round_trip_cost_at(trade)

        # A trade below the market's minimum economic position pays more in
        # spread and fees than the drift it corrects. Reporting it as a small
        # trade invites the user to make it.
        floor = (
            cost_floor_value(c.round_trip_cost_at, c.mic)
            if c.round_trip_cost_at is not None
            else Decimal(0)
        )
        reason = ""
        if iid in through_stop:
            action, reason = (
                "stop hit",
                f"trading at {c.currency} {c.price} against a {c.currency} "
                f"{c.stop_price} stop; no target was computed for it",
            )
            units_target, value_target, cost = units_now, value_now, Decimal(0)
            values_target[iid] = value_now
        elif units_target == units_now:
            action = "hold"
        elif trade < floor:
            action, reason = (
                "hold",
                f"a {c.currency} {trade:,.2f} adjustment costs more to make than the drift "
                f"it corrects (round trip {c.currency} {cost:,.2f}, minimum economic trade "
                f"{c.currency} {floor:,.2f})",
            )
            units_target, value_target, cost = units_now, value_now, Decimal(0)
            values_target[iid] = value_now
        elif units_now == 0:
            action = "open"
        elif units_target == 0:
            action = "exit"
        elif units_target > units_now:
            action = "add"
        else:
            action = "trim"

        deltas.append(
            Delta(
                instrument_id=iid,
                action=action,
                units_now=int(units_now),
                units_target=int(units_target),
                value_now=value_now,
                value_target=value_target,
                cost=cost,
                binding_cap=line.binding_cap if line else "not funded",
                reason=reason,
            )
        )

    return Rebalance(
        equity=equity,
        capital=capital,
        deltas=tuple(deltas),
        positions_now=_positions(values_now, meta, capital),
        positions_target=_positions(values_target, meta, capital),
        notes=tuple(notes) + target.notes,
        allocation=target,
    )
