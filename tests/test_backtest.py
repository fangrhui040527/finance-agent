"""P12: the gate. Purged splits, realistic costs, multiple-testing correction."""
import math
import random
from decimal import Decimal as D

import pytest

from engines.backtest.costs import fill_cost, participation_slippage, round_trip_cost
from engines.backtest.harness import Benchmark, Regime, run
from engines.backtest.metrics import (
    deflated_sharpe, drawdown_profile, expected_max_sharpe, performance,
    probabilistic_sharpe,
)
from engines.backtest.splitter import Fold, LeakageError, purged_walk_forward
from markets.registry import get

XKLS, XNAS = get("XKLS"), get("XNAS")


# --- splitter ------------------------------------------------------------
def test_test_blocks_move_strictly_forward():
    folds = purged_walk_forward(500, 4, 20, min_train=100)
    starts = [f.test[0] for f in folds]
    assert starts == sorted(starts)


def test_training_never_overlaps_test():
    for f in purged_walk_forward(500, 4, 20, min_train=100):
        f.assert_no_overlap()
        assert max(f.train) < min(f.test)


def test_purging_removes_the_label_horizon_before_each_test_block():
    for f in purged_walk_forward(500, 4, 20, min_train=100):
        assert f.purged == 20
        assert min(f.test) - max(f.train) > 20


def test_embargo_defaults_to_the_label_horizon():
    f = purged_walk_forward(500, 2, 15, min_train=100)[0]
    assert f.embargoed == 15


def test_there_is_no_shuffle_option_to_set_by_accident():
    import inspect
    assert "shuffle" not in inspect.signature(purged_walk_forward).parameters
    assert "random_state" not in inspect.signature(purged_walk_forward).parameters


def test_overlapping_fold_raises_rather_than_silently_leaking():
    with pytest.raises(LeakageError):
        Fold(train=[1, 2, 3], test=[3, 4], purged=0, embargoed=0).assert_no_overlap()


def test_too_little_history_refuses_to_build_folds():
    with pytest.raises(ValueError):
        purged_walk_forward(50, 5, 20, min_train=60)


# --- metrics -------------------------------------------------------------
def test_drawdown_and_underwater_period():
    dd, under = drawdown_profile([0.1, -0.5, 0.1, 0.1, 0.1, 0.5])
    assert dd == pytest.approx(0.5, abs=0.02)
    assert under >= 4


def test_flat_series_has_no_drawdown():
    assert drawdown_profile([0.0] * 50) == (0.0, 0)


def test_performance_on_an_empty_series_is_safe():
    assert performance([]).n == 0


def test_sharpe_annualises():
    r = [0.001] * 252
    assert performance(r).cagr == pytest.approx(math.exp(252 * math.log(1.001)) - 1, rel=1e-6)


def test_expected_max_sharpe_rises_with_trial_count():
    assert expected_max_sharpe(500, 0.25) > expected_max_sharpe(20, 0.25) > 0


def test_deflated_sharpe_punishes_many_trials():
    """The correction for having tested many variants, which you will."""
    random.seed(2)
    r = [random.gauss(0.0004, 0.011) for _ in range(1000)]
    s = performance(r).sharpe
    assert probabilistic_sharpe(s, r) > deflated_sharpe(s, r, 50, 0.25)
    assert deflated_sharpe(s, r, 500, 0.25) <= deflated_sharpe(s, r, 5, 0.25)


def test_a_genuinely_strong_strategy_survives_deflation():
    random.seed(3)
    r = [random.gauss(0.0016, 0.006) for _ in range(1500)]
    assert deflated_sharpe(performance(r).sharpe, r, 20, 0.10) > 0.95


# --- costs ---------------------------------------------------------------
def test_slippage_rate_is_concave_but_total_cost_is_superlinear():
    """Square-root impact: 10x the size is ~3.2x the BPS RATE, so ~32x the
    total cost. Trading big is disproportionately expensive, and the cost floor
    computed from fees alone understates that."""
    adv = D("1000000")
    small, big = D("10000"), D("100000")
    bps_small = participation_slippage(small, adv) / small * 10_000
    bps_big = participation_slippage(big, adv) / big * 10_000
    assert float(bps_big) == pytest.approx(float(bps_small) * math.sqrt(10), rel=0.01)
    assert participation_slippage(big, adv) > participation_slippage(small, adv) * 10


def test_unknown_liquidity_is_assumed_bad():
    assert participation_slippage(D("10000"), D("0")) == D("10000") * D("0.02")


def test_market_impact_dominates_fees_at_the_liquidity_cap():
    """At 5% of ADV - the liquidity cap itself - impact dwarfs exchange fees."""
    fc = fill_cost(XKLS, D("50000"), D("1000000"))
    assert fc.slippage > fc.exchange_fees * 5
    assert fc.bps_of(D("50000")) > 200


def test_cross_border_adds_an_fx_leg():
    dom = fill_cost(XNAS, D("50000"), D("1000000"), cross_border=False)
    xb = fill_cost(XNAS, D("50000"), D("1000000"), cross_border=True)
    assert xb.total > dom.total and dom.fx_spread == 0


def test_dividend_withholding_accrues_over_the_holding_period():
    held = round_trip_cost(XNAS, D("50000"), D("1000000"), cross_border=True,
                           holding_years=2.0, dividend_yield=D("0.03"), holder_country="MY")
    flat = round_trip_cost(XNAS, D("50000"), D("1000000"), cross_border=True)
    assert held - flat == pytest.approx(D("50000") * D("0.03") * D("0.30") * 2)


def test_bursa_dividends_carry_no_withholding():
    a = round_trip_cost(XKLS, D("50000"), D("1000000"), holding_years=2.0,
                        dividend_yield=D("0.05"))
    b = round_trip_cost(XKLS, D("50000"), D("1000000"))
    assert a == b


# --- harness -------------------------------------------------------------
def make(seed=4, alpha=0.00035, cost=0.0004, n=1000):
    random.seed(seed)
    mkt = [random.gauss(0.0003, 0.010) for _ in range(n)]
    gross = [m * 0.9 + random.gauss(alpha, 0.006) for m in mkt]
    net = [g - cost for g in gross]
    regimes = [Regime.RISK_ON if m > 0.004 else Regime.RISK_OFF if m < -0.004
               else Regime.NEUTRAL for m in mkt]
    benches = {
        Benchmark.LOCAL_INDEX: mkt,
        Benchmark.EQUAL_WEIGHT_UNIVERSE: [m * 0.98 for m in mkt],
        Benchmark.BUY_AND_HOLD: [m * 1.02 for m in mkt],
    }
    return net, gross, benches, regimes


def test_all_three_benchmarks_are_evaluated():
    net, gross, benches, regimes = make()
    rep = run(net, gross, benches, regimes, n_trials=50)
    assert {b.name for b in rep.benchmarks} == set(Benchmark)


def test_failing_benchmarks_produces_the_index_tracker_verdict():
    net, gross, benches, regimes = make(alpha=0.0)
    rep = run(net, gross, benches, regimes, n_trials=50)
    assert not rep.passes_gate
    assert "index tracker plus the planner" in rep.verdict()


def test_a_strategy_can_beat_benchmarks_and_still_fail_on_deflation():
    net, gross, benches, regimes = make(alpha=0.0012, cost=0.0001)
    rep = run(net, gross, benches, regimes, n_trials=2000, sharpe_variance=0.9)
    if rep.beats_all_benchmarks:
        assert not rep.passes_gate
        assert "consistent with luck" in rep.verdict()


def test_costs_eating_the_edge_is_reported_explicitly():
    net, gross, benches, regimes = make(alpha=0.0004, cost=0.0012)
    rep = run(net, gross, benches, regimes)
    assert rep.gross_sharpe > rep.strategy.sharpe
    if rep.strategy.sharpe <= 0:
        assert any("consumed by costs" in n for n in rep.notes)


def test_results_are_broken_down_by_regime():
    net, gross, benches, regimes = make()
    rep = run(net, gross, benches, regimes)
    assert set(rep.by_regime) <= {r.value for r in Regime} and len(rep.by_regime) >= 2


def test_a_single_regime_edge_is_flagged_on_its_face():
    """A model that only works in one regime should say so on its face."""
    random.seed(9)
    n = 600
    mkt = [random.gauss(0.0003, 0.010) for _ in range(n)]
    regimes = [Regime.RISK_ON if m > 0 else Regime.RISK_OFF for m in mkt]
    net = [random.gauss(0.0020, 0.004) if r is Regime.RISK_ON
           else random.gauss(-0.0002, 0.004) for r in regimes]
    rep = run(net, net, {Benchmark.LOCAL_INDEX: mkt}, regimes)
    assert rep.by_regime["risk_on"].sharpe > 0.3
    assert rep.by_regime["risk_off"].sharpe <= 0.3
    assert "risk_on" in rep.regime_warning()


def test_an_all_weather_edge_raises_no_regime_warning():
    random.seed(10)
    n = 600
    mkt = [random.gauss(0.0003, 0.010) for _ in range(n)]
    regimes = [Regime.RISK_ON if m > 0 else Regime.RISK_OFF for m in mkt]
    net = [random.gauss(0.0015, 0.004) for _ in range(n)]
    rep = run(net, net, {Benchmark.LOCAL_INDEX: mkt}, regimes)
    assert rep.regime_warning() is None


def test_report_carries_the_folds_it_was_validated_on():
    net, gross, benches, regimes = make()
    rep = run(net, gross, benches, regimes, n_folds=4)
    assert len(rep.folds) >= 1 and all(max(f.train) < min(f.test) for f in rep.folds)
