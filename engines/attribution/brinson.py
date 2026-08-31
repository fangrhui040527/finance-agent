"""Brinson-Fachler attribution with Carino linking, stdlib only.

Answers the PORTFOLIO's question - "why did the BOOK move against its
benchmark" - the way the single-name engine answers the instrument's. Per
sector: allocation (owning more or less of what did well) and selection
(owning better or worse names inside it). Multi-period effects are linked
with Carino coefficients so the linked effects sum EXACTLY to the compounded
active return instead of drifting apart period by period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SectorEffect:
    sector: str
    allocation: float
    selection: float

    @property
    def total(self) -> float:
        return self.allocation + self.selection


@dataclass(frozen=True)
class BrinsonPeriod:
    """One period's effects. Weights are fractions of each book; returns simple."""

    effects: list[SectorEffect]
    portfolio_return: float
    benchmark_return: float

    @property
    def active_return(self) -> float:
        return self.portfolio_return - self.benchmark_return


def brinson_fachler(
    portfolio_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_weights: dict[str, float],
    benchmark_returns: dict[str, float],
) -> BrinsonPeriod:
    """Sector effects for one period.

    Brinson-FACHLER, not Brinson-Hood-Beebower: allocation is measured against
    (sector benchmark return - TOTAL benchmark return), so overweighting a
    sector that merely matched the index scores zero rather than positive.
    """
    sectors = sorted(set(portfolio_weights) | set(benchmark_weights))
    rb_total = sum(benchmark_weights.get(s, 0.0) * benchmark_returns.get(s, 0.0) for s in sectors)
    rp_total = sum(portfolio_weights.get(s, 0.0) * portfolio_returns.get(s, 0.0) for s in sectors)
    effects: list[SectorEffect] = []
    for s in sectors:
        wp, wb = portfolio_weights.get(s, 0.0), benchmark_weights.get(s, 0.0)
        rp = portfolio_returns.get(s, 0.0)
        rb = benchmark_returns.get(s, 0.0)
        allocation = (wp - wb) * (rb - rb_total)
        selection = wp * (rp - rb)
        effects.append(SectorEffect(s, allocation, selection))
    return BrinsonPeriod(effects, rp_total, rb_total)


def carino_link(periods: list[BrinsonPeriod]) -> dict[str, SectorEffect]:
    """Link per-period effects so they sum to the compounded active return.

    Arithmetic effects do not compound; summing them across months leaves an
    unexplained residual that grows with volatility. Carino's log-coefficients
    scale each period so the linked effects add up EXACTLY - the property the
    Portfolio screen relies on when it shows a total.
    """
    if not periods:
        return {}
    rp = math.prod(1.0 + p.portfolio_return for p in periods) - 1.0
    rb = math.prod(1.0 + p.benchmark_return for p in periods) - 1.0

    def _k(r_p: float, r_b: float) -> float:
        if abs(r_p - r_b) < 1e-12:
            return 1.0 / (1.0 + r_p) if r_p > -1.0 else 1.0
        return (math.log1p(r_p) - math.log1p(r_b)) / (r_p - r_b)

    k_total = _k(rp, rb)
    out: dict[str, list[float]] = {}
    for p in periods:
        k_t = _k(p.portfolio_return, p.benchmark_return) / k_total if k_total else 1.0
        for e in p.effects:
            slot = out.setdefault(e.sector, [0.0, 0.0])
            slot[0] += k_t * e.allocation
            slot[1] += k_t * e.selection
    return {s: SectorEffect(s, a, sel) for s, (a, sel) in sorted(out.items())}
