"""Price-move attribution.

docs/03. The whole point: split the move into its statistical components BEFORE
naming a cause. Only the idiosyncratic residual gets a story, and the
unexplained share is always reported.

A system that says "4.7 of the 6.0 points were the market and the sector" is
telling the truth. A system that says "the bank fell on margin concerns" is
producing text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from engines.attribution.regression import Fit, corrado_rank_z, huber_fit

ESTIMATION_LOOKBACK = 260
ESTIMATION_GAP = 10  # window ends 10 sessions before the event
MIN_OBSERVATIONS = 120
SAR_HUNT_THRESHOLD = 1.5  # below this, no cause hunt
IDIO_SHARE_MARKET_DRIVEN = 0.20


class Component(str, Enum):
    MARKET = "market"
    SECTOR = "sector"
    STYLE = "style"
    #: The fitted intercept of the market model - what this instrument returned
    #: on an average session of the estimation window once the factors were paid
    #: for. docs/03 section 2.1 has always carried it as `alpha` in the model
    #: equation; it was missing from the output contract, so the printed
    #: components summed to the return MINUS this term and nothing said so.
    #:
    #: It never earns a cause hunt. A drift is a property of the estimation
    #: window, not an event, and `needs_cause_hunt` keys off the residual alone.
    DRIFT = "drift"
    CURRENCY = "currency"
    IDIOSYNCRATIC = "idiosyncratic"


class Verdict(str, Enum):
    EXPLAINED = "explained"
    PARTIALLY_EXPLAINED = "partially_explained"
    NO_IDENTIFIED_CATALYST = "no_identified_catalyst"
    NOT_SIGNIFICANT = "not_significant"
    MARKET_DRIVEN = "market_driven"
    ATTRIBUTION_UNAVAILABLE = "attribution_unavailable"


@dataclass(frozen=True)
class AttributionComponent:
    component: Component
    contribution: float
    share_of_total: float
    beta: float | None = None


@dataclass(frozen=True)
class Significance:
    standardised_ar: float
    rank_z: float
    parametric_significant: bool
    rank_significant: bool

    @property
    def agree(self) -> bool:
        return self.parametric_significant == self.rank_significant


@dataclass
class MoveExplanation:
    instrument_id: str
    window: tuple[date, date]
    base_currency: str
    total_return_local: float
    total_return_base: float
    components: list[AttributionComponent]
    abnormal_return: float
    significance: Significance | None
    unexplained_share: float
    verdict: Verdict
    method_version: str = "attribution-1.0"
    candidates: list = field(default_factory=list)
    reason: str = ""
    #: Commitment 10: a number travels with its sample size. None when no fit.
    estimation_n: int | None = None
    estimation_note: str = ""

    def component(self, c: Component) -> AttributionComponent | None:
        return next((x for x in self.components if x.component is c), None)

    def needs_cause_hunt(self) -> bool:
        """Only a significant idiosyncratic move earns a search for a story."""
        return (
            self.verdict
            not in (Verdict.NOT_SIGNIFICANT, Verdict.MARKET_DRIVEN, Verdict.ATTRIBUTION_UNAVAILABLE)
            and self.significance is not None
            and abs(self.significance.standardised_ar) > SAR_HUNT_THRESHOLD
        )


@dataclass(frozen=True)
class EstimationInputs:
    """Returns aligned by session. Factors are columns."""

    instrument: list[float]
    market: list[float]
    sector: list[float]
    styles: dict[str, list[float]] = field(default_factory=dict)

    def rows(self) -> list[list[float]]:
        cols = [self.market, self.sector] + [self.styles[k] for k in sorted(self.styles)]
        return [list(r) for r in zip(*cols)]

    def style_names(self) -> list[str]:
        return sorted(self.styles)


def estimate(inputs: EstimationInputs) -> Fit | None:
    """docs/03 section 2.2 rule 2: below the minimum, betas are noise."""
    if len(inputs.instrument) < MIN_OBSERVATIONS:
        return None
    return huber_fit(inputs.rows(), inputs.instrument)


def decompose(
    instrument_id: str,
    window: tuple[date, date],
    event_market: float,
    event_sector: float,
    event_styles: dict[str, float],
    realised_local: float,
    fx_return: float,
    fit: Fit | None,
    base_currency: str = "MYR",
    style_names: list[str] | None = None,
) -> MoveExplanation:
    # A non-finite input must never reach a verdict. Found by stress testing: a
    # NaN return produced verdict=no_identified_catalyst with unexplained=nan,
    # which renders to the user as a confident finding with "nan% unexplained".
    # That is the failure this whole design exists to prevent, arriving through
    # the data rather than through the model.
    dirty = [
        n
        for n, v in (
            ("realised_local", realised_local),
            ("fx_return", fx_return),
            ("event_market", event_market),
            ("event_sector", event_sector),
        )
        if not math.isfinite(v)
    ]
    dirty += [f"style:{k}" for k, v in event_styles.items() if not math.isfinite(v)]
    if dirty:
        return MoveExplanation(
            instrument_id,
            window,
            base_currency,
            0.0,
            0.0,
            [],
            0.0,
            None,
            1.0,
            Verdict.ATTRIBUTION_UNAVAILABLE,
            reason=f"non-finite input: {', '.join(dirty)}. A move cannot be decomposed from "
            "a value that is not a number, and reporting one anyway would be worse "
            "than reporting nothing.",
        )

    total_base = (1.0 + realised_local) * (1.0 + fx_return) - 1.0

    if fit is None:
        return MoveExplanation(
            instrument_id,
            window,
            base_currency,
            realised_local,
            total_base,
            [],
            0.0,
            None,
            1.0,
            Verdict.ATTRIBUTION_UNAVAILABLE,
            reason=f"fewer than {MIN_OBSERVATIONS} usable observations in the estimation window",
        )

    names = style_names or sorted(event_styles)
    est_note = f"betas from {fit.n} sessions"
    if getattr(fit, "shrinkage", 0.0):
        est_note += f", shrunk {fit.shrinkage:.0%} toward prior"
    if fit.n < 2 * MIN_OBSERVATIONS:
        est_note += " (short window; betas unstable)"
    est_note += f"; drift {fit.coefficients[0] * 100:+.3f}pp/session"
    if fit.intercept_se:
        t = abs(fit.coefficients[0]) / fit.intercept_se
        est_note += f", {t:.1f} standard errors from zero"
        if t <= 1.96:
            est_note += " (not distinguishable from zero)"
    betas = fit.coefficients[1:]
    b_mkt, b_sec = betas[0], betas[1]
    b_styles = dict(zip(names, betas[2:]))

    c_mkt = b_mkt * event_market
    c_sec = b_sec * event_sector
    c_sty = sum(b_styles.get(n, 0.0) * event_styles.get(n, 0.0) for n in names)
    c_fx = total_base - realised_local
    drift = fit.coefficients[0]
    expected = drift + c_mkt + c_sec + c_sty
    ar = realised_local - expected

    # `drift` is subtracted out of `ar` above - that is the textbook market-model
    # abnormal return and the significance test below depends on it - so it MUST
    # appear here too, or the printed components sum to the return minus alpha.
    # They now sum to `total_return_base` exactly, which is pinned by test.
    contribs = [
        (Component.MARKET, c_mkt, b_mkt),
        (Component.SECTOR, c_sec, b_sec),
        (Component.STYLE, c_sty, None),
        (Component.DRIFT, drift, None),
        (Component.CURRENCY, c_fx, None),
        (Component.IDIOSYNCRATIC, ar, None),
    ]
    # Absolute values in the denominator: offsetting components must never
    # produce a share above 100% (docs/03 section 2.5).
    gross = sum(abs(v) for _, v, _ in contribs) or 1.0
    components = [AttributionComponent(c, v, abs(v) / gross, b) for c, v, b in contribs]

    sar = ar / fit.residual_sigma if fit.residual_sigma > 1e-12 else 0.0
    rank_z = corrado_rank_z(ar, fit.residuals)
    sig = Significance(sar, rank_z, abs(sar) > 1.96, abs(rank_z) > 1.96)

    idio_share = abs(ar) / gross
    unexplained = idio_share

    if abs(sar) <= SAR_HUNT_THRESHOLD:
        verdict, reason = (
            Verdict.NOT_SIGNIFICANT,
            (
                f"idiosyncratic move is {abs(sar):.2f} sigma, within normal variation for this instrument"
            ),
        )
    elif idio_share < IDIO_SHARE_MARKET_DRIVEN:
        verdict, reason = (
            Verdict.MARKET_DRIVEN,
            (f"only {idio_share:.0%} of the move is company-specific; the market and sector moved"),
        )
    else:
        verdict, reason = (
            Verdict.NO_IDENTIFIED_CATALYST,
            ("significant idiosyncratic move; no catalyst matched yet"),
        )

    return MoveExplanation(
        instrument_id,
        window,
        base_currency,
        realised_local,
        total_base,
        components,
        ar,
        sig,
        unexplained,
        verdict,
        estimation_n=fit.n,
        estimation_note=est_note,
        reason=reason,
    )


# --------------------------------------------------------------------------
# Long-horizon decomposition (docs/03 section 4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LongHorizon:
    """Log-additive. Answers: a business compounding, or a crowd re-rating?"""

    total_return: float
    eps_growth: float
    multiple_change: float
    shareholder_yield: float
    fx: float
    years: float

    @property
    def reading(self) -> str:
        parts = {
            "earnings growth": self.eps_growth,
            "multiple re-rating": self.multiple_change,
            "shareholder yield": self.shareholder_yield,
            "currency": self.fx,
        }
        driver = max(parts, key=lambda k: abs(parts[k]))
        if driver == "earnings growth":
            return "The business compounded. Most durable shape."
        if driver == "multiple re-rating":
            return (
                "Mostly a re-rating. Mean-reverting - the same mechanism runs "
                "in reverse, and this return borrowed from the future."
            )
        if driver == "currency":
            return "Mostly currency. Says nothing about the company."
        return "Mostly cash returned. Durable, but check whether buybacks were debt-funded."

    def residual(self) -> float:
        """Cross-term the four components do not capture. Reported, not hidden."""
        modelled = (
            math.exp(
                math.log1p(self.eps_growth)
                + math.log1p(self.multiple_change)
                + math.log1p(self.shareholder_yield)
                + math.log1p(self.fx)
            )
            - 1.0
        )
        return self.total_return - modelled


def long_horizon_decompose(
    eps_start: float,
    eps_end: float,
    multiple_start: float,
    multiple_end: float,
    cumulative_shareholder_yield: float,
    fx_start: float,
    fx_end: float,
    years: float,
) -> LongHorizon:
    if eps_start <= 0 or multiple_start <= 0 or fx_start <= 0:
        raise ValueError("start values must be positive to decompose in logs")
    g = eps_end / eps_start - 1.0
    m = multiple_end / multiple_start - 1.0
    y = cumulative_shareholder_yield
    f = fx_end / fx_start - 1.0
    total = (1 + g) * (1 + m) * (1 + y) * (1 + f) - 1.0
    return LongHorizon(total, g, m, y, f, years)
