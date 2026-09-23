"""P4: components before narrative. The pure-beta suite is the gate."""

import random
from datetime import date

import pytest

from engines.attribution.decompose import (
    Component,
    EstimationInputs,
    Verdict,
    decompose,
    estimate,
    long_horizon_decompose,
)
from engines.attribution.regression import corrado_rank_z, huber_fit, rank_p_value

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
    assert "significant at 5%" in m.reason
    # decompose weighs no candidate, and its reason must not read as a search.
    assert "no candidate cause has been weighed" in m.reason
    assert "matched" not in m.reason


def test_a_residual_past_the_hunt_line_but_under_5pct_is_not_called_significant():
    """Tenaga's 09-18 row (1.92 sigma) and Petronas Chemicals' 09-17 row (1.60)
    both read "significant idiosyncratic move". The hunt starts at 1.5 sigma,
    which is not the 1.96 the same object's own parametric test uses."""
    fit = synthetic_fit()
    drift, sigma = fit.coefficients[0], fit.residual_sigma
    m = decompose("X", WINDOW, 0.0, 0.0, {}, drift + 1.7 * sigma, 0.0, fit)
    assert m.verdict is Verdict.NO_IDENTIFIED_CATALYST and m.needs_cause_hunt()
    assert m.significance is not None and not m.significance.parametric_significant
    assert "1.70 sigma" in m.reason and "short of the 1.96 a 5% test needs" in m.reason
    assert "significant at 5%" not in m.reason


def test_components_sum_back_to_the_realised_return():
    """No hand-added term. THIS TEST USED TO ADD `fit.coefficients[0]` ITSELF.

    That made it pass while its own name was false: the printed components
    summed to the return minus the fitted intercept, and the compensation lived
    in the test rather than being reported to anyone reading a decomposition.
    `Component.DRIFT` carries the intercept now, so the sum is the sum.
    """
    fit = synthetic_fit()
    m = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, fit)
    total = sum(c.contribution for c in m.components if c.component is not Component.CURRENCY)
    assert total == pytest.approx(m.total_return_local, abs=1e-12)


def test_every_component_together_is_the_base_currency_return():
    """The currency leg included, nothing left over, in a non-trivial case:
    a fitted drift, a market leg, a sector leg and an FX leg all non-zero."""
    fit = synthetic_fit()
    m = decompose("US_X", WINDOW, 0.021, -0.004, {}, 0.033, -0.07, fit, base_currency="MYR")
    assert {c.component for c in m.components} == set(Component)
    assert sum(c.contribution for c in m.components) == pytest.approx(
        m.total_return_base, abs=1e-12
    )
    assert m.component(Component.DRIFT).contribution == pytest.approx(fit.coefficients[0])


def test_the_drift_is_reported_with_its_own_standard_error():
    """A fitted intercept over a few hundred sessions is mostly noise, and the
    note says so rather than printing a number that reads like a finding."""
    fit = synthetic_fit()
    assert fit.intercept_se is not None and fit.intercept_se > 0.0
    m = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, fit)
    assert "drift" in m.estimation_note
    assert "standard errors from zero" in m.estimation_note
    # this synthetic series has no true drift, so the fit must not claim one
    assert "not distinguishable from zero" in m.estimation_note


def test_a_real_drift_is_distinguishable_and_says_so():
    rng = random.Random(3)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(400)]
    y = [0.004 + 1.1 * a + 0.6 * b + rng.gauss(0, 0.004) for a, b in rows]
    fit = huber_fit(rows, y)
    assert fit.coefficients[0] == pytest.approx(0.004, abs=0.001)
    m = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, fit)
    assert "not distinguishable from zero" not in m.estimation_note
    assert m.component(Component.DRIFT).contribution > 0.003


def test_the_drift_never_earns_a_cause_hunt():
    """A drift is a property of the estimation window, not an event. Only the
    residual is tested for significance, so a large drift cannot by itself
    turn an ordinary session into something that wants a story."""
    rng = random.Random(8)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(400)]
    y = [0.006 + 1.1 * a + 0.6 * b + rng.gauss(0, 0.004) for a, b in rows]
    fit = huber_fit(rows, y)
    expected = fit.coefficients[0] + fit.coefficients[1] * -0.03 + fit.coefficients[2] * -0.01
    m = decompose("X", WINDOW, -0.03, -0.01, {}, expected, 0.0, fit)
    assert abs(m.component(Component.DRIFT).contribution) > 0.005
    assert m.verdict is Verdict.NOT_SIGNIFICANT
    assert m.needs_cause_hunt() is False


def test_shares_never_exceed_one_hundred_percent_when_components_offset():
    """docs/03 section 2.5: absolute values in the denominator."""
    fit = synthetic_fit()
    m = decompose("X", WINDOW, 0.04, 0.01, {}, -0.03, 0.0, fit)  # market up, stock down
    assert all(0.0 <= c.share_of_total <= 1.0 for c in m.components)
    assert sum(c.share_of_total for c in m.components) == pytest.approx(1.0, abs=1e-9)


def test_currency_is_a_first_class_component():
    fit = synthetic_fit()
    m = decompose("US_X", WINDOW, 0.0, 0.0, {}, 0.08, -0.10, fit, base_currency="MYR")
    assert m.total_return_local == pytest.approx(0.08)
    assert m.total_return_base == pytest.approx(-0.028)
    assert m.component(Component.CURRENCY).contribution < 0


def test_an_extreme_residual_is_flagged_by_both_tests_and_they_agree():
    """The rank test used to be Corrado's z against 1.96, which one event day
    cannot reach, so `agree` was False for every significant move ever printed."""
    fit = synthetic_fit()
    m = decompose("X", WINDOW, 0.0, 0.0, {}, 0.05, 0.0, fit)
    s = m.significance
    assert s.parametric_significant and s.rank_significant and s.agree
    assert s.rank_p == pytest.approx(2 / (fit.n + 1))
    assert 0.0 < abs(s.rank_z) < 1.74, "the descriptive statistic is still reported, still bounded"
    assert "disagree" not in m.estimation_note


def test_an_ordinary_residual_is_flagged_by_neither_test():
    fit = synthetic_fit()
    m = decompose("X", WINDOW, 0.0, 0.0, {}, fit.coefficients[0], 0.0, fit)  # an AR of zero
    s = m.significance
    assert not s.parametric_significant and not s.rank_significant and s.agree
    assert s.rank_p > 0.5


def test_disagreement_between_the_tests_is_logged_in_the_note():
    """docs/03 section 2.3: disagreement is itself worth logging. Light-tailed
    residuals make the case: nothing in the window reaches 1.96 sigma, so a
    residual that sits above all but one of them is rank-significant (p = 4/251)
    while still under the parametric cut."""
    rng = random.Random(4)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [1.1 * a + 0.6 * b + rng.uniform(-0.01, 0.01) for a, b in rows]
    fit = huber_fit(rows, y)
    m = decompose("X", WINDOW, 0.0, 0.0, {}, fit.coefficients[0] + 0.0105, 0.0, fit)
    s = m.significance
    assert s.rank_significant and not s.parametric_significant and not s.agree
    assert "the parametric and rank tests disagree" in m.estimation_note
    assert f"rank p {s.rank_p:.3f}" in m.estimation_note


def test_rank_p_value_is_exact_and_reaches_significance_where_the_z_cannot():
    resid = [0.001 * (i % 7 - 3) for i in range(200)]
    assert rank_p_value(0.5, resid) == pytest.approx(2 / 201)
    assert rank_p_value(-0.5, resid) == pytest.approx(2 / 201)
    assert rank_p_value(0.0, resid) == pytest.approx(1.0, abs=0.02)  # the centre; ties mid-rank
    assert rank_p_value(0.5, []) == 1.0
    # p = 2 / (n + 1): exactly 0.05 with 39 estimation residuals, under it from 40 on
    assert rank_p_value(0.5, [0.0] * 39) == pytest.approx(0.05)
    assert rank_p_value(0.5, [0.0] * 40) < 0.05
    assert rank_p_value(0.5, [0.0] * 120) == pytest.approx(2 / 121)


def test_corrado_flags_an_extreme_residual():
    resid = [0.001 * (i % 7 - 3) for i in range(200)]
    assert corrado_rank_z(0.5, resid) > 1.5


def test_corrado_z_for_one_day_is_bounded_under_the_parametric_cut():
    """Why the z could never be the single-day decision: sqrt(3) is its ceiling."""
    assert 1.5 < corrado_rank_z(0.5, [0.001 * (i % 7 - 3) for i in range(2000)]) < 1.7321


# --- the currency leg and the verdict -----------------------------------------
def test_a_currency_move_on_a_flat_market_is_never_market_driven():
    """NVDA +3% in USD on a flat market, the ringgit leg -15%: the old gate had
    the currency in its denominator, so 16% "company-specific" was read as
    "the market and sector moved" on a day the market had not."""
    fit = synthetic_fit()
    m = decompose("US_X", WINDOW, 0.0, 0.0, {}, 0.03, -0.15, fit, base_currency="MYR")
    assert m.verdict is Verdict.NO_IDENTIFIED_CATALYST
    assert "market and sector moved" not in m.reason
    assert "in MYR the move is 84% currency" in m.reason
    assert m.needs_cause_hunt()
    # reporting keeps docs/03 section 2.5: every leg in the denominator
    assert m.unexplained_share < 0.2
    assert m.component(Component.IDIOSYNCRATIC).share_of_total == pytest.approx(m.unexplained_share)


@pytest.mark.parametrize("fx", [0.0, -0.15])
def test_the_market_driven_gate_reads_the_local_legs_only(fx):
    """A move that IS the market's stays market_driven whatever the currency did;
    only the reported share moves, because the FX leg joins the gross there."""
    fit = synthetic_fit()
    realised = fit.coefficients[0] + fit.coefficients[1] * -0.06 + 0.009  # 1.8 sigma left over
    m = decompose("X", WINDOW, -0.06, 0.0, {}, realised, fx, fit, base_currency="MYR")
    assert m.verdict is Verdict.MARKET_DRIVEN
    assert "of the local-currency move is company-specific" in m.reason
    assert ("% currency" in m.reason) == (fx != 0.0)


def test_a_quiet_currency_leg_is_not_mentioned():
    m = decompose("X", WINDOW, -0.002, 0.001, {}, 0.072, 0.0, synthetic_fit())
    assert "% currency" not in m.reason


# --- the estimation note --------------------------------------------------------
def test_the_note_carries_r_squared_and_warns_when_the_beta_is_not_identified():
    """PCHEM printed "Beta -0.12" from a fit with r-squared 0.01 and the note said
    nothing; the beta was a coin toss and the market leg built on it no evidence."""
    fit = synthetic_fit()
    good = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, fit)
    assert fit.r_squared > 0.5 and f"R2 {fit.r_squared:.2f}" in good.estimation_note
    assert "weakly identified" not in good.estimation_note
    rng = random.Random(21)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    noise = huber_fit(rows, [rng.gauss(0, 0.02) for _ in rows])  # nothing to do with the factors
    assert noise.r_squared < 0.10
    m = decompose("X", WINDOW, -0.03, -0.01, {}, -0.045, 0.0, noise)
    assert (
        "R2 0.00 (market beta weakly identified; read the unexplained share, not the market leg)"
        in m.estimation_note
    )


# --- long horizon --------------------------------------------------------
def test_return_decomposes_into_four_multiplicative_parts():
    lh = long_horizon_decompose(0.42, 0.59, 12.0, 12.7, 0.19, 4.20, 4.55, 5)
    rebuilt = (1 + lh.eps_growth) * (1 + lh.multiple_change) * (1 + lh.shareholder_yield) * (
        1 + lh.fx
    ) - 1
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


# --- found by stress testing (stress/run.py) --------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_return_never_reaches_a_verdict(bad):
    """A NaN return produced verdict=no_identified_catalyst with unexplained=nan,
    which renders to a user as a confident finding with 'nan% unexplained'. That
    is the failure this design exists to prevent, arriving through the data."""
    m = decompose("X", WINDOW, 0.0, 0.0, {}, bad, 0.0, synthetic_fit())
    assert m.verdict is Verdict.ATTRIBUTION_UNAVAILABLE
    assert "non-finite input" in m.reason
    assert m.unexplained_share == 1.0


def test_a_non_finite_factor_return_is_caught_too():
    m = decompose("X", WINDOW, float("nan"), 0.0, {}, -0.05, 0.0, synthetic_fit())
    assert m.verdict is Verdict.ATTRIBUTION_UNAVAILABLE
    assert "event_market" in m.reason


def test_a_non_finite_style_return_is_caught():
    m = decompose("X", WINDOW, 0.0, 0.0, {"value": float("inf")}, -0.05, 0.0, synthetic_fit())
    assert m.verdict is Verdict.ATTRIBUTION_UNAVAILABLE
    assert "style:value" in m.reason
