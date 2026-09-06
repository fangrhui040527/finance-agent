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
from core.config import ConfigError
from core.config import load as load_config
from core.contracts.money import BASE_CURRENCY
from core.guardrails.defaults import default_engine
from core.market.feed import PriceFeedError, default_feed
from core.registry.loader import load as load_registry
from engines.attribution.decompose import MIN_OBSERVATIONS
from engines.attribution.regression import huber_fit
from engines.risk.concentration import Limits, Position
from engines.sizing.caps import cost_floor_bps, cost_floor_unreachable, cost_floor_value, to_base
from markets.registry import get as market_get
from markets.registry import market_currency, mic_of
from ui.render import decomposition_bars, refusal_card

REGISTRY = "agents/registry.yaml"


def context() -> AgentContext:
    """The allowlist comes from the registry, never a hand-written dict."""
    reg = load_registry(REGISTRY)
    # holdings and watchlist come from config.toml. Without them
    # `should_escalate` (knowledge/news/features.py) can never match an article
    # to anything the user owns or is watching, so the news escalation gate was
    # closed on every article regardless of what the file said.
    try:
        cfg = load_config()
        holdings, watchlist = set(cfg.holdings), set(cfg.watchlist)
        corpus_db = cfg.corpus_db
    except ConfigError:
        # A broken settings file must not take out every other command; the
        # config commands report it properly.
        holdings, watchlist, corpus_db = set(), set(), None
    from knowledge.retrieval.index import router_for

    now = datetime.now(UTC)
    return AgentContext(
        # The corpus, indexed. `Router({})` here meant every retrieval was
        # refused before it looked at an article, so the daily sweep filled a
        # database nothing read.
        router=router_for(reg, corpus_db, now=now),
        engine=default_engine(reg.allowlist()),
        now=now,
        holdings=holdings,
        watchlist=watchlist,
    )


def _fit_from_csv(path: str):
    rows, y = [], []
    with open(path, encoding="utf-8", newline="") as fh:
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
    if not getattr(a, "fetch", False) and (a.move is None or a.market is None):
        print(
            "give --move AND --market, or --fetch --against <proxy> to measure both "
            "from the price feed. One typed leg against one measured leg is a "
            "subtraction, not a decomposition.",
            file=sys.stderr,
        )
        return 2
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

    from mcp_server.tools import graph_peers

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
        peers=graph_peers(a.instrument, window[1]),
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
    if a.book:
        return _prices_book(a)
    if not a.instrument:
        print("prices: name an instrument, or pass --book", file=sys.stderr)
        return 2
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


def _prices_book(a) -> int:
    """Warm the price cache for every name in the book and each market's proxy.

    The collector runs this after each close so data/price_cache.db carries
    the day's bars for every name the routine will ask about - the routine
    itself runs where no price host is reachable and reads the cache with
    FINPLANET_OFFLINE=1. Exit 3 if any name could not be fetched; the others
    are still cached.
    """
    from core.market.feed import market_proxy_for

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"prices: {e}", file=sys.stderr)
        return 2
    book = list(dict.fromkeys(tuple(cfg.watchlist) + tuple(cfg.holdings)))
    proxies = [p for p in dict.fromkeys(market_proxy_for(i) for i in book) if p]
    failed = 0
    feed = _feed()
    for iid in book + proxies:
        try:
            series = feed.fetch(iid)
        except PriceFeedError as e:
            failed += 1
            print(f"  {iid:<14} FAILED  {str(e).splitlines()[0][:120]}", file=sys.stderr)
            continue
        last = series.raw()[-1]
        print(
            f"  {iid:<14} {len(series):>5} bars  last {last.day} close {last.close:.4f}"
            f"  via {feed.source_used}"
        )
    print(f"  {'cached':<14} {len(book) + len(proxies) - failed} of {len(book) + len(proxies)}")
    return 3 if failed else 0


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

    valuation_range = None
    if getattr(a, "derive_valuation", False):
        from mcp_server.tools import ToolError, derived_valuation

        try:
            valuation_range, derived = derived_valuation(
                a.instrument, a.as_at or "", a.archetype or "", ctx
            )
        except ToolError as e:
            print(str(e), file=sys.stderr)
            return 2
        findings.extend(derived)

    a10 = A10Thesis(ctx)
    out = a10.run(
        a.instrument,
        findings,
        horizon_months=a.horizon,
        valuation_range=valuation_range,
        proposed_stance=Stance(a.stance),
        breakers=breakers,
    )

    print(f"thesis    {a.instrument}   horizon {a.horizon}m")
    if getattr(a, "derive_valuation", False):
        if valuation_range is not None:
            lo, hi = valuation_range
            print(
                f"  valuation range, derived by the engine: {lo:,.0f} to {hi:,.0f} (a range, not a target)"
            )
        else:
            print(f"  valuation range: none derived - {findings[-1].text}")
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
    red = A11RedTeam(ctx).run(thesis)
    challenges = [f for f in red if f.kind != "analogue"]
    if not challenges:
        print("  (silent - which on a live thesis is itself a finding)")
    for c in sorted(challenges, key=lambda f: -f.numbers.get("severity_rank", 0)):
        kind = c.caveats[0].split(": ")[-1] if c.caveats else "?"
        print(f"  [{kind}] {c.text}")
    analogues = [f for f in red if f.kind == "analogue"]
    if analogues:
        print("\nanalogues (what this resembles, not what will happen)")
        for f in analogues:
            print(f"  - {f.text}")
            for c in f.citations[:1]:
                from mcp_server.tools import cite_label

                print(f"      cites {cite_label(c)}")

    if getattr(a, "narrate", False):
        code = _narrate(ctx, thesis, challenges)
        if code:
            return code
    return 0


def _narrate(ctx, thesis, challenges) -> int:
    """Model prose over engine numbers. The label names the backend, because a
    placeholder that reads like analysis is the failure the label prevents."""
    from decimal import Decimal

    from agents.synthesis.narrate import narrate_thesis
    from core.guardrails.policy import Action, PolicyViolation, Rail
    from core.llm.backends import backend_from_env
    from core.llm.client import InferenceClient
    from core.provenance.ledger import ProvenanceLedger

    backend, reason = backend_from_env()
    cfg = load_config()
    client = InferenceClient(
        backend,
        ctx.engine,
        ProvenanceLedger(cfg.provenance_db),
        daily_budget_myr=Decimal(str(cfg.daily_budget_myr)),
    )
    done = narrate_thesis(client, thesis, challenges)
    if done.refused:
        print(f"\nnarrative refused by the model: {done.refusal_reason}")
        print("  (the analysis above stands; only the prose is missing)")
        return 0
    # The output rail sees the model text BEFORE a human does. A banned verb
    # from the model is the same violation as one typed by hand.
    try:
        ctx.engine.enforce(
            Action(
                name="narrate",
                rail=Rail.OUTPUT,
                agent="a10_thesis",
                payload={"text": done.text},
            )
        )
    except PolicyViolation as e:
        print(f"\nnarrative BLOCKED by the output rail: {e}")
        return 0
    from agents.synthesis.narrate import thesis_digest, unsupported_numbers

    unsupported = unsupported_numbers(done.text, thesis_digest(thesis, challenges))
    if unsupported:
        # Not proof of invention - a rounded restatement lands here too - but
        # every one of these is a number the engines did not supply, and that
        # is the list worth reading before trusting the prose.
        print(f"\n  UNVERIFIED NUMBERS in the narrative: {', '.join(unsupported)}")
        print("  Each appears in the prose and not in the engine output it was given.")
    print(f"\nnarrative  [{type(backend).__name__} - {reason.split(':')[0]}]")
    for line in done.text.strip().splitlines():
        print(f"  {line}")
    print("\n  This is analysis, not advice, and this system cannot place orders.")
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
    from agents.portfolio.agents import TYPED_CAPITAL_NOTE, plan_capital
    from core.config import load as load_cfg

    ctx = context()
    a13 = A13Sizing(ctx)
    if getattr(a, "from_plan", False):
        waterfall, _ = plan_capital(load_cfg(), ctx)
        if waterfall is None:
            print(
                "--from-plan needs a [capital] block in config.toml. Run `ask.py capital`.",
                file=sys.stderr,
            )
            return 2
        if waterfall.investable == 0:
            print("no position: the plan leaves nothing investable today.\n")
            print(waterfall.explain())
            return 0
        portfolio = waterfall.investable
        capital_note = f"capital DERIVED through the waterfall: {portfolio:,.2f} investable"
    elif a.portfolio is None:
        print(
            "give --portfolio, or --from-plan to derive it from [capital] in config.toml.",
            file=sys.stderr,
        )
        return 2
    else:
        portfolio = Decimal(str(a.portfolio))
        capital_note = TYPED_CAPITAL_NOTE
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
    # Resolved before the branch so the no-adapter path below can name it too.
    broker = load_cfg().broker
    try:
        # The BROKER's schedule where the account has one, the venue's otherwise.
        # markets/xnas.py models a zero-commission US account; sizing a moomoo
        # account against it understates the floor by two orders of magnitude.
        from markets.brokers import schedule_for

        schedule = schedule_for(mic, broker)
        # A broker that does not price this venue falls back to the venue's own
        # schedule. Saying "moomoo_my schedule on XKLS" when Bursa's schedule is
        # what was actually used is a label that reads as a fact and is not one.
        on_broker_terms = schedule is not market_get(mic).fee_schedule

        # Two of moomoo's legs are per-share and its commission waiver depends on
        # the share count, so the schedule cannot be costed from a value alone.
        # --price is required on this subcommand, so it is always in hand here.
        def round_trip_cost_at(value: Decimal) -> Decimal:
            return schedule.round_trip(value, price)

        cost_note = f"{broker} schedule on {mic}" if on_broker_terms else f"{mic} fee schedule"

        # The same impossibility the --cost-bps path below already guards, on the
        # path a real account actually takes. moomoo's 0.03% commission is 6 bps
        # round trip at ANY size, above the 5 bps XNAS floor - so the bisection
        # in cost_floor_value never comes down and returns its ceiling, which
        # reads as a USD 100,000,000 position requirement rather than as "this
        # account cannot trade this venue economically at all".
        floor_bps = cost_floor_bps(mic, broker)
        if cost_floor_unreachable(cost_floor_value(round_trip_cost_at, mic, broker)):
            asymptote = schedule.round_trip_bps(Decimal("100000000"), price)
            print(f"sizing    {a.instrument}")
            print(
                f"  no position: on the {cost_note} a round trip costs "
                f"{asymptote.quantize(Decimal('0.01'))} bps at ANY size, above the "
                f"{floor_bps} bps floor for {mic}."
            )
            print(
                "  No position can pay its own spread here. This is a fact about "
                "the account, not about the size you asked for."
            )
            return 0
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
    fx_note = ""
    if quote != BASE_CURRENCY and fx is None and getattr(a, "fetch_fx", False):
        from core.market.fx import BnmFxFeed, FxFeedError
        from core.market.prices import FxStore

        store = FxStore()
        try:
            BnmFxFeed().populate(store)
        except FxFeedError as e:
            print(f"no FX rate: {e}", file=sys.stderr)
            return 3
        hit = store.rate_asof(quote, BASE_CURRENCY, date.today())
        if hit is None:
            print(f"no FX rate: BNM publishes no {quote} rate", file=sys.stderr)
            return 3
        fx, asof = hit
        fx_note = f"  fx 1 {quote} = {BASE_CURRENCY} {fx} (BNM middle rate, {asof})"
    if quote != BASE_CURRENCY and fx is None:
        print(f"sizing    {a.instrument}")
        print(
            f"  {mic} prices in {quote}; --portfolio is {BASE_CURRENCY}. Pass "
            f"--fx <{BASE_CURRENCY} per {quote}>, or --fetch-fx to look it up "
            f"from BNM, so the two can be compared."
        )
        print(
            f"  Without it the position would be off by the {BASE_CURRENCY}/{quote} "
            f"rate and would still look correctly sized."
        )
        return 2

    if fx_note:
        print(fx_note)

    caps, findings = a13.caps(
        portfolio_value=portfolio,
        stop_distance_frac=stop_frac,
        adv_20d=Decimal(str(a.adv)),
        round_trip_cost_at=round_trip_cost_at,
        broker=broker,
        risk_per_trade=Decimal(str(a.risk_per_trade)),
        single_name_limit=Decimal(str(a.single_name)),
        win_rate=a.win_rate,
        payoff=a.payoff,
        n_trades=a.n_trades,
        mic=mic,
        fx_base_per_quote=fx,
    )
    print(f"  {capital_note}")
    print(
        f"sizing    {a.instrument}  portfolio {BASE_CURRENCY} {portfolio:,.2f}  "
        f"stop distance {stop_frac:.1%}"
    )
    print(f"cost      {cost_note}")
    if quote != BASE_CURRENCY:
        print(f"fx        1 {quote} = {BASE_CURRENCY} {fx}")
    # The cost nobody publishes, said out loud. A foreign position is converted
    # in and converted back, so the spread is paid TWICE, and on this account it
    # is larger than the whole fee schedule: about 1% round trip against roughly
    # 0.3% of commission, platform, settlement, duty and levies combined.
    #
    # It is NOT folded into the cost floor. The floor decides refusals, and a
    # refusal that turns on an unmeasured number is a refusal that cannot be
    # defended. So it is reported beside the floor and left out of it, until
    # somebody converts a small amount and measures the thing.
    if quote != BASE_CURRENCY:
        spread = load_cfg().fx_spread_per_side
        if spread > 0:
            rt = ((1 + spread) / (1 - spread) - 1) * Decimal(100)
            print(
                f"  currency  converting {BASE_CURRENCY} to {quote} and back costs about "
                f"{rt.quantize(Decimal('0.01'))}% at an assumed {spread:.2%} spread per side"
            )
            print(
                "            NOT measured and NOT in the cost floor below - moomoo "
                "publishes no spread. See account.fx_spread_per_side in config.toml."
            )
    for f in findings:
        print(f"  {f.text}")
        for c in f.caveats:
            print(f"    caveat: {c}")

    binding, value = caps.binding()
    units = int(value / price) // a.lot * a.lot
    print(f"\n  binding cap {binding.value} at {quote} {value:,.2f}")

    def shown(v: Decimal) -> str:
        native_txt = f"{quote} {v:,.2f}"
        if quote == BASE_CURRENCY:
            return native_txt
        return f"{native_txt} = {BASE_CURRENCY} {to_base(v, quote, fx):,.2f}"

    native = Decimal(units) * price
    if units < a.lot:
        print(
            f"  -> no position: the binding cap does not fund one {a.lot}-share lot "
            f"at {quote} {price}"
        )
    elif native < caps.cost_floor:
        # The floor is computed and PRINTED two lines above, then was ignored
        # here - so this command recommended positions the MCP tool refused for
        # the same inputs. Below the minimum economic position the round trip
        # cannot pay for itself at any edge; that is the whole point of it.
        print(
            f"  -> no position: {units:,} units is {shown(native)}, below the "
            f"{shown(caps.cost_floor)} minimum economic position on {mic}. "
            f"The round trip cannot pay for itself."
        )
    else:
        print(f"  -> {units:,} units ({shown(native)}) in lots of {a.lot}")
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


def cmd_method(a) -> int:
    """The curated method notes, through the same tool the MCP surface serves.

    Exit 2 for an unknown collection, concept or pattern; exit 1 when the
    store holds no matching note, because a script that reads silence as a
    lesson has learned nothing.
    """
    from mcp_server.tools import ToolError, method_note

    try:
        text = method_note(
            a.collection,
            query=" ".join(a.query or []),
            concept=a.concept or "",
            archetype=a.archetype or "",
            pattern=a.pattern or "",
            limit=a.limit,
        )
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(text)
    return 1 if "\nNO NOTE in " in text else 0


def cmd_ratios(a) -> int:
    """The ratio sheet and the earnings-quality scores, from the stored lines."""
    from mcp_server.tools import ToolError, ratio_sheet

    try:
        text = ratio_sheet(a.instrument, as_at=a.as_at or "")
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(text)
    return 1 if text.startswith("NO STATEMENTS STORED") else 0


def cmd_workup(a) -> int:
    """The twelve steps of docs/04 section 2, each honest about what the record answered."""
    from mcp_server.tools import ToolError, analyst_workup

    try:
        text = analyst_workup(a.instrument, as_at=a.as_at or "", archetype=a.archetype or "")
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(text)
    return 0


def cmd_valuation(a) -> int:
    """Cost of capital, then the bear-to-bull scenario DCF; a refusal is an answer."""
    from mcp_server.tools import ToolError, cost_of_capital, valuation_range

    try:
        if a.coc_only:
            text = cost_of_capital(a.instrument, as_at=a.as_at or "", archetype=a.archetype or "")
        else:
            text = valuation_range(
                a.instrument, as_at=a.as_at or "", archetype=a.archetype or "", peers=a.peer or None
            )
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(text)
    return 1 if text.startswith("NO STATEMENTS STORED") or "REFUSED:" in text else 0


# --- which model is actually answering ------------------------------------


def cmd_news(a) -> int:
    """Pull one configured source through the registry and show what arrived.

    The ingest contract on the command line: a broken source raises and exits
    3; a quiet window prints its own emptiness rather than pretending."""
    from datetime import timedelta as _td

    from knowledge.feeds.adapter import FeedError
    from knowledge.feeds.registry import UnknownSource, adapter_for

    try:
        feed = adapter_for(a.source)
    except UnknownSource as e:
        print(str(e), file=sys.stderr)
        return 2
    since = datetime.now(UTC) - _td(hours=a.hours)
    try:
        records = feed.fetch(since, limit=a.limit)
    except FeedError as e:
        print(f"no news: {e}", file=sys.stderr)
        return 3
    articles, stats = feed.normalize(records)
    print(f"{feed.name}  since {a.hours}h ago  {stats}")
    for art in articles[: a.limit]:
        when = art.published_at.strftime("%Y-%m-%d %H:%M")
        print(f"  {when}  {art.source_domain:<24} {art.title}")
    if not articles:
        print("  (a quiet window, reported as one - not an error)")
    return 0


# The sweep's helpers live in knowledge/sweep.py now; re-exported (the
# `name as name` form is the explicit re-export linters honour) so the tests
# that pin their behaviour keep reading them from here.
from knowledge.sweep import SWEEP_DEADLINE_SECONDS as SWEEP_DEADLINE_SECONDS  # noqa: E402
from knowledge.sweep import _fetch_each as _fetch_each  # noqa: E402
from knowledge.sweep import _mostly_failed as _mostly_failed  # noqa: E402
from knowledge.sweep import _rotate as _rotate  # noqa: E402
from knowledge.sweep import _sweep_note as _sweep_note  # noqa: E402


def cmd_fx(a) -> int:
    """Record today's official rates, or show what has been recorded.

    The command `config.toml` has referred to since before it existed. It is
    here now for one reason: `fx_spread_per_side` is the biggest unmeasured
    number in this system, and measuring it has needed two figures at the same
    moment - the rate the broker gave, and the official rate right then.

    Recording the official rate daily removes the timing problem. Convert
    whenever suits, read the rate off the app afterwards, and the official rate
    for that date is already stored to compare against.

    Exit codes match `sweep`: 0 recorded, 2 could not run, 3 the source failed.
    """
    from core.market.fx import BnmFxFeed, FxFeedError
    from core.market.fxlog import FX_DB, FxLog

    with FxLog(a.db or FX_DB) as log:
        if a.show:
            for row in log.history(a.currency, limit=a.limit):
                d = dict(row)
                print(f"  {d['rate_date']}  1 {d['currency']} = MYR {d['rate']}  ({d['source']})")
            counts = log.counts()
            print(
                f"  {'log':<10} {counts['rates']} rates, {counts['currencies']} currencies, "
                f"{counts['days']} days"
            )
            return 0

        try:
            rows = BnmFxFeed().fetch_rates()
        except FxFeedError as e:
            # Same posture as a failed sweep: say so, do not write a silence
            # that will later read as a day the rate did not move.
            print(f"fx: {e}", file=sys.stderr)
            return 3

        # Only what the book touches. BNM publishes 30-odd currencies and this
        # config names one or two; the rest is thirty times the rows to answer a
        # question about USD.
        wanted = tuple(dict.fromkeys(a.currency.upper().split(",")))
        new, held = log.record_all(rows, only=wanted)
        for code in wanted:
            latest = log.history(code, limit=1)
            if latest:
                d = dict(latest[0])
                print(f"  {code:<10} {d['rate_date']}  1 {code} = MYR {d['rate']}")
            else:
                print(f"  {code:<10} not published by the source", file=sys.stderr)
        print(f"  {'recorded':<10} {new} new, {held} already held")
        return 0


def cmd_sweep(a) -> int:
    """Fetch every enabled source for a slot and KEEP what arrives.

    `news` prints one source and forgets it, which is right for a person
    checking a feed by hand. This is the scheduled sibling: for the slot named
    (`bursa_close`, `us_preopen`, `us_close`, `weekly`, or `all`) it resumes
    each enabled source from its last SUCCESSFUL read, writes articles to the
    corpus and figures, events, series and documents to the fact book, links
    the articles into the graph, and records the attempt either way.

    Exit codes are the interface, like `watch`: 0 every source read, 3 a source
    failed OR came back badly degraded, 2 the sweep itself could not run. A
    scheduler can act on those without parsing text - and it needs to, because
    the failure this command exists to make visible is the one that looks like
    a quiet world. A source whose key is absent is SKIPPED, not failed: the row
    names the variable, and the job stays green until someone adds it.
    """
    from core.config import load as load_cfg
    from knowledge.sweep import run_sweep

    try:
        cfg = load_cfg()
    except Exception as e:  # a sweep that dies on its own config is the case 2 exists for
        print(f"sweep could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    report = run_sweep(
        cfg,
        a.slot,
        sources=tuple(a.source) if a.source else None,
        hours=a.hours,
        limit=a.limit,
        corpus_path=a.db or None,
        facts_path=a.facts_db or None,
        graph_db=a.graph_db,
        link_graph=not a.no_graph,
        log=lambda _msg: None,
    )
    if report.could_not_run:
        print(f"sweep could not run: {report.could_not_run}", file=sys.stderr)
        return 2
    print(report.render())
    # Failures also go to stderr, where a scheduler's log and a person's eye
    # both look first; the full table above is the record.
    for r in report.results:
        if r.status in ("failed", "degraded"):
            print(f"  {r.name:<16} {r.status.upper()}: {r.detail}", file=sys.stderr)
    if not (cfg.holdings or cfg.watchlist):
        print(
            "  note             holdings and watchlist are both empty, so the "
            "escalation gate\n                   cannot fire and nothing here "
            "will ever be flagged for review."
        )
    return report.exit_code


def _sources_coverage(cfg, days: int) -> int:
    """What each per-name source delivered ABOUT the name it was asked for.

    A source that returns a hundred articles for Tenaga and mentions Tenaga in
    four is not covering Tenaga, and "100 collected" says the opposite. Only
    rows collected since the corpus started recording which query fetched them
    can be counted; a corpus with none says so rather than printing zeroes.
    """
    from knowledge.corpus import Corpus
    from knowledge.graph.ids import display_names

    names = display_names()
    with Corpus(cfg.corpus_db) as corpus:
        rows = corpus.coverage(days=days)
    if not rows:
        print(
            "NO COVERAGE RECORDED. Articles carry the name they were fetched for only "
            "from 2026-09-06; run a sweep and ask again."
        )
        return 0
    print(
        f"what each per-name source delivered about the name it was asked for"
        f"{f', last {days} days' if days else ''}"
    )
    print(f"\n{'source':<18} {'asked for':<24} {'kept':>6} {'named it':>9} {'share':>7}")
    for source, iid, kept, named in rows:
        label = str(names.get(iid, iid))
        print(f"{source:<18} {label[:22]:<24} {kept:>6} {named:>9} {named / kept:>6.0%}")
    kept_all = sum(r[2] for r in rows)
    named_all = sum(r[3] for r in rows)
    print(
        f"\n{len(rows)} source/name pairs: {named_all} of {kept_all} kept articles "
        f"named the company they were fetched for ({named_all / kept_all:.0%})"
    )
    return 0


def cmd_sources(a) -> int:
    """The source catalogue, and - with --probe - one live fetch of each.

    `--probe` stores nothing. It exists because this repository's development
    environment has no route to any data host: the first real answer from a
    source comes from a GitHub Actions runner, and a table that says which
    sources answered, with what, is what turns a registered candidate into an
    enabled one.
    """
    from core.config import load as load_cfg
    from knowledge.sources import catalog
    from knowledge.sweep import probe

    try:
        cfg = load_cfg()
    except Exception as e:
        print(f"sources could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if getattr(a, "coverage", False):
        return _sources_coverage(cfg, a.days)
    if not a.probe:
        from knowledge.sources.base import configured_keys

        print(catalog.describe(cfg.sources))
        print()
        for name, present in configured_keys().items():
            print(f"  {name:<22} {'set' if present else 'NOT SET - the sources needing it skip'}")
        return 0
    results = probe(cfg, names=a.source or None, hours=a.hours, limit=a.limit, log=lambda _m: None)
    print(f"{'source':<24} {'status':<8} {'time':>6}  detail")
    for r in results:
        print(r.line())
    bad = [r for r in results if r.status in ("failed", "error")]
    print(
        f"\n{len(results)} probed: {sum(r.status == 'ok' for r in results)} ok, "
        f"{sum(r.status == 'no-key' for r in results)} without a key, "
        f"{sum(r.status == 'plan' for r in results)} outside the plan, {len(bad)} failed"
    )
    return 3 if bad else 0


def cmd_digest(a) -> int:
    """The day's page: per name, what was collected, what escalated, what moved.

    Derived from the stores and regenerated on every run - `--write` puts it in
    data/digests/<date>.md and .json (and latest.md), which is what the nightly
    feedback routine reads. Prints the markdown either way.
    """
    from core.config import load as load_cfg
    from knowledge.digest import build_digest, write_digest

    try:
        cfg = load_cfg()
    except Exception as e:
        print(f"digest could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    day = date.fromisoformat(a.date) if a.date else None
    digest = build_digest(
        cfg, day, slot=a.slot, corpus_path=a.db or None, facts_path=a.facts_db or None
    )
    if a.write:
        md, js = write_digest(digest, a.out)
        print(f"wrote {md} and {js}", file=sys.stderr)
    print(digest.to_markdown())
    return 0


def cmd_pack(a) -> int:
    """The feedback pack: the deterministic half of the nightly page (docs/20).

    Moves against each market's proxy from the cached bars, the decomposition,
    the day's digest, the fact book per name, the macro series. Run with
    FINPLANET_OFFLINE=1 where no price host is reachable. A name that cannot
    be measured is a NO DATA row, never a typed leg; the command exits 0 when
    the pack was written and the routine reads the rows.
    """
    from knowledge.pack import build_pack, write_pack

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"pack could not run: {e}", file=sys.stderr)
        return 2
    day = date.fromisoformat(a.date) if a.date else datetime.now(UTC).date() - timedelta(days=1)
    text = build_pack(cfg, day, corpus_path=a.db or None, facts_path=a.facts_db or None)
    if a.write:
        path = write_pack(text, day, a.out)
        print(f"wrote {path}", file=sys.stderr)
    print(text)
    return 0


def cmd_paper(a) -> int:
    """The USD 1,000 paper book (docs/22): init, decide, mark, status, pack, grade.

    Exit codes are the interface: 0 done, 2 refused or nothing to act on
    (a refusal is not an error), 3 a leg could not be priced or a store could
    not be read.
    """
    from dataclasses import replace

    from engines.paper.book import fx_for
    from engines.paper.store import PaperStore

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"paper: {e}", file=sys.stderr)
        return 2
    settings = cfg.paper
    db = a.db or settings.database
    try:
        day = date.fromisoformat(a.date) if a.date else datetime.now(UTC).date()
    except ValueError:
        print("paper: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    fx = fx_for(cfg, a.fx_db or None)

    if a.action == "init":
        try:
            start = date.fromisoformat(a.start) if a.start else settings.start_date
        except ValueError:
            print("paper: --start must be YYYY-MM-DD", file=sys.stderr)
            return 2
        with PaperStore(db) as store:
            try:
                store.init_books(replace(settings, start_date=start), start)
            except ValueError as e:
                print(f"paper init refused: {e}", file=sys.stderr)
                return 2
        print(
            f"opened the paper book at {db}: USD {settings.initial_cash_usd:,.2f}, start {start}, "
            f"observe {settings.observe_weeks} weeks, ramp {settings.ramp_weeks} weeks at "
            f"{settings.ramp_max_invested:.0%}, then {settings.max_invested:.0%} invested at most"
        )
        print("next: `ask.py paper mark` after each close; `ask.py paper status` before deciding")
        return 0

    store = PaperStore.open_existing(db)
    if store is None or not store.has_books():
        print(f"paper: NO BOOK at {db}; run `ask.py paper init` first", file=sys.stderr)
        return 2

    with store:
        feed = default_feed()
        if a.action == "status":
            from engines.paper.report import status, status_json

            st = status(store, cfg, feed, fx, day=day)
            print(status_json(st) if a.json else st.render())
            return 0

        if a.action == "decide":
            from agents.learning.store import LearningStore
            from engines.paper.book import decide

            weights: dict[str, Decimal] = {}
            for part in [x.strip() for x in a.weights.split(",") if x.strip()]:
                if "=" not in part:
                    print(
                        f"paper: --weights entries look like MYX:5183=0.20, got {part!r}",
                        file=sys.stderr,
                    )
                    return 2
                iid, raw = part.split("=", 1)
                try:
                    weights[iid.strip()] = Decimal(raw.strip())
                except ArithmeticError:
                    print(f"paper: {raw!r} is not a weight", file=sys.stderr)
                    return 2
            with LearningStore(a.learning_db or cfg.database) as learning:
                res = decide(
                    store,
                    cfg,
                    feed,
                    fx,
                    day=day,
                    weights=weights,
                    thesis=a.thesis,
                    horizon=a.horizon,
                    confidence=a.confidence,
                    learning=learning,
                    supersede=a.supersede,
                    dry_run=a.dry_run,
                )
            print(res.render())
            return 2 if res.refused else 0

        if a.action == "mark":
            from engines.paper.book import mark

            res = mark(store, cfg, feed, fx, day=day, slot=a.slot)
            print(res.render())
            return res.exit_code

        if a.action == "grade":
            from agents.learning.store import LearningStore
            from engines.paper.grade import grade_due

            with LearningStore(a.learning_db or cfg.database) as learning:
                graded = grade_due(
                    store, cfg, feed, fx, day=day, learning=learning, dry_run=a.dry_run
                )
            if not graded:
                print("nothing due: no paper prediction has reached its grading date")
                return 0
            head = "DRY RUN - would grade" if a.dry_run else "graded"
            print(f"{head} {len(graded)} prediction(s) on {day}")
            for g in graded:
                mark_ = "correct" if g.correct else "wrong"
                print(
                    f"  {g.prediction_id:<40} realised {g.realised:+.2%}  control {g.benchmark:+.2%}  {mark_}  {g.note}"
                )
            return 0

        if a.action == "pack":
            from knowledge.paper.pack import build_paper_pack, write_paper_pack

            text = build_paper_pack(cfg, day, store=store, feed=feed, fx=fx)
            if a.write:
                path = write_paper_pack(text, day, a.out)
                print(f"wrote {path}", file=sys.stderr)
            print(text)
            return 0
    print(f"paper: unknown action {a.action}", file=sys.stderr)
    return 2


def cmd_facts(a) -> int:
    """What the collector holds for one name: figures, events, documents."""
    from knowledge.facts import FactBook
    from knowledge.report import fact_snapshot

    try:
        cfg = load_config()
        mic_of(a.instrument)
    except (ConfigError, ValueError) as e:
        print(f"facts: {e}", file=sys.stderr)
        return 2
    with FactBook(a.facts_db or cfg.facts_db) as book:
        print(fact_snapshot(book, a.instrument, days=a.days))
    return 0


def cmd_macro(a) -> int:
    """Every recorded macro series at its latest point, or one series' recent points."""
    from knowledge.facts import FactBook
    from knowledge.report import macro_context

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"macro: {e}", file=sys.stderr)
        return 2
    with FactBook(a.facts_db or cfg.facts_db) as book:
        print(macro_context(book, a.series, points=a.points))
    return 0


def cmd_watch(a) -> int:
    """Evaluate the monitor rules and record what CHANGED.

    Built to be scheduled. Exit codes are the interface: 0 nothing open,
    1 something is open, 2 the check itself could not run - so a task
    scheduler can act on it without parsing text.
    """
    from core.config import load as load_cfg
    from core.monitor import check

    try:
        result = check(load_cfg(), db=a.db or "", alerts_db=a.alerts_db)
    except Exception as e:  # a monitor that dies silently is the thing it exists to catch
        print(f"monitor could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(result.render())
    return 1 if result.any_open else 0


def cmd_alerts(a) -> int:
    """What is open now, and the history of when things opened and cleared."""
    from core.monitor import AlertLog

    with AlertLog(a.alerts_db) as log:
        open_now = log.open_rules()
        rows = log.history(limit=a.limit)
    if open_now:
        print(f"{len(open_now)} open:")
        for rule, r in sorted(open_now.items()):
            print(f"  [{r['severity']}] {rule}: {r['title']}")
            print(f"      open since {r['at'][:19]}")
    else:
        print("nothing open.")
    if rows:
        print("\nhistory (newest first)")
        for r in rows:
            print(f"  {r['at'][:19]}  {r['state']:<8} {r['rule']:<22} {r['title'][:60]}")
    return 0


def cmd_positions(a) -> int:
    """What the broker says you hold, as distinct from what config.toml says.

    Deliberately does NOT write config.toml. A holdings list is an input to
    every concentration and rebalancing figure in this system, and a command
    that silently rewrote it would make a portfolio change with no diff and no
    decision. This prints; you paste.
    """
    from core.broker.account import BrokerError
    from core.broker.moomoo import MoomooAccountFeed

    try:
        snap = MoomooAccountFeed(market=a.market).snapshot()
    except BrokerError as e:
        print(f"account   unavailable: {e}", file=sys.stderr)
        return 3

    stamp = snap.as_of.strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"account   {snap.source}  as of {stamp}")
    print(f"cash      {snap.currency} {snap.cash:,.2f}")
    if snap.is_empty:
        # An empty account reached us as a real answer, not as a failure - the
        # feed raises for a broken link. Say which it was.
        print("holdings  none. The account was read and holds nothing.")
        return 0

    print(f"holdings  {len(snap.positions)}")
    for p in snap.positions:
        print(
            f"  {p.instrument_id:<14} {p.units:>10,.0f} units  "
            f"avg {p.currency} {p.avg_cost:>9,.4f}  value {p.currency} {p.market_value:>12,.2f}"
        )
    print()
    print("  To use these, paste into [account] holdings in config.toml:")
    book = ", ".join(
        f'{{ id = "{p.instrument_id}", units = {p.units:.0f}, avg_cost = {p.avg_cost} }}'
        for p in snap.positions
    )
    print(f"    holdings = [{book}]")
    print("  Add `stop` and `sector` per name: without a stop the risk-budget cap")
    print("  cannot be computed, and without a sector each name counts as its own.")
    return 0


def cmd_capital(a) -> int:
    """How much money is allowed to be in stocks at all.

    docs/05 section 2 puts this before any question about which stock. The
    first three steps are locked: no flag in this API reduces the emergency
    floor, funds a near-term goal out of equities, or lets equities outrank
    debt above the hurdle.
    """
    from agents.portfolio.agents import plan_capital
    from core.config import load as load_cfg

    cfg = load_cfg()
    waterfall, findings = plan_capital(cfg, context())
    if waterfall is None:
        print("no [capital] plan in config.toml.")
        print(
            "  Fill liquid_assets and essential_monthly_spend (plus any goals and\n"
            "  liabilities) and this command derives what is investable. Until then\n"
            "  `size` needs --portfolio, which bypasses the emergency floor, the\n"
            "  near-term goals and the debt hurdle."
        )
        return 2
    print(waterfall.explain())
    for f in findings:
        for c in f.caveats:
            print(f"\n  {c}")
    if waterfall.investable == 0:
        print("\n  Nothing is investable today. That is an answer, not a failure.")
    return 0


def cmd_allocate(a) -> int:
    """Split capital across names YOU nominate. It does not choose them."""
    from agents.portfolio.agents import TYPED_CAPITAL_NOTE, plan_capital
    from core.config import load as load_cfg
    from engines.sizing.allocate import allocate
    from mcp_server.protocol import ToolError
    from mcp_server.tools import _candidates

    cfg = load_cfg()
    if a.from_plan:
        waterfall, _ = plan_capital(cfg, context())
        if waterfall is None:
            print("--from-plan needs a [capital] block. Run `ask.py capital`.", file=sys.stderr)
            return 2
        investable = waterfall.investable
        note = f"capital derived through the waterfall: {investable:,.2f}"
    elif a.portfolio is None:
        print("give --portfolio, or --from-plan to derive it from [capital].", file=sys.stderr)
        return 2
    else:
        investable = Decimal(str(a.portfolio))
        note = TYPED_CAPITAL_NOTE

    if not a.name:
        print(
            "nominate names with --name MIC:CODE:PRICE:STOP:ADV:SECTOR (repeatable).\n"
            "This system does not choose them - it sizes and bounds the ones you bring.",
            file=sys.stderr,
        )
        return 2

    fx_notes: list[str] = []
    try:
        candidates = _candidates(list(a.name), fetch=a.fetch, end=None, notes=fx_notes)
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2

    result = allocate(
        investable,
        candidates,
        limits=cfg.limits,
        risk_per_trade=Decimal(str(a.risk_per_trade)),
        single_name_limit=Decimal(str(a.single_name)),
    )
    print(f"  {note}")
    for fx_note in fx_notes:
        print(f"  {fx_note}")
    print()
    print(result.explain())
    return 0


def cmd_rebalance(a) -> int:
    """What to change versus what you hold. The book lives in config.toml."""
    from mcp_server.protocol import ToolError
    from mcp_server.tools import rebalance_book

    try:
        text = rebalance_book(
            names=list(a.name or []),
            portfolio_value=a.portfolio,
            from_plan=a.from_plan,
            as_at=a.as_at or "",
            single_name_limit=a.single_name,
            risk_per_trade=a.risk_per_trade,
        )
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 2
    if text.startswith("NOTHING TO REBALANCE"):
        print(text, file=sys.stderr)
        return 2
    print(text)
    return 0


def cmd_doctor(a) -> int:
    from core.doctor import FAIL, render, run_checks

    results = run_checks(offline=a.offline)
    print(render(results))
    return 1 if any(r.status == FAIL and r.critical for r in results) else 0


def cmd_backend(a) -> int:
    """The difference between a real answer and a stub is worth one command."""
    from core.llm.backends import AuthError, backend_from_env

    if getattr(a, "list", False):
        return _print_providers()
    try:
        backend, reason = backend_from_env(a.use)
    except (AuthError, ValueError) as e:
        print(f"backend unavailable: {e}", file=sys.stderr)
        return 3
    print(f"backend   {type(backend).__name__}")
    print(f"reason    {reason}")
    from core.llm.backends import backend_name, effort_reaches, models_by_tier
    from core.llm.tiers import (
        MESSAGES_TIERS,
        cheap_capped,
        effective_tier,
        profile_for,
        selected_effort,
        selection_note,
    )

    # Under a pin the table must show what will ACTUALLY be called and billed.
    # Printing the unpinned model here is how a disclosure command ends up
    # disclosing the wrong thing. The reasoning column is the same rule applied
    # to effort: what the request will carry, in the form that model accepts.
    # And the model column comes from the BACKEND, because on a free provider
    # the routing table's Claude ids are not what answers.
    models = models_by_tier(backend)
    split = type(backend).__name__ == "SplitBackend"
    for tier, model in models.items():
        landed = effective_tier(tier)
        shape = profile_for(landed)
        if landed in MESSAGES_TIERS:
            if not effort_reaches(backend, landed):
                how = f"max {shape.max_tokens}, effort dial not sent to this provider"
            else:
                if shape.thinking_budget is not None:
                    how = f"thinking budget {shape.thinking_budget}"
                elif shape.effort:
                    how = f"effort {shape.effort}"
                else:
                    how = "no thinking"
                how = f"{how}, max {shape.max_tokens}" + (", streamed" if shape.stream else "")
            if split:
                how = f"{backend_name(backend, landed)}, {how}"
        else:
            how = "not a Messages model"
        moved = "" if landed is tier else f"   (pinned from {model})"
        print(f"  {tier.value:<9} {models[landed]:<24} {how}{moved}")
    note = selection_note()
    if note:
        print(f"  selection {note}")
    if cheap_capped():
        print("  every Messages tier is on the cheapest model; unset the pin to")
        print("  spend at each tier's own rate.")
    if selected_effort() is None:
        print("  effort unset: each tier keeps its own default (reason high, balanced")
        print("  medium, cheap none). --effort low|medium|high|xhigh|max overrides it.")
    return 0


def _print_providers() -> int:
    """The free-provider catalogue, with whether each one could answer right now."""
    from core.llm import providers
    from core.llm.tiers import Tier

    print("free providers (docs/21; catalogued from awesome-free-models, 2026-09-03)")
    print("  LLM_BACKEND=<name> selects one; LLM_BACKEND=free takes the first keyed one")
    for p in providers.PROVIDERS:
        if p.key_env is None:
            state = "keyless"
        elif p.key() is not None:
            state = f"{p.key_env} set"
        else:
            state = f"{p.key_env} unset"
        pace = f"{p.rpm}/min" if p.rpm else "unpaced"
        print(f"\n  {p.name:<18} {state:<24} {pace}")
        if p.aliases:
            print(f"    also       {', '.join(p.aliases)}")
        print(f"    endpoint   {p.base_url or '(LLM_BASE_URL)'}")
        for tier in (Tier.REASON, Tier.BALANCED, Tier.CHEAP):
            print(
                f"    {tier.value:<10} {p.models.get(tier) or '(LLM_MODEL_' + tier.value.upper() + ')'}"
            )
        print(f"    note       {p.note}")
    print("\n  per-tier model override: LLM_MODEL_REASON / _BALANCED / _CHEAP, or LLM_MODEL")
    print("  per-tier backend override: LLM_BACKEND_REASON / _BALANCED / _CHEAP")
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

    if getattr(a, "peers", None):
        from knowledge.graph.peers import peers_of

        target = resolve(a.peers)
        if g.node(target) is None:
            return reject(a.peers, target)
        ps = peers_of(g, a.peers, asof)
        print(ps.text())
        corpus = CuratedCorpus()
        for p in ps.peers:
            for doc in p.evidence:
                c = corpus.citation(doc)
                print(f"      [{doc}] {c.quoted_span if c else 'evidence unavailable'}")
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
    from core.env import load as _load_dotenv
    from core.logging import configure as _configure_logging

    _load_dotenv()
    _configure_logging()
    ap = argparse.ArgumentParser(
        prog="ask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Model and effort are one selection, available to every subcommand, and
    # they set the same environment variables an operator would export - so a
    # flag and a shell export cannot mean two different things. The flag wins
    # for the length of one command, which is the whole point of having it.
    def _selection(parser, *, suppress: bool) -> None:
        # On the subparsers the default is SUPPRESS, not None. With a plain
        # default the subparser would write None over whatever the top-level
        # parser had already parsed, and `ask.py --effort max why ...` would
        # silently think at the default level - the exact silent-selection
        # failure the disclosure line exists to prevent.
        default = argparse.SUPPRESS if suppress else None
        parser.add_argument(
            "--model",
            metavar="NAME",
            default=default,
            help="pin every Messages tier to one model: haiku | sonnet | opus "
            "(or the exact model id). Default: the task class routes it.",
        )
        parser.add_argument(
            "--effort",
            metavar="LEVEL",
            default=default,
            help="how hard the model thinks: low | medium | high | xhigh | max. "
            "On Haiku 4.5, which takes no effort parameter, this becomes a "
            "thinking budget of the matching size.",
        )

    _selection(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="what would the system do with this question")
    pl.add_argument("question")
    pl.add_argument("--instrument", action="append", help="repeatable")
    pl.add_argument("--budget", type=float, help="cap in MYR; too small is refused, not cheapened")
    pl.set_defaults(fn=cmd_plan)

    wy = sub.add_parser("why", help="decompose a move before naming a cause")
    wy.add_argument("instrument")
    # NOT required: --fetch exists to MEASURE these from the feed, and demanding
    # them anyway made the measured path - the one this command is for -
    # unreachable without typing the numbers you were asking it to measure.
    # cmd_why enforces the real rule: typed, or fetched, never half of each.
    wy.add_argument("--move", type=float, help="realised local return, e.g. -0.09")
    wy.add_argument("--market", type=float, help="index return over the same window")
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
    pr.add_argument("instrument", nargs="?", default="")
    pr.add_argument(
        "--book", action="store_true", help="warm the cache for every book name and market proxy"
    )
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
    th.add_argument(
        "--narrate",
        action="store_true",
        help="add three model-written paragraphs over the engine numbers (echo backend prints a labelled placeholder)",
    )
    th.add_argument("--stance", default="hold", choices=[s.value for s in Stance])
    th.add_argument("--horizon", type=int, default=12, help="months")
    th.add_argument(
        "--derive-valuation",
        action="store_true",
        help="derive the bear-to-bull range from the stored record via the engines",
    )
    th.add_argument("--as-at", help="YYYY-MM-DD for the derived range; default today")
    th.add_argument("--archetype", help="sector archetype for the derived range, e.g. bank")
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
    # Not required: --from-plan derives it from [capital] through the waterfall.
    # A typed figure still works, and is disclosed as the bypass it is.
    sz.add_argument(
        "--portfolio",
        type=float,
        help=f"investable capital in {BASE_CURRENCY}, typed (bypasses the waterfall)",
    )
    sz.add_argument(
        "--from-plan",
        action="store_true",
        help="derive investable capital from [capital] in config.toml, applying the "
        "emergency floor, near-term goals and debt hurdle",
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
    sz.add_argument(
        "--fetch-fx",
        action="store_true",
        help="look the rate up from Bank Negara Malaysia (keyless, dated) "
        "instead of typing --fx; the as-of date is printed with the answer",
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

    mt = sub.add_parser(
        "method", help="the curated method notes, cited (kb_craft, kb_method_*, kb_failures)"
    )
    mt.add_argument(
        "collection",
        help="kb_craft | kb_method_valuation | kb_method_technical | kb_method_risk | kb_failures",
    )
    mt.add_argument("query", nargs="*", help="free text")
    mt.add_argument("--concept", help="curriculum key, e.g. cash_flow")
    mt.add_argument("--archetype", help="sector archetype, e.g. bank")
    mt.add_argument("--pattern", help="failure pattern, e.g. accruals_divergence")
    mt.add_argument("--limit", type=int, default=4)
    mt.set_defaults(fn=cmd_method)

    rs = sub.add_parser(
        "ratios", help="ratio sheet and earnings-quality scores from the stored statement lines"
    )
    rs.add_argument("instrument")
    rs.add_argument("--as-at", help="YYYY-MM-DD; default today")
    rs.set_defaults(fn=cmd_ratios)

    vl = sub.add_parser(
        "valuation", help="cost of capital and a bear-to-bull scenario DCF; never a point target"
    )
    vl.add_argument("instrument")
    vl.add_argument("--as-at", help="YYYY-MM-DD; default today")
    vl.add_argument(
        "--archetype",
        help="bank | utility | cyclical | software | semis | chemicals | hospital | gaming | holding",
    )
    vl.add_argument("--peer", action="append", help="peer instrument id; repeatable")
    vl.add_argument("--coc-only", action="store_true", help="only the cost of capital")
    vl.set_defaults(fn=cmd_valuation)

    wk = sub.add_parser(
        "workup", help="the 12-step analyst workup over the stored record; a workup, not a stance"
    )
    wk.add_argument("instrument")
    wk.add_argument("--as-at", help="YYYY-MM-DD; default today")
    wk.add_argument("--archetype", help="sector archetype, e.g. bank, software")
    wk.set_defaults(fn=cmd_workup)

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
    gr.add_argument(
        "--peers", metavar="INSTRUMENT", help="who the graph says the peers are, with evidence"
    )
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
    bk.add_argument(
        "--use",
        help="force one: anthropic, echo, free, or a free provider (groq, gemini, openrouter, "
        "mistral, nvidia, ollama, openai-compatible)",
    )
    bk.add_argument(
        "--list", action="store_true", help="the free-provider catalogue and which keys are set"
    )
    bk.set_defaults(fn=cmd_backend)

    nw = sub.add_parser("news", help="pull one source through the feed registry")
    nw.add_argument("source", help="a name from knowledge/feeds/registry.py, e.g. gdelt")
    nw.add_argument("--hours", type=int, default=24, help="window back from now")
    nw.add_argument("--limit", type=int, default=20)
    nw.set_defaults(fn=cmd_news)

    sw = sub.add_parser("sweep", help="fetch every enabled source and KEEP what arrives")
    sw.add_argument(
        "--source", action="append", help="one source; repeatable. Default: [sources] enabled"
    )
    sw.add_argument(
        "--hours", type=int, default=24, help="window when a source has never been swept"
    )
    sw.add_argument("--limit", type=int, default=250, help="max records per source")
    sw.add_argument("--db", default="", help="corpus database (default: [sources] corpus_database)")
    sw.add_argument(
        "--facts-db", default="", help="fact book database (default: [sources] facts_database)"
    )
    sw.add_argument(
        "--slot",
        default="all",
        choices=["all", "bursa_close", "us_preopen", "us_close", "weekly"],
        help="which moment of the day this is; decides which sources and names run",
    )
    sw.add_argument("--graph-db", default="data/graph.db", help="graph to link articles into")
    sw.add_argument("--no-graph", action="store_true", help="store only; do not touch the graph")
    sw.set_defaults(fn=cmd_sweep)

    so = sub.add_parser("sources", help="the source catalogue; --probe fetches each once")
    so.add_argument("--probe", action="store_true", help="one live fetch per source, store nothing")
    so.add_argument("--source", action="append", help="probe only this source; repeatable")
    so.add_argument("--hours", type=int, default=48, help="probe window")
    so.add_argument("--limit", type=int, default=3, help="items per probe")
    so.add_argument(
        "--coverage",
        action="store_true",
        help="what each per-name source delivered about the name it was asked for",
    )
    so.add_argument("--days", type=int, default=0, help="--coverage window; 0 is everything")
    so.set_defaults(fn=cmd_sources)

    dg = sub.add_parser("digest", help="the day's page per name, from the stores")
    dg.add_argument("--date", default="", help="YYYY-MM-DD; default today (UTC)")
    dg.add_argument("--slot", default="all", help="label only: which run produced it")
    dg.add_argument("--write", action="store_true", help="also write data/digests/<date>.md/.json")
    dg.add_argument("--out", default="data/digests", help="where --write puts the files")
    dg.add_argument("--db", default="", help="corpus database")
    dg.add_argument("--facts-db", default="", help="fact book database")
    dg.set_defaults(fn=cmd_digest)

    pk = sub.add_parser("pack", help="the feedback pack: the deterministic half of the night")
    pk.add_argument("--date", default="", help="YYYY-MM-DD; default yesterday (UTC)")
    pk.add_argument(
        "--write", action="store_true", help="also write knowledge/feedback/<date>.pack.md"
    )
    pk.add_argument("--out", default="knowledge/feedback", help="where --write puts the file")
    pk.add_argument("--db", default="", help="corpus database")
    pk.add_argument("--facts-db", default="", help="fact book database")
    pk.set_defaults(fn=cmd_pack)

    pa = sub.add_parser(
        "paper", help="the USD 1,000 paper book: init, decide, mark, status, pack, grade"
    )
    pa.add_argument("action", choices=("init", "decide", "mark", "status", "pack", "grade"))
    pa.add_argument("--db", default="", help="paper ledger (default: [paper] database)")
    pa.add_argument("--date", default="", help="YYYY-MM-DD; default today (UTC)")
    pa.add_argument("--start", default="", help="init: the book's start date")
    pa.add_argument(
        "--weights",
        default="",
        help='decide: "MYX:5183=0.20,XNAS:NVDA=0.22"; a held name left out is an exit',
    )
    pa.add_argument("--thesis", default="", help="decide: why, in a sentence or two; required")
    pa.add_argument("--confidence", type=float, default=0.55, help="decide: in (0, 1)")
    pa.add_argument("--horizon", type=int, default=21, help="decide: sessions until graded")
    pa.add_argument("--supersede", action="store_true", help="decide: replace today's decision")
    pa.add_argument("--dry-run", action="store_true", help="decide/grade: print, write nothing")
    pa.add_argument("--learning-db", default="", help="prediction log (default: [learning])")
    pa.add_argument("--fx-db", default="", help="rate log (default: data/fx.db)")
    pa.add_argument(
        "--slot", default="manual", choices=("bursa_close", "us_close", "manual", "all")
    )
    pa.add_argument("--json", action="store_true", help="status: print JSON")
    pa.add_argument("--write", action="store_true", help="pack: write <date>.pack.md too")
    pa.add_argument("--out", default="knowledge/paper", help="pack: where --write puts it")
    pa.set_defaults(fn=cmd_paper)

    fc = sub.add_parser("facts", help="what the collector holds for one name")
    fc.add_argument("instrument")
    fc.add_argument("--days", type=int, default=30, help="event window back from now")
    fc.add_argument("--facts-db", default="", help="fact book database")
    fc.set_defaults(fn=cmd_facts)

    mc = sub.add_parser("macro", help="the recorded macro series, or one of them")
    mc.add_argument("series", nargs="?", default="", help="e.g. DGS10 or BNM:OPR; empty for all")
    mc.add_argument("--points", type=int, default=5, help="recent points for one series")
    mc.add_argument("--facts-db", default="", help="fact book database")
    mc.set_defaults(fn=cmd_macro)

    fx = sub.add_parser("fx", help="record the official MYR rate for the day, or show the log")
    fx.add_argument("--currency", default="USD", help="comma-separated; default USD")
    fx.add_argument("--show", action="store_true", help="print what is recorded and stop")
    fx.add_argument("--limit", type=int, default=30, help="rows to show")
    fx.add_argument("--db", default="", help="rate log path (default: data/fx.db)")
    fx.set_defaults(fn=cmd_fx)

    wt = sub.add_parser("watch", help="evaluate the monitor rules; exit 1 if anything is open")
    wt.add_argument("--db", help="provenance ledger path")
    wt.add_argument("--alerts-db", default="data/alerts.db")
    wt.set_defaults(fn=cmd_watch)

    al = sub.add_parser("alerts", help="what is open, and when things opened and cleared")
    al.add_argument("--alerts-db", default="data/alerts.db")
    al.add_argument("--limit", type=int, default=20)
    al.set_defaults(fn=cmd_alerts)

    al2 = sub.add_parser("allocate", help="split capital across names you nominate")
    al2.add_argument(
        "--name",
        action="append",
        metavar="MIC:CODE:PRICE:STOP:ADV:SECTOR",
        help="repeatable; leave PRICE and ADV empty with --fetch to measure them",
    )
    al2.add_argument("--portfolio", type=float, help="investable capital, typed")
    al2.add_argument("--from-plan", action="store_true", help="derive it from [capital]")
    al2.add_argument("--fetch", action="store_true", help="measure empty price/adv from the feed")
    al2.add_argument("--risk-per-trade", type=float, default=0.0075)
    al2.add_argument("--single-name", type=float, default=0.08)
    al2.set_defaults(fn=cmd_allocate)

    rb = sub.add_parser("rebalance", help="what to change versus what you hold")
    rb.add_argument(
        "--name",
        action="append",
        metavar="MIC:CODE:PRICE:STOP:ADV:SECTOR",
        help="extra names to consider alongside the book (repeatable)",
    )
    rb.add_argument("--portfolio", type=float, help="capital; default is the book's own value")
    rb.add_argument("--from-plan", action="store_true", help="derive capital from [capital]")
    rb.add_argument("--as-at", help="point-in-time bound for prices (YYYY-MM-DD)")
    rb.add_argument("--risk-per-trade", type=float, default=0.0075)
    rb.add_argument("--single-name", type=float, default=0.08)
    rb.set_defaults(fn=cmd_rebalance)

    ps = sub.add_parser("positions", help="what the broker says you hold (read-only)")
    ps.add_argument(
        "--market",
        default="MY",
        help="which market's account to read: MY, US, HK, SG. Needs OpenD running "
        "and logged in on this machine; nothing here holds your password.",
    )
    ps.set_defaults(fn=cmd_positions)

    cp = sub.add_parser("capital", help="how much may be invested at all, from [capital]")
    cp.set_defaults(fn=cmd_capital)

    dr = sub.add_parser("doctor", help="preflight: what this installation can actually do")
    dr.add_argument("--offline", action="store_true", help="skip the two network probes")
    dr.set_defaults(fn=cmd_doctor)

    # A global flag that only worked BEFORE the subcommand is a flag people
    # write after it and are told does not exist. Both positions accept it.
    for _p in sub.choices.values():
        _selection(_p, suppress=True)

    a = ap.parse_args(argv)
    # Set them before dispatch, and validate immediately: a typo that silently
    # selected Opus would be found on the invoice, not on the screen.
    import os

    if a.model:
        os.environ["FINPLANET_MODEL"] = a.model
    if a.effort:
        os.environ["FINPLANET_EFFORT"] = a.effort
    if a.model or a.effort:
        from core.llm.tiers import ModelSelectionError, pinned_tier, selected_effort

        try:
            pinned_tier(), selected_effort()
        except ModelSelectionError as e:
            print(f"{e}", file=sys.stderr)
            return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
