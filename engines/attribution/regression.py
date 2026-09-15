"""Robust multiple regression, pure Python.

docs/03 section 2.2 rule 3: ordinary least squares is dominated by outliers,
which in equity returns are exactly the days you care about. Huber-weighted
iteratively reweighted least squares instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ValueError("singular design matrix: factors are collinear")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _normal(X: list[list[float]], w: list[float]) -> list[list[float]]:
    """X'WX, the weighted normal matrix."""
    k = len(X[0])
    return [
        [sum(w[i] * X[i][p] * X[i][q] for i in range(len(w))) for q in range(k)] for p in range(k)
    ]


def _wls(X: list[list[float]], y: list[float], w: list[float]) -> list[float]:
    k = len(X[0])
    atb = [sum(w[i] * X[i][p] * y[i] for i in range(len(y))) for p in range(k)]
    return _solve(_normal(X, w), atb)


@dataclass(frozen=True)
class Fit:
    coefficients: list[float]  # [alpha, beta_1 ... beta_k]
    residuals: list[float]
    residual_sigma: float
    r_squared: float
    n: int
    #: How hard the betas were pulled toward the prior (0 = pure Huber). Carried
    #: so the output can SAY it - a shrunk beta presented as estimated is a lie.
    shrinkage: float = 0.0
    dof: int = 0
    #: Standard error of `coefficients[0]`, the intercept. The decomposition
    #: reports the intercept as a component (Component.DRIFT), and a fitted
    #: drift over 120 sessions is a NOISY estimate - a tenth of a percent a
    #: session is roughly one standard error on daily equity returns. Carrying
    #: the error lets the output say which it is instead of printing a number
    #: that reads like a finding. None when it could not be computed.
    #:
    #: Only the INTERCEPT's error is offered. The slopes are optionally shrunk
    #: toward a prior after fitting, so their fitted standard errors would no
    #: longer describe the coefficients actually reported - publishing them
    #: would be the same class of lie the `shrinkage` field exists to prevent.
    intercept_se: float | None = None

    def predict(self, row: list[float]) -> float:
        return self.coefficients[0] + sum(c * v for c, v in zip(self.coefficients[1:], row))


def huber_fit(
    factors: list[list[float]],
    y: list[float],
    iterations: int = 6,
    c: float = 1.345,
    shrink_to: list[float] | None = None,
    shrink_lambda: float = 0.0,
) -> Fit:
    """Design matrix gets an intercept column prepended automatically.

    `shrink_to`/`shrink_lambda`: an optional ridge-style pull of the SLOPES
    toward a prior (e.g. market beta 1.0, sector 0.0). Robust estimators still
    carry sampling noise on short windows, and the literature's answer is to
    shrink toward a structural prior rather than trust either alone. Lambda is
    a fraction in [0, 1]: 0 is pure Huber, 1 is the prior verbatim. The blend
    is recorded on the Fit so downstream output can disclose it.
    """
    n = len(y)
    if n != len(factors):
        raise ValueError("factor rows and observations must align")
    if not 0.0 <= shrink_lambda <= 1.0:
        raise ValueError(f"shrink_lambda must be in [0, 1], got {shrink_lambda}")
    X = [[1.0] + row for row in factors]
    w = [1.0] * n
    coef = _wls(X, y, w)
    for _ in range(iterations):
        resid = [y[i] - sum(coef[p] * X[i][p] for p in range(len(coef))) for i in range(n)]
        s = _mad_sigma(resid)
        if s < 1e-12:
            break
        w = [min(1.0, c * s / abs(r)) if abs(r) > 1e-12 else 1.0 for r in resid]
        coef = _wls(X, y, w)

    if shrink_to is not None and shrink_lambda > 0.0:
        if len(shrink_to) != len(coef) - 1:
            raise ValueError(f"shrink_to has {len(shrink_to)} priors for {len(coef) - 1} slopes")
        coef = [coef[0]] + [
            (1.0 - shrink_lambda) * b + shrink_lambda * prior
            for b, prior in zip(coef[1:], shrink_to)
        ]

    resid = [y[i] - sum(coef[p] * X[i][p] for p in range(len(coef))) for i in range(n)]
    dof = max(n - len(coef), 1)
    sigma = math.sqrt(sum(r * r for r in resid) / dof)
    ybar = sum(y) / n
    sst = sum((v - ybar) ** 2 for v in y)
    r2 = 1.0 - (sum(r * r for r in resid) / sst) if sst > 1e-15 else 0.0

    # sigma * sqrt((X'WX)^-1[0][0]) at the final Huber weights. For a robust
    # fit the exact variance is a sandwich estimator, so this is an
    # APPROXIMATION - stated here rather than in the field name, because the
    # one question asked of it is "is this drift distinguishable from zero"
    # and it is accurate enough to answer that. A singular matrix means the
    # error is unknown, which is reported as None and never as zero.
    try:
        e0 = [1.0] + [0.0] * (len(coef) - 1)
        v00 = _solve(_normal(X, w), e0)[0]
    except ValueError:  # pragma: no cover - _wls above would have raised first
        v00 = -1.0
    return Fit(
        coef,
        resid,
        sigma,
        r2,
        n,
        shrinkage=shrink_lambda if shrink_to else 0.0,
        dof=dof,
        intercept_se=sigma * math.sqrt(v00) if v00 > 0.0 else None,
    )


def _mad_sigma(resid: list[float]) -> float:
    """Median absolute deviation, scaled to a normal-consistent sigma."""
    s = sorted(abs(r) for r in resid)
    med = s[len(s) // 2] if s else 0.0
    return med / 0.6745 if med else 0.0


def corrado_rank_z(event_resid: float, estimation_resid: list[float]) -> float:
    """Non-parametric rank test.

    docs/03 section 2.3: equity residuals are fat-tailed and skewed; a t-test on
    20 observations will over-reject. Disagreement between the two tests is
    itself worth logging.
    """
    pool = list(estimation_resid) + [event_resid]
    n = len(pool)
    order = sorted(range(n), key=lambda i: pool[i])
    ranks = [0.0] * n
    for pos, idx in enumerate(order):
        ranks[idx] = pos + 1.0
    mean_rank = (n + 1) / 2.0
    k = ranks[-1] - mean_rank
    sd = math.sqrt(sum((r - mean_rank) ** 2 for r in ranks) / n)
    return k / sd if sd > 1e-12 else 0.0


def patell_z(event_resid: float, estimation_resid: list[float]) -> float:
    """Patell's standardised abnormal return test (single event day).

    The residual is standardised by the ESTIMATION-period sigma, so one loud
    event day cannot inflate its own denominator - the flaw the plain t on
    event-window residuals has.
    """
    n = len(estimation_resid)
    if n < 3:
        return 0.0
    s2 = sum(r * r for r in estimation_resid) / max(n - 2, 1)
    if s2 < 1e-18:
        return 0.0
    return event_resid / math.sqrt(s2)


def bmp_z(event_resids: list[float], estimation_resid: list[float]) -> float:
    """Boehmer-Musumeci-Poulsen: Patell, made robust to event-induced variance.

    Standardise each event-day residual by the estimation sigma FIRST, then
    test the cross-section of standardised values. Volatility that arrives
    WITH the event (it usually does) inflates plain Patell; BMP absorbs it.
    """
    n = len(event_resids)
    if n == 0:
        return 0.0
    sar = [patell_z(r, estimation_resid) for r in event_resids]
    mean = sum(sar) / n
    if n == 1:
        return sar[0]
    var = sum((x - mean) ** 2 for x in sar) / (n - 1)
    if var < 1e-18:
        return 0.0
    return mean / math.sqrt(var / n)


def cowan_sign_z(event_resids: list[float], estimation_resid: list[float]) -> float:
    """Cowan's generalised sign test: are positives more common than they were?

    The baseline positive rate comes from the ESTIMATION window rather than
    an assumed 0.5, so a security that drifts up on ordinary days does not
    read every up-day as news.
    """
    n = len(event_resids)
    m = len(estimation_resid)
    if n == 0 or m == 0:
        return 0.0
    p_hat = sum(1 for r in estimation_resid if r > 0) / m
    w = sum(1 for r in event_resids if r > 0)
    denom = math.sqrt(n * p_hat * (1.0 - p_hat))
    if denom < 1e-12:
        return 0.0
    return (w - n * p_hat) / denom
