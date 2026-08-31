"""P5: the analysis-side quant ports - event-study stats, negative controls,
multiple testing, CPCV, impact, Brinson. All stdlib, all offline."""

from __future__ import annotations

import math
import random

import pytest

from engines.attribution.brinson import brinson_fachler, carino_link
from engines.attribution.control import shuffle_control
from engines.attribution.decompose import EstimationInputs
from engines.attribution.regression import (
    bmp_z,
    cowan_sign_z,
    huber_fit,
    patell_z,
)
from engines.backtest.metrics import (
    benjamini_hochberg,
    probability_of_backtest_overfitting,
)
from engines.backtest.splitter import (
    combinatorial_purged_splits,
    detect_boundary_leakage,
    purged_walk_forward,
)

RNG = random.Random(7)


def _market(n=300):
    return [RNG.gauss(0.0003, 0.01) for _ in range(n)]


# --- shrinkage ------------------------------------------------------------------


def test_shrinkage_pulls_betas_toward_the_prior_and_is_disclosed():
    mkt = _market()
    y = [1.8 * m + RNG.gauss(0, 0.004) for m in mkt]
    rows = [[m] for m in mkt]
    raw = huber_fit(rows, y)
    shrunk = huber_fit(rows, y, shrink_to=[1.0], shrink_lambda=0.5)
    assert abs(shrunk.coefficients[1] - 1.0) < abs(raw.coefficients[1] - 1.0)
    assert shrunk.shrinkage == 0.5 and raw.shrinkage == 0.0
    assert shrunk.dof == raw.dof > 0


def test_full_shrinkage_is_the_prior_verbatim():
    mkt = _market(120)
    y = [2.5 * m for m in mkt]
    fit = huber_fit([[m] for m in mkt], y, shrink_to=[1.0], shrink_lambda=1.0)
    assert math.isclose(fit.coefficients[1], 1.0)


def test_a_bad_lambda_or_prior_shape_is_refused():
    with pytest.raises(ValueError):
        huber_fit([[0.1]] * 40, [0.1] * 40, shrink_to=[1.0], shrink_lambda=1.5)
    with pytest.raises(ValueError):
        huber_fit([[0.1]] * 40, [0.1] * 40, shrink_to=[1.0, 0.0], shrink_lambda=0.5)


# --- event-study statistics -----------------------------------------------------


def test_patell_standardises_by_estimation_sigma():
    resid = [RNG.gauss(0, 0.01) for _ in range(250)]
    z = patell_z(0.03, resid)
    assert 2.0 < z < 4.5  # a 3% residual against ~1% sigma
    assert patell_z(0.0, resid) == pytest.approx(0.0, abs=1e-9)


def test_bmp_absorbs_event_induced_variance():
    resid = [RNG.gauss(0, 0.01) for _ in range(250)]
    quiet = [0.02, 0.021, 0.019, 0.02]
    loud = [0.02, -0.05, 0.09, -0.04]  # same-ish mean, event-window variance blown up
    assert abs(bmp_z(quiet, resid)) > abs(bmp_z(loud, resid))


def test_cowan_uses_the_estimation_baseline_not_half():
    drifty = [0.005] * 200 + [-0.001] * 50  # 80% positive days is NORMAL here
    z = cowan_sign_z([0.01, 0.02, 0.01, 0.03], drifty)
    assert abs(z) < 2.0  # four up-days are not news for this name


# --- negative controls ----------------------------------------------------------


def _inputs(beta=1.5, n=300, noise=0.004):
    mkt = _market(n)
    sec = [RNG.gauss(0.0002, 0.008) for _ in range(n)]
    y = [beta * m + 0.2 * s + RNG.gauss(0, noise) for m, s in zip(mkt, sec)]
    return EstimationInputs(instrument=y, market=mkt, sector=sec)


def test_a_real_relationship_is_confirmed():
    report = shuffle_control(_inputs())
    assert report is not None
    assert report.category == "confirmed"
    assert report.real_r2 > max(report.shuffled_r2)
    assert report.t_stat >= report.threshold


def test_pure_noise_is_named_noise():
    n = 300
    inputs = EstimationInputs(
        instrument=[RNG.gauss(0, 0.01) for _ in range(n)],
        market=_market(n),
        sector=[RNG.gauss(0, 0.008) for _ in range(n)],
    )
    report = shuffle_control(inputs)
    assert report is not None
    assert report.category in ("noise", "train_only")  # never confirmed


def test_too_short_a_window_returns_none():
    assert shuffle_control(_inputs(n=10)) is None


def test_the_control_is_deterministic():
    inputs = _inputs()
    a = shuffle_control(inputs)
    b = shuffle_control(inputs)
    # same rng_seed, same shuffles - a control that flickers is not a control
    assert a.shuffled_r2 == b.shuffled_r2


# --- multiple testing -----------------------------------------------------------


def test_bh_keeps_strong_signals_and_drops_the_marginal_crowd():
    pvals = [0.001, 0.002, 0.04, 0.5, 0.9, 0.049, 0.03]
    keep = benjamini_hochberg(pvals, fdr=0.05)
    assert keep[0] and keep[1]
    assert not keep[3] and not keep[4]
    assert len(keep) == len(pvals)


def test_bh_on_nothing_is_nothing():
    assert benjamini_hochberg([]) == []


def test_pbo_is_high_for_pure_luck_and_lower_for_a_real_edge():
    rng = random.Random(3)
    lucky = [[rng.gauss(0, 0.01) for _ in range(96)] for _ in range(8)]
    pbo_luck = probability_of_backtest_overfitting(lucky, n_splits=8)
    skilled = [[rng.gauss(0.0001, 0.01) for _ in range(96)] for _ in range(7)]
    skilled.append([0.004 + rng.gauss(0, 0.001) for _ in range(96)])  # one real edge
    pbo_skill = probability_of_backtest_overfitting(skilled, n_splits=8)
    assert 0.0 <= pbo_skill < pbo_luck <= 1.0
    assert pbo_luck > 0.3


# --- CPCV and leakage -----------------------------------------------------------


def test_cpcv_produces_every_combination_and_never_overlaps():
    folds = combinatorial_purged_splits(120, n_blocks=6, test_blocks=2, embargo=0)
    assert len(folds) == 15  # C(6,2)
    for f in folds:
        assert not (set(f.train) & set(f.test))


def test_cpcv_embargo_removes_boundary_rows():
    folds = combinatorial_purged_splits(120, n_blocks=6, test_blocks=2, embargo=3)
    report = detect_boundary_leakage(folds, horizon=3)
    assert report.clean, report.contaminated[:5]


def test_the_leakage_audit_catches_a_bad_splitter():
    from engines.backtest.splitter import Fold

    bad = [Fold(train=[0, 1, 2, 49, 50], test=[51, 52, 53], purged=0, embargoed=0)]
    report = detect_boundary_leakage(bad, horizon=2)
    assert not report.clean
    assert (0, 50) in report.contaminated or (0, 49) in report.contaminated


def test_the_existing_walk_forward_passes_its_own_audit():
    folds = purged_walk_forward(200, n_folds=4, embargo=5)
    assert detect_boundary_leakage(folds, horizon=5).clean


# --- impact ---------------------------------------------------------------------


def test_sqrt_impact_grows_with_the_square_root():
    from engines.backtest.costs import sqrt_impact

    one = sqrt_impact(10_000, 1_000_000, 0.02)
    four = sqrt_impact(40_000, 1_000_000, 0.02)
    assert four == pytest.approx(2 * one)


def test_the_model_selector_names_its_regimes():
    from engines.backtest.costs import impact_model_for

    assert impact_model_for(0.001) == "fixed"
    assert impact_model_for(0.02) == "linear"
    assert impact_model_for(0.10) == "sqrt"
    with pytest.raises(ValueError):
        impact_model_for(-0.1)


# --- Brinson --------------------------------------------------------------------


def test_single_period_effects_sum_to_the_active_return():
    p = brinson_fachler(
        {"banks": 0.6, "tech": 0.4},
        {"banks": 0.02, "tech": -0.01},
        {"banks": 0.5, "tech": 0.5},
        {"banks": 0.015, "tech": 0.005},
    )
    total = sum(e.total for e in p.effects)
    assert total == pytest.approx(p.active_return, abs=1e-12)


def test_carino_linked_effects_sum_to_the_compounded_active_return():
    periods = [
        brinson_fachler(
            {"banks": 0.6, "tech": 0.4},
            {"banks": 0.02 + 0.01 * i, "tech": -0.01},
            {"banks": 0.5, "tech": 0.5},
            {"banks": 0.015, "tech": 0.005 * i},
        )
        for i in range(4)
    ]
    linked = carino_link(periods)
    rp = math.prod(1 + p.portfolio_return for p in periods) - 1
    rb = math.prod(1 + p.benchmark_return for p in periods) - 1
    total = sum(e.total for e in linked.values())
    assert total == pytest.approx(rp - rb, abs=1e-9)
