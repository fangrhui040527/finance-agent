"""Performance metrics, including the ones that correct for having tried a lot.

docs/05 section 9. The deflated Sharpe ratio (Bailey & Lopez de Prado) corrects
for selection bias under multiple testing, sample length and non-normality. The
probability of picking an overfit strategy grows rapidly with the number of
trials, and this is the correction for having run them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TRADING_DAYS = 252
EULER = 0.5772156649015329


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _skew(xs: list[float]) -> float:
    n, s = len(xs), _std(xs)
    if n < 3 or s == 0:
        return 0.0
    m = _mean(xs)
    return sum(((x - m) / s) ** 3 for x in xs) / n


def _kurtosis(xs: list[float]) -> float:
    """Non-excess (normal = 3.0)."""
    n, s = len(xs), _std(xs)
    if n < 4 or s == 0:
        return 3.0
    m = _mean(xs)
    return sum(((x - m) / s) ** 4 for x in xs) / n


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam's rational approximation. Adequate for the tail values used here."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q, r = p - 0.5, (p - 0.5) ** 2
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


@dataclass(frozen=True)
class Performance:
    n: int
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    longest_underwater_days: int
    hit_rate: float
    profit_factor: float
    skew: float
    kurtosis: float

    def summary(self) -> str:
        return (
            f"CAGR {self.cagr:+.2%}  vol {self.volatility:.2%}  Sharpe {self.sharpe:.2f}  "
            f"maxDD {self.max_drawdown:.2%}  underwater {self.longest_underwater_days}d"
        )


def drawdown_profile(returns: list[float]) -> tuple[float, int]:
    """Max drawdown and the longest underwater stretch.

    docs/05 section 5: report the underwater period alongside max drawdown. It is
    the number you actually have to live through.
    """
    peak, equity = 1.0, 1.0
    max_dd, longest, current = 0.0, 0, 0
    for r in returns:
        equity *= 1.0 + r
        if equity >= peak:
            peak, current = equity, 0
        else:
            current += 1
            longest = max(longest, current)
            max_dd = max(max_dd, 1.0 - equity / peak)
    return max_dd, longest


def performance(returns: list[float], periods_per_year: int = TRADING_DAYS) -> Performance:
    n = len(returns)
    if n == 0:
        return Performance(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3.0)
    total = 1.0
    for r in returns:
        total *= 1.0 + r
    years = n / periods_per_year
    cagr = total ** (1 / years) - 1.0 if years > 0 and total > 0 else -1.0
    vol = _std(returns) * math.sqrt(periods_per_year)
    mu = _mean(returns) * periods_per_year
    sharpe = mu / vol if vol > 1e-12 else 0.0
    downside = [r for r in returns if r < 0]
    dstd = _std(downside) * math.sqrt(periods_per_year) if len(downside) > 1 else 0.0
    sortino = mu / dstd if dstd > 1e-12 else 0.0
    max_dd, underwater = drawdown_profile(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    hit = len(wins) / n
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    return Performance(
        n,
        cagr,
        vol,
        sharpe,
        sortino,
        max_dd,
        underwater,
        hit,
        pf,
        _skew(returns),
        _kurtosis(returns),
    )


def probabilistic_sharpe(
    sharpe: float, returns: list[float], benchmark_sharpe: float = 0.0
) -> float:
    """P(true Sharpe > benchmark), correcting for skew and fat tails."""
    n = len(returns)
    if n < 4:
        return 0.0
    g3, g4 = _skew(returns), _kurtosis(returns)
    denom = math.sqrt(max(1e-12, 1 - g3 * sharpe + ((g4 - 1) / 4) * sharpe**2))
    return _norm_cdf(((sharpe - benchmark_sharpe) * math.sqrt(n - 1)) / denom)


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """The Sharpe you would expect from the BEST of n_trials random strategies."""
    if n_trials < 2:
        return 0.0
    sd = math.sqrt(max(sharpe_variance, 1e-12))
    a = _norm_ppf(1 - 1.0 / n_trials)
    b = _norm_ppf(1 - 1.0 / (n_trials * math.e))
    return sd * ((1 - EULER) * a + EULER * b)


def deflated_sharpe(
    sharpe: float, returns: list[float], n_trials: int, sharpe_variance: float
) -> float:
    """P(the observed Sharpe beats what the best of n_trials would give by luck).

    You WILL test many variants. Reporting the raw Sharpe of the winner without
    this correction is how a backtest lies.
    """
    return probabilistic_sharpe(sharpe, returns, expected_max_sharpe(n_trials, sharpe_variance))


def benjamini_hochberg(p_values: list[float], fdr: float = 0.10) -> list[bool]:
    """Which hypotheses survive at the given false-discovery rate.

    Testing thirty factors at p<0.05 and keeping the winners guarantees false
    discoveries; BH controls the EXPECTED fraction of them instead. Returns a
    keep/drop flag per input, in input order.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    cutoff = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= fdr * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= cutoff:
            keep[idx] = True
    return keep


def probability_of_backtest_overfitting(
    performance_matrix: list[list[float]], n_splits: int = 16
) -> float:
    """Bailey et al.'s CSCV estimate of P(backtest overfitting), stdlib-only.

    Rows are strategies, columns are per-period returns. Split the columns into
    S even blocks; for every half-and-half combination of blocks, pick the
    in-sample winner and ask where it ranks OUT of sample. PBO is the fraction
    of combinations where the IS winner lands in the OOS bottom half - a
    number, not a feeling, for "the best backtest was the luckiest one".
    """
    from itertools import combinations

    if not performance_matrix or len(performance_matrix) < 2:
        return 0.0
    n_cols = len(performance_matrix[0])
    if any(len(r) != n_cols for r in performance_matrix):
        raise ValueError("performance matrix rows must be equal length")
    s_blocks = min(n_splits, n_cols)
    if s_blocks % 2:
        s_blocks -= 1
    if s_blocks < 2:
        return 0.0
    bounds = [round(i * n_cols / s_blocks) for i in range(s_blocks + 1)]
    blocks = [range(bounds[i], bounds[i + 1]) for i in range(s_blocks)]

    def sharpe(row: list[float], cols: list[int]) -> float:
        vals = [row[c] for c in cols]
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / max(len(vals) - 1, 1)
        return mu / math.sqrt(var) if var > 1e-18 else 0.0

    below = 0
    total = 0
    for combo in combinations(range(s_blocks), s_blocks // 2):
        is_cols = [c for b in combo for c in blocks[b]]
        oos_cols = [c for b in range(s_blocks) if b not in combo for c in blocks[b]]
        is_scores = [sharpe(r, is_cols) for r in performance_matrix]
        oos_scores = [sharpe(r, oos_cols) for r in performance_matrix]
        winner = max(range(len(is_scores)), key=lambda i: is_scores[i])
        rank = sum(1 for x in oos_scores if x < oos_scores[winner])
        rel = rank / (len(oos_scores) - 1) if len(oos_scores) > 1 else 1.0
        below += rel < 0.5
        total += 1
    return below / total if total else 0.0
