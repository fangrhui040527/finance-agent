"""The twelve-step workup of docs/04 section 2, run over what the fact book holds.

Every step is always present and always says what it is: ``done`` when the
stored record answered it, ``partial`` when part of the record did, ``unavailable``
when nothing stored can answer it (with the collector that would change that),
``manual`` when the step is a person's judgement the arithmetic cannot make
(business model, the comprehensibility gate, cycle stage), ``not_applicable``
when the archetype rules it out. The arithmetic comes from the slice-2 engines
through the agents that own them, so every figure is point-in-time and every
citation is one the verifier can recheck.

A workup is not a stance. Step 12 suggests breakers a thesis could carry; the
thesis itself is composed by A10 and attacked by A11, elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from agents.base import AgentContext, Finding
from agents.evidence.agents import A1Fundamentals, A2Valuation, A7SectorTechnology
from agents.synthesis.agents import A9Attribution, A11RedTeam, Breaker, Stance, Thesis
from engines.fundamentals.quality import quality_report
from engines.fundamentals.ratios import FILLED_BY, Statements, cagr, fillers
from engines.valuation.cost_of_capital import (
    CostOfCapital,
    CostOfCapitalTable,
    country_of,
    load_table,
)
from engines.valuation.dcf import default_scenarios, terminal_growth_for
from knowledge.graph.ids import display_names
from knowledge.graph.ids import instrument_id as canonical_id
from knowledge.graph.peers import PeerSet, peers_of
from markets.registry import get as market_get
from markets.registry import mic_of

STATUSES = ("done", "partial", "unavailable", "not_applicable", "manual")

#: docs/04 section 2, verbatim order. Two gates sit after steps 3 and 4.
STEPS: tuple[tuple[int, str], ...] = (
    (1, "Identity and universe"),
    (2, "Business model"),
    (3, "Comprehensibility gate"),
    (4, "Earnings quality and solvency gate"),
    (5, "Historical record"),
    (6, "Capital allocation"),
    (7, "Competitive position"),
    (8, "Industry and technology"),
    (9, "Forward drivers"),
    (10, "Valuation"),
    (11, "Return decomposition"),
    (12, "Thesis and breakers"),
)

#: Working thresholds from docs/04 section 3.1.
NET_DEBT_TO_EBITDA_MAX = Decimal(4)
INTEREST_COVER_MIN = Decimal(3)
ROIC_MOAT = Decimal("0.15")
HISTORY_YEARS = 5
DECOMPOSE_MIN_DAYS = 730
BREAKER_REVIEW_DAYS = 90

#: The finnhub snapshot of market capitalisation, in millions of US dollars
#: (its name says so; the collector records no currency on the row).
MARKET_CAP_CONCEPT = "market_cap_musd"
MARKET_CAP_CURRENCY = "USD"


def cite_label(c: Any) -> str:
    """``source:chunk_id`` once: note chunk ids already carry their collection."""
    cid = str(c.chunk_id)
    return cid if cid.startswith(f"{c.source}:") else f"{c.source}:{cid}"


@dataclass
class Step:
    n: int
    title: str
    status: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    missing: tuple[str, ...] = ()
    fills: tuple[str, ...] = ()
    manual_prompt: str = ""
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"step {self.n}: status {self.status!r} is not one of {STATUSES}")

    def text(self) -> str:
        rows = [f"{self.n:>2}. {self.title}  [{self.status}]", f"    {self.summary}"]
        for f in self.findings:
            rows.append("    - " + f.text.replace("\n", "\n      "))
            for c in f.citations:
                rows.append(f"        cites {cite_label(c)}")
        if self.flags:
            rows.append(f"    flags: {', '.join(self.flags)}")
        if self.missing:
            rows.append(f"    missing: {', '.join(self.missing)}")
        if self.fills:
            rows.append(f"    filled by: {', '.join(self.fills)}")
        if self.manual_prompt:
            rows.append(f"    for you: {self.manual_prompt}")
        return "\n".join(rows)


@dataclass
class Workup:
    instrument_id: str
    asof: date
    archetype: str | None
    steps: list[Step]
    gates: dict[str, str]
    breakers: list[Breaker]
    peers: PeerSet | None = None
    coc: CostOfCapital | None = None

    def step(self, n: int) -> Step:
        return self.steps[n - 1]

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for st in self.steps:
            out[st.status] += 1
        return out

    def counts_text(self) -> str:
        c = self.counts()
        return f"{len(self.steps)} steps: " + ", ".join(
            f"{c[s]} {s.replace('_', ' ')}" for s in STATUSES if c[s]
        )


# --- helpers -------------------------------------------------------------------------------


def _pct(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _x(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v:.2f}x"


def _by_year(s: Statements, concept: str) -> dict[date, Decimal]:
    return {f.period_end: f.value for f in s.annual(concept)}


def _instant_at(s: Statements, concept: str, on: date) -> Decimal | None:
    """The balance-sheet line at a fiscal year end (within a month), if stored."""
    best = s.nearest(concept, on)
    return best.value if best else None


def _missing(s: Statements, concepts: tuple[str, ...], annual: bool = False) -> tuple[str, ...]:
    return tuple(c for c in concepts if not (s.annual(c) if annual else s.series(c)))


def _fills(missing: tuple[str, ...], market_sources: str) -> tuple[str, ...]:
    out: list[str] = []
    for c in missing:
        for src in fillers(c):
            if src not in out:
                out.append(src)
    if not out and missing and market_sources:
        out.append(market_sources)
    return tuple(out)


def _statement_sources(instrument_id: str) -> str:
    try:
        mic = mic_of(instrument_id)
    except ValueError:
        return ""
    if mic in ("XNAS", "XNYS"):
        return "sec_xbrl (keyless, weekly), fmp, eodhd (EODHD_API_KEY)"
    if mic == "XKLS":
        return "eodhd on its Fundamentals plan; no free source carries Bursa statements"
    if mic == "XTAI":
        return "finmind, twse_openapi (partial lines), eodhd on its Fundamentals plan"
    return "no statement source is catalogued for this market"


def _receivables_run(s: Statements) -> tuple[Decimal, Decimal] | None:
    """Receivables growth against revenue growth, latest quarter on a year earlier."""
    rec, rev = s.latest("receivables"), s.latest("revenue")
    if rec is None or rev is None or rec.value <= 0 or rev.value <= 0:
        return None
    rec0, rev0 = s.year_earlier("receivables", rec), s.year_earlier("revenue", rev)
    if rec0 is None or rev0 is None or rec0.value <= 0 or rev0.value <= 0:
        return None
    return rec.value / rec0.value - 1, rev.value / rev0.value - 1


def _market_cap(book: Any, s: Statements, iid: str, asof: date) -> tuple[Decimal | None, str]:
    """The market cap knowable on `asof`, in the statements' units, or None and why.

    Altman's MVE/TL divides it by total liabilities, so it must be in the same
    currency and scale: the snapshot is millions (x 1e6 to units), and a cap in
    one currency over liabilities in another is refused rather than divided.
    """
    cap = book.latest(iid, MARKET_CAP_CONCEPT, asof=asof)
    if cap is None or cap.value is None:
        return None, ""
    have = cap.currency or MARKET_CAP_CURRENCY
    _, inputs = s.balance("total_liabilities")
    stated = {f.currency for f in inputs.values()}
    if inputs and stated != {have}:
        named = ", ".join(sorted(c or "an unrecorded currency" for c in stated))
        return None, f"market cap not used: stored in {have}, total liabilities in {named}"
    return (
        cap.value * Decimal(1_000_000),
        f"market cap {cap.value:,.0f}m {have} known {cap.known_at}",
    )


# --- the steps -----------------------------------------------------------------------------


def _identity(iid: str) -> Step:
    n, title = STEPS[0]
    try:
        mic = mic_of(iid)
        adapter = market_get(mic)
    except (ValueError, KeyError) as e:
        return Step(n, title, "unavailable", f"market not resolved: {e}")
    canon = canonical_id(iid) or iid
    name = display_names().get(canon, iid)
    try:
        lot = adapter.lot_size(iid)
    except (KeyError, ValueError, NotImplementedError):
        lot = 0
    try:
        bps = adapter.fee_schedule.round_trip_bps(Decimal(10000))
        fee = f"round trip {bps:.0f} bps on a 10,000 {adapter.currency} ticket"
    except (AttributeError, TypeError, ValueError):
        fee = "fee schedule unavailable"
    summary = (
        f"{name} ({iid}) on {mic}: {adapter.currency}, lot {lot or 'n/a'}, "
        f"T+{adapter.settlement_days}, {adapter.accounting_standard.value}; {fee}"
    )
    return Step(n, title, "done", summary)


def _business_model(s: Statements) -> Step:
    n, title = STEPS[1]
    stored = [c for c in FILLED_BY if s.series(c) or s.annual(c)]
    return Step(
        n,
        title,
        "manual",
        f"how it makes money is not in the figures: {len(stored)} statement concepts stored "
        f"({', '.join(stored[:6])}{'...' if len(stored) > 6 else ''}); segments, geography, "
        f"customers and unit economics come from the annual report",
        manual_prompt="write the segments, the geography, who pays, and the unit economics, "
        "each with the filing page it comes from",
    )


def _comprehensibility() -> Step:
    n, title = STEPS[2]
    return Step(
        n,
        title,
        "manual",
        "can the revenue driver be stated in one sentence? A no stops the workup here: "
        "outside the circle of competence, logged, not scored",
        manual_prompt="one sentence naming what drives revenue; if it will not fit, stop",
    )


def _quality_gate(
    a1: A1Fundamentals,
    a11: A11RedTeam,
    book: Any,
    s: Statements,
    iid: str,
    asof: date,
    sources: str,
    archetype: str | None,
) -> tuple[Step, list[Finding]]:
    n, title = STEPS[3]
    # Both calls once went without the market cap or the archetype, so Altman Z
    # was "4 of 5 components; missing market_cap" for NVDA with 24 snapshots
    # stored, and its bank/insurer/REIT refusal never ran.
    market_cap, cap_note = _market_cap(book, s, iid, asof)
    scores = quality_report(s, market_cap, archetype)
    findings = a1.quality_scores(iid, asof, market_cap, archetype)
    flags = [f.text.split(":")[0] for f in findings if f.kind == "quality_flag"]
    flag_findings = [f for f in findings if f.kind == "quality_flag"]

    ratios = {f.text.split(":")[0]: f for f in a1.ratio_sheet(iid, asof)[1:]}
    checks: list[str] = [cap_note] if cap_note else []
    for name, worst, floor in (
        ("net_debt_to_ebitda", NET_DEBT_TO_EBITDA_MAX, False),
        ("interest_cover", INTEREST_COVER_MIN, True),
    ):
        f = ratios.get(name)
        if f is None:
            continue
        v = Decimal(str(f.numbers.get(name, 0)))
        breached = v < worst if floor else v > worst
        if breached:
            flags.append(name)
            flag_findings.append(f)
        checks.append(f"{name} {v:.2f}x ({'breach' if breached else 'ok'})")
    run = _receivables_run(s)
    if run is not None:
        rec_g, rev_g = run
        if rec_g > rev_g + Decimal("0.10"):
            flags.append("receivables_run")
            flag_findings.append(
                Finding(
                    a1.agent_id,
                    "ratio",
                    f"receivables grew {_pct(rec_g)} against revenue {_pct(rev_g)} year on year",
                    numbers={"receivables_growth": float(rec_g), "revenue_growth": float(rev_g)},
                )
            )
            checks.append("receivables growing faster than revenue (flag)")
        else:
            checks.append(f"receivables {_pct(rec_g)} vs revenue {_pct(rev_g)} y/y (ok)")

    # A score the archetype rules out (Altman Z for a bank, insurer or REIT) is
    # not applicable, not short of inputs: counted, it would hold a bank's gate
    # at partial however complete its statements were.
    refused = [sc.name for sc in scores if sc.verdict.startswith("not applicable")]
    applicable = [sc for sc in scores if sc.name not in refused]
    computable = sum(1 for sc in applicable if sc.computable)
    full = sum(1 for sc in applicable if sc.computable == sc.needed)
    missing = tuple(sorted({m for sc in applicable for m in sc.missing}))
    if computable == 0:
        status, gate = "unavailable", "unavailable"
    elif full == len(applicable):
        status, gate = "done", "flag" if flags else "clean"
    else:
        status, gate = "partial", "flag" if flags else "clean"
    summary = (
        f"{full} of {len(applicable)} scores fully computable; gate {gate}"
        + (f"; {', '.join(refused)} not applicable to a {archetype}" if refused else "")
        + (f"; {', '.join(checks)}" if checks else "")
    )
    analogues: list[Finding] = []
    if flags:
        probe = Thesis(iid, Stance.NO_VIEW, 12, "", [], [], None, [], supporting=flag_findings)
        patterns = a11.patterns_for(probe)
        if patterns:
            analogues = a11.analogues(probe, patterns)
            summary += f"; red team analogues on {', '.join(sorted(patterns))}"
    return (
        Step(
            n,
            title,
            status,
            summary,
            findings=[*findings, *analogues],
            missing=missing,
            fills=_fills(missing, sources),
            flags=tuple(dict.fromkeys(flags)),
        ),
        flag_findings,
    )


def _history(a1: A1Fundamentals, s: Statements, iid: str, asof: date, sources: str) -> Step:
    n, title = STEPS[4]
    years = s.annual("revenue")
    if len(years) < 2:
        missing = _missing(s, ("revenue", "operating_income", "net_income"), annual=True)
        return Step(
            n,
            title,
            "unavailable",
            f"{len(years)} annual year{'s' if len(years) != 1 else ''} stored; docs/04 asks for "
            f"{HISTORY_YEARS}-10",
            missing=missing,
            fills=_fills(missing or ("revenue",), sources),
        )
    rev, op = _by_year(s, "revenue"), _by_year(s, "operating_income")
    margins = [op[y] / rev[y] for y in sorted(rev) if y in op and rev[y]]
    span = f"{years[0].period_end.year}-{years[-1].period_end.year}"
    keep = {
        "revenue_cagr",
        "revenue_yoy",
        "net_income_yoy",
        "gross_margin",
        "operating_margin",
        "roic",
        "fcf_margin",
        "capex_to_revenue",
        "capex_to_depreciation",
        "dilution",
    }
    findings = [f for f in a1.ratio_sheet(iid, asof)[1:] if f.text.split(":")[0] in keep]
    g = cagr(s, "revenue", min(HISTORY_YEARS, len(years) - 1))
    summary = (
        f"{len(years)} annual years ({span}); revenue CAGR {_pct(g.value)} over "
        f"{min(HISTORY_YEARS, len(years) - 1)}y; operating margin "
        + (f"{_pct(min(margins))} to {_pct(max(margins))}" if margins else "n/a")
    )
    status = "done" if len(years) >= HISTORY_YEARS else "partial"
    missing = _missing(s, ("shares_outstanding", "dividends_paid", "depreciation"), annual=True)
    return Step(n, title, status, summary, findings, missing, _fills(missing, sources))


def _capital_allocation(s: Statements, sources: str) -> Step:
    n, title = STEPS[5]
    cfo, cx = _by_year(s, "cash_from_operations"), _by_year(s, "capex")
    years = sorted(y for y in cfo if y in cx)
    if len(years) < 2:
        missing = _missing(s, ("cash_from_operations", "capex"), annual=True)
        return Step(
            n,
            title,
            "unavailable",
            "free cash flow by year needs operating cash flow and capex for at least two years",
            missing=missing,
            fills=_fills(missing or ("cash_from_operations",), sources),
        )
    fcf = sum((cfo[y] - abs(cx[y]) for y in years), Decimal(0))
    div = _by_year(s, "dividends_paid")
    bb = _by_year(s, "buybacks")
    paid = sum((abs(div[y]) for y in years if y in div), Decimal(0)) if div else None
    bought = sum((abs(bb[y]) for y in years if y in bb), Decimal(0)) if bb else None
    debt0, debt1 = _instant_at(s, "total_debt", years[0]), _instant_at(s, "total_debt", years[-1])
    if debt0 is None or debt1 is None:
        lt0, lt1 = (
            _instant_at(s, "long_term_debt", years[0]),
            _instant_at(s, "long_term_debt", years[-1]),
        )
        debt0, debt1 = lt0, lt1
    cash0, cash1 = _instant_at(s, "cash", years[0]), _instant_at(s, "cash", years[-1])
    parts = [f"cumulative FCF {fcf:,.0f} over {years[0].year}-{years[-1].year}"]
    parts.append(f"dividends {paid:,.0f}" if paid is not None else "dividends not stored")
    parts.append(f"buybacks {bought:,.0f}" if bought is not None else "buybacks not stored")
    if debt0 is not None and debt1 is not None:
        parts.append(f"debt {'down' if debt1 < debt0 else 'up'} {abs(debt1 - debt0):,.0f}")
    if cash0 is not None and cash1 is not None:
        parts.append(f"cash {'up' if cash1 > cash0 else 'down'} {abs(cash1 - cash0):,.0f}")
    accounted = (paid or Decimal(0)) + (bought or Decimal(0))
    if debt0 is not None and debt1 is not None and debt1 < debt0:
        accounted += debt0 - debt1
    if cash0 is not None and cash1 is not None and cash1 > cash0:
        accounted += cash1 - cash0
    if fcf > 0:
        parts.append(
            f"unaccounted {max(fcf - accounted, Decimal(0)):,.0f} "
            f"(acquisitions and working capital are not stored lines)"
        )
    missing = tuple(
        c
        for c, present in (
            ("dividends_paid", paid is not None),
            ("buybacks", bought is not None),
            ("total_debt", debt0 is not None),
            ("cash", cash0 is not None),
        )
        if not present
    )
    status = "done" if not missing else "partial"
    return Step(n, title, status, "; ".join(parts), missing=missing, fills=_fills(missing, sources))


def _competitive_position(s: Statements, coc: CostOfCapital, sources: str) -> Step:
    n, title = STEPS[6]
    rate, which = coc.discount
    op, rev = _by_year(s, "operating_income"), _by_year(s, "revenue")
    tax = coc.tax_rate if coc.tax_rate is not None else Decimal("0.25")
    roics: list[tuple[date, Decimal]] = []
    for y in sorted(op):
        eq, debt, cash = (
            _instant_at(s, "equity", y),
            _instant_at(s, "total_debt", y),
            _instant_at(s, "cash", y),
        )
        if debt is None:
            debt = _instant_at(s, "long_term_debt", y)
        if eq is None:
            continue
        capital = eq + (debt or Decimal(0)) - (cash or Decimal(0))
        if capital > 0:
            roics.append((y, op[y] * (1 - tax) / capital))
    gm = _by_year(s, "gross_profit")
    gross = [gm[y] / rev[y] for y in sorted(rev) if y in gm and rev[y]]
    if not roics:
        missing = _missing(s, ("operating_income",), annual=True) + _missing(s, ("equity",))
        return Step(
            n,
            title,
            "unavailable",
            "ROIC by year needs annual operating income and the year-end balance sheet",
            missing=missing,
            fills=_fills(missing or ("operating_income",), sources),
        )
    if rate is None:
        above = None
        summary = (
            f"ROIC {_pct(roics[0][1])} to {_pct(roics[-1][1])} over {len(roics)} years; "
            f"cost of capital unavailable ({which}), so the moat test cannot be run"
        )
        status = "partial"
    else:
        above = sum(1 for _, r in roics if r > rate)
        summary = (
            f"ROIC above the cost of capital ({_pct(rate)}) in {above} of {len(roics)} years; "
            f"latest {_pct(roics[-1][1])}"
            + (f"; above {_pct(ROIC_MOAT)} in {sum(1 for _, r in roics if r > ROIC_MOAT)} years")
        )
        status = "done" if len(roics) >= 3 else "partial"
    if gross:
        spread = max(gross) - min(gross)
        summary += f"; gross margin {_pct(min(gross))} to {_pct(max(gross))} (range {_pct(spread)})"
    flags = ("roic_below_cost_of_capital",) if above is not None and above == 0 else ()
    return Step(n, title, status, summary, flags=flags)


def _industry(
    a7: A7SectorTechnology | None, a2: A2Valuation, iid: str, asof: date, archetype: str | None
) -> tuple[Step, PeerSet | None]:
    n, title = STEPS[7]
    findings: list[Finding] = []
    peers: PeerSet | None = None
    if a7 is None or a7.graph is None:
        summary = "no graph at data/graph.db: peers and structure unavailable (make graph)"
        status = "unavailable"
    else:
        peers = peers_of(a7.graph, iid, asof)
        findings.extend(a7.peers(iid, asof))
        if peers.node_id is None:
            summary = peers.note
            status = "partial"
        else:
            summary = (
                f"sub-sector {peers.subsector or 'unrecorded'}; {len(peers.direct)} stated "
                f"rival{'s' if len(peers.direct) != 1 else ''}, {len(peers.same_subsector)} by "
                f"sub-sector; cycle stage, S-curve position and substitution risk are yours"
            )
            status = "partial"
    if archetype:
        findings.extend(a2.method_note(archetype))
    return (
        Step(
            n,
            title,
            status,
            summary,
            findings,
            manual_prompt="where in the capex and inventory cycle, which S-curve stage, what "
            "substitutes, which regulator; each resolved to a dated, observable item",
        ),
        peers,
    )


def _forward_drivers(
    s: Statements, coc: CostOfCapital, table: CostOfCapitalTable, country: str, sources: str
) -> Step:
    n, title = STEPS[8]
    if s.flow("revenue")[0] is None:
        return Step(
            n,
            title,
            "unavailable",
            "what must be true starts from a stored revenue line; none is stored",
            missing=("revenue",),
            fills=_fills(("revenue",), sources),
        )
    scenarios, reasons = default_scenarios(s, coc, table, country)
    if reasons:
        return Step(n, title, "unavailable", "; ".join(reasons))
    base = next((sc for sc in scenarios if sc.name == "base"), scenarios[0])
    a = base.assumptions
    musts = [
        f"revenue grows {_pct(a.growth_first_year)} next year, fading to {_pct(a.terminal_growth)}",
        f"operating margin holds at {_pct(a.margin)}",
        f"each unit of new revenue needs {a.reinvestment:.2f} of capital",
        f"the discount rate stays near {_pct(a.discount)}",
    ]
    findings = [
        Finding(
            "a2_valuation",
            "driver",
            m,
            caveats=[f"basis: {b}" for b in base.basis][:2],
        )
        for m in musts
    ]
    return Step(
        n,
        title,
        "done",
        f"the base scenario's four measurable drivers, each from the stored record "
        f"({len(base.basis)} stated bases)",
        findings,
    )


def _valuation(
    a2: A2Valuation,
    book: Any,
    s: Statements,
    coc: CostOfCapital,
    coc_findings: list[Finding],
    table: CostOfCapitalTable,
    iid: str,
    asof: date,
    country: str,
    currency: str,
    peer_ids: set[str],
    sources: str,
) -> tuple[Step, Any]:
    n, title = STEPS[9]
    findings = list(coc_findings)
    if s.flow("revenue")[0] is None:
        return (
            Step(
                n,
                title,
                "unavailable",
                "no statements stored: nothing to project; the cost of capital alone is below",
                findings,
                missing=("revenue",),
                fills=_fills(("revenue",), sources),
            ),
            None,
        )
    vr, range_findings = a2.scenario_range(s, coc, table, country, currency)
    findings.extend(range_findings)
    head = range_findings[0]
    flags: list[str] = []
    if head.kind == "valuation_range" and vr is not None and vr.low is not None:
        summary = (
            f"scenario range {vr.low:,.0f} to {vr.high:,.0f} {currency} equity value (bear to bull)"
        )
        status = "done"
    elif head.kind == "valuation_refused":
        summary = f"refused: {vr.refused if vr is not None else head.text}"
        status, flags = "done", ["valuation_refused"]
    else:
        summary = head.text
        status = "unavailable"
    eps = book.latest(iid, "eps_ttm", asof=asof)
    pe = book.latest(iid, "pe_ttm", asof=asof)
    rate, _ = coc.discount
    if eps is not None and eps.value:
        price = (eps.value * pe.value) if pe is not None and pe.value else None
        # the reverse DCF's terminal growth is the scenario DCF's, so the two agree on perpetuity
        terminal = terminal_growth_for(coc, table, country)
        comps = a2.peer_multiples(
            book, iid, set(peer_ids), "pe_ttm", asof, price, eps.value, rate, terminal
        )
        findings.extend(comps)
        summary += "; multiple in its contexts below"
    else:
        summary += "; reverse DCF needs eps_ttm and pe_ttm snapshots (finnhub)"
    return Step(n, title, status, summary, findings, flags=tuple(flags)), vr


def _return_decomposition(a9: A9Attribution, book: Any, iid: str, asof: date) -> Step:
    from engines.valuation.comps import own_history

    n, title = STEPS[10]
    eps = own_history(book, iid, "eps_ttm", asof)
    pe = own_history(book, iid, "pe_ttm", asof)
    if len(eps) < 2 or len(pe) < 2:
        return Step(
            n,
            title,
            "unavailable",
            f"needs eps_ttm and pe_ttm snapshots at two dates at least two years apart; "
            f"stored: {len(eps)} eps, {len(pe)} multiple snapshots (finnhub, us_close)",
        )
    span = (min(eps[-1][0], pe[-1][0]) - max(eps[0][0], pe[0][0])).days
    if span < DECOMPOSE_MIN_DAYS:
        return Step(
            n,
            title,
            "unavailable",
            f"snapshots span {span} days since {max(eps[0][0], pe[0][0])}; the decomposition "
            f"needs {DECOMPOSE_MIN_DAYS} (they accumulate with every collection run)",
        )
    if eps[0][1] <= 0 or pe[0][1] <= 0:
        return Step(n, title, "not_applicable", "starting earnings or multiple not positive")
    years = span / 365.25
    findings = a9.since_purchase(
        iid,
        float(eps[0][1]),
        float(eps[-1][1]),
        float(pe[0][1]),
        float(pe[-1][1]),
        0.0,
        1.0,
        1.0,
        years,
    )
    for f in findings:
        f.caveats.append(
            "shareholder yield set to zero and FX to flat: dividends and currency are not in "
            "the snapshots, so the total understates a dividend payer"
        )
    return Step(n, title, "partial", findings[0].text if findings else "", findings)


def _price_query(book: Any, iid: str, asof: date, bull: str) -> tuple[str, str] | None:
    """The latest price and whether it is above `bull`, as a query, and what the price is.

    The stored close when the fact book holds one for the name; otherwise the
    price the snapshots imply, eps_ttm x pe_ttm, as step 10 derives it. None
    when neither is stored: a price breaker with no price to read is a wish.
    """
    held = book.latest(iid, "close", asof=asof)
    if held is not None and held.value is not None:
        return (
            f"SELECT period_end, value_num AS price, value_num > {bull} AS above_bull "
            f"FROM observations WHERE instrument_id = '{iid}' AND concept = 'close' "
            "AND value_num IS NOT NULL ORDER BY period_end DESC, known_at DESC LIMIT 1",
            "the stored close",
        )
    eps, pe = book.latest(iid, "eps_ttm", asof=asof), book.latest(iid, "pe_ttm", asof=asof)
    if eps is None or eps.value is None or pe is None or pe.value is None:
        return None
    return (
        f"SELECT known_at, price, price > {bull} AS above_bull FROM (SELECT p.known_at, "
        "p.value_num * (SELECT e.value_num FROM observations e WHERE e.instrument_id = "
        "p.instrument_id AND e.concept = 'eps_ttm' AND e.value_num IS NOT NULL AND e.known_at "
        f"<= p.known_at ORDER BY e.known_at DESC LIMIT 1) AS price FROM observations p WHERE "
        f"p.instrument_id = '{iid}' AND p.concept = 'pe_ttm' AND p.value_num IS NOT NULL "
        "ORDER BY p.known_at DESC LIMIT 1)",
        "eps_ttm x pe_ttm, no close being stored",
    )


def _suggest_breakers(
    book: Any,
    iid: str,
    asof: date,
    s: Statements,
    flags: list[str],
    vr: Any,
    base_growth: Decimal | None,
) -> list[Breaker]:
    # Every query reads value_num: the observations table has value_text and
    # value_num and no `value`, and each breaker once failed with "no such
    # column: value", so none of them could ever fire.
    review = asof + timedelta(days=BREAKER_REVIEW_DAYS)
    out: list[Breaker] = []
    where = f"instrument_id = '{iid}'"
    if s.flow("revenue")[0] is None:
        return out
    if "accruals" in flags or "beneish_m" in flags:
        out.append(
            Breaker(
                "accruals stay above 10% of assets at the next annual print",
                f"SELECT concept, period_end, value_num FROM observations WHERE {where} AND concept "
                "IN ('net_income_fy','cash_from_operations_fy','total_assets') ORDER BY period_end DESC",
                "facts",
                review,
            )
        )
    if "net_debt_to_ebitda" in flags or "interest_cover" in flags:
        out.append(
            Breaker(
                "net debt above 4x EBITDA or interest cover below 3x at the next balance sheet",
                f"SELECT concept, period_end, value_num FROM observations WHERE {where} AND concept IN "
                "('total_debt','cash','operating_income_fy','depreciation_fy','interest_expense_fy') "
                "ORDER BY period_end DESC",
                "facts",
                review,
            )
        )
    if base_growth is not None:
        out.append(
            Breaker(
                f"revenue growth below {_pct(base_growth)} year on year for two quarters",
                f"SELECT period_end, value_num FROM observations WHERE {where} AND concept = 'revenue' "
                "ORDER BY period_end DESC LIMIT 8",
                "facts",
                review,
            )
        )
    bull = vr.per_share.get("bull") if vr is not None else None
    # A price, never pe_ttm: the query read the multiple, so a P/E near 30 stood
    # against a bull case of 79.81 per share.
    priced = _price_query(book, iid, asof, f"{bull:.2f}") if bull is not None else None
    if priced is not None:
        query, basis = priced
        out.append(
            Breaker(
                f"price above the bull case ({bull:,.2f} per share): the range no longer holds; "
                f"price is {basis}",
                query,
                "facts",
                review,
            )
        )
    # Only a value a source files after `asof` for a period it already held:
    # unscoped, two values for one period was true on day one (NVDA's 2016 10-K
    # re-reported fiscal 2015 buybacks rounded, 813.6m to 814m, and its splits
    # restated diluted EPS), so the breaker was broken before the thesis began.
    out.append(
        Breaker(
            f"a stored annual line is restated (a source files a new value for a period it held "
            f"on {asof})",
            "SELECT source, concept, period_end, COUNT(DISTINCT value_num) AS n FROM observations "
            f"WHERE {where} AND concept LIKE '%_fy' GROUP BY source, concept, period_end "
            f"HAVING MIN(known_at) <= '{asof}' AND n > COUNT(DISTINCT CASE WHEN known_at <= "
            f"'{asof}' THEN value_num END)",
            "facts",
            review,
        )
    )
    return out[:4]


def _thesis_step(breakers: list[Breaker], gates: dict[str, str]) -> Step:
    n, title = STEPS[11]
    if not breakers:
        return Step(
            n,
            title,
            "unavailable",
            "no breaker can be suggested without stored statements; a thesis without two "
            "checkable breakers takes no stance",
        )
    status = "done"
    summary = (
        f"{len(breakers)} suggested breakers, each an executable query against the facts store "
        f"with a review date; gates: {', '.join(f'{k} {v}' for k, v in gates.items())}. "
        "A workup is not a stance: compose with `ask.py thesis --derive-valuation` and let the red team at it"
    )
    return Step(n, title, status, summary)


# --- the run -------------------------------------------------------------------------------


def run_workup(
    instrument_id: str,
    book: Any,
    ctx: AgentContext,
    *,
    asof: date,
    table: CostOfCapitalTable | None = None,
    graph: Any = None,
    evidence: Any = None,
    archetype: str | None = None,
    currency: str = "",
) -> Workup:
    """All twelve steps, each honest about what the stored record could answer."""
    table = table or load_table()
    sources = _statement_sources(instrument_id)
    store = book.as_fact_store([instrument_id], asof)
    s = Statements.from_store(store, instrument_id, asof)
    a1 = A1Fundamentals(ctx, store)
    a2 = A2Valuation(ctx)
    a7 = A7SectorTechnology(ctx, graph, evidence)
    a9 = A9Attribution(ctx)
    a11 = A11RedTeam(ctx)
    country = country_of(instrument_id)

    steps: list[Step] = [_identity(instrument_id), _business_model(s), _comprehensibility()]
    gates = {"comprehensibility": "manual"}
    quality, _flag_findings = _quality_gate(
        a1, a11, book, s, instrument_id, asof, sources, archetype
    )
    gates["quality"] = (
        "unavailable" if quality.status == "unavailable" else ("flag" if quality.flags else "clean")
    )
    steps.append(quality)
    steps.append(_history(a1, s, instrument_id, asof, sources))
    steps.append(_capital_allocation(s, sources))

    coc, coc_findings = a2.cost_of_capital(instrument_id, book, table, asof, archetype, s)
    steps.append(_competitive_position(s, coc, sources))
    industry, peers = _industry(a7, a2, instrument_id, asof, archetype)
    steps.append(industry)
    steps.append(_forward_drivers(s, coc, table, country, sources))
    valuation, vr = _valuation(
        a2,
        book,
        s,
        coc,
        coc_findings,
        table,
        instrument_id,
        asof,
        country,
        currency,
        peers.ids if peers else set(),
        sources,
    )
    steps.append(valuation)
    steps.append(_return_decomposition(a9, book, instrument_id, asof))

    base_growth = None
    if s.flow("revenue")[0] is not None:
        scenarios, reasons = default_scenarios(s, coc, table, country)
        if not reasons:
            base = next((sc for sc in scenarios if sc.name == "base"), scenarios[0])
            base_growth = base.assumptions.growth_first_year
    flags = list(quality.flags) + list(steps[6].flags) + list(valuation.flags)
    breakers = _suggest_breakers(book, instrument_id, asof, s, flags, vr, base_growth)
    steps.append(_thesis_step(breakers, gates))

    assert [st.n for st in steps] == [n for n, _ in STEPS]
    return Workup(instrument_id, asof, archetype, steps, gates, breakers, peers, coc)


def workup_text(w: Workup) -> str:
    rows = [
        f"WORKUP  {w.instrument_id}  as of {w.asof}"
        + (f"  archetype {w.archetype}" if w.archetype else ""),
        f"  {w.counts_text()}",
        f"  gates: {', '.join(f'{k} {v}' for k, v in w.gates.items())}",
        "",
    ]
    for st in w.steps:
        rows.append(st.text())
        rows.append("")
    if w.breakers:
        rows.append("SUGGESTED BREAKERS  (a thesis would carry these; nothing here is a stance)")
        for b in w.breakers:
            rows.append(f"  - {b.statement}  review {b.check_by}")
            rows.append(f"      check: {b.query}  against {b.store}")
        rows.append("")
    rows.append(
        "Every figure is point-in-time from the fact book as of the date above; a step that says "
        "unavailable names the collector that would change it."
    )
    return "\n".join(rows)
