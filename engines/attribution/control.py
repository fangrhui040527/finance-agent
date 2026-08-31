"""Negative controls for a factor fit: is this beta better than shuffled noise?

Ported (stdlib-only) from a strict bench-runner design: fit the real data,
then fit the same design against SHUFFLED instrument returns several times.
A real relationship survives; a fit that only reflects overlap in volatility
does not. The out-of-sample gate asks the other honest question: do the betas
estimated on the front of the window still explain the back of it?

Categories, deliberately blunt:
  confirmed  - real fit beats every shuffle and holds out of sample
  train_only - beats the shuffles but decays out of sample (overfit smell)
  reversed   - the OOS relationship flips sign (regime break, or noise)
  noise      - indistinguishable from shuffled returns
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from engines.attribution.decompose import EstimationInputs, estimate
from engines.attribution.regression import huber_fit

#: Paired t-statistic threshold. 2.0 is the classical bar; pass strict=True for
#: the Harvey-Liu-Zhu 3.5, which prices in how many factors the field has mined.
T_THRESHOLD = 2.0
T_THRESHOLD_STRICT = 3.5


@dataclass(frozen=True)
class ControlReport:
    category: str  # confirmed | train_only | reversed | noise
    real_r2: float
    shuffled_r2: list[float]
    t_stat: float
    oos_r2: float | None
    threshold: float

    def describe(self) -> str:
        shuf = sum(self.shuffled_r2) / len(self.shuffled_r2) if self.shuffled_r2 else 0.0
        oos = f", oos R2 {self.oos_r2:.3f}" if self.oos_r2 is not None else ""
        return (
            f"{self.category}: R2 {self.real_r2:.3f} vs shuffled {shuf:.3f} "
            f"(t={self.t_stat:.2f}, bar {self.threshold}){oos}"
        )


def shuffle_control(
    inputs: EstimationInputs,
    seeds: int = 5,
    oos_fraction: float = 0.25,
    strict: bool = False,
    rng_seed: int = 20260831,
) -> ControlReport | None:
    """Deterministic (seeded) by design: a control that flickers is not a control."""
    real = estimate(inputs)
    if real is None:
        return None
    threshold = T_THRESHOLD_STRICT if strict else T_THRESHOLD

    rng = random.Random(rng_seed)
    rows = inputs.rows()
    shuffled_r2: list[float] = []
    for _ in range(seeds):
        y = list(inputs.instrument)
        rng.shuffle(y)
        try:
            shuffled_r2.append(huber_fit(rows, y).r_squared)
        except ValueError:
            shuffled_r2.append(0.0)

    diffs = [real.r_squared - r for r in shuffled_r2]
    mean = sum(diffs) / len(diffs)
    if len(diffs) > 1:
        var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
        t = (
            mean / math.sqrt(var / len(diffs))
            if var > 1e-18
            else (float("inf") if mean > 0 else 0.0)
        )
    else:
        t = 0.0

    # Out-of-sample: fit on the front, score on the back.
    n = len(inputs.instrument)
    split = int(n * (1.0 - oos_fraction))
    oos_r2: float | None = None
    front_beats = t >= threshold
    category = "noise"
    if split >= 30 and n - split >= 10:
        front = huber_fit(rows[:split], inputs.instrument[:split])
        back_y = inputs.instrument[split:]
        back_pred = [front.predict(r) for r in rows[split:]]
        ybar = sum(back_y) / len(back_y)
        sst = sum((v - ybar) ** 2 for v in back_y)
        sse = sum((a - b) ** 2 for a, b in zip(back_y, back_pred))
        oos_r2 = 1.0 - sse / sst if sst > 1e-15 else 0.0
        back = huber_fit(rows[split:], back_y)
        flipped = (
            len(front.coefficients) > 1
            and len(back.coefficients) > 1
            and front.coefficients[1] * back.coefficients[1] < 0
            and abs(back.coefficients[1]) > 0.1
        )
        if front_beats and flipped:
            category = "reversed"
        elif front_beats and oos_r2 > 0.0:
            category = "confirmed"
        elif front_beats:
            category = "train_only"
    elif front_beats:
        category = "confirmed"  # too short to split; the shuffle bar stands alone

    return ControlReport(
        category=category,
        real_r2=real.r_squared,
        shuffled_r2=shuffled_r2,
        t_stat=t,
        oos_r2=oos_r2,
        threshold=threshold,
    )
