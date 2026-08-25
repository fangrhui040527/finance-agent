"""Concentration, measured four ways.

docs/05 section 4. The instinct is right and the naive implementation is wrong:
holding twenty stocks is not diversification if they are twenty banks in one
country. Position count is not a diversification measure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Position:
    instrument_id: str
    weight: float
    sector: str
    country: str
    currency: str
    risk_to_stop: float = 0.0   # fraction of portfolio at risk if the stop fills


@dataclass(frozen=True)
class Limits:
    """Defaults are user-configurable within bounds; the bounds are not."""

    single_name: float = 0.08
    sector: float = 0.25
    country: float = 0.40
    non_base_currency: float = 0.50
    correlation_cluster: float = 0.30
    hhi: float = 0.18
    min_effective_bets: float = 5.0
    portfolio_heat: float = 0.06
    min_positions: int = 5

    def __post_init__(self) -> None:
        if self.single_name > 0.15:
            raise ValueError("single-name cap cannot be raised above 15%")
        if self.min_effective_bets < 3.0:
            raise ValueError("effective bets cannot be set below 3")


def hhi(weights: list[float]) -> float:
    """Herfindahl. Punishes a heavy tail that a position count hides."""
    return sum(w * w for w in weights)


def effective_number_of_bets(weights: list[float], corr: list[list[float]]) -> float:
    """1 / (w' R w) normalised so that uncorrelated equal weights gives n."""
    n = len(weights)
    if n == 0:
        return 0.0
    total = sum(weights) or 1.0
    w = [x / total for x in weights]
    var = sum(w[i] * w[j] * corr[i][j] for i in range(n) for j in range(n))
    return 1.0 / var if var > 1e-12 else float(n)


def correlation_clusters(corr: list[list[float]], threshold: float = 0.6) -> list[list[int]]:
    """Single-linkage clustering on the correlation matrix.

    docs/05 section 4.3: sector classification is a taxonomy someone else chose;
    correlation is what your money actually experiences. A palm-oil producer, a
    fertiliser distributor and a shipping company sit in three sectors and one
    commodity cycle.
    """
    n = len(corr)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if corr[i][j] >= threshold:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values()]


@dataclass
class Breach:
    limit: str
    actual: float
    allowed: float
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.limit}: {self.actual:.3f} vs limit {self.allowed:.3f} {self.detail}".strip()


def check(
    positions: list[Position],
    corr: list[list[float]] | None,
    limits: Limits,
    base_currency: str = "MYR",
    cluster_threshold: float = 0.6,
) -> list[Breach]:
    """Every breach, not just the first. Empty list means compliant."""
    out: list[Breach] = []
    if not positions:
        return out
    weights = [p.weight for p in positions]

    for p in positions:
        if p.weight > limits.single_name:
            out.append(Breach("single_name", p.weight, limits.single_name, p.instrument_id))

    for key, cap, label in (
        ("sector", limits.sector, "sector"),
        ("country", limits.country, "country"),
    ):
        agg: dict[str, float] = {}
        for p in positions:
            agg[getattr(p, key)] = agg.get(getattr(p, key), 0.0) + p.weight
        for name, w in agg.items():
            if w > cap:
                out.append(Breach(label, w, cap, name))

    foreign = sum(p.weight for p in positions if p.currency.upper() != base_currency.upper())
    if foreign > limits.non_base_currency:
        out.append(Breach("non_base_currency", foreign, limits.non_base_currency))

    h = hhi(weights)
    if h > limits.hhi:
        out.append(Breach("hhi", h, limits.hhi))

    heat = sum(p.risk_to_stop for p in positions)
    if heat > limits.portfolio_heat:
        out.append(Breach("portfolio_heat", heat, limits.portfolio_heat))

    if corr:
        eb = effective_number_of_bets(weights, corr)
        if eb < limits.min_effective_bets:
            out.append(Breach("effective_bets", eb, limits.min_effective_bets,
                              "correlated holdings are one bet wearing many hats"))
        for cluster in correlation_clusters(corr, cluster_threshold):
            w = sum(positions[i].weight for i in cluster)
            if w > limits.correlation_cluster:
                names = ",".join(positions[i].instrument_id for i in cluster)
                out.append(Breach("correlation_cluster", w, limits.correlation_cluster, names))
    return out
