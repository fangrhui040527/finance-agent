"""P15, the surface the user actually reads.

docs/01 section 8. Three views, and the design constraint behind all of them is
the same: the interface must make the honest answer as easy to show as the
confident one. A UI that has a slot for "the reason" will get a reason invented
to fill it, so every view here renders "no identified catalyst" and "insufficient
evidence" as first-class states with their own layout, not as an empty string.

Text renderers, deliberately. They are testable, they paste into a chat, and the
web view is a stylesheet over the same structures rather than a second source of
truth about what the system believes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from agents.base import Finding
from engines.attribution.decompose import Component, MoveExplanation, Verdict

BAR_WIDTH = 28


def _bar(value: float, scale: float, width: int = BAR_WIDTH) -> str:
    """Signed bar centred on zero. Negative left, positive right."""
    if scale <= 0:
        return " " * (2 * width + 1)
    half = max(1, width)
    n = min(half, int(round(abs(value) / scale * half)))
    if value >= 0:
        return " " * half + "|" + "#" * n + " " * (half - n)
    return " " * (half - n) + "#" * n + "|" + " " * half


def decomposition_bars(exp: MoveExplanation) -> str:
    """The core view. Components as signed bars, unexplained share always shown.

    docs/03 section 6: the unexplained share is never hidden, because hiding it
    is how a system trains its user to believe it knows more than it does.
    """
    if not exp.components:
        return f"{exp.instrument_id}  attribution unavailable\n  {exp.reason}"

    scale = max(abs(c.contribution) for c in exp.components) or 0.01
    lines = [
        f"{exp.instrument_id}   {exp.window[0]} to {exp.window[1]}   "
        f"{exp.total_return_base * 100:+.2f}% ({exp.base_currency})",
        "",
    ]
    for c in exp.components:
        label = c.component.value.replace("_", " ")
        beta = f"  b={c.beta:+.2f}" if c.beta is not None else ""
        lines.append(
            f"  {label:<14}{c.contribution * 100:>7.2f}pp  {_bar(c.contribution, scale)}{beta}"
        )
    lines.append("")
    lines.append(
        f"  unexplained    {exp.unexplained_share * 100:>6.0f}%   verdict: {exp.verdict.value}"
    )
    if exp.reason:
        lines.append(f"  {exp.reason}")
    # Branch on the verdict, never on whether the candidate list is empty. A
    # candidate that was scored and REJECTED must not be rendered as though it
    # were the cause - that is exactly how a rejected story becomes the headline.
    if exp.verdict is Verdict.NO_IDENTIFIED_CATALYST:
        lines.append("")
        lines.append("  no catalyst cleared the evidence threshold. This is a finding,")
        lines.append("  not a gap: unexplained moves of this size historically reverse")
        lines.append("  more often than news-driven ones continue.")
        if exp.candidates:
            lines.append("")
            lines.append(f"  {len(exp.candidates)} candidate(s) were scored and rejected:")
            for c in exp.candidates[:4]:
                lines.append(
                    f"    {c.score:.2f}  {c.description}  (+{c.lag_sessions}s)  BELOW THRESHOLD"
                )
    elif exp.candidates:
        lines.append("")
        lines.append("  candidate causes, scored against the residual:")
        for c in exp.candidates[:4]:
            lines.append(f"    {c.score:.2f}  {c.description}  (+{c.lag_sessions}s)")
    return "\n".join(lines)


@dataclass(frozen=True)
class Annotation:
    """One marker on the price chart. Only citable events earn one."""

    at: date
    label: str
    kind: str  # event | breaker | entry | stop | review
    score: float | None = None
    source: str | None = None


def annotated_chart(
    instrument_id: str,
    bars: list[tuple[date, float]],
    annotations: list[Annotation],
    height: int = 12,
    width: int = 64,
) -> str:
    """Sparkline plus a legend of markers.

    The rule that matters is not the drawing: an annotation exists only where a
    citable event exists. The chart may not carry an unsourced arrow, because an
    arrow is a claim.
    """
    if len(bars) < 2:
        return f"{instrument_id}: not enough history to plot"

    for a in annotations:
        if a.kind == "event" and not a.source:
            raise ValueError(
                f"annotation {a.label!r} has no source. An arrow on a chart is a claim "
                "and every claim carries a citation (docs/02 section 6)."
            )

    step = max(1, len(bars) // width)
    sampled = bars[::step][-width:]
    values = [v for _, v in sampled]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0

    grid = [[" "] * len(sampled) for _ in range(height)]
    for x, v in enumerate(values):
        y = height - 1 - int((v - lo) / span * (height - 1))
        grid[y][x] = "*"

    marks = [" "] * len(sampled)
    dates = [d for d, _ in sampled]
    legend: list[str] = []
    for i, a in enumerate(annotations, start=1):
        nearest = min(range(len(dates)), key=lambda j: abs((dates[j] - a.at).days))
        marks[nearest] = str(i)[-1]
        score = f"  score {a.score:.2f}" if a.score is not None else ""
        src = f"  [{a.source}]" if a.source else ""
        legend.append(f"  {i}. {a.at}  {a.kind:<8}{a.label}{score}{src}")

    out = [f"{instrument_id}   {dates[0]} to {dates[-1]}   high {hi:,.2f}  low {lo:,.2f}", ""]
    out += ["  " + "".join(row) for row in grid]
    out.append("  " + "".join(marks))
    if legend:
        out.append("")
        out += legend
    return "\n".join(out)


def thesis_memo(
    instrument_id: str,
    stance: str,
    one_sentence: str,
    what_must_be_true: list[str],
    breakers: list[tuple[str, str, date | None]],
    valuation_range: tuple[Decimal, Decimal] | None,
    uncertainties: list[str],
    gaps: list[str],
    challenges: list[str],
    confidence: float,
) -> str:
    """docs/04 section 8. The gaps section is above the conclusion on purpose."""
    lines = [
        f"THESIS  {instrument_id}   stance: {stance}   confidence {confidence:.0%}",
        "=" * 68,
        "",
        one_sentence,
        "",
    ]
    if gaps:
        lines += ["EVIDENCE GAPS  (read these before the rest)"]
        lines += [f"  - {g}" for g in gaps]
        lines.append("")
    if valuation_range:
        lo, hi = valuation_range
        lines += [
            f"VALUATION RANGE  {lo:,.2f} to {hi:,.2f}",
            "  A range, not a target. The width is the honest part.",
            "",
        ]
    if what_must_be_true:
        lines += ["WHAT MUST BE TRUE"]
        lines += [f"  - {w}" for w in what_must_be_true]
        lines.append("")
    lines += ["BREAKERS  (written before entry, checkable by machine)"]
    for statement, query, check_by in breakers:
        when = f"  review {check_by}" if check_by else "  NO REVIEW DATE"
        lines.append(f"  - {statement}{when}")
        lines.append(f"      check: {query}")
    lines.append("")
    if challenges:
        lines += ["THE CASE AGAINST"]
        lines += [f"  - {c}" for c in challenges]
        lines.append("")
    if uncertainties:
        lines += ["KEY UNCERTAINTIES"]
        lines += [f"  - {u}" for u in uncertainties]
        lines.append("")
    lines.append("This is analysis, not advice, and this system cannot place orders.")
    return "\n".join(lines)


def daily_brief(
    as_of: datetime,
    holdings_moves: list[MoveExplanation],
    breakers_due: list[tuple[str, str, date]],
    risk_findings: list[Finding],
    watchlist_events: list[tuple[str, str, float]] | None = None,
) -> str:
    """What changed, what needs a decision, what does not.

    Ordered by what needs action, not by what is interesting. The 'nothing needs
    your attention' state is rendered explicitly - a brief that always finds
    something urgent trains the user to trade for no reason.
    """
    lines = [f"DAILY BRIEF   {as_of:%Y-%m-%d %H:%M %Z}".rstrip(), "=" * 68, ""]

    needs_attention = [m for m in holdings_moves if m.needs_cause_hunt()]
    routine = [m for m in holdings_moves if m not in needs_attention]

    if breakers_due:
        lines += ["BREAKERS DUE FOR REVIEW"]
        for instrument, statement, due in breakers_due:
            lines.append(f"  {instrument:<14}{statement}  (due {due})")
        lines.append("")

    if needs_attention:
        lines += ["MOVES THAT ARE NOT THE MARKET"]
        for m in needs_attention:
            idio = m.component(Component.IDIOSYNCRATIC)
            share = f"{idio.contribution * 100:+.1f}pp stock-specific" if idio else ""
            top = m.candidates[0].description if m.candidates else "no identified catalyst"
            lines.append(
                f"  {m.instrument_id:<14}{m.total_return_base * 100:+6.2f}%  {share:<24}{top}"
            )
        lines.append("")

    if routine:
        lines += ["EVERYTHING ELSE MOVED WITH ITS MARKET"]
        names = ", ".join(f"{m.instrument_id} {m.total_return_base * 100:+.1f}%" for m in routine)
        lines.append(f"  {names}")
        lines.append("  No explanation is required for these and none is offered.")
        lines.append("")

    breaches = [f for f in risk_findings if f.kind == "breach"]
    if breaches:
        lines += ["RISK LIMITS"]
        lines += [f"  {f.text}" for f in breaches]
        lines.append("")
    else:
        conc = next((f for f in risk_findings if f.kind == "concentration"), None)
        if conc:
            lines += ["RISK LIMITS", f"  within limits: {conc.text}", ""]

    if watchlist_events:
        lines += ["WATCHLIST"]
        for instrument, what, score in watchlist_events:
            lines.append(f"  {instrument:<14}{what}  (score {score:.2f})")
        lines.append("")

    if not (breakers_due or needs_attention or breaches):
        lines += [
            "Nothing needs a decision today.",
            "That is the most common correct state and it is shown as one.",
            "",
        ]
    return "\n".join(lines)


def refusal_card(reason: str, what_would_help: str) -> str:
    """A refusal gets a layout, so it does not read as a failure."""
    return "\n".join(
        [
            "CANNOT ANSWER THIS",
            "-" * 68,
            f"  {reason}",
            "",
            f"  What would help: {what_would_help}",
        ]
    )
