"""The gate. Nothing reaches a user before it clears this.

docs/05 section 9: must beat all three benchmarks AFTER costs - the local index,
an equal-weight version of the same universe, and buy-and-hold on the current
portfolio. If it beats none of them, the correct product is an index tracker plus
the planner, and this harness is built to be able to say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engines.backtest.metrics import (
    TRADING_DAYS,
    Performance,
    deflated_sharpe,
    performance,
    probabilistic_sharpe,
)
from engines.backtest.splitter import Fold, purged_walk_forward


class Regime(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class Benchmark(str, Enum):
    LOCAL_INDEX = "local_index"
    EQUAL_WEIGHT_UNIVERSE = "equal_weight_universe"
    BUY_AND_HOLD = "buy_and_hold"


@dataclass
class BenchmarkResult:
    name: Benchmark
    performance: Performance
    excess_cagr: float
    beaten: bool


@dataclass
class BacktestReport:
    strategy: Performance
    benchmarks: list[BenchmarkResult]
    by_regime: dict[str, Performance]
    folds: list[Fold]
    n_trials: int
    deflated_sharpe: float
    probabilistic_sharpe: float
    gross_sharpe: float
    turnover: float
    notes: list[str] = field(default_factory=list)

    @property
    def beats_all_benchmarks(self) -> bool:
        return all(b.beaten for b in self.benchmarks)

    @property
    def passes_gate(self) -> bool:
        """docs/05: beat all three after costs, and survive the multiple-testing
        correction. A raw Sharpe from the best of many trials is not evidence."""
        return self.beats_all_benchmarks and self.deflated_sharpe >= 0.95

    def verdict(self) -> str:
        if self.passes_gate:
            return "PASS - clears all three benchmarks and the deflated Sharpe threshold"
        losses = [b.name.value for b in self.benchmarks if not b.beaten]
        if losses:
            return (
                f"FAIL - does not beat {', '.join(losses)} after costs. "
                "If it beats none, the correct product is an index tracker plus the planner"
            )
        return (
            f"FAIL - deflated Sharpe {self.deflated_sharpe:.2f} below 0.95 after correcting "
            f"for {self.n_trials} trials; the result is consistent with luck"
        )

    def regime_warning(self) -> str | None:
        """A model that only works in one regime should say so on its face."""
        positive = [k for k, p in self.by_regime.items() if p.sharpe > 0.3]
        if len(self.by_regime) > 1 and len(positive) == 1:
            return f"edge appears only in the {positive[0]} regime"
        return None


def _per_period_sharpe(returns: list[float]) -> float:
    """Mean over standard deviation of the returns as they are: no annualising."""
    n = len(returns)
    if n < 2:
        return 0.0
    mu = sum(returns) / n
    sd = (sum((r - mu) ** 2 for r in returns) / (n - 1)) ** 0.5
    return mu / sd if sd > 1e-12 else 0.0


def run(
    strategy_returns: list[float],
    gross_returns: list[float],
    benchmark_returns: dict[Benchmark, list[float]],
    regimes: list[Regime] | None = None,
    n_trials: int = 1,
    sharpe_variance: float | None = None,
    turnover: float = 0.0,
    n_folds: int = 5,
    label_horizon: int = 20,
) -> BacktestReport:
    net = performance(strategy_returns)
    gross = performance(gross_returns) if gross_returns else net

    bench: list[BenchmarkResult] = []
    for name, series in benchmark_returns.items():
        bp = performance(series)
        bench.append(BenchmarkResult(name, bp, net.cagr - bp.cagr, net.cagr > bp.cagr))

    by_regime: dict[str, Performance] = {}
    if regimes:
        for r in Regime:
            sel = [ret for ret, reg in zip(strategy_returns, regimes) if reg is r]
            if len(sel) >= 20:
                by_regime[r.value] = performance(sel)

    folds = purged_walk_forward(
        len(strategy_returns), n_folds, label_horizon, min_train=max(60, len(strategy_returns) // 5)
    )

    notes: list[str] = []
    if gross.sharpe > 0 and net.sharpe <= 0:
        notes.append("the edge exists gross and is entirely consumed by costs")
    if net.longest_underwater_days > 500:
        notes.append(
            f"longest underwater stretch is {net.longest_underwater_days} sessions - "
            "that is what you would have had to live through"
        )

    # The deflated and probabilistic Sharpe (Bailey and Lopez de Prado) scale a
    # PER-PERIOD Sharpe by sqrt(n - 1). They were handed the annualised one, a
    # factor of sqrt(252) too large: the z-score saturated, and the 0.95 gate
    # passed about 47% of strategies with no edge at all. The variance across
    # trials goes in per-period units too: `sharpe_variance` is still given in
    # annual units, as it always was, and by default it is the sampling
    # variance of a per-period Sharpe under the null, 1 / (T - 1).
    per_period = _per_period_sharpe(strategy_returns)
    t = len(strategy_returns)
    variance = 1.0 / max(1, t - 1) if sharpe_variance is None else sharpe_variance / TRADING_DAYS

    return BacktestReport(
        strategy=net,
        benchmarks=bench,
        by_regime=by_regime,
        folds=folds,
        n_trials=n_trials,
        deflated_sharpe=deflated_sharpe(per_period, strategy_returns, n_trials, variance),
        probabilistic_sharpe=probabilistic_sharpe(per_period, strategy_returns),
        gross_sharpe=gross.sharpe,
        turnover=turnover,
        notes=notes,
    )
