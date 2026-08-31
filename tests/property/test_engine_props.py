"""Hypothesis invariants over the deterministic core.

Example-based tests prove the cases someone thought of; these prove the
properties that must hold for EVERY input - the sizing caps can never be
breached, a decomposition's components always reconcile, money arithmetic
never invents value, and no parsed bar is ever internally inconsistent.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

# --- sizing caps ----------------------------------------------------------------

pos_dec = st.decimals(
    min_value="0.01", max_value="10000000", allow_nan=False, allow_infinity=False, places=2
)
frac = st.decimals(min_value="0.001", max_value="0.15", allow_nan=False, places=3)


@given(portfolio=pos_dec, risk=frac, stop=frac)
def test_risk_budget_cap_is_positive_and_monotone_in_risk(portfolio, risk, stop):
    from engines.sizing.caps import risk_budget_cap

    cap = risk_budget_cap(portfolio, risk, stop)
    assert cap > 0
    assert risk_budget_cap(portfolio, risk * 2, stop) >= cap  # more budget, never less


@given(portfolio=pos_dec, risk=frac, stop=frac)
def test_risk_budget_cap_shrinks_as_the_stop_tightens_wait_no_widens(portfolio, risk, stop):
    from engines.sizing.caps import risk_budget_cap

    # A WIDER stop (bigger fraction) means fewer units for the same loss budget.
    assert risk_budget_cap(portfolio, risk, stop * 2) <= risk_budget_cap(portfolio, risk, stop)


@given(adv=pos_dec, participation=frac)
def test_liquidity_cap_is_monotone_in_participation(adv, participation):
    from engines.sizing.caps import liquidity_cap

    assert liquidity_cap(adv, participation) <= liquidity_cap(adv, participation * 2)


@given(
    risk=pos_dec,
    concentration=pos_dec,
    liquidity=pos_dec,
    cost_floor=pos_dec,
    kelly=st.one_of(st.none(), pos_dec),
)
def test_binding_is_never_above_any_individual_cap(
    risk, concentration, liquidity, cost_floor, kelly
):
    from engines.sizing.caps import CapSet

    caps = CapSet(risk, kelly, concentration, liquidity, cost_floor, "MYR")
    _, value = caps.binding()
    assert value <= risk and value <= concentration and value <= liquidity
    if kelly is not None:
        assert value <= kelly


# --- decomposition --------------------------------------------------------------

ret = st.floats(min_value=-0.30, max_value=0.30, allow_nan=False, allow_infinity=False)


@given(
    market=ret,
    sector=ret,
    realised=ret,
    fx=st.floats(min_value=-0.10, max_value=0.10, allow_nan=False),
    b_mkt=st.floats(min_value=-3, max_value=3, allow_nan=False),
    b_sec=st.floats(min_value=-3, max_value=3, allow_nan=False),
)
@settings(max_examples=60)
def test_component_shares_sum_to_one_and_unexplained_stays_bounded(
    market, sector, realised, fx, b_mkt, b_sec
):
    from engines.attribution.decompose import decompose
    from engines.attribution.regression import Fit

    fit = Fit([0.0, b_mkt, b_sec], [0.001] * 260, 0.01, 0.5, 260)
    exp = decompose(
        "MYX:1155", (date(2026, 8, 1), date(2026, 8, 28)), market, sector, {}, realised, fx, fit
    )
    assert exp.components, exp.reason
    assert 0.0 <= exp.unexplained_share <= 1.0
    shares = sum(c.share_of_total for c in exp.components)
    assert math.isclose(shares, 1.0, abs_tol=1e-9) or shares == 0.0
    # contributions reconcile: local pieces + fx == total base within float noise
    total = sum(c.contribution for c in exp.components)
    assert math.isclose(total, exp.total_return_base - fit.coefficients[0], abs_tol=1e-9)


@given(bad=st.sampled_from([float("nan"), float("inf"), -float("inf")]))
def test_a_non_finite_input_is_always_unavailable_never_a_verdict(bad):
    from engines.attribution.decompose import Verdict, decompose
    from engines.attribution.regression import Fit

    fit = Fit([0.0, 1.0, 0.0], [0.001] * 260, 0.01, 0.5, 260)
    exp = decompose("MYX:1155", (date(2026, 8, 1), date(2026, 8, 28)), bad, 0.0, {}, 0.01, 0.0, fit)
    assert exp.verdict is Verdict.ATTRIBUTION_UNAVAILABLE
    assert exp.unexplained_share == 1.0


# --- money ----------------------------------------------------------------------

amount = st.decimals(min_value="-1000000", max_value="1000000", allow_nan=False, places=2)


@given(a=amount, rate=st.decimals(min_value="0.01", max_value="100", places=4))
def test_convert_round_trip_preserves_scale(a, rate):
    from datetime import UTC, datetime

    from core.contracts.money import Money

    now = datetime(2026, 8, 31, tzinfo=UTC)
    usd = Money(amount=a, currency="USD", fx_asof=now)
    myr = usd.convert("MYR", rate, now)
    assert myr.amount == a * rate
    assert myr.currency == "MYR" and myr.fx_asof == now


@given(a=amount)
def test_same_currency_conversion_is_the_identity_whatever_the_rate(a):
    from core.contracts.money import Money

    usd = Money(amount=a, currency="USD")
    assert usd.convert("USD", Decimal("999"), None) is usd


# --- bars -----------------------------------------------------------------------


@given(
    o=st.floats(min_value=0.01, max_value=1000, allow_nan=False),
    spread=st.floats(min_value=0.0, max_value=10, allow_nan=False),
    down=st.floats(min_value=0.0, max_value=10, allow_nan=False),
    close_frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    vol=st.floats(min_value=0, max_value=1e9, allow_nan=False),
)
@settings(max_examples=80)
def test_every_parsed_bar_is_internally_consistent(o, spread, down, close_frac, vol):
    from core.market.feed import PriceFeed

    high = o + spread
    low = max(o - down, 0.001)
    close = low + (high - low) * close_frac
    csv = f"Date,Open,High,Low,Close,Volume\n2026-08-28,{o},{high},{low},{close},{vol}\n"
    bars = PriceFeed.parse(csv, "t")
    for b in bars:
        assert b.high >= max(b.open, b.close) >= min(b.open, b.close) >= b.low > 0
        assert b.volume >= 0


def test_a_corrupt_bar_is_dropped_not_repaired():
    import pytest

    from core.market.feed import NoData, PriceFeed

    csv = "Date,Open,High,Low,Close,Volume\n2026-08-28,10,9,11,10,5\n"  # high < low
    with pytest.raises(NoData):
        PriceFeed.parse(csv, "t")
