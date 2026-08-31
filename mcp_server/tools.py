"""The tool surface Claude drives.

Design rule, and the reason this file is worth reading before adding to it:

    **The model reasons. The engines decide.**

Every number here is computed by code that was already tested - attribution,
concentration, the five sizing caps, the cost floors, the prediction log. Claude
is handed typed results and writes the narrative. It is never handed a lever that
lets it *choose* a number the engines are supposed to bound, because a model that
can talk its way past a position limit is not a risk system.

So three invariants hold across every tool below:

  1. **Nothing bypasses the guardrail chain.** Each tool enforces through the
     same `PolicyEngine` `ask.py` uses, built from the same registry allowlist.
  2. **A refusal is a successful answer.** When a guardrail denies, or a cap
     forbids a position, the tool RETURNS that text. It is not an error. The
     model must be able to read "no, and here is why" and reason about it.
  3. **No tool places, routes, or simulates an order.** `test_no_execution_anywhere`
     greps this directory like every other.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from agents.base import AgentContext, Finding
from agents.learning.teacher import A14Teacher, Learner
from agents.portfolio.agents import A12PortfolioRisk, A13Sizing
from agents.supervisor import A0Supervisor
from agents.synthesis.agents import (
    A9Attribution,
    A10Thesis,
    A11RedTeam,
    Breaker,
    Stance,
)
from core.contracts.money import BASE_CURRENCY
from core.guardrails.defaults import default_engine
from core.market.feed import ChainedFeed, PriceFeedError, default_feed
from core.registry.loader import load as load_registry
from engines.attribution.decompose import MIN_OBSERVATIONS, decompose
from engines.attribution.regression import huber_fit
from engines.risk.concentration import Limits, Position
from engines.sizing.caps import cost_floor_bps, cost_floor_value, to_base
from knowledge.retrieval.pipeline import Router
from markets.registry import get as market_get
from markets.registry import known_prefixes, market_currency, mic_of, supported
from mcp_server.protocol import ToolError
from ui.render import decomposition_bars

REGISTRY = "agents/registry.yaml"
DISCLAIMER = (
    "\n\nNot financial advice. This is analysis with an evidence chain, not a "
    "recommendation to transact. No order is placed by any tool here."
)


def context() -> AgentContext:
    reg = load_registry(REGISTRY)
    return AgentContext(
        router=Router({}), engine=default_engine(reg.allowlist()), now=datetime.now(UTC)
    )


def _feed() -> ChainedFeed:
    return default_feed()


def _dec(value, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ToolError(f"{field} must be a number, got {value!r}") from None
    if not d.is_finite():
        raise ToolError(f"{field} must be a finite number, got {value!r}")
    return d


def _positive(value, field: str) -> Decimal:
    """Finite AND strictly positive.

    Every one of these guards was added because the stress suite got a real
    position out of an input that is not a quantity:

      - a NEGATIVE portfolio makes `concentration_cap` negative, and a negative
        cap WINS `binding()` - the same inversion docs/05 3.5 records for
        `liquidity_cap`, arriving through a new door. The engine fix stopped a
        cap computing negative; nothing stopped a caller feeding one a negative
        portfolio.
      - an INFINITE portfolio sizes cleanly off whichever cap is finite.
      - a ZERO ADV yields a liquidity cap of 0, and "0 units" is reported as a
        sizing outcome rather than as an untradeable instrument.

    None of these crash. All of them produce a number that looks like an answer,
    which is the failure mode this whole system is built against.
    """
    d = _dec(value, field)
    if d <= 0:
        raise ToolError(
            f"{field} must be positive, got {value!r}. A non-positive {field} is "
            f"not a smaller quantity - it inverts the caps that bound it."
        )
    return d


def _lines(findings: list[Finding]) -> str:
    out = []
    for f in findings:
        out.append(f"- {f.text}")
        for c in f.caveats:
            out.append(f"    caveat: {c}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# markets
# --------------------------------------------------------------------------


def market_info(market: str = "") -> str:
    """What a market costs and how it trades. The facts sizing depends on."""
    if not market:
        rows = ["Markets with an adapter:", ""]
        for mic in supported():
            a = market_get(mic)
            floor = cost_floor_value(a.fee_schedule.round_trip, mic)
            rows.append(
                f"  {mic}  {a.country}/{a.currency}  T{a.tier}  "
                f"floor {cost_floor_bps(mic)} bps  "
                f"minimum economic position {floor:,.0f} {a.currency}"
            )
        rows += [
            "",
            f"Accepted id prefixes: {', '.join(known_prefixes())}",
            "Anything else is refused rather than guessed.",
        ]
        return "\n".join(rows)

    try:
        mic = mic_of(market) if ":" in market else market
        a = market_get(mic)
    except (KeyError, ValueError) as e:
        raise ToolError(str(e)) from None

    fs = a.fee_schedule
    floor_value = cost_floor_value(fs.round_trip, a.mic)
    legs = "\n".join(
        f"    {l.name:<12} rate {l.rate}  min {l.minimum}  cap {l.cap}" for l in fs.legs
    )
    sample = "\n".join(
        f"    {v:>12,}  {fs.round_trip(Decimal(v)) / Decimal(v) * 10000:>7.1f} bps round trip"
        for v in (1000, 10000, 100000, 1000000)
    )
    return (
        f"{a.mic} - {a.country}, {a.currency}, tier {a.tier}\n"
        f"  index {a.local_index}   settlement T+{a.settlement_days}\n"
        f"  accounting {a.accounting_standard.value}   known_at {a.known_at_strategy.value}\n"
        f"  sessions {len(a.calendar.windows)} window(s)\n\n"
        f"  fee legs\n{legs}\n\n"
        f"  cost by size\n{sample}\n\n"
        f"  cost floor {cost_floor_bps(a.mic)} bps\n"
        f"  MINIMUM ECONOMIC POSITION {floor_value:,.2f} {a.currency}\n"
        f"  Below this the round trip cannot pay for itself at any edge."
    )


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------


def get_prices(instrument: str, bars: int = 30, as_at: str = "") -> str:
    """Daily bars from the live feed, bounded by an as-at date."""
    end = _parse_date(as_at) if as_at else None
    try:
        series = _feed().fetch(instrument, end=end)
    except PriceFeedError as e:
        # A coverage or transport failure is a real answer, not a crash: the
        # model needs to know the data is absent rather than infer a quiet market.
        return f"NO DATA for {instrument}: {e}"

    window = series.raw()[-max(1, bars) :]
    head = f"{instrument}: {len(series)} bars held, showing {len(window)}"
    rows = "\n".join(
        f"  {b.day}  O {b.open:>9.3f}  H {b.high:>9.3f}  L {b.low:>9.3f}  "
        f"C {b.close:>9.3f}  V {b.volume:>13,.0f}"
        for b in window
    )
    tail = ""
    if len(window) > 1:
        ret = (window[-1].close / window[0].close) - 1.0
        tail = (
            f"\n\n  return over shown window {ret:+.2%}"
            f"\n  20d ADV {series.adv(20):,.0f}   20d ATR {series.atr(20):.4f}"
        )
    return f"{head}\n{rows}{tail}"


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ToolError(f"as_at must be YYYY-MM-DD, got {s!r}") from None


# --------------------------------------------------------------------------
# attribution - the "why did it move" question
# --------------------------------------------------------------------------


def why_did_it_move(
    instrument: str,
    instrument_return: float | None = None,
    market_return: float | None = None,
    sector_return: float | None = None,
    fx_return: float = 0.0,
    market_proxy: str = "",
    bars: int = 1,
    as_at: str = "",
    beta_market: float = 1.1,
    beta_sector: float = 0.5,
    currency: str = "MYR",
) -> str:
    """Decompose a move BEFORE naming a cause.

    Either supply the returns, or give market_proxy and both legs are measured
    from the feed. A measured instrument leg against a typed market leg is not a
    decomposition, so mixing them is refused.
    """
    end = _parse_date(as_at) if as_at else None
    measured = False
    window = ((end or date.today()) - timedelta(days=max(1, bars)), end or date.today())

    if market_proxy:
        try:
            if instrument == market_proxy:
                instrument_return, first, last = _window_return(instrument, bars, end)
                market_return = instrument_return
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=2) as pool:
                    fa = pool.submit(_window_return, instrument, bars, end)
                    fb = pool.submit(_window_return, market_proxy, bars, end)
                    instrument_return, first, last = fa.result()
                    market_return, _, _ = fb.result()
            window, measured = (first, last), True
        except PriceFeedError as e:
            return f"NO DATA: {e}"
    elif instrument_return is None or market_return is None:
        raise ToolError(
            "give instrument_return AND market_return, or give market_proxy to "
            "measure both from the feed. One measured leg against one typed leg "
            "is a subtraction, not a decomposition."
        )

    import math as _math

    for label, v in (
        ("instrument_return", instrument_return),
        ("market_return", market_return),
        ("sector_return", sector_return),
        ("fx_return", fx_return),
    ):
        if v is not None and not _math.isfinite(float(v)):
            raise ToolError(
                f"{label} must be finite, got {v!r}. docs/05 3.5: a NaN return "
                f"once reached a verdict as `nan% unexplained`."
            )

    fit = _synthetic_fit(beta_market, beta_sector)
    sector = sector_return if sector_return is not None else 0.0
    exp = decompose(
        instrument,
        window,
        market_return,
        sector,
        {},
        instrument_return,
        fx_return,
        fit,
        base_currency=currency,
    )

    a9 = A9Attribution(context())
    findings = a9.run(
        instrument,
        window,
        realised_local=instrument_return,
        event_market=market_return,
        event_sector=sector,
        event_styles={},
        fx_return=fx_return,
        fit=fit,
        base_currency=currency,
    )

    provenance = (
        f"returns MEASURED from the price feed, {window[0]} to {window[1]}"
        if measured
        else "returns as SUPPLIED by the caller, not measured"
    )
    caveat = (
        ""
        if measured
        else "\n\nBetas are stated, not estimated from a real window. Treat the split "
        "between market and sector as illustrative until a factor model is fitted."
    )
    return (
        f"{decomposition_bars(exp)}\n\n"
        f"{_lines(findings)}\n\n"
        f"provenance: {provenance}{caveat}{DISCLAIMER}"
    )


def _window_return(instrument: str, bars_back: int, end: date | None):
    series = _feed().fetch(instrument, end=end)
    raw = series.raw()
    if len(raw) < bars_back + 1:
        raise PriceFeedError(
            f"{instrument} has {len(raw)} bars up to {end or 'today'}, "
            f"need {bars_back + 1} for a {bars_back}-bar return"
        )
    win = raw[-(bars_back + 1) :]
    return (win[-1].close / win[0].close) - 1.0, win[0].day, win[-1].day


def _synthetic_fit(beta_market: float, beta_sector: float, seed: int = 7):
    import random

    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [beta_market * a + beta_sector * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


def fit_factor_model(returns_csv: str) -> str:
    """Fit real betas from `instrument,market,sector` rows.

    Refuses below MIN_OBSERVATIONS rather than fitting betas to noise.
    """
    rows, y = [], []
    for line in returns_csv.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            inst, mkt, sec = float(parts[0]), float(parts[1]), float(parts[2])
        except (ValueError, IndexError):
            continue
        y.append(inst)
        rows.append([mkt, sec])
    if len(y) < MIN_OBSERVATIONS:
        return (
            f"REFUSED: {len(y)} usable rows, below the {MIN_OBSERVATIONS} the "
            f"engine requires. Fitting betas to fewer is fitting to noise."
        )
    try:
        fit = huber_fit(rows, y)
    except ValueError as e:
        return f"REFUSED: the history cannot support a factor model ({e})."
    alpha, beta_market, beta_sector = fit.coefficients[0], *fit.coefficients[1:3]
    fragile = ""
    if fit.r_squared < 0.2:
        fragile = (
            "\n\n  WARNING: r-squared below 0.20. These factors explain little of "
            "this instrument's variance, so the market/sector split they "
            "produce is weakly identified. Read the unexplained share, not "
            "the components."
        )
    return (
        f"Huber fit on {fit.n} observations\n"
        f"  alpha        {alpha:+.5f} per period\n"
        f"  beta_market  {beta_market:+.3f}\n"
        f"  beta_sector  {beta_sector:+.3f}\n"
        f"  r-squared    {fit.r_squared:.3f}\n"
        f"  residual sigma {fit.residual_sigma:.5f}{fragile}"
    )


# --------------------------------------------------------------------------
# thesis and its red team
# --------------------------------------------------------------------------


def compose_thesis(
    instrument: str,
    evidence: list | None = None,
    breakers: list | None = None,
    stance: str = "hold",
    horizon_months: int = 12,
) -> str:
    """Assemble findings into a stance, then attack it.

    evidence: [{"agent": "a1_fundamentals", "text": "..."}]
    breakers: [{"statement": "...", "query": "...", "store": "kb_filings"}]

    A breaker needs an executable query. One without is a wish, and is refused
    here rather than defaulted in - the check is the only thing that makes a
    breaker a breaker.
    """
    ctx = context()
    try:
        st = Stance(stance)
    except ValueError:
        raise ToolError(
            f"stance must be one of {[s.value for s in Stance]}, got {stance!r}"
        ) from None

    findings = []
    for e in evidence or []:
        if not isinstance(e, dict) or not e.get("agent") or not e.get("text"):
            raise ToolError(f"evidence items need 'agent' and 'text', got {e!r}")
        findings.append(Finding(str(e["agent"]), "supplied", str(e["text"])))

    brks = []
    for b in breakers or []:
        if not isinstance(b, dict):
            raise ToolError(f"breaker must be an object, got {b!r}")
        missing = [k for k in ("statement", "query", "store") if not b.get(k)]
        if missing:
            raise ToolError(
                f"breaker missing {missing}. A breaker with no executable query "
                f"cannot be checked, and an unfalsifiable thesis is not a thesis."
            )
        try:
            brks.append(
                Breaker(statement=str(b["statement"]), query=str(b["query"]), store=str(b["store"]))
            )
        except ValueError as e:
            raise ToolError(str(e)) from None

    a10 = A10Thesis(ctx)
    out = a10.run(
        instrument, findings, horizon_months=horizon_months, proposed_stance=st, breakers=brks
    )
    thesis = a10.last

    challenges = A11RedTeam(ctx).run(thesis)
    ranked = sorted(challenges, key=lambda f: -f.numbers.get("severity_rank", 0))
    attack = (
        "\n".join(
            f"  [{c.caveats[0].split(': ')[-1] if c.caveats else '?'}] {c.text}" for c in ranked
        )
        or "  (silent, which on a live thesis is itself a finding)"
    )

    return (
        f"THESIS  {instrument}  horizon {horizon_months}m\n"
        f"{_lines(out)}\n"
        f"  actionable: {'yes' if thesis.is_actionable() else 'NO'}\n"
        f"  stance asked for: {st.value}   stance reached: {thesis.stance.value}\n\n"
        f"WHAT WOULD FALSIFY IT\n"
        + (
            "\n".join(f"  - {b.statement}  [{b.query} against {b.store}]" for b in thesis.breakers)
            or "  (none - so no stance may be taken)"
        )
        + f"\n\nRED TEAM\n{attack}{DISCLAIMER}"
    )


# --------------------------------------------------------------------------
# portfolio risk and sizing
# --------------------------------------------------------------------------


def check_portfolio_risk(
    positions: list | None = None,
    base_currency: str = "MYR",
    single_name_limit: float | None = None,
    equity: float | None = None,
    peak_equity: float | None = None,
) -> str:
    """Concentration, heat, effective bets and every breach.

    positions: [{"instrument":"MYX:1155","weight":0.22,"sector":"bank",
                 "country":"MY","risk_to_stop":0.01}]
    """
    parsed = []
    for p in positions or []:
        if not isinstance(p, dict):
            raise ToolError(f"position must be an object, got {p!r}")
        for k in ("instrument", "weight", "sector", "country"):
            if p.get(k) in (None, ""):
                raise ToolError(f"position missing {k!r}: {p!r}")
        try:
            weight = float(p["weight"])
            risk = float(p.get("risk_to_stop", 0.0))
        except (TypeError, ValueError):
            raise ToolError(f"weight and risk_to_stop must be numbers: {p!r}") from None
        import math as _math

        if not (_math.isfinite(weight) and _math.isfinite(risk)):
            raise ToolError(f"weight and risk_to_stop must be finite: {p!r}")
        if weight < 0:
            raise ToolError(
                f"weight must not be negative: {p!r}. A negative weight is a short, "
                f"and the concentration limits here are not defined over shorts."
            )
        # The currency defaults to the MARKET's, not to the country field. They
        # are different strings for the same fact and only one of them is a
        # currency: `country="MY"` gave `currency="MY"`, which is not "MYR", so
        # a book of nothing but Bursa names reported 100% foreign-currency
        # exposure and breached the 50% limit. A refusal caused by a field
        # nobody was reading.
        try:
            default_ccy = market_currency(mic_of(str(p["instrument"])))
        except ValueError:
            default_ccy = BASE_CURRENCY
        parsed.append(
            Position(
                instrument_id=str(p["instrument"]),
                weight=weight,
                sector=str(p["sector"]),
                country=str(p["country"]),
                currency=str(p.get("currency") or default_ccy).upper(),
                risk_to_stop=risk,
            )
        )

    try:
        limits = Limits(single_name=single_name_limit) if single_name_limit else Limits()
    except ValueError as e:
        return f"REFUSED: {e}"

    out = A12PortfolioRisk(context()).run(
        parsed,
        limits=limits,
        base_currency=base_currency,
        equity=_dec(equity, "equity") if equity is not None else None,
        peak_equity=_dec(peak_equity, "peak_equity") if peak_equity is not None else None,
    )
    return f"BOOK  {len(parsed)} positions, base {base_currency}\n{_lines(out)}{DISCLAIMER}"


def investable_capital(db: str = "") -> str:
    """How much money is allowed to be in stocks at all, from [capital].

    docs/05 section 2 puts this before any question about which stock. Three
    steps are LOCKED and no argument here reduces them: the emergency floor,
    near-term goals inside 24 months, and debt above the hurdle.
    """
    from agents.portfolio.agents import plan_capital
    from core.config import load as load_config

    cfg = load_config()
    waterfall, findings = plan_capital(cfg, context())
    if waterfall is None:
        return (
            "NO PLAN: config.toml has no [capital] block, so investable capital "
            "cannot be derived.\n"
            "  Fill liquid_assets and essential_monthly_spend (plus any goals and "
            "liabilities).\n"
            "  Until then size_position needs portfolio_value supplied by the caller, "
            "which BYPASSES the emergency floor, near-term goals and debt hurdle." + DISCLAIMER
        )
    body = waterfall.explain()
    notes = "\n".join(f"  {c}" for f in findings for c in f.caveats)
    tail = ""
    if waterfall.investable == 0:
        tail = (
            "\n\n  NO CAPITAL: the floor and reservations consume everything liquid. "
            "The correct amount to invest today is zero - an answer, not a failure."
        )
    return f"{body}\n{notes}{tail}{DISCLAIMER}"


def size_position(
    instrument: str,
    portfolio_value: float,
    price: float,
    stop_price: float,
    adv_20d: float,
    risk_per_trade: float = 0.0075,
    single_name_limit: float = 0.08,
    participation: float = 0.05,
    win_rate: float | None = None,
    payoff: float | None = None,
    n_trades: int = 0,
    fx_myr_per_unit: float | None = None,
) -> str:
    """Five caps, the binding one, and the market's own fee schedule.

    "No position" is an outcome, not a failure - and it is the right one more
    often than the interface makes it feel.

    `price` and `adv_20d` are in the MARKET's currency; `portfolio_value` is in
    MYR. For any market that does not price in MYR, pass `fx_myr_per_unit` - the
    two cannot be compared without it, and comparing them anyway is wrong by
    exactly the exchange rate while looking entirely reasonable.
    """
    pv = _positive(portfolio_value, "portfolio_value")
    px = _positive(price, "price")
    stop = _positive(stop_price, "stop_price")
    adv = _positive(adv_20d, "adv_20d")
    risk_frac = _positive(risk_per_trade, "risk_per_trade")
    name_limit = _positive(single_name_limit, "single_name_limit")
    part = _positive(participation, "participation")
    if stop >= px:
        return (
            f"REFUSED: stop {stop} is at or above the entry {px}. "
            f"A stop above entry is not a stop, and the risk cap it feeds "
            f"would be meaningless."
        )

    try:
        mic = mic_of(instrument)
        adapter = market_get(mic)
    except (ValueError, KeyError) as e:
        return (
            f"REFUSED: {e}\nSizing needs the market's real fee schedule. A "
            f"flat-bps stand-in has no fixed minimum, and it is the minimum "
            f"that makes small positions uneconomic."
        )

    quote = market_currency(mic)
    fx = _positive(fx_myr_per_unit, "fx_myr_per_unit") if fx_myr_per_unit else None
    if quote != BASE_CURRENCY and fx is None:
        return (
            f"REFUSED: {mic} prices in {quote} and portfolio_value is "
            f"{BASE_CURRENCY}. Pass fx_myr_per_unit (how many {BASE_CURRENCY} one "
            f"{quote} buys) so the cap and the price can be compared. Without it "
            f"the size is wrong by the {BASE_CURRENCY}/{quote} rate and still looks "
            f"correctly bounded by whichever cap it names."
        )

    a13 = A13Sizing(context())
    caps, findings = a13.caps(
        portfolio_value=pv,
        stop_distance_frac=(px - stop) / px,
        adv_20d=adv,
        round_trip_cost_at=adapter.fee_schedule.round_trip,
        risk_per_trade=risk_frac,
        single_name_limit=name_limit,
        participation=part,
        win_rate=win_rate,
        payoff=payoff,
        n_trades=n_trades,
        mic=mic,
        fx_base_per_quote=fx,
    )
    binding, value = caps.binding()
    lot = adapter.lot_size(instrument)
    units = int(value / px) // lot * lot
    floor = cost_floor_value(adapter.fee_schedule.round_trip, mic)

    def shown(v: Decimal) -> str:
        """Native always; MYR alongside only when it is a different number."""
        native = f"{quote} {v:,.2f}"
        return (
            native
            if quote == BASE_CURRENCY
            else f"{native} ({BASE_CURRENCY} {to_base(v, quote, fx):,.2f})"
        )

    if units < lot:
        verdict = (
            f"NO POSITION: the binding cap ({binding.value}, {shown(value)}) does "
            f"not fund one {lot}-share lot at {quote} {px}."
        )
    elif Decimal(units) * px < floor:
        verdict = (
            f"NO POSITION: {units:,} units is {shown(Decimal(units) * px)}, below "
            f"the {shown(floor)} minimum economic position on {mic}. The round "
            f"trip cannot pay for itself."
        )
    else:
        verdict = (
            f"{units:,} units = {shown(Decimal(units) * px)}, in lots of {lot}, "
            f"bound by {binding.value}."
        )

    lot_note = ""
    lot_known = getattr(adapter, "lot_size_is_known", None)
    if lot_known is not None and not lot_known(instrument):
        lot_note = (
            f"\n  WARNING: {lot} is the DEFAULT board lot, not a looked-up fact "
            f"for this issuer. A wrong lot produces an order that cannot fill."
        )

    fx_line = "" if quote == BASE_CURRENCY else f"  fx 1 {quote} = {BASE_CURRENCY} {fx}\n"
    # portfolio_value arrives as a number the caller chose. That is exactly what
    # the waterfall exists to produce, so say which locks did not apply to it.
    from agents.portfolio.agents import TYPED_CAPITAL_NOTE

    capital_line = f"  {TYPED_CAPITAL_NOTE}\n"
    return (
        f"SIZING  {instrument} on {mic}\n"
        f"{capital_line}"
        f"  portfolio {BASE_CURRENCY} {pv:,.2f}  entry {quote} {px}  "
        f"stop {quote} {stop}  stop distance {((px - stop) / px):.2%}\n"
        f"{fx_line}"
        f"{_lines(findings)}\n"
        f"  minimum economic position on {mic}: {shown(floor)}{lot_note}\n\n"
        f"  {verdict}{DISCLAIMER}"
    )


# --------------------------------------------------------------------------
# planning, teaching, the forward record
# --------------------------------------------------------------------------


def plan_question(
    question: str, instruments: list | None = None, budget_myr: float | None = None
) -> str:
    """What the system would do with a question, and what it would refuse."""
    a0 = A0Supervisor(context())
    plan = a0.plan(
        question,
        budget_myr=_dec(budget_myr, "budget_myr") if budget_myr else None,
        instrument_ids=tuple(str(i) for i in (instruments or ())),
    )
    if not plan.allowed:
        r = plan.refusal
        if r is None:  # a disallowed plan always carries a refusal; belt and braces
            return "REFUSED: the supervisor declined this question and gave no reason"
        return f"REFUSED: {r.reason}\nWhat would help: {r.what_would_help}"
    agents = "\n".join(f"    {a}" for a in plan.agents)
    notes = "\n".join(f"  note: {n}" for n in plan.notes)
    return (
        f"intent {plan.intent.value}\n"
        f"estimated cost about RM {plan.estimated_cost.amount:.2f}\n"
        f"agents ({len(plan.agents)}):\n{agents}\n{notes}"
    )


def explain_concept(concept: str = "", mastered: list | None = None) -> str:
    """The curriculum, in the order it enforces. Prerequisites are not optional."""
    a14 = A14Teacher(context())
    learner = Learner()
    for k in mastered or []:
        try:
            learner.mastered(str(k))
        except KeyError as e:
            raise ToolError(e.args[0]) from None
    if not concept:
        return a14.next_concept(learner).text
    return _lines(a14.run(concept, learner))


def log_hypothesis(
    title: str,
    thesis: str,
    db=None,
) -> str:
    """Register the IDEA above the predictions. Append-only, like everything
    else in the learning store: a hypothesis can gain events and links but can
    never be edited, so a thesis cannot quietly survive its own dead calls."""
    from agents.learning.hypotheses import HypothesisStore
    from agents.learning.store import DEFAULT_PATH

    if not title.strip() or not thesis.strip():
        raise ToolError("a hypothesis needs both a title and a falsifiable thesis")
    with HypothesisStore(db or DEFAULT_PATH) as store:
        try:
            hid = store.create(title, thesis, created_by="mcp")
        except (ValueError, sqlite3.IntegrityError) as e:
            return f"REFUSED: {e}"
        n = len(store.all())
    return (
        f"registered {hid} (status: exploring)\n"
        f"  the registry holds {n} hypothesis(es)\n"
        f"  link predictions to it with log_prediction + predict.py hypothesis link"
    )


def log_prediction(
    instrument: str, direction: int, horizon_days: int, confidence: float, thesis: str, db: str = ""
) -> str:
    """Write a view down BEFORE the outcome is known. The clock P16 needs.

    Append-only: a logged prediction can never be edited or deleted. That is the
    whole value - a record you can revise is not a record.
    """
    from agents.learning.reflection import Horizon
    from agents.learning.store import DEFAULT_PATH, LearningStore, Prediction

    if direction not in (1, -1):
        raise ToolError("direction must be +1 or -1")
    if not 0.0 < confidence < 1.0:
        raise ToolError("confidence must be strictly between 0 and 1")
    if not thesis.strip():
        raise ToolError("a prediction with no thesis cannot be learned from")
    try:
        horizon = Horizon(f"{horizon_days}d")
    except ValueError:
        raise ToolError(
            f"horizon_days must be one of {sorted(int(h.value[:-1]) for h in Horizon)}; "
            f"got {horizon_days}. A horizon set at logging time is what makes the "
            f"grade date non-negotiable."
        ) from None

    made_at = datetime.now(UTC)
    made = made_at.date()
    # Sessions -> calendar days, same arithmetic as predict.py _grade_date.
    grade_on = (made_at + timedelta(days=horizon.sessions / 5 * 7)).date()
    p = Prediction(
        prediction_id=f"{instrument}-{made}-{horizon_days}d",
        instrument_id=instrument,
        agent="mcp",
        made_at=made_at,
        horizon=horizon,
        statement=thesis,
        direction=direction,
        confidence=confidence,
        grade_on=grade_on,
    )
    with LearningStore(db or DEFAULT_PATH) as store:
        try:
            store.record(p)
        except sqlite3.IntegrityError as e:
            # The append-only trigger or a duplicate id refusing the write is an
            # ANSWER. Anything else - AttributeError, disk full - used to render
            # identically, so a code defect read as a policy refusal.
            return f"REFUSED: {e}"
        counts = store.counts()
    return (
        f"logged {p.prediction_id}\n"
        f"  direction {direction:+d}  horizon {horizon_days}d  "
        f"confidence {confidence:.2f}\n"
        f"  gradeable on or after {grade_on}\n"
        f"  store now holds {counts}"
    )


def calibration_status(db: str = "") -> str:
    """Are the confident calls actually right more often? The only honest score."""
    from agents.learning.store import DEFAULT_PATH, LearningStore

    with LearningStore(db or DEFAULT_PATH) as store:
        pairs = store.calibration_pairs()
        counts = store.counts()
        pending = store.pending()

    if not pairs:
        return (
            f"No graded predictions yet. Store holds {counts}.\n"
            f"{len(pending)} pending. Calibration is a forward record: it "
            f"cannot be back-filled, only waited for."
        )

    buckets: dict[str, list[bool]] = {}
    for conf, hit in pairs:
        key = f"{int(conf * 10) * 10}-{int(conf * 10) * 10 + 9}%"
        buckets.setdefault(key, []).append(hit)
    rows = "\n".join(
        f"  stated {k:<8} n={len(v):<4} actual {sum(v) / len(v):.0%}"
        for k, v in sorted(buckets.items())
    )
    return (
        f"CALIBRATION on {len(pairs)} graded predictions\n{rows}\n\n"
        f"  Stated above actual = overconfident. {len(pending)} still pending."
    )


def explain_path(a: str, b: str, asof: str = "", db: str = "") -> str:
    """Why are these two connected, and how strongly - the multi-hop question.

    docs/02 section 5 names this as the whole point of P9: vector search cannot
    answer "the Strait of Hormuz closed, which of my holdings is exposed",
    because no single document contains the chain.

    Three things this returns that a similarity search cannot: the chain itself,
    a weight that decays per hop so a three-hop guess is visibly weaker than a
    filing, and one citation per link - which the caller may verify.
    """
    from datetime import date as _date
    from pathlib import Path as _P

    from knowledge.graph.build import DEFAULT_DB
    from knowledge.graph.entity_graph import PathRequired, path_to_citations
    from knowledge.graph.evidence import CuratedCorpus
    from knowledge.graph.store import GraphStore

    path_db = db or DEFAULT_DB
    if path_db != ":memory:" and not _P(path_db).exists():
        return (
            f"REFUSED: no graph at {path_db}. Build it with `make graph` "
            f"(offline, no keys). Answering without one would mean guessing."
        )
    try:
        on = _date.fromisoformat(asof) if asof else _date.today()
    except ValueError:
        return f"REFUSED: as-of {asof!r} is not an ISO date (YYYY-MM-DD)."

    with GraphStore(path_db) as store:
        g = store.load()

        src, dst = (g.resolve(a) or a), (g.resolve(b) or b)
        for nid, raw in ((src, a), (dst, b)):
            if g.node(nid) is None:
                options = g.candidates(raw)
                if len(options) > 1:
                    return (
                        f"REFUSED: {raw!r} is ambiguous - it could be "
                        f"{', '.join(options)}. Name one; guessing which "
                        f"you meant would answer a different question."
                    )
                return (
                    f"REFUSED: {raw!r} is not in the graph. It holds "
                    f"{len(g.nodes())} entities; add it to "
                    f"knowledge/graph/data/ and rebuild rather than "
                    f"inferring what it might be."
                )
        if src == dst:
            return f"REFUSED: {a!r} and {b!r} are the same entity."

        paths = g.traverse(src, asof=on, target=dst)
        if not paths:
            return (
                f"No path from {g.label(src)} to {g.label(dst)} as of {on}.\n\n"
                f"Traversal is a best-first heuristic, not an exhaustive "
                f"search, so this means NOT FOUND CHEAPLY - never that the "
                f"two are unconnected. Do not report them as unrelated."
            )

        best = paths[0]
        lines = [
            f"{g.label(src)} -> {g.label(dst)}, as of {on}",
            f"  {best.describe()}",
            f"  strength: {best.strength} ({best.n_hops} hop"
            f"{'s' if best.n_hops != 1 else ''}, weight {best.weight:.2f})",
        ]
        if best.n_hops >= 3:
            lines.append("  three or more hops: treat as speculative")

        corpus = CuratedCorpus()
        try:
            cites = path_to_citations(best, corpus.citation)
            lines.append("\n  evidence, one document per link:")
            lines += [f"    [{c.chunk_id}] {c.quoted_span}" for c in cites]
        except PathRequired as exc:
            lines.append(
                f"\n  NOT CITABLE: {exc}\n  The chain exists and cannot support an emitted claim."
            )

        if len(paths) > 1:
            lines.append(
                f"\n  {len(paths) - 1} weaker route"
                f"{'s' if len(paths) > 2 else ''} also found; the "
                f"strongest is shown."
            )
        return "\n".join(lines)
