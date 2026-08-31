#!/usr/bin/env python3
"""Ask the system a question.

docs/14 section 3 month 1 says to run the system read-only against questions you
already know the answer to. That instruction needed a way to ask, which did not
exist: the agents were classes with no entrypoint.

    python ask.py plan "why did maybank fall today" --instrument MYX:1155
    python ask.py why MYX:1155 --move -0.090 --market -0.080 --sector -0.020
    python ask.py why XNAS:NVDA --fetch --against XNAS:SPY --days 5
    python ask.py prices XNAS:NVDA --days 30
    python ask.py thesis MYX:1155 --breaker "NIM falls below 2.0%" --breaker "CASA below 25%"
    python ask.py risk --position MYX:1155:0.22:bank:MY --position XNAS:NVDA:0.18:tech:US
    python ask.py size MYX:1155 --portfolio 200000 --price 6.20 --stop 5.60 --adv 900000
    python ask.py learn kelly_criterion
    python ask.py backend

Returns may be typed in or fetched. `--fetch` pulls daily bars from the price
feed and derives the window return from them; without it the numbers are yours
and are labelled as stated rather than measured. `--history` takes a CSV of
`instrument_return,market_return,sector_return` rows for the estimation window.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from agents.base import AgentContext, Finding
from agents.learning.teacher import A14Teacher, Learner
from agents.portfolio.agents import A12PortfolioRisk, A13Sizing
from agents.supervisor import A0Supervisor
from agents.synthesis.agents import A9Attribution, A10Thesis, A11RedTeam, Breaker, Stance
from core.contracts.money import BASE_CURRENCY
from core.guardrails.defaults import default_engine
from core.market.feed import PriceFeedError, default_feed
from core.registry.loader import load as load_registry
from engines.attribution.decompose import MIN_OBSERVATIONS
from engines.attribution.regression import huber_fit
from engines.risk.concentration import Limits, Position
from engines.sizing.caps import cost_floor_bps, to_base
from knowledge.retrieval.pipeline import Router
from markets.registry import get as market_get
from markets.registry import market_currency, mic_of
from ui.render import decomposition_bars, refusal_card

REGISTRY = "agents/registry.yaml"


def context() -> AgentContext:
    """The allowlist comes from the registry, never a hand-written dict."""
    reg = load_registry(REGISTRY)
    return AgentContext(
        router=Router({}), engine=default_engine(reg.allowlist()), now=datetime.now(UTC)
    )


def _fit_from_csv(path: str):
    rows, y = [], []
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if not r or r[0].lstrip().startswith("#"):
                continue
            try:
                inst, mkt, sec = float(r[0]), float(r[1]), float(r[2])
            except (ValueError, IndexError):
                continue  # header row
            y.append(inst)
            rows.append([mkt, sec])
    # Same guard the engine applies in estimate(). Calling huber_fit directly
    # would slip past MIN_OBSERVATIONS and fit betas to noise.
    if len(y) < MIN_OBSERVATIONS:
        print(
            f"note: {len(y)} usable rows in {path}, below the {MIN_OBSERVATIONS} the "
            "engine requires. Reporting attribution_unavailable rather than guessing.",
            file=sys.stderr,
        )
        return None
    try:
        return huber_fit(rows, y)
    except ValueError as e:
        # Collinear or degenerate history. A refusal, not a crash.
        print(f"note: {path} cannot support a factor model ({e}).", file=sys.stderr)
        return None


def _fit_synthetic(beta_mkt: float, beta_sec: float, seed: int = 7):
    """A stated-beta stand-in for a real estimation window.

    This is NOT an estimate of anything. It exists so the pipeline can be
    exercised on a known answer, which is what month 1 asks for. Every output
    built on it is labelled accordingly.
    """
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [beta_mkt * a + beta_sec * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


def cmd_plan(a) -> int:
    a0 = A0Supervisor(context())
    plan = a0.plan(
        a.question,
        budget_myr=Decimal(str(a.budget)) if a.budget else None,
        instrument_ids=tuple(a.instrument or ()),
    )
    if not plan.allowed:
        print(refusal_card(plan.refusal.reason, plan.refusal.what_would_help))
        return 2  # a refusal is not an error
    print(f"intent    {plan.intent.value}")
    print(f"cost      about RM {plan.estimated_cost.amount:.2f}")
    print(f"agents    {len(plan.agents)}")
    for name in plan.agents:
        print(f"            {name}")
    for note in plan.notes:
        print(f"note      {note}")
    return 0


def cmd_why(a) -> int:
    fit = _fit_from_csv(a.history) if a.history else _fit_synthetic(a.beta_market, a.beta_sector)
    end = date.fromisoformat(a.on) if a.on else date.today()
    window = (end - timedelta(days=a.days), end)
    measured = False

    if getattr(a, "fetch", False):
        # Measured beats typed, but only if BOTH legs are measured. A real
        # instrument return against a typed market return is not a decomposition,
        # it is a subtraction dressed as one.
        if not a.against:
            print(
                "--fetch needs --against: an instrument return measured against a "
                "typed market return is not a decomposition.",
                file=sys.stderr,
            )
            return 2
        try:
            legs = _window_returns(
                [a.instrument, a.against] + ([a.sector_proxy] if a.sector_proxy else []),
                a.days,
                end,
            )
            a.move, first_day, last_day = legs[a.instrument]
            a.market, _, _ = legs[a.against]
            if a.sector_proxy:
                a.sector, _, _ = legs[a.sector_proxy]
        except PriceFeedError as e:
            print(f"no prices: {e}", file=sys.stderr)
            return 3
        window = (first_day, last_day)
        measured = True
        print(
            f"measured  {a.instrument} {a.move:+.2%} against {a.against} "
            f"{a.market:+.2%} over {first_day} to {last_day}\n"
        )

    a9 = A9Attribution(context())
    findings = a9.run(
        a.instrument,
        window,
        realised_local=a.move,
        event_market=a.market,
        event_sector=a.sector,
        event_styles={},
        fx_return=a.fx,
        fit=fit,
        base_currency=a.currency,
    )
    head = findings[0]

    # Rebuild the explanation for the renderer rather than re-deriving numbers.
    from engines.attribution.decompose import decompose

    exp = decompose(
        a.instrument, window, a.market, a.sector, {}, a.move, a.fx, fit, base_currency=a.currency
    )
    print(decomposition_bars(exp))
    print()
    print(head.text)
    for c in head.caveats:
        print(f"  caveat: {c}")
    if not a.history:
        print("\n  betas are stated, not estimated: pass --history with 120+ rows of")
        print("  instrument,market,sector returns for a real estimation window.")
    if not measured:
        print("  returns are stated, not measured: pass --fetch --against <proxy> to")
        print("  take both legs from the price feed instead.")
    return 0


# --- live prices ----------------------------------------------------------
def _feed():
    """The price seam. A chain, not a name: stooq.com walled itself off on
    2026-08-31 and a CLI wired to one source by name went dark with it."""
    return default_feed()


def _window_returns(instruments: list[str], bars_back: int, end: date | None) -> dict:
    """All legs of a decomposition, concurrently, each id fetched exactly once.

    The legs are independent HTTP calls to a slow free source; serially they
    cost up to 3x the timeout. Duplicates (instrument == market proxy) are
    deduplicated BEFORE fetching, or the same URL would be paid for twice in
    one command.
    """
    from concurrent.futures import ThreadPoolExecutor

    unique = list(dict.fromkeys(instruments))
    if len(unique) == 1:
        return {unique[0]: _window_return(unique[0], bars_back, end)}
    with ThreadPoolExecutor(max_workers=min(3, len(unique))) as pool:
        futures = {i: pool.submit(_window_return, i, bars_back, end) for i in unique}
        return {i: f.result() for i, f in futures.items()}


def _window_return(instrument: str, bars_back: int, end: date | None):
    """Realised return over the last `bars_back` TRADING bars, from real data.

    Trading bars, not calendar days: a 5-calendar-day window over a long weekend
    is three sessions, and pretending otherwise silently changes the horizon the
    whole decomposition is about.
    """
    series = _feed().fetch(instrument, end=end)
    bars = series.raw()
    if len(bars) < bars_back + 1:
        raise PriceFeedError(
            f"{instrument} has {len(bars)} bars up to {end or 'today'}, "
            f"need {bars_back + 1} to measure a {bars_back}-bar return"
        )
    window = bars[-(bars_back + 1) :]
    first, last = window[0], window[-1]
    return (last.close / first.close) - 1.0, first.day, last.day


def cmd_prices(a) -> int:
    end = date.fromisoformat(a.on) if a.on else None
    try:
        series = _feed().fetch(a.instrument, end=end)
    except PriceFeedError as e:
        print(f"no prices: {e}", file=sys.stderr)
        return 3
    bars = series.raw()[-a.days :]
    print(f"{a.instrument}  {len(series)} bars held, showing {len(bars)}")
    print(f"{'day':<12}{'open':>10}{'high':>10}{'low':>10}{'close':>10}{'volume':>14}")
    for b in bars:
        print(
            f"{b.day.isoformat():<12}{b.open:>10.3f}{b.high:>10.3f}"
            f"{b.low:>10.3f}{b.close:>10.3f}{b.volume:>14,.0f}"
        )
    if len(bars) > 1:
        ret = (bars[-1].close / bars[0].close) - 1.0
        print(f"\nreturn over the shown window  {ret:+.2%}")
    print(f"20d ADV {series.adv(20):,.0f}   20d ATR {series.atr(20):.4f}")
    return 0


# --- thesis and its red team ---------------------------------------------
def _parse_breaker(raw: str) -> Breaker:
    """`statement|query|store`.

    The three fields are not ceremony. docs/04 section 6: a breaker that cannot
    be checked is a wish, and `Breaker.__post_init__` refuses one with no query.
    Letting the CLI default a query would route around the only thing that makes
    a breaker a breaker.
    """
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 3 or not all(parts):
        raise SystemExit(
            f"--breaker wants 'statement|query|store', got {raw!r}\n"
            '  e.g. --breaker "NIM falls below 2.0%|nim < 0.020|kb_filings"\n'
            "  a breaker with no executable query is a wish, not a breaker."
        )
    statement, query, store = parts
    return Breaker(statement=statement, query=query, store=store)


def cmd_thesis(a) -> int:
    """A10 composes, A11 attacks. Neither was reachable from a shell before."""
    ctx = context()
    try:
        breakers = [_parse_breaker(b) for b in (a.breaker or [])]
    except ValueError as e:
        print(f"breaker refused: {e}", file=sys.stderr)
        return 2

    # Evidence is supplied, not retrieved: no collection is populated offline, and
    # a thesis built on an empty store would report full coverage of nothing.
    findings = [
        Finding(agent, "supplied", text) for agent, text in _parse_evidence(a.evidence or [])
    ]

    a10 = A10Thesis(ctx)
    out = a10.run(
        a.instrument,
        findings,
        horizon_months=a.horizon,
        proposed_stance=Stance(a.stance),
        breakers=breakers,
    )

    print(f"thesis    {a.instrument}   horizon {a.horizon}m")
    for f in out:
        print(f"  {f.text}")
        print(
            f"  confidence {f.numbers.get('confidence', 0):.2f} "
            f"on {int(f.numbers.get('breakers', 0))} breakers"
        )
        for c in f.caveats:
            print(f"    caveat: {c}")

    thesis = a10.last
    print(f"  actionable: {'yes' if thesis.is_actionable() else 'no'}")
    if thesis.breakers:
        print("\n  what would falsify this")
        for b in thesis.breakers:
            print(f"    - {b.statement}   [{b.query} against {b.store}]")

    print("\nred team")
    challenges = A11RedTeam(ctx).run(thesis)
    if not challenges:
        print("  (silent - which on a live thesis is itself a finding)")
    for c in sorted(challenges, key=lambda f: -f.numbers.get("severity_rank", 0)):
        kind = c.caveats[0].split(": ")[-1] if c.caveats else "?"
        print(f"  [{kind}] {c.text}")
    return 0


def _parse_evidence(pairs: list[str]):
    """`--evidence a1_fundamentals=CASA fell to 24%` -> (agent, text)."""
    for raw in pairs:
        agent, sep, text = raw.partition("=")
        if not sep or not text.strip():
            raise SystemExit(f"--evidence wants agent=text, got {raw!r}")
        yield agent.strip(), text.strip()


# --- portfolio risk -------------------------------------------------------
def _parse_position(raw: str) -> Position:
    """`MYX:1155:0.22:bank:MY[:0.01[:MYR]]` -> Position. Colons, because tickers have them.

    The currency comes from the MARKET, not from the country field. It used to be
    `currency=parts[4]`, so a Bursa holding typed with country `MY` carried the
    currency `MY`, which is not `MYR` - and `check()` counts anything that is not
    the base currency as foreign-currency exposure. A book of nothing but Bursa
    stocks therefore reported 100% foreign exposure and breached the 50% limit:
    a refusal produced by a typo in a field nobody was looking at.
    """
    parts = raw.split(":")
    if len(parts) < 5:
        raise SystemExit(
            f"--position wants MIC:CODE:weight:sector:country[:risk_to_stop[:currency]], "
            f"got {raw!r}"
        )
    instrument = f"{parts[0]}:{parts[1]}"
    try:
        weight = float(parts[2])
        risk = float(parts[5]) if len(parts) > 5 and parts[5] else 0.0
    except ValueError:
        raise SystemExit(f"--position weight/risk must be numbers, got {raw!r}") from None
    currency = parts[6].upper() if len(parts) > 6 and parts[6] else market_currency(parts[0])
    return Position(
        instrument_id=instrument,
        weight=weight,
        sector=parts[3],
        country=parts[4],
        currency=currency,
        risk_to_stop=risk,
    )


def cmd_risk(a) -> int:
    positions = [_parse_position(p) for p in (a.position or [])]
    limits = Limits(single_name=a.single_name) if a.single_name else Limits()
    out = A12PortfolioRisk(context()).run(
        positions,
        limits=limits,
        base_currency=a.currency,
        equity=Decimal(str(a.equity)) if a.equity else None,
        peak_equity=Decimal(str(a.peak)) if a.peak else None,
    )
    print(f"book      {len(positions)} positions, base {a.currency}")
    for f in out:
        print(f"  {f.text}")
        for c in f.caveats:
            print(f"    caveat: {c}")
    return 0


# --- sizing ---------------------------------------------------------------
def cmd_size(a) -> int:
    a13 = A13Sizing(context())
    portfolio = Decimal(str(a.portfolio))
    price = Decimal(str(a.price))
    stop = Decimal(str(a.stop))
    if stop >= price:
        print(
            f"stop {stop} is at or above price {price}; a stop above entry is not a stop.",
            file=sys.stderr,
        )
        return 2

    stop_frac = (price - stop) / price

    try:
        mic = mic_of(a.instrument)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    # The market's real schedule when we have one. A flat-bps lambda is not a
    # cheap approximation of it, it is a different shape: fees as a CONSTANT
    # fraction never fall with size, so cost_floor_value's bisection can never
    # come down and returns its RM 100m ceiling. What makes small positions
    # uneconomic is the fixed MINIMUM (RM 8 on Bursa), and a model without one
    # cannot express the thing being measured.
    try:
        schedule = market_get(mic).fee_schedule
        round_trip_cost_at = schedule.round_trip
        cost_note = f"{mic} fee schedule"
    except KeyError:
        rate = Decimal(str(a.cost_bps)) / Decimal(10_000)
        minimum = Decimal(str(a.cost_minimum))

        def round_trip_cost_at(value: Decimal) -> Decimal:  # noqa: E306
            return max(value * rate, minimum) * 2

        # The asymptote: what a round trip costs once the minimum stops binding.
        # If that already exceeds the floor, NO size satisfies it - the bisection
        # in cost_floor_value cannot come down and returns its RM 100,000,000
        # ceiling, which reads as a position requirement rather than as the
        # impossibility it is. Say so instead.
        asymptote_bps = rate * 2 * Decimal(10_000)
        floor_bps = cost_floor_bps(mic)
        if asymptote_bps > floor_bps:
            print(f"sizing    {a.instrument}")
            print(
                f"  no position: at {a.cost_bps} bps per side a round trip costs "
                f"{asymptote_bps} bps at ANY size, above the {floor_bps} bps floor "
                f"for {mic}."
            )
            print(
                "  No position can pay its own spread here. Lower --cost-bps to a "
                "real rate for this market, or register an adapter for it."
            )
            return 0
        cost_note = (
            f"no adapter for {mic}; using {a.cost_bps} bps with a {a.cost_minimum} minimum per side"
        )

    # --price, --adv and the fee schedule are in the MARKET's currency; --portfolio
    # is the book's, which is MYR. Sizing one against the other without a rate is
    # wrong by exactly that rate, so say so rather than print a plausible number.
    quote = market_currency(mic)
    fx = Decimal(str(a.fx)) if a.fx else None
    if quote != BASE_CURRENCY and fx is None:
        print(f"sizing    {a.instrument}")
        print(
            f"  {mic} prices in {quote}; --portfolio is {BASE_CURRENCY}. Pass "
            f"--fx <{BASE_CURRENCY} per {quote}> so the two can be compared."
        )
        print(
            f"  Without it the position would be off by the {BASE_CURRENCY}/{quote} "
            f"rate and would still look correctly sized."
        )
        return 2

    caps, findings = a13.caps(
        portfolio_value=portfolio,
        stop_distance_frac=stop_frac,
        adv_20d=Decimal(str(a.adv)),
        round_trip_cost_at=round_trip_cost_at,
        risk_per_trade=Decimal(str(a.risk_per_trade)),
        single_name_limit=Decimal(str(a.single_name)),
        win_rate=a.win_rate,
        payoff=a.payoff,
        n_trades=a.n_trades,
        mic=mic,
        fx_base_per_quote=fx,
    )
    print(
        f"sizing    {a.instrument}  portfolio {BASE_CURRENCY} {portfolio:,.2f}  "
        f"stop distance {stop_frac:.1%}"
    )
    print(f"cost      {cost_note}")
    if quote != BASE_CURRENCY:
        print(f"fx        1 {quote} = {BASE_CURRENCY} {fx}")
    for f in findings:
        print(f"  {f.text}")
        for c in f.caveats:
            print(f"    caveat: {c}")

    binding, value = caps.binding()
    units = int(value / price) // a.lot * a.lot
    print(f"\n  binding cap {binding.value} at {quote} {value:,.2f}")
    if units < a.lot:
        print(
            f"  -> no position: the binding cap does not fund one {a.lot}-share lot "
            f"at {quote} {price}"
        )
    else:
        native = Decimal(units) * price
        base = to_base(native, quote, fx)
        shown = f"{quote} {native:,.2f}"
        if quote != BASE_CURRENCY:
            shown += f" = {BASE_CURRENCY} {base:,.2f}"
        print(f"  -> {units:,} units ({shown}) in lots of {a.lot}")
    return 0


# --- teacher --------------------------------------------------------------
def cmd_learn(a) -> int:
    a14 = A14Teacher(context())
    learner = Learner()
    for k in a.mastered or []:
        try:
            learner.mastered(k)
        except KeyError as e:
            print(f"--mastered: {e.args[0]}", file=sys.stderr)
            return 2

    if a.syllabus:
        for f in a14.syllabus(learner):
            print(f"  {f.text}")
        return 0
    if not a.concept:
        print(a14.next_concept(learner).text)
        return 0

    findings = a14.run(a.concept, learner)
    for f in findings:
        print(f.text)
        for c in f.caveats:
            print(f"  caveat: {c}")

    # A concept that does not exist, or one whose prerequisites are unmet, is not
    # a successful lesson. Exiting 0 lets a script think it taught something.
    kinds = {f.kind for f in findings}
    if "unknown_concept" in kinds:
        return 2
    if "prerequisite" in kinds:
        return 2
    return 0


# --- which model is actually answering ------------------------------------
def cmd_doctor(a) -> int:
    from core.doctor import FAIL, render, run_checks

    results = run_checks(offline=a.offline)
    print(render(results))
    return 1 if any(r.status == FAIL and r.critical for r in results) else 0


def cmd_backend(a) -> int:
    """The difference between a real answer and a stub is worth one command."""
    from core.llm.backends import AuthError, backend_from_env

    try:
        backend, reason = backend_from_env(a.use)
    except (AuthError, ValueError) as e:
        print(f"backend unavailable: {e}", file=sys.stderr)
        return 3
    print(f"backend   {type(backend).__name__}")
    print(f"reason    {reason}")
    from core.llm.tiers import MODEL_IDS

    for tier, model in MODEL_IDS.items():
        print(f"  {tier.value:<9} {model}")
    return 0


def cmd_fitness(a) -> int:
    """docs/01 section 10, computed. Mostly a report of what is missing."""
    from core.config import load as load_cfg
    from core.provenance.fitness import compute
    from core.provenance.ledger import ProvenanceLedger

    cfg = load_cfg()
    with ProvenanceLedger(a.db or cfg.provenance_db) as led:
        print(compute(led, window_days=a.days).describe())
    return 0


# ---------------------------------------------------------------- graph
def _graph(db: str | None):
    """Load the built graph, or explain how to build it. Never guesses."""
    from pathlib import Path as _P

    from knowledge.graph.build import DEFAULT_DB
    from knowledge.graph.store import GraphStore

    path = db or DEFAULT_DB
    if path != ":memory:" and not _P(path).exists():
        print(f"no graph at {path}. Build it first:\n\n    make graph\n", file=sys.stderr)
        return None, None
    store = GraphStore(path)
    return store, store.load()


def cmd_graph(a) -> int:
    from knowledge.graph.analyze import graph_diff
    from knowledge.graph.entity_graph import NodeKind, PathRequired, path_to_citations
    from knowledge.graph.evidence import CuratedCorpus

    store, g = _graph(a.db)
    if g is None:
        return 2
    asof = date.fromisoformat(a.asof) if a.asof else date.today()

    def resolve(raw: str) -> str:
        """'Maybank', 'MYX:1155', 'Thermal coal' or a minted id. Shared with the
        MCP tool via EntityGraph.resolve so the two cannot drift."""
        return g.resolve(raw) or raw

    def reject(raw: str, nid: str) -> int:
        options = g.candidates(raw)
        if len(options) > 1:
            print(f"{raw!r} is ambiguous: {', '.join(options)}. Name one of them.", file=sys.stderr)
        else:
            print(f"{raw!r} is not in the graph", file=sys.stderr)
        return 2

    if a.untested:
        from knowledge.graph.analyze import untested_modules

        missing = untested_modules(g, ignore=("tests_", "_pycache"))
        if not missing:
            print("every module has a test importing it.")
            return 0
        print(f"{len(missing)} module{'s' if len(missing) != 1 else ''} no test imports:")
        for nid in missing:
            print(f"  {g.label(nid)}")
        print(
            "\n  INFERRED: a module exercised only through a helper reads as "
            "untested here. The list over-reports rather than under-reports."
        )
        return 0

    if a.uses:
        target = resolve(a.uses)
        if g.node(target) is None:
            matches = [
                n.node_id
                for n in g.nodes()
                if n.label == a.uses or n.node_id.endswith(f".{a.uses}")
            ]
            if len(matches) != 1:
                print(
                    f"{a.uses!r} is not in the graph"
                    + (f"; did you mean one of {matches}?" if matches else ""),
                    file=sys.stderr,
                )
                return 2
            target = matches[0]
        callers = g.inbound(target)
        if not callers:
            print(f"nothing in the graph references {g.label(target)}.")
            return 0
        print(
            f"{len(callers)} reference{'s' if len(callers) != 1 else ''} to "
            f"{g.label(target)} ({target}):"
        )
        for e in sorted(callers, key=lambda e: e.src):
            note = "" if e.citable else "   (inferred from a name, not a binding)"
            print(f"  {e.src} --{e.kind.value}-->{note}")
        return 0

    if a.report:
        from knowledge.graph.report import render

        print(render(g, asof=asof))
        return 0

    if a.benchmark:
        from knowledge.graph import benchmark as bm

        pairs = [
            (f"{g.label(resolve(x))} -> {g.label(resolve(y))}", resolve(x), resolve(y))
            for x, y in (a.benchmark or [])
        ]
        print(bm.describe(bm.run(g, CuratedCorpus(), asof=asof, questions=pairs)))
        return 0

    if a.diff:
        other, og = _graph(a.diff)
        if og is None:
            return 2
        d = graph_diff(og, g)
        print(d.describe())
        other.close()
        return 0

    if a.impact:
        start = resolve(a.impact)
        if g.node(start) is None:
            return reject(a.impact, start)
        holdings = {resolve(h) for h in (a.holding or [])} or {
            n.node_id for n in g.nodes() if n.kind is NodeKind.COMPANY
        }
        hits = g.impact_of(start, holdings, asof=asof)
        if not hits:
            print(
                f"nothing reachable from {g.label(start)} as of {asof}.\n"
                "Traversal is a best-first heuristic, so this means 'not found "
                "cheaply', never 'no connection exists'."
            )
            return 0
        for iid, path in hits:
            print(f"{g.label(iid)}: {path.describe()}")
        return 0

    if a.path:
        src, dst = (resolve(x) for x in a.path)
        for nid, raw in ((src, a.path[0]), (dst, a.path[1])):
            if g.node(nid) is None:
                return reject(raw, nid)
        paths = g.traverse(src, asof=asof, target=dst)
        if not paths:
            print(
                f"no path from {g.label(src)} to {g.label(dst)} as of {asof}.\n"
                "Traversal is a best-first heuristic, so this is 'not found "
                "cheaply', never proof they are unconnected."
            )
            return 0
        best = paths[0]
        print(best.describe())
        corpus = CuratedCorpus()
        try:
            for c in path_to_citations(best, corpus.citation):
                print(f"  [{c.chunk_id}] {c.quoted_span}")
        except PathRequired as e:
            print(f"  uncitable: {e}")
        return 0

    print("nothing asked. Try --path A B, --impact NODE, --report or --benchmark", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    from core.logging import configure as _configure_logging

    _configure_logging()
    ap = argparse.ArgumentParser(
        prog="ask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="what would the system do with this question")
    pl.add_argument("question")
    pl.add_argument("--instrument", action="append", help="repeatable")
    pl.add_argument("--budget", type=float, help="cap in MYR; too small is refused, not cheapened")
    pl.set_defaults(fn=cmd_plan)

    wy = sub.add_parser("why", help="decompose a move before naming a cause")
    wy.add_argument("instrument")
    wy.add_argument("--move", type=float, required=True, help="realised local return, e.g. -0.09")
    wy.add_argument("--market", type=float, required=True, help="index return over the same window")
    wy.add_argument("--sector", type=float, default=0.0)
    wy.add_argument("--fx", type=float, default=0.0, help="base-currency leg")
    wy.add_argument("--currency", default="MYR")
    wy.add_argument("--days", type=int, default=1, help="window length in calendar days")
    wy.add_argument("--on", help="window end date (YYYY-MM-DD), default today")
    wy.add_argument("--history", help="CSV of instrument,market,sector returns")
    wy.add_argument("--beta-market", type=float, default=1.1, help="used only without --history")
    wy.add_argument("--beta-sector", type=float, default=0.5, help="used only without --history")
    wy.add_argument(
        "--fetch",
        action="store_true",
        help="measure --move from the price feed instead of taking it typed",
    )
    wy.add_argument("--against", help="market proxy instrument for --fetch, e.g. XNAS:SPY")
    wy.add_argument("--sector-proxy", help="sector proxy instrument for --fetch")
    wy.set_defaults(fn=cmd_why)

    pr = sub.add_parser("prices", help="daily bars from the live feed")
    pr.add_argument("instrument")
    pr.add_argument("--days", type=int, default=20, help="bars to show")
    pr.add_argument("--on", help="as-at date (YYYY-MM-DD); later bars are not returned")
    pr.set_defaults(fn=cmd_prices)

    th = sub.add_parser("thesis", help="compose a thesis, then red-team it")
    th.add_argument("instrument")
    th.add_argument(
        "--breaker",
        action="append",
        metavar="STATEMENT|QUERY|STORE",
        help="repeatable; fewer than two means no stance may be taken",
    )
    th.add_argument(
        "--evidence",
        action="append",
        metavar="AGENT=TEXT",
        help="repeatable, e.g. a1_fundamentals=CASA fell to 24%%",
    )
    th.add_argument("--stance", default="hold", choices=[s.value for s in Stance])
    th.add_argument("--horizon", type=int, default=12, help="months")
    th.set_defaults(fn=cmd_thesis)

    rk = sub.add_parser("risk", help="concentration, heat and drawdown state of a book")
    rk.add_argument(
        "--position",
        action="append",
        metavar="MIC:CODE:WEIGHT:SECTOR:COUNTRY[:RISK]",
        help="repeatable",
    )
    rk.add_argument("--currency", default="MYR")
    rk.add_argument("--single-name", type=float, help="override the single-name cap")
    rk.add_argument("--equity", type=float)
    rk.add_argument("--peak", type=float, help="peak equity, for drawdown state")
    rk.set_defaults(fn=cmd_risk)

    sz = sub.add_parser("size", help="turn a stance into lots, or into a refusal")
    sz.add_argument("instrument")
    sz.add_argument(
        "--portfolio", type=float, required=True, help=f"investable capital, in {BASE_CURRENCY}"
    )
    sz.add_argument(
        "--price", type=float, required=True, help="in the market's own currency, like --adv"
    )
    sz.add_argument(
        "--fx",
        type=float,
        default=0.0,
        help=f"{BASE_CURRENCY} per 1 unit of the market's currency; "
        f"required for any market that does not price in {BASE_CURRENCY}",
    )
    sz.add_argument("--stop", type=float, required=True)
    sz.add_argument("--adv", type=float, required=True, help="20-day average daily volume")
    sz.add_argument("--lot", type=int, default=100)
    sz.add_argument("--risk-per-trade", type=float, default=0.0075)
    sz.add_argument("--single-name", type=float, default=0.08)
    sz.add_argument(
        "--cost-bps",
        type=float,
        default=46.0,
        help="per-side cost in bps, used only when the market has no adapter",
    )
    sz.add_argument(
        "--cost-minimum",
        type=float,
        default=8.0,
        help="fixed per-side minimum; this is what makes small positions "
        "uneconomic, so a model without one cannot find a floor",
    )
    sz.add_argument("--win-rate", type=float, help="with --payoff, enables the Kelly cap")
    sz.add_argument("--payoff", type=float)
    sz.add_argument("--n-trades", type=int, default=0)
    sz.set_defaults(fn=cmd_size)

    ln = sub.add_parser("learn", help="the curriculum, in an order it enforces")
    ln.add_argument("concept", nargs="?")
    ln.add_argument("--mastered", action="append", help="repeatable")
    ln.add_argument("--syllabus", action="store_true")
    ln.set_defaults(fn=cmd_learn)

    ft = sub.add_parser("fitness", help="can the system score itself yet?")
    ft.add_argument("--days", type=int, default=30, help="window (default 30)")
    ft.add_argument("--db", help="ledger path (default from config)")
    ft.set_defaults(fn=cmd_fitness)

    gr = sub.add_parser("graph", help="the entity graph: paths, impact, review")
    gr.add_argument(
        "--path",
        nargs=2,
        metavar=("FROM", "TO"),
        help="strongest citable path between two entities",
    )
    gr.add_argument("--impact", metavar="NODE", help="what this event or commodity reaches")
    gr.add_argument("--holding", action="append", help="limit --impact to these; repeatable")
    gr.add_argument(
        "--untested",
        action="store_true",
        help="modules no test imports; point --db at the code graph",
    )
    gr.add_argument(
        "--uses", metavar="SYMBOL", help="what references this? point --db at the code graph"
    )
    gr.add_argument(
        "--report", action="store_true", help="hubs, orphans, the review queue, surprising links"
    )
    gr.add_argument(
        "--benchmark",
        nargs=2,
        action="append",
        metavar=("FROM", "TO"),
        help="subgraph vs corpus tokens for this question; repeatable",
    )
    gr.add_argument("--diff", metavar="OTHER_DB", help="what changed against another build")
    gr.add_argument("--asof", help="YYYY-MM-DD; defaults to today")
    gr.add_argument("--db", help="graph database (default data/graph.db)")
    gr.set_defaults(fn=cmd_graph)

    bk = sub.add_parser("backend", help="which model is actually answering")
    bk.add_argument("--use", choices=["anthropic", "echo"], help="force one")
    bk.set_defaults(fn=cmd_backend)

    dr = sub.add_parser("doctor", help="preflight: what this installation can actually do")
    dr.add_argument("--offline", action="store_true", help="skip the two network probes")
    dr.set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
