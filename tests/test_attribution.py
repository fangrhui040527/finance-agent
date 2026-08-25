"""P4: components before narrative. The pure-beta suite is the gate."""
import random
from datetime import date

import pytest

from engines.attribution.decompose import (
    Component, EstimationInputs, MIN_OBSERVATIONS, Verdict, decompose, estimate,
    long_horizon_decompose,
)
from engines.attribution.regression import corrado_rank_z, huber_fit

WINDOW = (date(2026, 8, 1), date(2026, 8, 12))


def synthetic_fit(beta_mkt=1.1, beta_sec=0.6, n=250, seed=11):
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(n)]
    y = [beta_mkt * a + beta_sec * b + rng.gauss(0, 0.005) for a, b in rows]
    return huber_fit(rows, y)


def test_huber_resists_outliers_that_would_drag_ols():
    rng = random.Random(5)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [1.2 * a + 0.5 * b + rng.gauss(0, 0.004) for a, b in rows]
    for i in (10, 90, 200):
        y[i] += 0.30
    fit = huber_fit(rows, y)
    assert fit.coefficients[1] == pytest.approx(1.2, abs=0.08)
    assert fit.coefficients[2] == pytest.approx(0.5, abs=0.10)


def test_too_few_observations_returns_none_not_a_guess():
    short = EstimationInputs([0.0] * 50, [0.0] * 50, [0.0] * 50)
    assert estimate(short) is None


def test_attribution_unavailable_when_history_is_short():
    m = decompose("NEW", WINDOW, -0.05, -0.01, {}, -0.06, 0.0, None)
    assert m.verdict is Verdict.ATTRIBUTION_UNAVAILABLE
    assert m.unexplained_share == 1.0


# --- the pure-beta suite (docs/03 section 8, the gate) -------------------
@pytest.mark.parametrize("seed", range(20))
def test_market_driven_moves_never_get_a_company_story(seed):
    """A stock that fell because the index fell must return market_driven or
    not_significant, and must never trigger a cause hunt."""
    rng = random.Random(seed)
    fit = synthetic_fit(seed=seed)
    mkt = rng.uniform(-0.09, -0.04)
    sec = rng.uniform(-0.02, -0.005)
    beta_mkt, beta_sec = fit.coefficients[1], fit.coefficients[2]
    realised = beta_mkt * mkt + beta_sec * sec + rng.gauss(0, fit.residual_sigma * 0.6)
    m = decompose("X", WINDOW, mkt, sec, {}, realised, 0.0, fit)
    assert m.verdict in (Verdict.MARKET_DRIVEN, Verdict.NOT_SIGNIFICANT)
    assert m.needs_cause_hunt() is False


def test_a_genuine_idiosyncratic_move_does_trigger_a_hunt():
    fit = synthetic_fit()
    m = decompose("X", WINDOW, -0.002, 0.001, {}, 0.072, 0.0, fit)
    assert m.verdict is Verdict.NO_IDENTIFIED_CATALYST
    assert m.needs_cause_hunt() is True
    assert m.unexplained_share > 0.8


def test_components_sum_back_to_the_realised_return():
    fit = synthetic_fit()
    m = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, fit)
    total = sum(c.contribution for c in m.components if c.component is not Component.CURRENCY)
    assert total + fit.coefficients[0] == pytest.approx(m.total_return_local, abs=1e-9)


def test_shares_never_exceed_one_hundred_percent_when_components_offset():
    """docs/03 section 2.5: absolute values in the denominator."""
    fit = synthetic_fit()
    m = decompose("X", WINDOW, 0.04, 0.01, {}, -0.03, 0.0, fit)   # market up, stock down
    assert all(0.0 <= c.share_of_total <= 1.0 for c in m.components)
    assert sum(c.share_of_total for c in m.components) == pytest.approx(1.0, abs=1e-9)


def test_currency_is_a_first_class_component():
    fit = synthetic_fit()
    m = decompose("US_X", WINDOW, 0.0, 0.0, {}, 0.08, -0.10, fit, base_currency="MYR")
    assert m.total_return_local == pytest.approx(0.08)
    assert m.total_return_base == pytest.approx(-0.028)
    assert m.component(Component.CURRENCY).contribution < 0


def test_rank_test_is_reported_alongside_the_parametric_one():
    fit = synthetic_fit()
    m = decompose("X", WINDOW, 0.0, 0.0, {}, 0.05, 0.0, fit)
    assert m.significance.rank_z != 0.0
    assert isinstance(m.significance.agree, bool)


def test_corrado_flags_an_extreme_residual():
    resid = [0.001 * (i % 7 - 3) for i in range(200)]
    assert corrado_rank_z(0.5, resid) > 1.5


# --- long horizon --------------------------------------------------------
def test_return_decomposes_into_four_multiplicative_parts():
    lh = long_horizon_decompose(0.42, 0.59, 12.0, 12.7, 0.19, 4.20, 4.55, 5)
    rebuilt = (1 + lh.eps_growth) * (1 + lh.multiple_change) * (1 + lh.shareholder_yield) * (1 + lh.fx) - 1
    assert lh.total_return == pytest.approx(rebuilt)


def test_earnings_driven_return_reads_as_durable():
    lh = long_horizon_decompose(0.40, 0.90, 12.0, 12.2, 0.05, 4.0, 4.0, 5)
    assert "compounded" in lh.reading


def test_rerating_driven_return_is_flagged_as_borrowed_from_the_future():
    lh = long_horizon_decompose(0.40, 0.42, 8.0, 20.0, 0.02, 4.0, 4.0, 5)
    assert "reverse" in lh.reading


def test_currency_driven_return_says_nothing_about_the_company():
    lh = long_horizon_decompose(0.40, 0.41, 12.0, 12.1, 0.01, 3.0, 4.5, 5)
    assert "nothing about the company" in lh.reading


def test_zero_start_values_are_rejected_rather_than_producing_infinity():
    with pytest.raises(ValueError):
        long_horizon_decompose(0.0, 0.5, 12.0, 13.0, 0.1, 4.0, 4.1, 5)
