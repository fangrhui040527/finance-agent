"""A scenario DCF that emits a range, reports the terminal share, and refuses bad inputs.

The model is deliberately small and every driver is named:

  revenue_t   = revenue_{t-1} x (1 + g_t)          g fades linearly to terminal growth
  NOPAT_t     = revenue_t x margin x (1 - tax)
  FCFF_t      = NOPAT_t - reinvestment x (revenue_t - revenue_{t-1})
  TV          = NOPAT_{n+1} x (1 - g / discount) / (discount - g)
                (in perpetuity the return on new capital equals the cost of capital,
                 so reinvestment is g / discount: growth is not free)
  EV          = sum PV(FCFF_t) + PV(TV);  equity = EV - net debt;  per share = equity / shares

Unlevered cash flows are discounted at the WACC. When only a cost of equity
is available the same cash flows are discounted at it and net debt is still
subtracted; the result says so, because it is an approximation, not a model.

Three scenarios (bear, base, bull) are built from the business's own history
when no assumptions are supplied, each labelled with the series it came from.
Every scenario runs through engines/valuation/sanity.py; a fatal flag drops
it, and the range is the bear-to-bull span of what survives. Fewer than two
survivors is a refusal, never a point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from engines.fundamentals.ratios import Statements, cagr
from engines.valuation.cost_of_capital import CostOfCapital, CostOfCapitalTable
from engines.valuation.sanity import History, SanityFlag, check

ONE = Decimal(1)
ZERO = Decimal(0)
DEFAULT_YEARS = 5
DEFAULT_REINVESTMENT = Decimal("0.50")


@dataclass(frozen=True)
class Assumptions:
    years: int
    growth_first_year: Decimal
    terminal_growth: Decimal
    margin: Decimal  # operating margin on revenue
    tax: Decimal
    reinvestment: Decimal  # capital per unit of incremental revenue
    discount: Decimal
    mode: str = "fcff"  # fcff at WACC, or fcfe-approx at the cost of equity

    def growth_path(self) -> tuple[Decimal, ...]:
        """Linear fade from the first-year rate to the terminal rate."""
        if self.years <= 1:
            return (self.growth_first_year,)
        step = (self.growth_first_year - self.terminal_growth) / (self.years - 1)
        return tuple(self.growth_first_year - step * i for i in range(self.years))


@dataclass(frozen=True)
class Scenario:
    name: str
    assumptions: Assumptions
    basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class DcfResult:
    scenario: str
    revenues: tuple[Decimal, ...]
    cash_flows: tuple[Decimal, ...]
    pv_explicit: Decimal
    pv_terminal: Decimal
    ev: Decimal
    terminal_share: Decimal | None
    equity: Decimal | None
    per_share: Decimal | None
    flags: tuple[SanityFlag, ...] = ()

    @property
    def fatal(self) -> bool:
        return any(f.fatal for f in self.flags)


def project(
    base_revenue: Decimal,
    a: Assumptions,
    net_debt: Decimal | None = None,
    shares: Decimal | None = None,
) -> DcfResult:
    if a.discount <= a.terminal_growth:
        raise ValueError("discount rate must exceed terminal growth; the perpetuity is undefined")
    revenues: list[Decimal] = []
    flows: list[Decimal] = []
    prev = base_revenue
    pv = ZERO
    for t, g in enumerate(a.growth_path(), start=1):
        rev = prev * (ONE + g)
        nopat = rev * a.margin * (ONE - a.tax)
        fcff = nopat - a.reinvestment * (rev - prev)
        revenues.append(rev)
        flows.append(fcff)
        pv += fcff / (ONE + a.discount) ** t
        prev = rev
    nopat_next = prev * (ONE + a.terminal_growth) * a.margin * (ONE - a.tax)
    terminal_fcff = nopat_next * (ONE - a.terminal_growth / a.discount)
    tv = terminal_fcff / (a.discount - a.terminal_growth)
    pv_tv = tv / (ONE + a.discount) ** a.years
    ev = pv + pv_tv
    share = (pv_tv / ev) if ev > 0 else None
    equity = None if net_debt is None else ev - net_debt
    per_share = None if equity is None or not shares or shares <= 0 else equity / shares
    return DcfResult("", tuple(revenues), tuple(flows), pv, pv_tv, ev, share, equity, per_share)


def implied_growth(price: Decimal, base: Decimal, discount: Decimal, years: int = 10) -> Decimal:
    """The constant growth for `years` that makes the present value of `base` equal `price`.

    The reverse DCF the valuation agent has always run, in Decimal: a statement
    about the price, not an estimate of value.
    """
    lo, hi = Decimal("-0.20"), Decimal("0.60")
    for _ in range(60):
        g = (lo + hi) / 2
        pv = sum((base * (ONE + g) ** t / (ONE + discount) ** t for t in range(1, years + 1)), ZERO)
        if pv < price:
            lo = g
        else:
            hi = g
    return hi


def _margin_history(s: Statements) -> list[Decimal]:
    revs = {f.period_end: f.value for f in s.annual("revenue")}
    ops = {f.period_end: f.value for f in s.annual("operating_income")}
    out = []
    for period, rev in sorted(revs.items()):
        op = ops.get(period)
        if op is not None and rev > 0:
            out.append(op / rev)
    return out


def _reinvestment_history(s: Statements) -> Decimal | None:
    """Median of (capex - depreciation) per unit of incremental revenue, annual."""
    revs = s.annual("revenue")
    capex = {f.period_end: abs(f.value) for f in s.annual("capex")}
    dep = {f.period_end: f.value for f in s.annual("depreciation")}
    rates = []
    for prev, cur in zip(revs, revs[1:]):
        d_rev = cur.value - prev.value
        if d_rev <= 0 or cur.period_end not in capex:
            continue
        net = capex[cur.period_end] - dep.get(cur.period_end, ZERO)
        rates.append(max(ZERO, net / d_rev))
    if not rates:
        return None
    rates.sort()
    return rates[len(rates) // 2]


def history_of(s: Statements) -> History:
    margins = _margin_history(s)
    g = cagr(s, "revenue")
    return History(
        margin_max=max(margins) if margins else None,
        revenue_cagr=g.value,
        reinvestment=_reinvestment_history(s),
    )


def default_scenarios(
    s: Statements,
    coc: CostOfCapital,
    table: CostOfCapitalTable,
    country: str,
    years: int = DEFAULT_YEARS,
) -> tuple[tuple[Scenario, ...], tuple[str, ...]]:
    """Bear, base and bull from the record. Returns (scenarios, reasons it could not)."""
    reasons: list[str] = []
    discount, which = coc.discount
    if discount is None:
        reasons.append(which)
    margins = _margin_history(s)
    if len(margins) < 2:
        reasons.append("fewer than two annual operating margins stored")
    g = cagr(s, "revenue")
    if g.value is None:
        reasons.append("no revenue compound growth computable (needs two annual revenues)")
    ceiling = table.long_run_growth.get(country)
    if coc.rf is None and ceiling is None:
        reasons.append(
            "no terminal growth ceiling: neither a risk-free rate nor a long-run growth for the country"
        )
    if reasons:
        return (), tuple(reasons)
    assert discount is not None and g.value is not None
    terminal = min(x for x in (coc.rf, ceiling) if x is not None)
    if terminal >= discount:
        terminal = discount - Decimal("0.01")
    tax = coc.tax_rate if coc.tax_rate is not None else Decimal("0.21")
    reinvest = _reinvestment_history(s)
    reinvest_basis = "median (capex - depreciation) per unit of revenue growth, annual"
    if reinvest is None:
        reinvest, reinvest_basis = (
            DEFAULT_REINVESTMENT,
            "default 0.50 per unit of revenue growth (no capex history)",
        )
    margins_sorted = sorted(margins)
    median = margins_sorted[len(margins_sorted) // 2]
    base_g = max(terminal, min(g.value, Decimal("0.25")))
    mode = "fcff" if which == "wacc" else "fcfe-approx"
    mk = lambda name, gf, m: Scenario(  # noqa: E731
        name,
        Assumptions(years, gf, terminal, m, tax, reinvest, discount, mode),
        (
            f"growth {gf:.1%} fading to {terminal:.1%} (revenue CAGR {g.value:.1%} over the stored annual record)",
            f"operating margin {m:.1%} ({name} pick from {len(margins)} annual margins: min {margins_sorted[0]:.1%}, median {median:.1%}, max {margins_sorted[-1]:.1%})",
            f"reinvestment {reinvest:.2f}: {reinvest_basis}",
            f"tax {tax:.1%} ({coc.tax_source or 'default'}), discount {discount:.2%} ({which})",
        ),
    )
    bear = mk("bear", max(terminal, g.value / 2), margins_sorted[0])
    base = mk("base", base_g, median)
    bull = mk(
        "bull", max(base_g, min(g.value * Decimal("1.5"), Decimal("0.30"))), margins_sorted[-1]
    )
    return (bear, base, bull), ()


@dataclass
class ValuationRange:
    instrument_id: str
    asof: date
    currency: str
    low: Decimal | None
    high: Decimal | None
    base: Decimal | None
    per_share: dict[str, Decimal | None]
    results: dict[str, DcfResult]
    dropped: dict[str, tuple[str, ...]]
    caveats: list[str] = field(default_factory=list)
    refused: str | None = None

    @property
    def range(self) -> tuple[Decimal, Decimal] | None:
        return None if self.low is None or self.high is None else (self.low, self.high)


def scenario_range(
    s: Statements,
    coc: CostOfCapital,
    scenarios: tuple[Scenario, ...],
    table: CostOfCapitalTable,
    country: str,
    currency: str = "",
) -> ValuationRange:
    """Run every scenario, drop the fatal ones, and report the span of the survivors."""
    base_rev, _ = s.flow("revenue")
    net_debt = None
    d, _ = s.balance("total_debt")
    c, _ = s.balance("cash")
    if d is not None:
        net_debt = d - (c or ZERO)
    shares = s.latest("shares_outstanding")
    shares_v = shares.value if shares else None
    hist = history_of(s)
    vr = ValuationRange(s.instrument_id, s.asof, currency, None, None, None, {}, {}, {})
    if base_rev is None:
        vr.refused = "no revenue stored: nothing to project"
        return vr
    if net_debt is None:
        vr.caveats.append("no debt stored: enterprise value shown, equity value not derived")
    if shares_v is None:
        vr.caveats.append("no share count stored: no per-share figure")
    if coc.discount[1] != "wacc":
        vr.caveats.append(f"discount rate is the {coc.discount[1]}")
    for sc in scenarios:
        a = sc.assumptions
        try:
            r = project(base_rev, a, net_debt, shares_v)
        except ValueError as e:
            vr.dropped[sc.name] = (str(e),)
            continue
        flags = check(
            terminal_growth=a.terminal_growth,
            discount=a.discount,
            growth_first_year=a.growth_first_year,
            margin=a.margin,
            reinvestment=a.reinvestment,
            terminal_share=r.terminal_share,
            coc=coc,
            table=table,
            country=country,
            history=hist,
        )
        r = DcfResult(
            sc.name,
            r.revenues,
            r.cash_flows,
            r.pv_explicit,
            r.pv_terminal,
            r.ev,
            r.terminal_share,
            r.equity,
            r.per_share,
            flags,
        )
        if r.fatal:
            vr.dropped[sc.name] = tuple(f.message for f in flags if f.fatal)
            continue
        vr.results[sc.name] = r
        vr.per_share[sc.name] = r.per_share
    values = {n: (r.equity if r.equity is not None else r.ev) for n, r in vr.results.items()}
    if len(values) < 2:
        vr.refused = (
            (
                "fewer than two scenarios survive the sanity checks: "
                + "; ".join(f"{n}: {', '.join(why)}" for n, why in vr.dropped.items())
            )
            if vr.dropped
            else "fewer than two scenarios to span a range"
        )
        return vr
    vr.low, vr.high = min(values.values()), max(values.values())
    vr.base = values.get("base")
    if vr.low == vr.high:
        vr.refused = "the scenarios collapse to one value; a range needs a span"
    return vr


def _money(v: Decimal | None, ccy: str) -> str:
    return "n/a" if v is None else f"{ccy} {v:,.0f}".strip()


def valuation_text(vr: ValuationRange, scenarios: tuple[Scenario, ...] = ()) -> str:
    rows = [f"{vr.instrument_id} scenario DCF as of {vr.asof}"]
    if vr.refused:
        rows.append(f"  REFUSED: {vr.refused}")
    else:
        kind = (
            "equity value"
            if all(r.equity is not None for r in vr.results.values())
            else "enterprise value"
        )
        rows.append(
            f"  range ({kind}): {_money(vr.low, vr.currency)} to {_money(vr.high, vr.currency)}; base {_money(vr.base, vr.currency)}"
        )
        ps = {n: v for n, v in vr.per_share.items() if v is not None}
        if ps:
            rows.append("  per share: " + ", ".join(f"{n} {v:,.2f}" for n, v in ps.items()))
    for n, r in vr.results.items():
        share = "n/a" if r.terminal_share is None else f"{r.terminal_share:.0%}"
        rows.append(
            f"  {n}: EV {_money(r.ev, vr.currency)}, terminal share {share}"
            + (f"; flags: {', '.join(f.code for f in r.flags)}" if r.flags else "")
        )
        for f in r.flags:
            rows.append(f"      {f.severity}: {f.message}")
    for n, why in vr.dropped.items():
        rows.append(f"  {n}: dropped - {'; '.join(why)}")
    for sc in scenarios:
        rows.append(f"  what must be true for {sc.name}:")
        for b in sc.basis:
            rows.append(f"      {b}")
    for c in vr.caveats:
        rows.append(f"  caveat: {c}")
    rows.append("  a range, not a target: each end is a stated set of assumptions")
    return "\n".join(rows)
