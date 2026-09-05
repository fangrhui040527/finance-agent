"""What MY broker charges me, as distinct from what the exchange charges everyone.

`markets/<mic>.py` answers the second question. Every schedule in this repository
answers it, and `markets/xnas.py` answers it with a ZERO-COMMISSION US retail
account: a 5 bps floor and a minimum economic position of about USD 1.00.

That is a real account shape and it is not this one. A moomoo Malaysia account
pays 0.03% plus a flat USD 0.99 per order, which is 204.9 bps round trip on a
USD 100 position. Sized against the venue schedule the engine funds US positions
that cannot pay for their own round trip, and it does so silently, because a
wrong floor is still a number.

The first test here is the one that matters most, and it is the same guard
`test_fee_shape.py` opens with: adding a broker layer must move no venue.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from engines.sizing.caps import (
    COST_FLOOR_CEILING,
    cost_floor_bps,
    cost_floor_unreachable,
    cost_floor_value,
)
from markets.brokers import (
    MOOMOO_MY_XKLS,
    MOOMOO_MY_XNAS,
    SEC_FEE_RATE,
    TAF_CAP,
    TAF_PER_SHARE,
    known_brokers,
    schedule_for,
)
from markets.registry import get, supported

D = Decimal


def bps(schedule, value: Decimal, price: Decimal) -> Decimal:
    return schedule.round_trip_bps(value, price)


def leg(name: str):
    """One leg by name. `one_side` sums the sell-only legs too, so a test about
    what ONE leg charges has to ask that leg."""
    return next(x for x in MOOMOO_MY_XNAS.legs if x.name == name)


# --- the regression guard ---------------------------------------------------------


@pytest.mark.parametrize("mic", supported())
def test_no_venue_moves_because_a_broker_exists(mic: str):
    """`test_fee_shape.py` pins these to the cent. A broker schedule is a NEW
    object beside them, never a mutation of one."""
    assert schedule_for(mic) is get(mic).fee_schedule


@pytest.mark.parametrize("mic", supported())
def test_a_venue_schedule_still_needs_no_price(mic: str):
    """The broker schedule demands a price. No venue may start demanding one
    because it was added - that is the constraint test_fee_shape.py:56 sets."""
    assert schedule_for(mic).round_trip(D(10000)) >= 0


def test_an_unknown_broker_is_refused_rather_than_silently_ignored():
    """Falling back to the venue schedule for a typo would size a moomoo account
    against a zero-commission model and say nothing."""
    with pytest.raises(KeyError, match="moomo"):
        schedule_for("XNAS", "moomo_my")


def test_a_broker_with_no_schedule_for_this_venue_falls_back_and_is_not_invented():
    """moomoo prices Bursa and XNAS. Tokyo it does not, so asking about Tokyo
    must return Tokyo's own schedule rather than a fabricated one."""
    assert schedule_for("XTKS", "moomoo_my") is get("XTKS").fee_schedule


# --- the shape of the moomoo schedule ---------------------------------------------


def test_a_hundred_dollar_us_position_pays_226_bps_round_trip():
    """226.6 bps, from the account's real fee card.

    An earlier version of this file asserted 204.9, which was this schedule
    reasoned from a partial card. The 22 bps difference is almost entirely
    MALAYSIAN STAMP DUTY, which this broker charges on US trades too and which
    nothing here knew about until the card was read."""
    got = bps(MOOMOO_MY_XNAS, D(100), D(100))
    assert got.quantize(D("0.0001")) == D("226.6006")


def test_commission_is_waived_below_one_share():
    whole = MOOMOO_MY_XNAS.one_side(D(100), D(100))  # 1.00 share
    frac = MOOMOO_MY_XNAS.one_side(D(99), D(100))  # 0.99 share
    # 0.03% of 99 would be 0.0297; the fractional order pays none of it.
    assert whole - frac > D("0.0297")


def test_the_platform_fee_becomes_a_capped_rate_below_one_share():
    """0.99% of value, capped at USD 0.99 - not the flat USD 0.99."""
    platform = leg("platform")
    # 0.5 share of a USD 100 stock: 0.99% of 50 = 0.495, rounded UP to the cent
    # the card specifies, and well under the 0.99 cap.
    assert platform.charge(D(50), D(100)) == D("0.50")
    # 1.0 share of the same stock pays the flat fee instead, which is twice as
    # much for twice the exposure - and then keeps costing 0.99 all the way up.
    assert platform.charge(D(100), D(100)) == D("0.99")
    assert platform.charge(D(100000), D(100)) == D("0.99")


def test_the_ninety_nine_percent_cap_binds_only_above_a_hundred_dollar_share_price():
    """Below one share the consideration is smaller than the share price, so
    0.99% of it only reaches the USD 0.99 cap on a stock priced above USD 100."""
    platform = leg("platform")
    # 0.99% of 99 is 0.9801 -> 0.99 at the cent, which is also the cap: on a
    # USD 100 stock the two meet. Below that price the rate is what binds.
    assert platform.charge(D(50), D(100)) < D("0.99")
    assert platform.charge(D(495), D(500)) == D("0.99")  # capped, 0.99% = 4.90


def test_one_whole_share_is_the_worst_size_on_a_cheap_stock():
    """The cliff. Crossing 1 share loses the capped 0.99% platform fee and gains
    the flat USD 0.99 PLUS commission, so cost per unit of value MORE THAN
    DOUBLES going from 0.99 shares to 1.00. On a USD 50 stock that is 199.5 bps
    against 403.5 bps, and nothing in the sizing output would otherwise say so.
    """
    just_under = bps(MOOMOO_MY_XNAS, D("49.50"), D(50))
    exactly_one = bps(MOOMOO_MY_XNAS, D(50), D(50))
    assert just_under.quantize(D("0.1")) == D("222.0")
    assert exactly_one.quantize(D("0.1")) == D("429.2")
    assert exactly_one > just_under * D("1.9")


def test_the_regulatory_legs_are_charged_on_the_sell_only():
    """SEC and FINRA fees are levied on sales. Charging them per side overstates
    the floor, and an overstated floor refuses positions that would have cleared.
    """
    one_way = MOOMOO_MY_XNAS.one_way(D(10000), D(100))
    sec = D(10000) * SEC_FEE_RATE
    taf = D(100) * TAF_PER_SHARE
    assert one_way.quantize(D("0.000001")) == (sec + taf).quantize(D("0.000001"))


def test_the_schedule_refuses_to_be_costed_without_a_price():
    """Two legs are per-share and the fractional rule needs a share count. A
    value-only answer here would understate the cost, which is the direction
    that funds a position that cannot pay its own spread."""
    with pytest.raises(ValueError, match="price"):
        MOOMOO_MY_XNAS.round_trip(D(1000))


def test_the_regulatory_rates_are_pinned_because_they_were_not_verified():
    """Regulator pass-throughs, revised annually. Read 2026-09-03 from the
    account's own fee card - the previous values (0.0000278 and 0.000166/8.30)
    were carried over from markets/xnas.py, flagged then as the least-certain
    inputs here, and were wrong. Pinned so the next drift is visible."""
    assert SEC_FEE_RATE == D("0.0000206")
    assert TAF_PER_SHARE == D("0.000195")
    assert TAF_CAP == D("9.79")


def test_moomoo_my_is_a_known_broker():
    assert "moomoo_my" in known_brokers()


# --- what it does to the floor ----------------------------------------------------


def test_the_venue_floor_is_unreachable_on_this_schedule():
    """0.03% commission is 6 bps round trip at ANY size, above the 5 bps XNAS
    tolerance. No position satisfies it, and the bisection returns its ceiling -
    which reads as a USD 100,000,000 position requirement rather than as the
    impossibility it is. Naming the sentinel is what lets a caller say so."""
    at_100 = lambda v: MOOMOO_MY_XNAS.round_trip(v, D(100))  # noqa: E731
    floor = cost_floor_value(at_100, "XNAS")
    assert floor == COST_FLOOR_CEILING
    assert cost_floor_unreachable(floor)


def test_the_broker_carries_its_own_reachable_floor():
    assert cost_floor_bps("XNAS") == D(5)
    assert cost_floor_bps("XNAS", "moomoo_my") == D(35)
    at_100 = lambda v: MOOMOO_MY_XNAS.round_trip(v, D(100))  # noqa: E731
    floor = cost_floor_value(at_100, "XNAS", "moomoo_my")
    assert not cost_floor_unreachable(floor)


def test_the_minimum_position_is_a_function_of_share_price():
    """A per-share leg means there is no single minimum. A USD 10 stock buys ten
    times the share count of a USD 100 one for the same money, and pays ten
    times the per-share settlement fee for it."""
    floors = {
        p: cost_floor_value(lambda v, p=p: MOOMOO_MY_XNAS.round_trip(v, D(p)), "XNAS", "moomoo_my")
        for p in (10, 50, 100, 250)
    }
    assert floors[10] > floors[50] > floors[100] > floors[250]
    assert D(7500) < floors[10] < D(7900)
    assert D(2300) < floors[250] < D(2400)


def test_the_minimum_position_is_three_orders_of_magnitude_above_the_venue_model():
    """The headline. The zero-commission model says a USD 1 position clears its
    own costs; this account needs about USD 2,400 at a USD 100 share price."""
    venue = cost_floor_value(get("XNAS").fee_schedule.round_trip, "XNAS")
    broker = cost_floor_value(lambda v: MOOMOO_MY_XNAS.round_trip(v, D(100)), "XNAS", "moomoo_my")
    assert venue < D(2)
    assert D(2300) < broker < D(2500)


# --- naming the thing that supplied the number ------------------------------------


def test_the_floor_label_names_the_broker_only_when_the_broker_supplied_it():
    """moomoo does not price Bursa, so a moomoo account sizing a Bursa name uses
    Bursa's 60 bps. Output that still said "60 bps round trip on moomoo_my"
    would attribute Bursa's number to an account - the same wrong-label defect
    as claiming the broker's schedule while using the venue's."""
    from engines.sizing.caps import cost_floor_source

    assert cost_floor_source("XNAS", "moomoo_my") == "moomoo_my"
    assert cost_floor_source("XKLS", "moomoo_my") == "moomoo_my"
    # Tokyo moomoo does not price, so Tokyo's own number is what gets used
    # and Tokyo is what must be named.
    assert cost_floor_source("XTKS", "moomoo_my") == "XTKS"
    assert cost_floor_source("XKLS") == "XKLS"
    assert cost_floor_source(None) == "default"


def test_a_moomoo_account_sizes_bursa_on_moomoos_bursa_terms():
    """moomoo prices Bursa too, and cheaper than the exchange's standard retail
    schedule - RM3 flat rather than a RM8 minimum brokerage. Sizing a Bursa
    name against the venue card OVERstates cost by roughly a factor of two."""
    assert schedule_for("XKLS", "moomoo_my") is MOOMOO_MY_XKLS
    assert schedule_for("XKLS", "moomoo_my") is not get("XKLS").fee_schedule
    assert MOOMOO_MY_XKLS.round_trip(D(10_000)) < get("XKLS").fee_schedule.round_trip(D(10_000))


def test_a_venue_moomoo_does_not_price_falls_back_whole():
    """The floor and the schedule must fall back TOGETHER. One falling back
    without the other is how a book gets sized against a mixture."""
    assert cost_floor_bps("XTKS", "moomoo_my") == cost_floor_bps("XTKS")
    assert schedule_for("XTKS", "moomoo_my") is get("XTKS").fee_schedule


def test_the_alias_map_still_resolves_under_a_broker():
    """Bursa ids say MYX. A broker lookup that missed the alias would silently
    hand back the 30 bps default - the exact defect markets/registry.py exists
    to prevent."""
    assert cost_floor_bps("MYX", "moomoo_my") == cost_floor_bps("XKLS", "moomoo_my")
    assert schedule_for("MYX", "moomoo_my") is MOOMOO_MY_XKLS


def test_ask_size_prices_a_us_name_on_the_broker_schedule(tmp_path, capsys, monkeypatch):
    """End to end through the CLI: the note names the broker, and the floor is
    the ~USD 1,500 one rather than the venue model's USD 1.00."""
    import ask
    import core.config

    # Set the key rather than insert one: config.toml ships with a broker
    # selected, and a blind insert makes a duplicate key and invalid TOML.
    shipped = (pathlib.Path(__file__).resolve().parents[1] / "config.toml").read_text(
        encoding="utf-8"
    )
    kept = [ln for ln in shipped.splitlines() if not ln.startswith("broker =")]
    kept.insert(kept.index('markets = ["XKLS", "XNAS"]') + 1, 'broker = "moomoo_my"')
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("\n".join(kept), encoding="utf-8")
    real = core.config.load
    monkeypatch.setattr(core.config, "load", lambda path=None: real(cfg_file))

    code = ask.main(
        [
            "size",
            "XNAS:NVDA",
            "--price",
            "100",
            "--stop",
            "92",
            "--adv",
            "5000000",
            "--portfolio",
            "100000",
            "--fx",
            "4.15",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "moomoo_my schedule on XNAS" in out
    assert "35 bps round trip on moomoo_my" in out
    assert "2,430.71" in out, "the minimum economic position this account really has"
    assert "100,000,000" not in out


# --- the cost nobody publishes ----------------------------------------------------


def test_the_rate_is_a_rate_and_the_spread_is_a_cost():
    """They used to be one number: 4.15, documented as mid-market plus a CARD's
    2.5% markup. This account converts through a broker, not a card, and a cost
    bundled into a rate shrinks the portfolio on every foreign decision without
    naming the reason."""
    from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD, DEFAULT_FX_SPREAD_PER_SIDE

    assert DEFAULT_FX_MYR_PER_USD == D("4.055"), "the mid-market rate, and nothing else"
    assert DEFAULT_FX_SPREAD_PER_SIDE == D("0.005")
    cfg = __import__("core.config", fromlist=["load"]).load()
    assert cfg.fx_myr_per_usd == DEFAULT_FX_MYR_PER_USD
    assert cfg.fx_spread_per_side == DEFAULT_FX_SPREAD_PER_SIDE


def test_the_spread_dwarfs_the_entire_fee_schedule():
    """The reason it is reported at all. Every commission, platform fee,
    settlement fee, stamp duty and levy on a USD 5,000 position comes to about
    0.3% round trip. The currency conversion, at the assumed spread, is about
    1% - more than three times all of it."""
    from core.provenance.ledger import DEFAULT_FX_SPREAD_PER_SIDE as spread

    value = D(5000)
    fees = MOOMOO_MY_XNAS.round_trip_bps(value, D(100))
    fx_round_trip = ((1 + spread) / (1 - spread) - 1) * D(10_000)
    assert fees < D(40), "the whole fee schedule, in bps"
    assert fx_round_trip > fees * 2, "and the spread is multiples of it"


def test_the_spread_is_reported_and_deliberately_not_in_the_floor(tmp_path, capsys, monkeypatch):
    """A floor decides refusals. A refusal that turns on an unmeasured number
    cannot be defended, so the spread is named beside the floor and kept out of
    it until somebody measures the thing."""
    import ask
    import core.config

    shipped = (pathlib.Path(__file__).resolve().parents[1] / "config.toml").read_text(
        encoding="utf-8"
    )
    kept = [ln for ln in shipped.splitlines() if not ln.startswith("broker =")]
    kept.insert(kept.index('markets = ["XKLS", "XNAS"]') + 1, 'broker = "moomoo_my"')
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("\n".join(kept), encoding="utf-8")
    real = core.config.load
    monkeypatch.setattr(core.config, "load", lambda path=None: real(cfg_file))

    ask.main(
        [
            "size",
            "XNAS:NVDA",
            "--price",
            "100",
            "--stop",
            "92",
            "--adv",
            "5000000",
            "--portfolio",
            "100000",
            "--fx",
            "4.055",
        ]
    )
    out = capsys.readouterr().out
    assert "currency" in out and "NOT measured" in out
    assert "NOT in the cost floor" in out
    # the floor itself is unchanged by the spread
    assert "2,430.71" in out


def test_a_domestic_position_says_nothing_about_currency(tmp_path, capsys, monkeypatch):
    """Bursa is priced in the book's own currency. There is no conversion, so
    there is no spread, and a line claiming one would be noise."""
    import ask
    import core.config

    shipped = (pathlib.Path(__file__).resolve().parents[1] / "config.toml").read_text(
        encoding="utf-8"
    )
    kept = [ln for ln in shipped.splitlines() if not ln.startswith("broker =")]
    kept.insert(kept.index('markets = ["XKLS", "XNAS"]') + 1, 'broker = "moomoo_my"')
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("\n".join(kept), encoding="utf-8")
    real = core.config.load
    monkeypatch.setattr(core.config, "load", lambda path=None: real(cfg_file))

    ask.main(
        [
            "size",
            "MYX:1155",
            "--price",
            "10.68",
            "--stop",
            "9.83",
            "--adv",
            "900000",
            "--portfolio",
            "100000",
        ]
    )
    assert "currency  converting" not in capsys.readouterr().out
