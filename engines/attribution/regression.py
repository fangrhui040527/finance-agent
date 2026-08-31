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


def _wls(X: list[list[float]], y: list[float], w: list[float]) -> list[float]:
    k = len(X[0])
    ata = [
        [sum(w[i] * X[i][p] * X[i][q] for i in range(len(y))) for q in range(k)] for p in range(k)
    ]
    atb = [sum(w[i] * X[i][p] * y[i] for i in range(len(y))) for p in range(k)]
    return _solve(ata, atb)


@dataclass(frozen=True)
class Fit:
    coefficients: list[float]  # [alpha, beta_1 ... beta_k]
    residuals: list[float]
    residual_sigma: float
    r_squared: float
    n: int

    def predict(self, row: list[float]) -> float:
        return self.coefficients[0] + sum(c * v for c, v in zip(self.coefficients[1:], row))


def huber_fit(
    factors: list[list[float]], y: list[float], iterations: int = 6, c: float = 1.345
) -> Fit:
    """Design matrix gets an intercept column prepended automatically."""
    n = len(y)
    if n != len(factors):
        raise ValueError("factor rows and observations must align")
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
    resid = [y[i] - sum(coef[p] * X[i][p] for p in range(len(coef))) for i in range(n)]
    dof = max(n - len(coef), 1)
    sigma = math.sqrt(sum(r * r for r in resid) / dof)
    ybar = sum(y) / n
    sst = sum((v - ybar) ** 2 for v in y)
    r2 = 1.0 - (sum(r * r for r in resid) / sst) if sst > 1e-15 else 0.0
    return Fit(coef, resid, sigma, r2, n)


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
