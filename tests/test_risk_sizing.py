"""P10/P11: concentration measured honestly, and caps that cannot be breached."""
from datetime import date
from decimal import Decimal as D

import pytest

from engines.risk.concentration import (
    Breach, Limits, Position, check, correlation_clusters, effective_number_of_bets, hhi,
)
from engines.sizing.caps import (
    Band, BindingCap, CapSet, ImplausibleEdge, KELLY_MIN_TRADES, concentration_cap,
    cost_floor_bps, cost_floor_value, kelly_cap, liquidity_cap, risk_budget_cap,
    vol_target_scalar,
)
from engines.sizing.decision import CapBreach, NoPosition, SizingDecision, size
from engines.sizing.waterfall import Goal, Liability, compute
from markets.registry import get

XKLS = get("XKLS")
BREAKERS = ("ROIC below 8% for two consecutive quarters", "net debt/EBITDA above 4x")


# --- concentration -------------------------------------------------------
def ten_banks():
    pos = [Position(f"B{i}", 0.10, "Financials", "MY", "MYR", 0.006) for i in range(10)]
    corr = [[1.0 if i == j else 0.85 for j in range(10)] for i in range(10)]
    return pos, corr


def test_ten_correlated_holdings_are_one_bet():
    """The eggs-in-one-basket failure a position count cannot see."""
    pos, corr = ten_banks()
    assert len(pos) == 10
    assert effective_number_of_bets([p.weight for p in pos], corr) < 1.5


def test_hhi_alone_would_have_passed_this_portfolio():
    """Exactly why four measures exist rather than one."""
    pos, corr = ten_banks()
    assert hhi([p.weight for p in pos]) <= Limits().hhi
    names = {b.limit for b in check(pos, corr, Limits())}
    assert "effective_bets" in names and "correlation_cluster" in names


def test_clustering_finds_the_group_without_being_told_the_sector():
    _, corr = ten_banks()
    assert len(correlation_clusters(corr)) == 1


def test_genuinely_diversified_portfolio_passes():
    """Clearing 5 effective bets needs ~10 names at <=0.10 correlation.

    Six names at 0.15 correlation gives only 3.4 - see the test below. The bar
    is demanding on purpose: it measures independence, not headcount.
    """
    sectors = ["Financials", "Utilities", "Tech", "Healthcare", "Industrials",
               "Energy", "Staples", "Materials", "Telecom", "Property"]
    countries = ["MY", "MY", "US", "US", "JP", "AU", "MY", "SG", "US", "MY"]
    ccys = ["MYR", "MYR", "USD", "USD", "JPY", "AUD", "MYR", "SGD", "USD", "MYR"]
    pos = [Position(f"P{i}", 0.07, sectors[i], countries[i], ccys[i], 0.005)
           for i in range(10)]
    corr = [[1.0 if i == j else 0.10 for j in range(10)] for i in range(10)]
    assert check(pos, corr, Limits()) == []


def test_six_names_at_modest_correlation_is_only_three_and_a_half_bets():
    """Headcount flatters. 6 positions at 0.15 correlation is 3.4 bets, not 6."""
    w = [1 / 6] * 6
    corr = [[1.0 if i == j else 0.15 for j in range(6)] for i in range(6)]
    assert effective_number_of_bets(w, corr) == pytest.approx(3.43, abs=0.05)


def test_every_breach_is_reported_not_just_the_first():
    pos, corr = ten_banks()
    assert len({b.limit for b in check(pos, corr, Limits())}) >= 4


def test_portfolio_heat_is_capped():
    pos = [Position(f"P{i}", 0.05, f"S{i}", "MY", "MYR", 0.02) for i in range(6)]
    corr = [[1.0 if i == j else 0.1 for j in range(6)] for i in range(6)]
    assert any(b.limit == "portfolio_heat" for b in check(pos, corr, Limits()))


def test_single_name_cap_cannot_be_configured_above_fifteen_percent():
    with pytest.raises(ValueError, match="above 15%"):
        Limits(single_name=0.20)


def test_effective_bets_floor_cannot_be_configured_below_three():
    with pytest.raises(ValueError, match="below 3"):
        Limits(min_effective_bets=1.0)


# --- waterfall -----------------------------------------------------------
def test_emergency_floor_and_reservations_come_out_first():
    w = compute(D("42000"), D("3500"), [Goal("car", D("8000"), 18)],
                [Liability("card", D("4000"), D("0.18"))], D("1500"))
    assert w.investable == D("7500")
    assert [s.locked for s in w.steps][:3] == [True, True, True]


def test_high_rate_debt_outranks_equity():
    with_debt = compute(D("30000"), D("2000"), [], [Liability("card", D("9000"), D("0.18"))])
    without = compute(D("30000"), D("2000"), [], [Liability("mortgage", D("9000"), D("0.04"))])
    assert with_debt.investable == without.investable - D("9000")


def test_goals_beyond_24_months_are_not_reserved():
    near = compute(D("30000"), D("2000"), [Goal("g", D("5000"), 12)], [])
    far = compute(D("30000"), D("2000"), [Goal("g", D("5000"), 36)], [])
    assert far.investable > near.investable


def test_floor_is_never_breached_even_when_it_exceeds_assets():
    w = compute(D("5000"), D("3000"), [], [])
    assert w.investable == D(0)


# --- caps ----------------------------------------------------------------
# Realistic inputs. docs/04 puts the honest short-horizon ceiling at 53-56%,
# and payoff ratios above ~1.5 are rare after costs.
P_WIN, PAYOFF = D("0.54"), D("1.5")


def test_kelly_is_disabled_below_the_trade_count_gate():
    assert kelly_cap(D("20000"), P_WIN, PAYOFF, KELLY_MIN_TRADES - 1) is None
    assert kelly_cap(D("20000"), P_WIN, PAYOFF, KELLY_MIN_TRADES) is not None


def test_quarter_kelly_not_full_kelly():
    full_f = (P_WIN * PAYOFF - (1 - P_WIN)) / PAYOFF
    assert kelly_cap(D("20000"), P_WIN, PAYOFF, 60) == pytest.approx(
        D("20000") * full_f * D("0.25"))


def test_implausible_edge_is_refused():
    with pytest.raises(ImplausibleEdge, match="model is broken"):
        kelly_cap(D("20000"), D("0.95"), D("3.0"), 100)


def test_the_plans_own_worked_example_trips_its_own_sanity_ceiling():
    """A contradiction between two documents, found by building it.

    docs/05 section 3.4 works through p=0.56, b=1.8 -> f*=0.316 -> quarter-Kelly
    7.9%. Its arithmetic is right. But docs/09 section 8 records the rule that a
    claimed 30% edge means the model is broken, and 31.6% clears that ceiling.

    The ceiling wins. p=0.56 with a 1.8:1 payoff is an extraordinary strategy,
    not a worked example - the honest ceiling in docs/04 is a 53-56% hit rate.
    Keeping the test documents which rule governs.
    """
    with pytest.raises(ImplausibleEdge):
        kelly_cap(D("20000"), D("0.56"), D("1.8"), 100)


def test_risk_budget_bounds_the_loss_not_the_position():
    cap = risk_budget_cap(D("20000"), D("0.0075"), D("0.125"))
    assert cap * D("0.125") == pytest.approx(D("150"))


def test_vol_overlay_may_derisk_but_never_lever():
    assert vol_target_scalar(D("0.40"), D("0.20")) == D("0.5")
    assert vol_target_scalar(D("0.10"), D("0.20")) == D(1)


def test_bursa_cost_floor_is_market_specific():
    """A single 30 bps floor would refuse every Bursa position ever."""
    assert cost_floor_bps("XKLS") > cost_floor_bps("XNAS")
    floor = cost_floor_value(XKLS.fee_schedule.round_trip, "XKLS")
    assert D("3000") < floor < D("8000")


# --- decision ------------------------------------------------------------
def caps_for(investable, price, atr, trades=12):
    return CapSet(
        risk=risk_budget_cap(investable, D("0.0075"), (D("2.5") * atr) / price),
        kelly=kelly_cap(investable, D("0.56"), D("1.8"), trades),
        concentration=concentration_cap(investable, D("0.08")),
        liquidity=liquidity_cap(D("800000")),
        cost_floor=cost_floor_value(XKLS.fee_schedule.round_trip, "XKLS"),
    )


def test_small_capital_correctly_yields_no_position():
    inv, price, atr = D("7500"), D("6.20"), D("0.31")
    with pytest.raises(NoPosition, match="does not fund one lot"):
        size("1155.KL", Band.ACCUMULATE, inv, caps_for(inv, price, atr), price, 100,
             price - D("2.5") * atr, BREAKERS, date(2028, 8, 24),
             XKLS.fee_schedule.round_trip, mic="XKLS")


def test_adequate_capital_produces_a_lot_rounded_position():
    inv, price, atr = D("400000"), D("6.20"), D("0.31")
    d = size("1155.KL", Band.ACCUMULATE, inv, caps_for(inv, price, atr), price, 100,
             price - D("2.5") * atr, BREAKERS, date(2028, 8, 24),
             XKLS.fee_schedule.round_trip, mic="XKLS")
    assert d.target_units % 100 == 0 and d.target_units > 0
    assert d.binding_cap is BindingCap.RISK


def test_binding_cap_is_surfaced_by_name():
    inv, price, atr = D("400000"), D("6.20"), D("0.31")
    d = size("X", Band.ACCUMULATE, inv, caps_for(inv, price, atr), price, 100,
             price - D("2.5") * atr, BREAKERS, date(2028, 1, 1),
             XKLS.fee_schedule.round_trip, mic="XKLS")
    assert d.binding_cap in set(BindingCap)


def test_sub_economic_position_is_refused_on_cost():
    inv, price, atr = D("60000"), D("0.40"), D("0.02")
    caps = CapSet(risk=D("900"), kelly=None, concentration=D("900"),
                  liquidity=D("900000"), cost_floor=D("4706"))
    with pytest.raises(NoPosition, match="bps floor"):
        size("PENNY", Band.ACCUMULATE, inv, caps, price, 100, price - D("0.05"),
             BREAKERS, date(2028, 1, 1), XKLS.fee_schedule.round_trip, mic="XKLS")


def test_a_position_without_breakers_cannot_be_constructed():
    with pytest.raises(CapBreach, match="thesis breakers"):
        SizingDecision("X", Band.ACCUMULATE, D("1000"), BindingCap.RISK, D("620"), 100,
                       100, D("5"), (), date(2028, 1, 1))


def test_one_breaker_is_not_enough():
    with pytest.raises(CapBreach, match="2-4 breakers"):
        SizingDecision("X", Band.ACCUMULATE, D("1000"), BindingCap.RISK, D("620"), 100,
                       100, D("5"), ("only one",), date(2028, 1, 1))


def test_partial_lot_cannot_be_constructed():
    with pytest.raises(CapBreach, match="board lot"):
        SizingDecision("X", Band.ACCUMULATE, D("1000"), BindingCap.RISK, D("620"), 157,
                       100, D("5"), BREAKERS, date(2028, 1, 1))


def test_non_accumulate_bands_deploy_no_capital():
    for band in (Band.HOLD, Band.TRIM, Band.EXIT, Band.NO_SIGNAL):
        d = size("X", band, D("20000"), caps_for(D("20000"), D("6"), D("0.3")), D("6"),
                 100, D("5"), BREAKERS, date(2028, 1, 1), XKLS.fee_schedule.round_trip)
        assert d.target_units == 0 and d.binding_cap is BindingCap.NONE


def test_trade_breaching_concentration_after_the_fact_is_refused():
    inv = D("400000")
    existing = [Position(f"B{i}", 0.10, "Financials", "MY", "MYR", 0.005) for i in range(9)]
    corr = [[1.0 if i == j else 0.85 for j in range(10)] for i in range(10)]
    with pytest.raises(CapBreach, match="would breach"):
        size("B9", Band.ACCUMULATE, inv, caps_for(inv, D("6.20"), D("0.31")), D("6.20"),
             100, D("5.4"), BREAKERS, date(2028, 1, 1), XKLS.fee_schedule.round_trip,
             existing=existing, limits=Limits(), corr=corr,
             candidate_meta={"sector": "Financials", "country": "MY", "currency": "MYR"},
             mic="XKLS")


# --- found by stress testing (stress/run.py) --------------------------------

def test_a_negative_weight_is_refused_because_it_inflates_hhi_past_its_own_range():
    """HHI is bounded [0,1] and compared against a 0.18 limit. Weights of
    [-0.5, 1.5] returned 2.5 - which reads as extreme concentration rather than
    as the data error it is."""
    import pytest as _pytest
    from engines.risk.concentration import hhi
    with _pytest.raises(ValueError, match="negative"):
        hhi([-0.5, 1.5])
    with _pytest.raises(ValueError, match="not a number"):
        hhi([float("nan"), 0.5])


def test_an_impossible_correlation_matrix_is_refused():
    """corr=2.0 gave 0.67 effective bets from two positions. The range is [1, n],
    and this is the number the entire eggs-in-one-basket rule rests on."""
    import pytest as _pytest
    from engines.risk.concentration import effective_number_of_bets
    with _pytest.raises(ValueError, match=r"outside \[-1, 1\]"):
        effective_number_of_bets([0.5, 0.5], [[1.0, 2.0], [2.0, 1.0]])
    with _pytest.raises(ValueError, match="correlates 1.0 with itself"):
        effective_number_of_bets([0.5, 0.5], [[0.5, 0.1], [0.1, 1.0]])
    with _pytest.raises(ValueError, match="must be 3x3"):
        effective_number_of_bets([0.3, 0.3, 0.4], [[1.0, 0.1], [0.1, 1.0]])


def test_effective_bets_never_leaves_its_mathematical_range():
    import random as _random
    from engines.risk.concentration import effective_number_of_bets
    rng = _random.Random(23)
    for _ in range(200):
        k = rng.randint(2, 8)
        rho = rng.uniform(-0.99, 0.99)
        corr = [[1.0 if i == j else rho for j in range(k)] for i in range(k)]
        bets = effective_number_of_bets([rng.random() for _ in range(k)], corr)
        assert 1.0 - 1e-9 <= bets <= k + 1e-9, f"{bets} bets from {k} positions"


def test_a_negative_adv_cannot_produce_a_negative_liquidity_cap():
    """A negative cap is the smallest of the five, so it always wins binding()
    and carries a negative target size downstream - a cap that inverts."""
    import pytest as _pytest
    from decimal import Decimal as _D
    from engines.sizing.caps import liquidity_cap
    with _pytest.raises(ValueError, match="cannot be negative"):
        liquidity_cap(_D("-1000000"))
    with _pytest.raises(ValueError, match="participation"):
        liquidity_cap(_D("1000000"), _D("2.0"))
