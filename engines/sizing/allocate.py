"""Split investable capital across names the user nominated.

This does NOT pick names. It answers the question after that one: given these
candidates and this much capital, how much of each may be held without
breaching a limit — and which of them cannot be funded at all, with the reason.

Three properties decide whether this is useful or dangerous:

  * **It never constructs a position.** `SizingDecision` requires two written
    breakers before a position exists (`engines/sizing/decision.py`), and that
    is a commitment, not a formality. An allocation is a CAPITAL answer; every
    line still needs its own thesis and its breakers before anything is held.
    The rendered output says so.
  * **It refuses rather than fabricates diversification.** If the names given
    cannot meet `min_positions` or `min_effective_bets`, the whole allocation
    is refused. A book of two names that satisfies every arithmetic check is
    still a book of two names, and splitting capital across too few is the
    failure the effective-bets floor exists to catch.
  * **Every excluded name carries its reason.** Below the cost floor, cannot
    fund one lot, missing an FX rate, trimmed to nothing by a limit — silence
    would leave the user assuming the name was simply not worth funding.

The loop is deliberately a trim-and-recheck, not an optimiser: it must be
explainable line by line to the person whose money it is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from core.contracts.money import BASE_CURRENCY
from engines.risk.concentration import Limits, Position, check, effective_number_of_bets
from engines.sizing.caps import (
    CapSet,
    concentration_cap,
    cost_floor_value,
    liquidity_cap,
    risk_budget_cap,
    to_base,
)

#: Trim passes before giving up. Each pass removes one board lot from the
#: largest position in a breaching dimension, so this is bounded work with a
#: visible reason for stopping rather than a solver that might not converge.
MAX_TRIM_PASSES = 200

#: Caps that size ONE name. Anything else on a line ("country", "sector", "hhi")
#: came from the trim loop and is a property of the book, which reads very
#: differently to the user.
_PER_NAME_CAPS = frozenset({"risk", "kelly", "concentration", "liquidity", "cost_floor"})


@dataclass(frozen=True)
class Candidate:
    """A name the user nominated, with what sizing needs to know about it."""

    instrument_id: str
    price: Decimal
    #: None means no stop was stated. The risk-budget cap is then undefined and
    #: simply does not bind - inventing a stop would invent the risk budget with
    #: it. Whoever sizes such a name says which cap it lost.
    stop_price: Decimal | None
    adv_20d: Decimal
    sector: str = "unknown"
    country: str = "MY"
    currency: str = BASE_CURRENCY
    lot_size: int = 100
    mic: str | None = None
    fx_base_per_quote: Decimal | None = None
    round_trip_cost_at: Callable[[Decimal], Decimal] | None = None


@dataclass(frozen=True)
class Line:
    """One funded name. `value_base` is MYR and is the only comparable number."""

    instrument_id: str
    units: int
    value_native: Decimal
    value_base: Decimal
    weight: float
    binding_cap: str
    currency: str


@dataclass(frozen=True)
class Excluded:
    instrument_id: str
    reason: str


@dataclass(frozen=True)
class Allocation:
    investable: Decimal
    lines: tuple[Line, ...] = ()
    excluded: tuple[Excluded, ...] = ()
    breaches: tuple[str, ...] = ()
    refusal: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def deployed(self) -> Decimal:
        return sum((x.value_base for x in self.lines), Decimal(0))

    @property
    def cash(self) -> Decimal:
        return self.investable - self.deployed

    def explain(self) -> str:
        if self.refusal:
            body = [f"NO ALLOCATION: {self.refusal}", ""]
            if self.excluded:
                body.append("  names that could not be funded")
                body += [f"    {x.instrument_id:<14} {x.reason}" for x in self.excluded]
            return "\n".join(body)

        lines = [
            f"ALLOCATION over {len(self.lines)} name(s), "
            f"{BASE_CURRENCY} {self.investable:,.2f} investable",
            "",
        ]
        for line in self.lines:
            native = (
                f"{line.currency} {line.value_native:,.2f} = "
                if line.currency != BASE_CURRENCY
                else ""
            )
            lines.append(
                f"  {line.instrument_id:<14} {line.units:>8,} units  "
                f"{native}{BASE_CURRENCY} {line.value_base:>12,.2f}  "
                f"{line.weight:>6.1%}  bound by {line.binding_cap}"
            )
        lines.append("")
        lines.append(
            f"  deployed {BASE_CURRENCY} {self.deployed:,.2f}, "
            f"cash {BASE_CURRENCY} {self.cash:,.2f}"
        )
        if self.excluded:
            lines.append("")
            lines.append("  not funded")
            for x in self.excluded:
                lines.append(f"    {x.instrument_id:<14} {x.reason}")
        for note in self.notes:
            lines.append(f"\n  {note}")
        lines.append(
            "\n  This is a CAPITAL split, not a set of positions. Each name still "
            "needs its own thesis and at least two falsifiable breakers before "
            "anything is held (docs/04 section 7)."
        )
        return "\n".join(lines)


def _cap_set(
    c: Candidate,
    investable: Decimal,
    risk_per_trade: Decimal,
    single_name_limit: Decimal,
    participation: Decimal,
) -> CapSet | tuple[str, Decimal, Decimal]:
    """The same five caps the single-name path uses, in the market's currency.

    Without a stop the risk budget is undefined, and a `CapSet` cannot express
    that - its `binding()` always includes `risk`. Rather than fake a stop or
    weaken the shared type, that case returns the binding cap already chosen
    from the caps that ARE defined, so the missing one cannot be mistaken for
    an applied one.
    """
    pv_quote = (
        investable
        if c.currency == BASE_CURRENCY
        else investable / (c.fx_base_per_quote or Decimal(1))
    )
    floor = cost_floor_value(c.round_trip_cost_at, c.mic) if c.round_trip_cost_at else Decimal(0)
    conc = concentration_cap(pv_quote, single_name_limit)
    liq = liquidity_cap(c.adv_20d, participation)
    if c.stop_price is None:
        cap, value = min([("concentration", conc), ("liquidity", liq)], key=lambda x: x[1])
        return cap, value, floor
    return CapSet(
        risk=risk_budget_cap(pv_quote, risk_per_trade, (c.price - c.stop_price) / c.price),
        kelly=None,
        concentration=conc,
        liquidity=liq,
        cost_floor=floor,
        currency=c.currency,
    )


def _lots(value: Decimal, price: Decimal, lot: int) -> int:
    units = int((value / price).to_integral_value(rounding=ROUND_DOWN))
    return (units // lot) * lot


def allocate(
    investable: Decimal,
    candidates: list[Candidate],
    limits: Limits | None = None,
    risk_per_trade: Decimal = Decimal("0.0075"),
    single_name_limit: Decimal = Decimal("0.08"),
    participation: Decimal = Decimal("0.05"),
    corr: list[list[float]] | None = None,
) -> Allocation:
    """Investable capital across nominated names, or a refusal with the reason."""
    limits = limits or Limits()
    if investable <= 0:
        return Allocation(investable, refusal="there is no investable capital to allocate")
    if not candidates:
        return Allocation(investable, refusal="no candidates were nominated")

    excluded: list[Excluded] = []
    sized: dict[str, tuple[Candidate, int, Decimal, str]] = {}
    # The correlation matrix is indexed by the CALLER's candidate order. Names
    # get excluded and trimmed away below, so every use of it has to be sliced
    # to the survivors - handing check() a 6x6 matrix for 5 positions raised
    # rather than answered.
    order = {c.instrument_id: i for i, c in enumerate(candidates)}

    for c in candidates:
        if c.stop_price is not None and c.stop_price >= c.price:
            excluded.append(Excluded(c.instrument_id, "stop is at or above entry"))
            continue
        if c.currency != BASE_CURRENCY and c.fx_base_per_quote is None:
            excluded.append(
                Excluded(
                    c.instrument_id,
                    f"priced in {c.currency} with no {BASE_CURRENCY} rate supplied; "
                    f"the cap and the price cannot be compared without one",
                )
            )
            continue
        caps = _cap_set(c, investable, risk_per_trade, single_name_limit, participation)
        if isinstance(caps, CapSet):
            cap, value, floor = (*caps.binding(), caps.cost_floor)
            label = cap.value
        else:
            label, value, floor = caps
        units = _lots(value, c.price, c.lot_size)
        if units <= 0:
            excluded.append(
                Excluded(
                    c.instrument_id,
                    f"the binding {label} cap of {c.currency} {value:,.2f} does "
                    f"not fund one {c.lot_size}-share lot at {c.price}",
                )
            )
            continue
        sized[c.instrument_id] = (c, units, floor, label)

    # Scale to fit the capital actually available, then re-round to whole lots.
    def value_base(c: Candidate, units: int) -> Decimal:
        return to_base(Decimal(units) * c.price, c.currency, c.fx_base_per_quote)

    total = sum((value_base(c, u) for c, u, _, _ in sized.values()), Decimal(0))
    if total > investable and total > 0:
        scale = investable / total
        for iid, (c, units, floor, binding_label) in list(sized.items()):
            scaled = _lots(Decimal(units) * c.price * scale, c.price, c.lot_size)
            sized[iid] = (c, scaled, floor, binding_label)

    # The cost floor applies to the FINAL size, not the pre-scaled one: a name
    # scaled below the minimum economic position is not a smaller position, it
    # is a position that cannot pay for its own round trip.
    for iid, (c, units, floor, _binding) in list(sized.items()):
        native = Decimal(units) * c.price
        if units <= 0:
            excluded.append(Excluded(iid, "scaled below one board lot by the available capital"))
            del sized[iid]
        elif floor > 0 and native < floor:
            excluded.append(
                Excluded(
                    iid,
                    f"{units:,} units is {c.currency} {native:,.2f}, below the "
                    f"{c.currency} {floor:,.2f} minimum economic position",
                )
            )
            del sized[iid]

    if not sized:
        # "Too small" is not a satisfying answer without the number that makes it
        # true. Two limits collide here: a position must clear the market's
        # minimum economic position to pay for its own round trip, and it must
        # stay under the single-name cap. Below floor/cap there is no size that
        # satisfies both, and that threshold is worth stating outright.
        floors = [
            (cost_floor_value(c.round_trip_cost_at, c.mic), c)
            for c in candidates
            if c.round_trip_cost_at is not None
        ]
        need = ""
        if floors and single_name_limit > 0:
            floor, c = min(floors, key=lambda x: x[0])
            need = (
                f". The cheapest name to hold here needs {c.currency} {floor:,.2f} to clear "
                f"the {c.mic or 'market'} cost floor, and a {single_name_limit:.0%} single-name "
                f"cap puts that position inside a portfolio of at least {c.currency} "
                f"{floor / single_name_limit:,.0f}. Below that, a position either breaches the "
                f"cap or cannot pay for its own round trip"
            )
        return Allocation(
            investable,
            excluded=tuple(excluded),
            refusal=f"no nominated name could be funded at this capital level{need}",
        )

    # Trim until every concentration limit clears, or until it is clear that
    # trimming cannot fix it.
    notes: list[str] = []
    breaches: list[str] = []
    for _ in range(MAX_TRIM_PASSES):
        positions = _positions(sized, investable)
        sub = _submatrix(corr, order, [p.instrument_id for p in positions])
        found = check(positions, sub, limits, base_currency=BASE_CURRENCY)
        breaches = [str(b) for b in found]
        if not found:
            break
        trimmed = _trim_largest(sized, positions, found)
        if not trimmed:
            break
    else:
        notes.append(f"stopped after {MAX_TRIM_PASSES} trim passes")

    if breaches:
        return Allocation(
            investable,
            excluded=tuple(excluded),
            breaches=tuple(breaches),
            refusal=(
                "the nominated names cannot be held together inside the limits: "
                + "; ".join(breaches)
            ),
        )

    lines = _lines(sized, investable)

    # Diversification you had to invent is not diversification.
    if len(lines) < limits.min_positions:
        return Allocation(
            investable,
            excluded=tuple(excluded),
            refusal=(
                f"{len(lines)} fundable name(s) is below the {limits.min_positions}-position "
                f"floor. Nominate more names, or hold cash - splitting capital across too "
                f"few names is the concentration this floor exists to prevent"
            ),
        )
    # Undeployed cash is the usual outcome and the usual surprise: N names each
    # capped at the single-name limit cannot absorb more than N x limit of the
    # capital. Say the arithmetic rather than leave a large cash balance to be
    # read as a mistake.
    deployed = sum((x.value_base for x in lines), Decimal(0))
    if investable > 0 and (investable - deployed) / investable > Decimal("0.2"):
        portfolio_caps = {x.binding_cap for x in lines if x.binding_cap not in _PER_NAME_CAPS}
        if portfolio_caps:
            notes.append(
                f"deployment is bounded by the {', '.join(sorted(portfolio_caps))} limit, not "
                f"by capital: the names given cannot hold more of it together and stay inside "
                f"the limits. The rest stays in cash."
            )
        else:
            ceiling = Decimal(len(lines)) * single_name_limit
            notes.append(
                f"{len(lines)} names at a {single_name_limit:.0%} single-name cap can hold at "
                f"most {ceiling:.0%} of capital; the rest stays in cash. Nominate more names to "
                f"deploy more - raising the cap concentrates instead."
            )

    weights = [x.weight for x in lines]
    sub = _submatrix(corr, order, [x.instrument_id for x in lines])
    bets = effective_number_of_bets(weights, sub) if sub else float(len(weights))
    if bets < limits.min_effective_bets:
        return Allocation(
            investable,
            excluded=tuple(excluded),
            refusal=(
                f"effective bets {bets:.1f} is below the {limits.min_effective_bets:.1f} floor; "
                f"the book would look diversified and behave as one bet"
            ),
        )

    return Allocation(investable, lines=lines, excluded=tuple(excluded), notes=tuple(notes))


def _submatrix(corr: list[list[float]] | None, order: dict, ids: list[str]):
    """The correlation rows and columns for the names still standing."""
    if corr is None:
        return None
    idx = [order[i] for i in ids if i in order and order[i] < len(corr)]
    if len(idx) != len(ids):
        return None  # a name the caller gave no correlation for: use none at all
    return [[corr[r][c] for c in idx] for r in idx]


def _positions(sized: dict, investable: Decimal) -> list[Position]:
    out = []
    for iid, (c, units, _, _) in sized.items():
        base = to_base(Decimal(units) * c.price, c.currency, c.fx_base_per_quote)
        # No stop, no risk-to-stop: portfolio heat cannot count what was never
        # stated, and counting it as zero is the honest reading - it is unbounded,
        # and the caller was told the risk cap did not apply.
        stop = c.stop_price if c.stop_price is not None else c.price
        risk_native = Decimal(units) * (c.price - stop)
        out.append(
            Position(
                instrument_id=iid,
                weight=float(base / investable) if investable else 0.0,
                sector=c.sector,
                country=c.country,
                currency=c.currency,
                risk_to_stop=float(
                    to_base(risk_native, c.currency, c.fx_base_per_quote) / investable
                )
                if investable
                else 0.0,
            )
        )
    return out


def _lines(sized: dict, investable: Decimal) -> tuple[Line, ...]:
    out = []
    for iid, (c, units, _, binding) in sorted(sized.items()):
        native = Decimal(units) * c.price
        base = to_base(native, c.currency, c.fx_base_per_quote)
        out.append(
            Line(
                instrument_id=iid,
                units=units,
                value_native=native,
                value_base=base,
                weight=float(base / investable) if investable else 0.0,
                binding_cap=binding,
                currency=c.currency,
            )
        )
    return tuple(out)


def _trim_largest(sized: dict, positions: list[Position], breaches: list) -> bool:
    """Remove one board lot from the heaviest position. Returns False when
    nothing can be trimmed, which is how the caller learns to stop.

    The trimmed name takes the breached dimension as its binding cap. It was
    sized by a per-name cap and then cut by a portfolio one, and reporting the
    first hides the constraint that actually decided the number - a book of six
    Malaysian names is bounded by the 40% country limit, not by the 8%
    single-name cap that sized each of them.
    """
    heaviest = max(positions, key=lambda p: p.weight, default=None)
    if heaviest is None:
        return False
    c, units, floor, binding = sized[heaviest.instrument_id]
    if breaches:
        binding = getattr(breaches[0], "limit", None) or binding
    new_units = units - c.lot_size
    if new_units <= 0:
        del sized[heaviest.instrument_id]
        return bool(sized)
    sized[heaviest.instrument_id] = (c, new_units, floor, binding)
    return True
