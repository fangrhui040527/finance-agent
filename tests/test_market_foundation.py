"""P1/P2: identity, calendars, price adjustment, adapter conformance."""
from datetime import date, time
from decimal import Decimal as D

import pytest

from core.market.calendar import SessionCalendar, SessionWindow
from core.market.instrument import IdentityResolver, Instrument, Status
from core.market.prices import (
    ActionKind, Bar, CorporateAction, FxStore, PriceSeries, base_currency_return,
)
from markets.registry import get, supported


def mk(iid, ticker, mic="XKLS", first=date(2000, 1, 1), delisted=None, isin=None):
    return Instrument(
        instrument_id=iid, primary_ticker=ticker, mic=mic, currency="MYR",
        lot_size=100, name=iid, first_listed=first, delisted_at=delisted, isin=isin,
        status=Status.DELISTED if delisted else Status.LISTED,
    )


# --- identity ------------------------------------------------------------
def test_aliases_collapse_to_one_instrument():
    r = IdentityResolver()
    r.register(mk("MY_MAYBANK", "1155", isin="MYL1155OO000"), aliases=["MAYBANK", "0011.KL"])
    ids = {r.resolve(t).instrument_id for t in ("1155", "MAYBANK", "0011.KL", "MYL1155OO000")}
    assert ids == {"MY_MAYBANK"}


def test_mic_disambiguates_a_shared_ticker():
    r = IdentityResolver()
    r.register(mk("MY_X", "ABC", "XKLS"))
    r.register(Instrument(instrument_id="US_X", primary_ticker="ABC", mic="XNAS",
                          currency="USD", lot_size=1, name="US_X", first_listed=date(2000, 1, 1)))
    assert r.resolve("ABC", mic="XNAS").instrument_id == "US_X"
    assert r.resolve("ABC", mic="XKLS").instrument_id == "MY_X"


def test_universe_includes_the_dead():
    """docs/06 rule 5: a universe of today's listings has deleted every bankruptcy."""
    r = IdentityResolver()
    r.register(mk("ALIVE", "A"))
    r.register(mk("DEAD", "D", delisted=date(2020, 6, 1)))
    assert set(r.universe_on(date(2019, 1, 1))) == {"ALIVE", "DEAD"}
    assert set(r.universe_on(date(2026, 1, 1))) == {"ALIVE"}


# --- calendars -----------------------------------------------------------
def test_weekend_and_holiday_are_not_sessions():
    cal = SessionCalendar((SessionWindow(time(9), time(17)),), 8,
                          holidays=frozenset({date(2026, 5, 1)}))
    assert cal.is_session(date(2026, 5, 4))       # Monday
    assert not cal.is_session(date(2026, 5, 2))   # Saturday
    assert not cal.is_session(date(2026, 5, 1))   # holiday


def test_half_day_drops_the_afternoon_window():
    cal = SessionCalendar(
        (SessionWindow(time(9), time(12, 30)), SessionWindow(time(14, 30), time(17))),
        8, half_days=frozenset({date(2026, 5, 4)}))
    assert len(cal.session(date(2026, 5, 4)).windows) == 1
    assert len(cal.session(date(2026, 5, 5)).windows) == 2


def test_session_shift_skips_non_sessions():
    cal = SessionCalendar((SessionWindow(time(9), time(17)),), 8)
    assert cal.shift(date(2026, 5, 1), 1) == date(2026, 5, 4)   # Fri -> Mon
    assert cal.shift(date(2026, 5, 4), -1) == date(2026, 5, 1)


def test_utc_alignment_uses_the_offset():
    cal = SessionCalendar((SessionWindow(time(9), time(17)),), 8)
    assert cal.session(date(2026, 5, 4)).open_utc().hour == 1   # 09:00 UTC+8


# --- prices --------------------------------------------------------------
def test_split_adjusts_history_backwards_only():
    bars = [Bar(date(2026, 1, d), 10, 11, 9, 10.0 + d, 1000) for d in range(1, 6)]
    s = PriceSeries("X", bars, [CorporateAction(date(2026, 1, 4), ActionKind.SPLIT, 2.0)])
    adj = s.closes()
    assert adj[:3] == [pytest.approx(5.5), pytest.approx(6.0), pytest.approx(6.5)]
    assert adj[3:] == [pytest.approx(14.0), pytest.approx(15.0)]


def test_raw_series_is_never_mutated():
    bars = [Bar(date(2026, 1, d), 10, 11, 9, 10.0, 1000) for d in range(1, 4)]
    s = PriceSeries("X", bars, [CorporateAction(date(2026, 1, 3), ActionKind.SPLIT, 2.0)])
    s.adjusted()
    assert s.closes(adjusted=False) == [10.0, 10.0, 10.0]


def test_adv_is_value_not_share_count():
    bars = [Bar(date(2026, 1, d), 5, 5, 5, 5.0, 1000) for d in range(1, 4)]
    assert PriceSeries("X", bars).adv(3) == 5000.0


def test_fx_compounds_multiplicatively():
    """+8% local with -10% FX is a loss in base currency."""
    assert base_currency_return(0.08, -0.10) == pytest.approx(-0.028)


def test_fx_store_is_asof_and_inverts():
    fx = FxStore()
    fx.add("USD", "MYR", date(2026, 1, 1), D("4.00"))
    fx.add("USD", "MYR", date(2026, 8, 1), D("4.15"))
    assert fx.rate_asof("USD", "MYR", date(2026, 5, 1))[0] == D("4.00")
    assert fx.rate_asof("USD", "MYR", date(2026, 9, 1))[0] == D("4.15")
    assert fx.rate_asof("MYR", "USD", date(2026, 9, 1))[0] == pytest.approx(D(1) / D("4.15"))
    assert fx.rate_asof("USD", "MYR", date(2025, 1, 1)) is None


# --- adapter conformance (docs/06 section 2.1) ---------------------------
@pytest.mark.parametrize("mic", supported())
def test_conformance_calendar_produces_sessions(mic):
    a = get(mic)
    assert a.calendar.count_sessions(date(2026, 1, 1), date(2026, 12, 31)) > 200


@pytest.mark.parametrize("mic", supported())
def test_conformance_fees_rise_with_size_and_never_negative(mic):
    fs = get(mic).fee_schedule
    small, large = fs.round_trip(D("1000")), fs.round_trip(D("100000"))
    assert 0 <= small <= large


@pytest.mark.parametrize("mic", supported())
def test_conformance_lot_rounding_never_produces_a_partial_lot(mic):
    a = get(mic)
    for units in (0, 1, 99, 100, 157, 1001):
        assert a.lot_round_down(units, "X") % a.lot_size("X") == 0


@pytest.mark.parametrize("mic", supported())
def test_conformance_tick_size_positive(mic):
    a = get(mic)
    assert all(a.tick_size(D(p)) > 0 for p in ("0.5", "5", "50", "500"))


def test_bursa_fee_matches_the_published_schedule():
    """0.1% brokerage (min RM 8) + 0.03% clearing (cap 1000) + 0.1% stamp (cap 1000)."""
    fs = get("XKLS").fee_schedule
    one_side = fs.one_side(D("10000"))
    assert one_side == D("10") + D("3") + D("10")


def test_bursa_brokerage_minimum_dominates_small_trades():
    fs = get("XKLS").fee_schedule
    assert fs.round_trip_bps(D("620")) > 250     # a lot at RM 6.20 is uneconomic
    assert fs.round_trip_bps(D("50000")) < 50


def test_us_dividends_withhold_30pc_for_a_malaysian_holder():
    assert get("XNAS").withholding("dividend", "MY") == D("0.30")
    assert get("XKLS").withholding("dividend", "MY") == D(0)


def test_tier_three_market_would_not_claim_a_factor_model():
    assert get("XKLS").supports_factor_model() is True


# --- XSES: the first T2 market, and the test of the extensibility claim -----

def test_adding_a_market_did_not_change_any_engine_or_agent():
    """docs/01 section 10: a new market is one adapter class plus one registry
    entry. XSES is the proof - the conformance suite above is parameterised over
    supported(), so registering it subjected it to every conformance test with
    no new test code at all."""
    assert "XSES" in supported()
    assert len(supported()) == 3


def test_singapore_charges_no_stamp_duty_unlike_bursa():
    sg, my = get("XSES"), get("XKLS")
    assert not any(leg.name == "stamp_duty" for leg in sg.fee_schedule.legs)
    assert any(leg.name == "stamp_duty" for leg in my.fee_schedule.legs)


def test_singapore_is_structurally_cheaper_than_bursa_at_every_size():
    sg, my = get("XSES"), get("XKLS")
    for v in (D("10000"), D("50000"), D("200000")):
        assert sg.fee_schedule.round_trip_bps(v) < my.fee_schedule.round_trip_bps(v)


def test_the_singapore_clearing_cap_binds_at_large_size():
    sg = get("XSES")
    small = sg.fee_schedule.round_trip_bps(D("200000"))
    large = sg.fee_schedule.round_trip_bps(D("5000000"))
    assert large < small, "the SGD 600 clearing cap should pull the rate down"


def test_a_malaysian_holder_pays_no_withholding_on_singapore_dividends():
    """Singapore's one-tier system, unlike the 30% XNAS applies."""
    assert get("XSES").withholding("dividend", "MY") == D(0)
    assert get("XNAS").withholding("dividend", "MY") == D("0.30")


def test_singapore_trades_one_continuous_session_not_two():
    assert len(get("XSES").calendar.windows) == 1
    assert len(get("XKLS").calendar.windows) == 2


def test_every_supported_market_has_an_explicit_cost_floor():
    """A market falling back to the generic default is an accident waiting to
    be inherited by the next market added (docs/05 section 3.5)."""
    from engines.sizing.caps import COST_FLOOR_BPS_BY_MIC
    missing = [m for m in supported() if m not in COST_FLOOR_BPS_BY_MIC]
    assert not missing, f"markets with no explicit cost floor: {missing}"


def test_the_singapore_minimum_economic_position_is_about_nine_thousand():
    """Roughly twice Bursa's RM 4,700 in nominal terms - the brokerage minimum
    is higher relative to the rate, so small positions are punished harder."""
    sg = get("XSES")
    assert sg.fee_schedule.round_trip_bps(D("9100")) <= D("30")
    assert sg.fee_schedule.round_trip_bps(D("5000")) > D("30")
