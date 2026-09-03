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

from decimal import ROUND_CEILING, Decimal

from markets.contract import FeeLeg, FeeSchedule

#: US regulatory pass-throughs, levied on SALES only, plus the audit-trail fee
#: which is levied on both. These are the SEC's and FINRA's numbers, not
#: moomoo's, and they are revised annually.
#:
#: VERIFIED 2026-09-03 against the fee schedule shown in the account holder's
#: own moomoo Universal Account. The earlier values here were wrong: SEC was
#: 0.0000278 (now 0.0000206) and TAF was 0.000166/share capped 8.30 (now
#: 0.000195/share, min 0.01, capped 9.79). Both were carried over from
#: markets/xnas.py and flagged at the time as the least-certain inputs. They
#: were.
SEC_FEE_RATE = Decimal("0.0000206")
SEC_FEE_MIN = Decimal("0.01")
TAF_PER_SHARE = Decimal("0.000195")
TAF_MIN = Decimal("0.01")
TAF_CAP = Decimal("9.79")

#: Consolidated Audit Trail, NMS stocks. Charged per share on BOTH sides, and
#: missing from this schedule entirely until the real card was read. Tiny -
#: about 0.03 bps at a USD 100 share price - but a leg that is absent is a
#: different kind of wrong from a leg that is small.
CAT_PER_SHARE_NMS = Decimal("0.000003")

#: Settlement is capped at 1% of the trade, which matters exactly where a
#: per-share fee would otherwise run away: a cheap stock bought in size.
SETTLEMENT_PER_SHARE = Decimal("0.003")
SETTLEMENT_CAP_RATE = Decimal("0.01")

#: Malaysian stamp duty, charged by this MALAYSIAN broker on foreign trades
#: too - it appears on the US, HK and SG cards, not only the Bursa one. RM1.00
#: per RM1,000 or fractional part, capped RM1,000 per trade.
#:
#: Modelled as a flat 0.1% rate here because the PROPORTION is currency-
#: independent: RM1 per RM1,000 is 0.1% whether the trade is priced in MYR or
#: USD. What is NOT modelled is the round-up to the next whole ringgit
#: (understates by at most RM1) and the RM1,000 cap (binds only above a
#: RM1,000,000 trade). Both need the MYR value of a USD trade, which means an
#: FX rate inside the fee layer - see docs/19 for why that is deferred.
MY_STAMP_DUTY_RATE = Decimal("0.001")
MY_STAMP_DUTY_PER = Decimal(1000)
MY_STAMP_DUTY_CAP = Decimal(1000)

#: Below this share count moomoo treats the order as fractional and charges a
#: different shape entirely: the platform fee becomes a capped rate and
#: EVERYTHING else - commission, settlement, SEC, TAF, audit trail - is zero.
ONE_SHARE = Decimal(1)
MOOMOO_FRACTIONAL_RATE = Decimal("0.0099")
MOOMOO_FRACTIONAL_CAP = Decimal("0.99")

#: Bursa, from the same card. No minimum commission: the flat RM3 platform fee
#: is what makes a small Malaysian order uneconomic, not a commission floor.
MY_COMMISSION_RATE = Decimal("0.0003")
MY_PLATFORM_FEE = Decimal(3)
MY_CLEARING_RATE = Decimal("0.0003")
MY_CLEARING_CAP = Decimal(1000)
MY_SST_RATE = Decimal("0.08")


def _round_up_cent(amount: Decimal) -> Decimal:
    """moomoo rounds commission UP to the next cent, per order."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _shares(consideration: Decimal, price: Decimal | None, leg: str) -> Decimal:
    """Share count, or a refusal naming the leg that needed it.

    Several of moomoo's legs depend on the count and not the value, and the
    fractional rule depends on whether the count is below one. A value-only
    answer would understate the cost, which is the direction that funds a
    position unable to pay its own spread.
    """
    if price is None or price <= 0:
        raise ValueError(
            f"fee leg {leg!r} depends on the share count and cannot be costed "
            f"from a consideration alone; pass the price"
        )
    return consideration / price


class FractionalFree(FeeLeg):
    """Zero below one share.

    moomoo's fractional card charges the platform fee and NOTHING else:
    commission, settlement, SEC, FINRA and the audit-trail fee are all listed
    as 0. The first version of this file charged the regulatory legs on a
    fractional order because it reasoned from the whole-share card. The real
    card says otherwise.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if _shares(consideration, price, self.name) < ONE_SHARE:
            return Decimal(0)
        return super().charge(consideration, price)


class MoomooCommission(FractionalFree):
    """0.03% of value, rounded UP to the next cent per order, waived below one
    share.

    The rounding is not decoration: on a small order it is most of the fee. At
    a USD 30 trade, 0.03% is 0.9 of a cent and the charge is a whole cent.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if _shares(consideration, price, self.name) < ONE_SHARE:
            return Decimal(0)
        return _round_up_cent(consideration * self.rate)


class MoomooPlatform(FeeLeg):
    """USD 0.99 flat per order - but 0.99% of value, capped at USD 0.99, below
    one share.

    The only leg NOT waived on a fractional order, which is what makes the
    discontinuity at one share so sharp: below it you pay a rate and nothing
    else, at it you pay a flat fee plus five other legs.

    The cap binds only above a USD 100 share price, because below one share the
    consideration is smaller than the price.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if _shares(consideration, price, self.name) < ONE_SHARE:
            return min(
                _round_up_cent(consideration * MOOMOO_FRACTIONAL_RATE), MOOMOO_FRACTIONAL_CAP
            )
        return super().charge(consideration, price)


class MoomooSettlement(FractionalFree):
    """USD 0.003 a share, capped at 1% of the trade.

    A PROPORTIONAL cap, which `FeeLeg.cap` cannot express - that one is an
    absolute ceiling. It binds where a per-share fee would otherwise run away:
    a USD 2 stock pays 0.15% a side uncapped, and 1% is the stated ceiling.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        shares = _shares(consideration, price, self.name)
        if shares < ONE_SHARE:
            return Decimal(0)
        return min(shares * SETTLEMENT_PER_SHARE, consideration * SETTLEMENT_CAP_RATE)


class MalaysianStampDuty(FeeLeg):
    """RM1.00 per RM1,000 of value or fractional part, capped RM1,000.

    A STEP, not a rate: RM1,001 and RM1,999 both pay RM2. On a small Bursa
    order the step is the single largest line - RM1 on a RM100 trade is 1% -
    and no broker choice can reduce it.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        if consideration <= 0:
            return Decimal(0)
        units = (consideration / MY_STAMP_DUTY_PER).quantize(Decimal(1), rounding=ROUND_CEILING)
        return min(units, MY_STAMP_DUTY_CAP)


class MalaysianSst(FeeLeg):
    """8% on commission + platform fee + clearing fee. NOT on stamp duty.

    A tax on OTHER FEES, which no independent leg can express - FeeSchedule
    sums each leg from the consideration alone and shows a leg nothing else.
    So it recomputes the three it taxes from the same module constants those
    legs are built from: one source of truth, at the cost of naming them twice.
    """

    def charge(self, consideration: Decimal, price: Decimal | None = None) -> Decimal:
        commission = _round_up_cent(consideration * MY_COMMISSION_RATE)
        clearing = min(consideration * MY_CLEARING_RATE, MY_CLEARING_CAP)
        return (commission + MY_PLATFORM_FEE + clearing) * MY_SST_RATE


#: moomoo Malaysia, US listings. The STANDARD card - the account currently has
#: a fee-reduction promotion in effect that zeroes commission, platform and
#: settlement, and the standard card is what it reverts to. Modelling the promo
#: as permanent would understate the floor on the day it ends.
#:
#: Commission, platform, settlement and the audit-trail fee are charged on both
#: legs; SEC and FINRA on the sale only, which `per_side=False` expresses and
#: `round_trip` charges once.
MOOMOO_MY_XNAS = FeeSchedule(
    (
        MoomooCommission("commission", rate=MY_COMMISSION_RATE),
        MoomooPlatform("platform", flat=Decimal("0.99")),
        MoomooSettlement("settlement"),
        FractionalFree("cat_fee", per_share=CAT_PER_SHARE_NMS),
        FractionalFree("sec_fee", rate=SEC_FEE_RATE, minimum=SEC_FEE_MIN, per_side=False),
        FractionalFree(
            "finra_taf", per_share=TAF_PER_SHARE, minimum=TAF_MIN, cap=TAF_CAP, per_side=False
        ),
        # Charged by this MALAYSIAN broker on US trades too - see the constant.
        FeeLeg("my_stamp_duty", rate=MY_STAMP_DUTY_RATE),
    )
)

#: moomoo Malaysia, Bursa. Native MYR throughout, so the stamp-duty step and
#: the SST are modelled exactly rather than approximated. No minimum
#: commission: the flat RM3 platform fee is what makes a small order
#: uneconomic, not a commission floor.
MOOMOO_MY_XKLS = FeeSchedule(
    (
        FeeLeg("commission", rate=MY_COMMISSION_RATE),
        FeeLeg("platform", flat=MY_PLATFORM_FEE),
        FeeLeg("clearing", rate=MY_CLEARING_RATE, cap=MY_CLEARING_CAP),
        MalaysianStampDuty("stamp_duty"),
        MalaysianSst("sst"),
    )
)


#: (broker, MIC) -> schedule. A broker absent from a venue here is not an error:
#: it means the account trades that venue on the venue's own terms, and
#: `schedule_for` falls back rather than inventing a schedule for it.
BROKER_SCHEDULES: dict[tuple[str, str], FeeSchedule] = {
    ("moomoo_my", "XNAS"): MOOMOO_MY_XNAS,
    ("moomoo_my", "XKLS"): MOOMOO_MY_XKLS,
}

#: (broker, MIC) -> the cost floor tolerance in bps, set the way the per-market
#: table in engines/sizing/caps.py is: roughly 1.25x the schedule's own
#: asymptotic round-trip cost.
#:
#: Both are far above the venue floors they replace, and the reason is the same
#: for each: MALAYSIAN STAMP DUTY. This broker charges it on every market, not
#: only Bursa, at RM1 per RM1,000 - 10 bps a side, 20 round trip, and it does
#: not fall with size until a RM1,000,000 trade. No position a retail account
#: takes escapes it, so no floor below 20 bps is reachable at all.
#:
#:   Bursa  asymptote ~33 bps in the range a retail account trades. The
#:          "true" asymptote of 7 bps is an artefact of the clearing and stamp
#:          caps, which bind above RM1m and are irrelevant here.
#:   XNAS   asymptote ~27 bps at a USD 100 share price, ~32 at USD 10; two
#:          legs are per-share so it rises as the share price falls.
BROKER_FLOOR_BPS: dict[tuple[str, str], Decimal] = {
    ("moomoo_my", "XNAS"): Decimal("35"),
    ("moomoo_my", "XKLS"): Decimal("40"),
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
