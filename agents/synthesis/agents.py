"""A9-A11, the synthesis layer.

The evidence layer produces facts. This layer produces a position. The seam
matters: docs/01 section 3 says synthesis agents may read findings but may NOT
retrieve their own evidence to patch a gap. A thesis with a hole stays holed,
and the hole is named in the memo.

A11 is the exception that proves it. The red team retrieves - but only from a
config that EXCLUDES what A10 used, because a challenge built from the bull's
own sources is not a challenge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from agents.base import Agent, AgentContext, Finding
from core.llm.tiers import TaskClass
from engines.attribution.decompose import (
    Component,
    MoveExplanation,
    Verdict,
    decompose,
    long_horizon_decompose,
)
from engines.events.catalyst import CandidateCause, attach, score_candidates
from engines.events.taxonomy import BaseRateTable, Event


class A9Attribution(Agent):
    """Why it moved. Decomposition first, story second, never the reverse."""

    agent_id = "a9_attribution"
    collections = ("kb_factor_returns",)
    tools = ("decompose", "abnormal_return", "candidate_causes", "long_horizon_decompose")
    tier = TaskClass.ATTRIBUTION_HARD

    def run(
        self,
        instrument_id: str,
        window: tuple[date, date],
        realised_local: float,
        event_market: float,
        event_sector: float,
        event_styles: dict[str, float],
        fx_return: float,
        fit,
        events: list[Event] | None = None,
        table: BaseRateTable | None = None,
        session_lag: dict[str, int] | None = None,
        market: str = "XKLS",
        peers: set[str] | None = None,
        source_kind: dict[str, str] | None = None,
        sector_wide: set[str] | None = None,
        base_currency: str = "MYR",
    ) -> list[Finding]:
        self._guard_tool("decompose")
        exp = decompose(
            instrument_id, window, event_market, event_sector, event_styles,
            realised_local, fx_return, fit, base_currency=base_currency,
        )

        # The cause hunt is gated on the decomposition, not on whether the move
        # felt large. This is the single most important ordering in the system.
        if exp.needs_cause_hunt() and events and table is not None:
            self._guard_tool("candidate_causes")
            cands = score_candidates(
                exp, events, table, session_lag or {}, market,
                peers=peers, source_kind=source_kind, sector_wide=sector_wide,
            )
            exp = attach(exp, cands)
        else:
            exp = attach(exp, [])

        return self._to_findings(exp)

    def _to_findings(self, exp: MoveExplanation) -> list[Finding]:
        out = [Finding(
            self.agent_id, "decomposition",
            self.narrate(exp),
            numbers={
                "total_return_base": exp.total_return_base,
                "abnormal_return": exp.abnormal_return,
                "unexplained_share": exp.unexplained_share,
                **{c.component.value: c.contribution for c in exp.components},
            },
            caveats=([exp.reason] if exp.reason else []),
        )]
        for c in exp.candidates:
            out.append(Finding(
                self.agent_id, "candidate_cause",
                f"{c.cause_type}: {c.description} ({c.lag_sessions} sessions from the move)",
                numbers={"score": c.score},
                as_of=c.occurred_at,
                caveats=self._candidate_caveats(c),
            ))
        return out

    @staticmethod
    def _candidate_caveats(c: CandidateCause) -> list[str]:
        out: list[str] = []
        if c.base_rate is None:
            out.append("no historical base rate for this event type in this market")
        elif c.base_rate.n < 30:
            out.append(f"base rate rests on only {c.base_rate.n} observations")
        if c.lag_sessions < 0:
            out.append("event post-dates the move; treated as leakage, not causation")
        return out

    @staticmethod
    def narrate(exp: MoveExplanation) -> str:
        """The sentence a human reads. Numbers before nouns."""
        pct = lambda x: f"{x * 100:+.1f}%"
        parts = [f"{exp.instrument_id} returned {pct(exp.total_return_base)} in {exp.base_currency}"]
        for c in exp.components:
            if c.component is Component.IDIOSYNCRATIC:
                continue
            if abs(c.contribution) > 0.0005:
                parts.append(f"{c.component.value} {pct(c.contribution)}")
        idio = exp.component(Component.IDIOSYNCRATIC)
        if idio is not None:
            parts.append(f"stock-specific {pct(idio.contribution)}")

        head = "; ".join(parts)
        if exp.verdict is Verdict.MARKET_DRIVEN:
            return f"{head}. This was the market, not the company. No cause was sought."
        if exp.verdict is Verdict.NOT_SIGNIFICANT:
            return f"{head}. Within normal daily variation for this name; no explanation is required."
        if exp.verdict is Verdict.ATTRIBUTION_UNAVAILABLE:
            return f"{head}. Attribution unavailable: {exp.reason}"
        if exp.verdict is Verdict.NO_IDENTIFIED_CATALYST:
            return (f"{head}. Statistically significant and unexplained: no catalyst cleared "
                    f"the evidence threshold. {exp.unexplained_share * 100:.0f}% unexplained.")
        top = exp.candidates[0] if exp.candidates else None
        cause = f" Most likely cause: {top.description}." if top else ""
        return (f"{head}.{cause} {exp.unexplained_share * 100:.0f}% of the move remains "
                f"unexplained by the factors and the identified catalysts.")

    def since_purchase(
        self,
        instrument_id: str,
        eps_start: float, eps_end: float,
        multiple_start: float, multiple_end: float,
        cumulative_shareholder_yield: float,
        fx_start: float, fx_end: float,
        years: float,
    ) -> list[Finding]:
        """The question an owner actually asks: did I make money on the business
        or on the re-rating? docs/03 section 5."""
        self._guard_tool("long_horizon_decompose")
        lh = long_horizon_decompose(
            eps_start, eps_end, multiple_start, multiple_end,
            cumulative_shareholder_yield, fx_start, fx_end, years,
        )
        driver = max(
            (("earnings growth", lh.eps_growth), ("multiple change", lh.multiple_change),
             ("shareholder yield", lh.shareholder_yield), ("currency", lh.fx)),
            key=lambda kv: abs(kv[1]),
        )
        caveat = []
        if driver[0] == "multiple change":
            caveat.append("the return came from sentiment re-rating, which is not repeatable "
                          "and can reverse without the business changing")
        return [Finding(
            self.agent_id, "long_horizon",
            f"over {years:.1f} years {instrument_id} returned {lh.total_return * 100:+.1f}%, "
            f"driven mainly by {driver[0]} ({driver[1] * 100:+.1f}%)",
            numbers={
                "total": lh.total_return, "eps_growth": lh.eps_growth,
                "multiple_change": lh.multiple_change,
                "shareholder_yield": lh.shareholder_yield, "fx": lh.fx,
            },
            caveats=caveat,
        )]


class Stance(str, Enum):
    ACCUMULATE = "accumulate"
    HOLD = "hold"
    AVOID = "avoid"
    NO_VIEW = "no_view"


@dataclass(frozen=True)
class Breaker:
    """A falsifier that a machine can check. Prose does not qualify."""

    statement: str
    query: str                       # must be executable against a named store
    store: str
    check_by: date | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError(
                f"breaker {self.statement!r} has no executable query. docs/04 section 6: "
                "a thesis breaker that cannot be checked is a wish, not a breaker."
            )


@dataclass
class Thesis:
    instrument_id: str
    stance: Stance
    horizon_months: int
    in_one_sentence: str
    what_must_be_true: list[str]
    breakers: list[Breaker]
    valuation_range: tuple[Decimal, Decimal] | None
    key_uncertainties: list[str]
    gaps: list[str] = field(default_factory=list)
    supporting: list[Finding] = field(default_factory=list)
    confidence: float = 0.5

    def is_actionable(self) -> bool:
        """docs/04 section 6.2: no breakers, no position. Ever."""
        return self.stance is not Stance.NO_VIEW and len(self.breakers) >= 2


class A10Thesis(Agent):
    """Assembles findings into a stance. Cannot retrieve. Cannot fill gaps."""

    agent_id = "a10_thesis"
    collections = ()
    tools = ("compose", "check_coverage")
    tier = TaskClass.THESIS_SYNTHESIS

    REQUIRED_EVIDENCE = ("a1_fundamentals", "a2_valuation", "a5_catalyst_events", "a6_macro_regime")

    def retrieve(self, *a, **kw):  # noqa: D102 - deliberate hard block
        raise PermissionError(
            "a10_thesis may not retrieve. It synthesises what the evidence layer "
            "found; a gap is reported as a gap (docs/01 section 3)."
        )

    def run(
        self,
        instrument_id: str,
        findings: list[Finding],
        horizon_months: int = 12,
        valuation_range: tuple[Decimal, Decimal] | None = None,
        proposed_stance: Stance = Stance.HOLD,
        breakers: list[Breaker] | None = None,
    ) -> list[Finding]:
        self._guard_tool("compose")
        gaps = self.coverage_gaps(findings)
        breakers = list(breakers or [])

        stance = proposed_stance
        notes: list[str] = []
        if gaps:
            notes.append("evidence gaps: " + ", ".join(gaps))
        if len(breakers) < 2:
            stance = Stance.NO_VIEW
            notes.append("fewer than two falsifiable breakers; no stance may be taken")
        if "a1_fundamentals" in gaps or "a2_valuation" in gaps:
            if stance is Stance.ACCUMULATE:
                stance = Stance.NO_VIEW
                notes.append("cannot accumulate without fundamentals and a valuation range")

        unresolved = [f for f in findings
                      if f.kind == "decomposition"
                      and f.numbers.get("unexplained_share", 0.0) > 0.7]
        if unresolved and stance is Stance.ACCUMULATE:
            notes.append("recent move is largely unexplained; entry is staged, not full size")

        conf = self.confidence(findings, gaps, breakers)
        thesis = Thesis(
            instrument_id=instrument_id,
            stance=stance,
            horizon_months=horizon_months,
            in_one_sentence=self.one_sentence(instrument_id, stance, findings),
            what_must_be_true=[b.statement for b in breakers],
            breakers=breakers,
            valuation_range=valuation_range,
            key_uncertainties=[c for f in findings for c in f.caveats],
            gaps=gaps,
            supporting=list(findings),
            confidence=conf,
        )
        self.last = thesis
        return [Finding(
            self.agent_id, "thesis", thesis.in_one_sentence,
            numbers={"confidence": conf, "breakers": float(len(breakers))},
            citations=[c for f in findings for c in f.citations],
            caveats=notes,
        )]

    def coverage_gaps(self, findings: list[Finding]) -> list[str]:
        self._guard_tool("check_coverage")
        seen = {f.agent for f in findings if f.kind != "unavailable"}
        return [a for a in self.REQUIRED_EVIDENCE if a not in seen]

    @staticmethod
    def confidence(findings: list[Finding], gaps: list[str], breakers: list[Breaker]) -> float:
        """Confidence falls with gaps and caveats. It never rises with word count."""
        base = 0.75
        base -= 0.12 * len(gaps)
        base -= 0.02 * sum(len(f.caveats) for f in findings)
        if len(breakers) < 2:
            base = min(base, 0.2)
        return max(0.05, min(0.9, base))

    @staticmethod
    def one_sentence(instrument_id: str, stance: Stance, findings: list[Finding]) -> str:
        if stance is Stance.NO_VIEW:
            return f"No view on {instrument_id}: the evidence does not support a stance."
        return (f"{stance.value.capitalize()} {instrument_id} on {len(findings)} evidence "
                f"findings, subject to the breakers below.")


@dataclass(frozen=True)
class Challenge:
    kind: str
    statement: str
    severity: str            # fatal | material | minor
    evidence: tuple[str, ...] = ()


class A11RedTeam(Agent):
    """Argues the other side. Retrieves from sources A10 did NOT use."""

    agent_id = "a11_red_team"
    collections = ("kb_failures", "kb_news", "kb_filings")
    tools = ("retrieve", "find_disconfirming", "check_crowding")
    tier = TaskClass.RED_TEAM

    #: docs/04 section 7. The list is fixed so the challenge cannot be tuned to pass.
    STANDING_CHALLENGES = (
        ("consensus", "This thesis is the consensus view and is already in the price."),
        ("mechanism", "The claimed causal mechanism has no evidence, only correlation."),
        ("survivorship", "The comparison set excludes the companies that failed."),
        ("accounting", "Earnings quality has not been checked against cash flow."),
        ("regime", "The evidence comes from one regime and is assumed to hold in another."),
        ("liquidity", "The position cannot be exited at the size assumed."),
    )

    def run(self, thesis: Thesis, excluded_sources: set[str] | None = None) -> list[Finding]:
        self._guard_tool("find_disconfirming")
        excluded = excluded_sources or {c.source for f in thesis.supporting for c in f.citations}
        self.excluded = excluded
        challenges = list(self.structural_challenges(thesis))

        for kind, statement in self.STANDING_CHALLENGES:
            if self._applies(kind, thesis):
                challenges.append(Challenge(kind, statement, "material"))

        return [Finding(
            self.agent_id, "challenge", c.statement,
            numbers={"severity_rank": {"fatal": 3.0, "material": 2.0, "minor": 1.0}[c.severity]},
            caveats=[f"challenge type: {c.kind}"],
        ) for c in challenges]

    def structural_challenges(self, thesis: Thesis):
        if thesis.gaps:
            yield Challenge("coverage",
                            "The thesis rests on incomplete evidence: " + ", ".join(thesis.gaps),
                            "fatal" if len(thesis.gaps) > 2 else "material",
                            tuple(thesis.gaps))
        if thesis.valuation_range is None and thesis.stance is Stance.ACCUMULATE:
            yield Challenge("valuation",
                            "Accumulating with no valuation range means paying any price.",
                            "fatal")
        if len(thesis.breakers) < 2:
            yield Challenge("falsifiability",
                            "Fewer than two checkable breakers: this thesis cannot be wrong, "
                            "which means it cannot be right either.",
                            "fatal")
        for b in thesis.breakers:
            if b.check_by is None:
                yield Challenge("breaker_timing",
                                f"Breaker {b.statement!r} has no review date and will never fire.",
                                "minor", (b.statement,))
        if thesis.confidence > 0.7 and thesis.gaps:
            yield Challenge("calibration",
                            f"Confidence of {thesis.confidence:.0%} is not earned with "
                            f"{len(thesis.gaps)} evidence gaps outstanding.",
                            "material")

    @staticmethod
    def _applies(kind: str, thesis: Thesis) -> bool:
        agents_seen = {f.agent for f in thesis.supporting}
        if kind == "accounting":
            return not any(f.kind == "quality_flag" for f in thesis.supporting)
        if kind == "liquidity":
            return thesis.stance is Stance.ACCUMULATE
        if kind == "regime":
            return "a6_macro_regime" not in agents_seen
        if kind == "mechanism":
            return any(f.kind == "candidate_cause" for f in thesis.supporting)
        return kind == "consensus" and thesis.stance is not Stance.NO_VIEW

    def verdict(self, challenges: list[Finding]) -> str:
        """A fatal challenge kills the thesis. It does not become a caveat."""
        if any(f.numbers.get("severity_rank") == 3.0 for f in challenges):
            return "thesis_rejected"
        if sum(1 for f in challenges if f.numbers.get("severity_rank") == 2.0) >= 3:
            return "thesis_weakened"
        return "thesis_survives"
