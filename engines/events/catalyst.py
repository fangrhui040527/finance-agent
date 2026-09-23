"""Catalyst matching: six factors, all six always shown.

docs/03 section 3. Two rules do the work:

  - Candidates are matched to the RESIDUAL, never to the raw return. This is the
    mechanical reason the system cannot tell you a company story about a day when
    the whole index fell.
  - No forced explanation. If nothing clears the threshold the answer is
    no_identified_catalyst, and that is informative rather than a failure: moves
    with identifiable news tend to drift, large moves without news tend to
    reverse, and a system that always invents a cause throws that away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from engines.attribution.decompose import MoveExplanation, Verdict, sigma_phrase
from engines.events.taxonomy import BaseRate, BaseRateTable, Event, EventType

SCORE_THRESHOLD = 0.25

# docs/03 section 3.1: primary filing 1.0, exchange announcement 1.0,
# curated news 0.8, general news 0.6, web search 0.4.
SOURCE_TRUST = {
    "filing": 1.0,
    "exchange": 1.0,
    "curated_news": 0.8,
    "general_news": 0.6,
    "web": 0.4,
}

# Historical mean direction per event type. Used ONLY for direction agreement -
# magnitude always comes from the measured base-rate table.
EXPECTED_SIGN: dict[EventType, int] = {
    EventType.EARNINGS_RESULT: 0,  # depends on surprise
    EventType.GUIDANCE_CHANGE: 0,
    EventType.MA_TARGET: +1,
    EventType.MA_ACQUIRER: -1,
    EventType.BUYBACK: +1,
    EventType.DIVIDEND_CHANGE: 0,
    EventType.CAPITAL_RAISE: -1,
    EventType.INSIDER_BUY: +1,
    EventType.INSIDER_SELL: 0,  # docs/02 A8: not a signal by default
    EventType.INDEX_ADD: +1,
    EventType.INDEX_DROP: -1,
    EventType.RATING_CHANGE: 0,
    EventType.CONTRACT_WIN: +1,
    EventType.PRODUCT_LAUNCH: +1,
    EventType.REGULATORY_ACTION: -1,
    EventType.LITIGATION: -1,
    EventType.EXECUTIVE_CHANGE: 0,
    EventType.GOING_CONCERN: -1,
    EventType.HALT: 0,
    EventType.DELISTING: -1,
    EventType.LOCKUP_EXPIRY: -1,
    EventType.MACRO_PRINT: 0,
    EventType.PEER_EARNINGS: 0,
}

# Prior probability that this event type produces a significant idiosyncratic
# move at all. Overridden by the measured table wherever one exists.
DEFAULT_PRIOR: dict[EventType, float] = {
    EventType.EARNINGS_RESULT: 0.75,
    EventType.GUIDANCE_CHANGE: 0.80,
    EventType.MA_TARGET: 0.95,
    EventType.MA_ACQUIRER: 0.60,
    EventType.BUYBACK: 0.45,
    EventType.DIVIDEND_CHANGE: 0.25,
    EventType.CAPITAL_RAISE: 0.65,
    EventType.INSIDER_BUY: 0.20,
    EventType.INSIDER_SELL: 0.10,
    EventType.INDEX_ADD: 0.55,
    EventType.INDEX_DROP: 0.55,
    EventType.RATING_CHANGE: 0.30,
    EventType.CONTRACT_WIN: 0.40,
    EventType.PRODUCT_LAUNCH: 0.25,
    EventType.REGULATORY_ACTION: 0.70,
    EventType.LITIGATION: 0.45,
    EventType.EXECUTIVE_CHANGE: 0.35,
    EventType.GOING_CONCERN: 0.90,
    EventType.HALT: 0.50,
    EventType.DELISTING: 0.85,
    EventType.LOCKUP_EXPIRY: 0.30,
    EventType.MACRO_PRINT: 0.15,
    EventType.PEER_EARNINGS: 0.20,
}

# Information-diffusion speed. Slower markets get a longer half-life.
LAMBDA_BY_MARKET = {"XNAS": 0.9, "XNYS": 0.9, "XKLS": 0.55, "XSES": 0.6}


@dataclass(frozen=True)
class ScoreBreakdown:
    prior: float
    proximity: float
    specificity: float
    direction: float
    magnitude: float
    source_trust: float

    @property
    def score(self) -> float:
        return (
            self.prior
            * self.proximity
            * self.specificity
            * self.direction
            * self.magnitude
            * self.source_trust
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "prior": self.prior,
            "proximity": self.proximity,
            "specificity": self.specificity,
            "direction": self.direction,
            "magnitude": self.magnitude,
            "source_trust": self.source_trust,
        }


@dataclass(frozen=True)
class CandidateCause:
    cause_type: str
    description: str
    occurred_at: datetime
    lag_sessions: int
    breakdown: ScoreBreakdown
    base_rate: BaseRate | None
    evidence: tuple[str, ...]

    @property
    def score(self) -> float:
        return self.breakdown.score


def proximity(lag_sessions: int, market: str) -> float:
    """Exponential decay, tuned per market. Pre-announcement lags are penalised
    less than post, because information leaks (docs/03 section 3.2 rule 4)."""
    lam = LAMBDA_BY_MARKET.get(market.upper(), 0.7)
    return math.exp(-abs(lag_sessions) / (1.0 / lam * 4.0))


def specificity(event: Event, instrument_id: str, peers: set[str], sector_wide: bool) -> float:
    if event.instrument_id == instrument_id:
        return 1.0
    if event.instrument_id in peers:
        return 0.6
    if sector_wide:
        return 0.3
    return 0.1


def direction_agreement(event: Event, residual: float, base_rate: BaseRate | None) -> float:
    """An earnings beat does not explain a fall without a base-rated adjustment."""
    expected = 0
    if base_rate is not None and base_rate.n >= 10:
        expected = 1 if base_rate.median_car > 0 else -1 if base_rate.median_car < 0 else 0
    else:
        expected = EXPECTED_SIGN.get(event.event_type, 0)
        if event.event_type in (EventType.EARNINGS_RESULT, EventType.GUIDANCE_CHANGE):
            s = event.surprise.value
            expected = 1 if "beat" in s else -1 if "miss" in s else 0
    if expected == 0:
        return 0.6  # genuinely two-sided
    return 1.0 if (residual >= 0) == (expected > 0) else 0.2


def magnitude_plausibility(residual: float, base_rate: BaseRate | None) -> float:
    """Where the move sits in the historical CAR distribution for this event.

    A routine dividend declaration cannot explain a five-sigma move.
    """
    if base_rate is None or base_rate.n < 5:
        return 0.5
    lo, hi = base_rate.iqr
    span = max(abs(hi - lo), 1e-6)
    typical = max(abs(base_rate.median_car), span / 2)
    ratio = abs(residual) / max(typical, 1e-6)
    if ratio <= 1.0:
        return 1.0
    return float(max(0.05, 1.0 / ratio))


def score_candidates(
    explanation: MoveExplanation,
    events: list[Event],
    table: BaseRateTable,
    session_lag: dict[str, int],
    market: str,
    peers: set[str] | None = None,
    source_kind: dict[str, str] | None = None,
    sector_wide: set[str] | None = None,
) -> list[CandidateCause]:
    """Score every candidate against the RESIDUAL. Never against the raw return."""
    if not explanation.needs_cause_hunt():
        return []

    residual = explanation.abnormal_return
    peers = peers or set()
    source_kind = source_kind or {}
    sector_wide = sector_wide or set()
    out: list[CandidateCause] = []

    for ev in events:
        if not ev.citable:
            continue  # docs/02 A5: unconfirmed cannot be cited
        br = table.lookup(ev.event_type, market, ev.cap_band, ev.surprise)
        lag = session_lag.get(ev.event_id, 0)
        bd = ScoreBreakdown(
            prior=(br.hit_rate if br and br.n >= 10 else DEFAULT_PRIOR.get(ev.event_type, 0.2)),
            proximity=proximity(lag, market),
            specificity=specificity(
                ev, explanation.instrument_id, peers, ev.event_id in sector_wide
            ),
            direction=direction_agreement(ev, residual, br),
            magnitude=magnitude_plausibility(residual, br),
            source_trust=SOURCE_TRUST.get(source_kind.get(ev.event_id, "general_news"), 0.6),
        )
        out.append(
            CandidateCause(
                cause_type=ev.event_type.value,
                description=ev.detail or ev.event_type.value,
                occurred_at=ev.announced_at,
                lag_sessions=lag,
                breakdown=bd,
                base_rate=br,
                evidence=(ev.source_doc_id,) if ev.source_doc_id else (),
            )
        )
    out.sort(key=lambda c: -c.score)
    return out


def attach(explanation: MoveExplanation, candidates: list[CandidateCause]) -> MoveExplanation:
    """Set the final verdict once candidates are known.

    Multiple close candidates stay multiple - collapsing to one is false
    precision (docs/03 section 3.2 rule 2).
    """
    explanation.candidates = candidates
    if explanation.verdict in (
        Verdict.NOT_SIGNIFICANT,
        Verdict.MARKET_DRIVEN,
        Verdict.ATTRIBUTION_UNAVAILABLE,
    ):
        return explanation

    # An empty list and a list that was weighed and rejected used to share one
    # reason, so a name the collector held nothing about read as a searched
    # no-news move - the reading the REVERSE sentence is licensed for. Only a
    # rejection earns it; an empty evidence set says nothing about the news.
    sigma = explanation.significance
    size = f"idiosyncratic move of {sigma_phrase(sigma.standardised_ar)}" if sigma else "move"
    if not candidates:
        explanation.verdict = Verdict.NO_IDENTIFIED_CATALYST
        explanation.reason = (
            f"{size}, and no candidate cause was offered to weigh. An empty evidence set is "
            "not a rejection: whether this was a no-news move cannot be told from it"
        )
        return explanation
    if candidates[0].score < SCORE_THRESHOLD:
        top = candidates[0]
        explanation.verdict = Verdict.NO_IDENTIFIED_CATALYST
        explanation.reason = (
            f"{size}; {len(candidates)} candidate(s) weighed and none cleared {SCORE_THRESHOLD} "
            f"(best: {top.cause_type} at lag {top.lag_sessions}, {top.score:.2f}). "
            "No-news moves of this size have historically tended to REVERSE, whereas "
            "news-driven moves tend to drift - the distinction is the information here"
        )
        return explanation

    top = candidates[0]
    close = [c for c in candidates if c.score >= top.score * 0.8]
    explanation.verdict = Verdict.EXPLAINED if top.score >= 0.5 else Verdict.PARTIALLY_EXPLAINED
    if len(close) > 1:
        explanation.reason = (
            f"{len(close)} candidates score within 20% of each other and are all shown; "
            "collapsing to one would be false precision"
        )
    else:
        explanation.reason = (
            f"{top.cause_type} at lag {top.lag_sessions} scores {top.score:.2f}"
            + (
                f"; historically worth a median {top.base_rate.median_car:+.2%} "
                f"(n={top.base_rate.n})"
                if top.base_rate
                else ""
            )
        )
    return explanation
