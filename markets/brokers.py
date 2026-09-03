"""What MY broker charges me, as distinct from what the exchange charges everyone.

`markets/<mic>.py` answers the second question: the venue's own schedule, the one
every participant pays. This module answers the first, and they are not the same
question. `markets/xnas.py` models a ZERO-COMMISSION US retail account - a real
account shape, and the reason its floor is 5 bps and its minimum economic
position about USD 1.00.

An account that is not that shape sized against that schedule is funded into
positions that cannot pay for their own round trip, silently, because a wrong
floor is still a number.

Two rules hold this module in place:

  * A schedule here is a NEW object beside the venue's, never a mutation of one.
    `tests/test_fee_shape.py` pins all eleven venues to the cent and separately
    asserts that no shipped venue may start demanding a price to be costed. Both
    are correct and both stay green.
  * An unknown broker is refused, not quietly resolved to the venue schedule. A
    typo in config that fell back would size a moomoo account against the
    zero-commission model and say nothing about it.
"""

from __future__ import annotations

from decimal import Decimal

from markets.contract import FeeLeg, FeeSchedule

#: US regulatory pass-throughs, levied on SALES only. Identical at every US
#: broker - these are not moomoo's numbers, they are the SEC's and FINRA's, and
#: both are revised annually.
#:
#: NOT VERIFIED against a primary source for this schedule. SEC_FEE_RATE is the
#: constant `markets/xnas.py` already ships and that test_fee_shape.py's XNAS
#: baseline is pinned to; the TAF figures are the published retail rates. They
#: are pinned by test so a silent drift cannot happen and a deliberate revision
#: is visible in the diff. Re-check both before trusting a cost floor computed
#: from them to the basis point.
SEC_FEE_RATE = Decimal("0.0000278")
TAF_PER_SHARE = Decimal("0.000166")
TAF_CAP = Decimal("8.30")

#: Below this share count moomoo treats the order as fractional and charges a
#: different shape entirely - see MoomooPlatform.
ONE_SHARE = Decimal(1)
MOOMOO_FRACTIONAL_RATE = Decimal("0.0099")
MOOMOO_FRACTIONAL_CAP = Decimal("0.99")


def _shares(consideration: Decimal, price: Decimal | None, leg: str) -> Decimal:
    if price is None or price <= 0:
        raise ValueError(
            f"fee leg {leg!r} depends on the share count and cannot be costed "
            f"from a consideration alone; pass the price"
        )
    return consideration / price


class MoomooCommission(FeeLeg):
    """0.03% of value, waived entirely below one share.

    The waiver is not generosity and not rounding: it is what makes the cost of
    a fractional order fall almost entirely on the platform fee, and it is half
    of why crossing one whole share is the worst size on a cheap stock.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if _shares(consideration, price, self.name) < ONE_SHARE:
            return Decimal(0)
        return super().charge(consideration, price)


class MoomooPlatform(FeeLeg):
    """USD 0.99 flat per order - but 0.99% of value, capped at USD 0.99, below
    one share.

    The cap only binds above a USD 100 share price, because below one share the
    consideration is smaller than the price. So on a cheap stock a fractional
    order pays a true rate and a whole-share order pays a fixed fee, and the
    two do not meet: at USD 50 a share, 0.99 shares costs 199.5 bps round trip
    and 1.00 share costs 403.5. Nothing else in the schedule has a
    discontinuity, which is exactly why this one is worth naming.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if _shares(consideration, price, self.name) < ONE_SHARE:
            return min(consideration * MOOMOO_FRACTIONAL_RATE, MOOMOO_FRACTIONAL_CAP)
        return super().charge(consideration, price)


#: moomoo Malaysia, US listings. Commission and the platform fee are charged on
#: both legs; the two regulatory fees are levied on the sale only, which
#: `per_side=False` expresses and `FeeSchedule.round_trip` charges once.
MOOMOO_MY_XNAS = FeeSchedule(
    (
        MoomooCommission("commission", rate=Decimal("0.0003")),
        MoomooPlatform("platform", flat=Decimal("0.99")),
        FeeLeg("settlement", per_share=Decimal("0.003")),
        FeeLeg("sec_fee", rate=SEC_FEE_RATE, per_side=False),
        FeeLeg("finra_taf", per_share=TAF_PER_SHARE, cap=TAF_CAP, per_side=False),
    )
)

#: (broker, MIC) -> schedule. A broker absent from a venue here is not an error:
#: it means the account trades that venue on the venue's own terms, and
#: `schedule_for` falls back rather than inventing a schedule for it.
BROKER_SCHEDULES: dict[tuple[str, str], FeeSchedule] = {
    ("moomoo_my", "XNAS"): MOOMOO_MY_XNAS,
}

#: (broker, MIC) -> the cost floor tolerance in bps, set the way the per-market
#: table in engines/sizing/caps.py is: roughly 1.25x the schedule's own
#: asymptotic round-trip cost.
#:
#: moomoo's asymptote is PRICE-DEPENDENT because two legs are per-share: ~6.9 bps
#: at a USD 100 share price, ~12.3 bps at USD 10. 20 bps clears the range that
#: matters without being so loose it stops binding. It is emphatically not the
#: venue's 5 bps: 0.03% commission is 6 bps round trip on its own, so the XNAS
#: entry is not a starting point to nudge - it is unreachable at any size.
BROKER_FLOOR_BPS: dict[tuple[str, str], Decimal] = {
    ("moomoo_my", "XNAS"): Decimal("20"),
}


def known_brokers() -> tuple[str, ...]:
    return tuple(sorted({broker for broker, _ in BROKER_SCHEDULES}))


def prices_venue(broker: str | None, mic: str) -> bool:
    """True when this broker sets its own terms on this venue.

    The label question. A broker that does not price a venue falls through to
    the venue's schedule, and output that still named the broker would credit
    it for a number the exchange supplied.
    """
    from markets.registry import resolve_mic

    return broker is not None and (broker, resolve_mic(mic)) in BROKER_SCHEDULES


def cost_at(mic: str, broker: str | None, price: Decimal):
    """A value-only round-trip cost function for this name, at this price.

    Every sizing surface takes `round_trip_cost_at` as a callable of value
    alone, which a per-share leg cannot answer. The price is known where a
    candidate is built and unknown downstream, so it is bound in here rather
    than threaded through six signatures that have no use for it.
    """
    schedule = schedule_for(mic, broker)
    return lambda value: schedule.round_trip(value, price)


def schedule_for(mic: str, broker: str | None = None) -> FeeSchedule:
    """The schedule this account actually pays on this venue.

    With no broker, or with a broker that does not trade this venue on its own
    terms, this is the venue's schedule - the same object, so identity holds and
    nothing downstream can tell a broker layer was consulted.
    """
    from markets.registry import get, resolve_mic

    canonical = resolve_mic(mic)
    if broker is not None:
        if broker not in known_brokers():
            raise KeyError(
                f"unknown broker {broker!r}. Known brokers: "
                f"{', '.join(known_brokers()) or '(none)'}. A broker is one entry "
                f"in BROKER_SCHEDULES plus, if its costs differ from the venue's, "
                f"one in BROKER_FLOOR_BPS."
            )
        schedule = BROKER_SCHEDULES.get((broker, canonical))
        if schedule is not None:
            return schedule
    return get(canonical).fee_schedule
