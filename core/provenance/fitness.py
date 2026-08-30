"""The nightly fitness function.

docs/01 section 10 specifies it and nothing computed it:

    fitness = w1·groundedness + w2·citation_validity + w3·refusal_precision
            + w4·attribution_accuracy + w5·forecast_calibration
            - w6·p95_latency - w7·cost_per_query

THE POINT OF THIS MODULE IS WHAT IT REFUSES TO DO. Three of those seven terms
cannot be computed from anything this system records today, and averaging the
four that can would produce a number that looks like fitness, moves when the
system changes, and is not fitness. So a headline score is emitted only when
EVERY term is available, and the rest of the time the report is a list of what
is missing and what would supply it.

That list is the useful output right now. It says, precisely, that the system
cannot yet score itself and names the two artefacts that would let it.

Computable from the provenance ledger alone, today:

    groundedness       claims that survived the output gate / claims proposed
    citation_validity  surviving claims that carry a citation
    cost_per_query     ledger cost divided by distinct runs

Needs the forward record P16 opens (elapsed time, not effort):

    forecast_calibration   Brier over graded predictions

Needs a human-labelled set that does not exist:

    attribution_accuracy   ~200 historical moves with undisputed causes
    refusal_precision      refusals labelled warranted or not

Needs latency capture the ledger does not do:

    p95_latency        core/trace records duration_ms per span; the ledger does
                       not. Pass them in, or accept the term as unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

#: Weights are a CHOICE, not a measurement, and they are written here rather
#: than buried so that changing them is a visible act. Groundedness and citation
#: validity carry the most because they are the property the whole system exists
#: to hold; a fast cheap system that cites nothing has failed at its only job.
DEFAULT_WEIGHTS: dict[str, float] = {
    "groundedness": 0.25,
    "citation_validity": 0.25,
    "refusal_precision": 0.10,
    "attribution_accuracy": 0.15,
    "forecast_calibration": 0.15,
    "p95_latency": -0.05,
    "cost_per_query": -0.05,
}

#: Above this a query is expensive enough that the penalty term should bite.
COST_REFERENCE_MYR = Decimal("1.00")
#: Above this a response is slow enough to matter to a person waiting.
LATENCY_REFERENCE_MS = 30_000.0


@dataclass(frozen=True)
class Term:
    name: str
    weight: float
    value: float | None = None
    unavailable_because: str | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None

    def describe(self) -> str:
        if not self.available:
            return f"  {self.name:22} UNAVAILABLE  {self.unavailable_because}"
        return (f"  {self.name:22} {self.value:>6.3f}  (w {self.weight:+.2f})"
                f"{'  ' + self.detail if self.detail else ''}")


@dataclass
class Fitness:
    terms: list[Term] = field(default_factory=list)
    window_days: int = 30

    @property
    def missing(self) -> list[Term]:
        return [t for t in self.terms if not t.available]

    @property
    def score(self) -> float | None:
        """None unless every term is available. See the module docstring.

        Returning a partial sum here would be the whole mistake: it is a number,
        it moves, and it is not fitness.
        """
        if self.missing:
            return None
        return sum(t.weight * t.value for t in self.terms)

    def describe(self) -> str:
        lines = [f"FITNESS over the last {self.window_days} days", "-" * 66]
        lines += [t.describe() for t in self.terms]
        lines.append("-" * 66)
        if self.score is not None:
            lines.append(f"  {'FITNESS':22} {self.score:>6.3f}")
            return "\n".join(lines)
        lines.append(f"  NO SCORE. {len(self.missing)} of {len(self.terms)} terms "
                     f"cannot be computed, and averaging the rest would produce a "
                     f"number that looks like fitness and is not.")
        lines.append("")
        lines.append("  To score itself the system needs:")
        for t in self.missing:
            lines.append(f"    - {t.name}: {t.unavailable_because}")
        return "\n".join(lines)


def _normalise_penalty(value: float, reference: float) -> float:
    """Map a cost or latency onto [0, 1] where 1 is 'at or past the reference'.

    Bounded so one absurd outlier cannot dominate a weighted sum - an eight
    minute request should not make the month's fitness negative on its own.
    """
    if reference <= 0:
        return 0.0
    return max(0.0, min(1.0, value / reference))


def compute(ledger, *, now: datetime | None = None, window_days: int = 30,
            calibration=None, latencies_ms=None, labelled_moves=None,
            labelled_refusals=None, weights=None) -> Fitness:
    """Score what can be scored; name what cannot.

    `calibration`      an agents.learning.reflection.Calibration, from graded
                       predictions. P16 opens this and elapsed time is the only
                       way to get it.
    `latencies_ms`     durations, e.g. from a core.trace run.
    `labelled_moves`   (predicted_cause, true_cause) pairs for A9.
    `labelled_refusals` (was_refused, should_have_been) pairs.
    """
    now = now or datetime.now(timezone.utc)
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    since = now - timedelta(days=window_days)
    terms: list[Term] = []

    rows = ledger._rows(
        "SELECT survived, citations_json FROM claims WHERE at >= ?",
        (since.isoformat(),))
    proposed = len(rows)
    survived = [r for r in rows if r["survived"]]

    if proposed:
        terms.append(Term("groundedness", w["groundedness"],
                          len(survived) / proposed,
                          detail=f"{len(survived)}/{proposed} claims survived the gate"))
        cited = sum(1 for r in survived if (r["citations_json"] or "[]") != "[]")
        terms.append(Term(
            "citation_validity", w["citation_validity"],
            cited / len(survived) if survived else 0.0,
            detail=f"{cited}/{len(survived)} surviving claims carry a citation"))
    else:
        why = (f"no claims recorded in the last {window_days} days. The ledger "
               f"logs them; nothing has run.")
        terms.append(Term("groundedness", w["groundedness"], unavailable_because=why))
        terms.append(Term("citation_validity", w["citation_validity"],
                          unavailable_because=why))

    if labelled_refusals:
        warranted = sum(1 for _, should in labelled_refusals if should)
        terms.append(Term("refusal_precision", w["refusal_precision"],
                          warranted / len(labelled_refusals),
                          detail=f"{warranted}/{len(labelled_refusals)} refusals warranted"))
    else:
        terms.append(Term(
            "refusal_precision", w["refusal_precision"],
            unavailable_because="no labelled refusals. Needs a person to mark a "
                                "sample warranted or not; the system cannot grade "
                                "its own refusals without beginning to justify them."))

    if labelled_moves:
        hits = sum(1 for pred, true in labelled_moves if pred == true)
        terms.append(Term("attribution_accuracy", w["attribution_accuracy"],
                          hits / len(labelled_moves),
                          detail=f"{hits}/{len(labelled_moves)} causes matched"))
    else:
        terms.append(Term(
            "attribution_accuracy", w["attribution_accuracy"],
            unavailable_because="no labelled move set. docs/01 section 10 specifies "
                                "~200 historical moves with undisputed causes "
                                "(earnings dates, announced M&A, index rebalances). "
                                "It does not exist and is a day of human work."))

    if calibration is not None and getattr(calibration, "n", 0) > 0:
        # Brier is an error: 0 is perfect, so fitness takes 1 - brier.
        terms.append(Term("forecast_calibration", w["forecast_calibration"],
                          1.0 - calibration.brier,
                          detail=f"1 - Brier over {calibration.n} graded predictions"))
    else:
        terms.append(Term(
            "forecast_calibration", w["forecast_calibration"],
            unavailable_because="no graded predictions. P16 opens this and it is "
                                "elapsed time, not effort - a forward record "
                                "cannot be back-filled, only waited for."))

    latencies_ms = latencies_ms or ledger.latencies_between(since, now)
    if latencies_ms:
        ordered = sorted(latencies_ms)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        terms.append(Term("p95_latency", w["p95_latency"],
                          _normalise_penalty(p95, LATENCY_REFERENCE_MS),
                          detail=f"p95 {p95:.0f} ms of {LATENCY_REFERENCE_MS:.0f} ms budget"))
    else:
        terms.append(Term(
            "p95_latency", w["p95_latency"],
            unavailable_because=f"no timed calls in the last {window_days} days. "
                                f"The ledger records latency_ms per call now; "
                                f"rows written before it existed read 0 and are "
                                f"excluded rather than counted as instant."))

    runs = ledger.runs_between(since, now)
    spend = ledger.cost_since(since)
    if runs:
        per = spend / Decimal(len(runs))
        terms.append(Term("cost_per_query", w["cost_per_query"],
                          _normalise_penalty(float(per), float(COST_REFERENCE_MYR)),
                          detail=f"RM {per:.4f} over {len(runs)} runs"))
    else:
        terms.append(Term(
            "cost_per_query", w["cost_per_query"],
            unavailable_because=f"no runs with a run_id in the last {window_days} "
                                f"days. Cost is recorded; nothing grouped it."))

    return Fitness(terms, window_days)
