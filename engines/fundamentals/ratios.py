"""Ratios from stored statement lines: point-in-time, and every missing input named.

The fact book stores what the collectors saw, each line stamped with the day
it became knowable. Nothing here fetches; nothing here trusts a vendor's
ready-made ratio. A margin is gross profit over revenue from the rows the
store holds as of the valuation date, read through `FactStore.as_known_at`,
so a figure filed after that date is invisible - the same look-ahead guard the
backtests use.

Two conventions the collectors share (knowledge/sources/sec_xbrl.py,
eodhd.py, fmp.py):

  * quarterly flows under the plain concept key (`revenue`), annual flows under
    `<concept>_fy`, balance-sheet instants under the plain key with the
    statement date as `period_end`;
  * signs as reported: FMP stores capital expenditure negative, the SEC positive.
    `Statements.capex()` takes the absolute value, once, here.

Honest partials: a ratio whose input is not stored has `value None` and names
the concept, and `FILLED_BY` names the collector that would supply it. Six
Bursa names have no statement source on a free plan today; their sheet says
"0 of N computable" and which source would change that, which is the truth
and is more useful than a sheet of zeros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from core.market.pointintime import Fact, FactStore

#: Which collectors store each concept, so a missing input can say who would fill it.
FILLED_BY: dict[str, tuple[str, ...]] = {
    "revenue": ("fmp", "sec_xbrl", "eodhd", "finmind"),
    "cost_of_revenue": ("sec_xbrl", "eodhd"),
    "gross_profit": ("fmp", "sec_xbrl", "eodhd", "finmind"),
    "operating_income": ("fmp", "sec_xbrl", "eodhd"),
    "net_income": ("fmp", "sec_xbrl", "eodhd", "finmind"),
    "ebitda": ("fmp", "eodhd"),
    "eps_diluted": ("fmp", "sec_xbrl"),
    "cash_from_operations": ("fmp", "sec_xbrl", "eodhd"),
    "free_cash_flow": ("fmp", "eodhd"),
    "capex": ("fmp", "sec_xbrl", "eodhd"),
    "depreciation": ("sec_xbrl", "eodhd"),
    "sga": ("sec_xbrl", "eodhd"),
    "interest_expense": ("sec_xbrl", "eodhd"),
    "income_tax": ("sec_xbrl", "eodhd"),
    "pretax_income": ("sec_xbrl", "eodhd"),
    "total_assets": ("fmp", "sec_xbrl", "eodhd"),
    "current_assets": ("sec_xbrl", "eodhd"),
    "current_liabilities": ("sec_xbrl", "eodhd"),
    "total_liabilities": ("sec_xbrl", "eodhd"),
    "receivables": ("sec_xbrl", "eodhd"),
    "ppe_net": ("sec_xbrl", "eodhd"),
    "total_debt": ("fmp",),
    "long_term_debt": ("sec_xbrl", "eodhd"),
    "short_term_debt": ("sec_xbrl", "eodhd"),
    "cash": ("fmp", "sec_xbrl", "eodhd"),
    "equity": ("fmp", "sec_xbrl", "eodhd"),
    "retained_earnings": ("sec_xbrl", "eodhd"),
    "shares_outstanding": ("sec_xbrl", "eodhd"),
    "dividends_paid": ("sec_xbrl", "eodhd"),
    "buybacks": ("sec_xbrl", "eodhd"),
}

#: A trailing-twelve-month sum needs four quarters that span about a year.
TTM_SPAN = (timedelta(days=270), timedelta(days=380))
#: "The same quarter a year earlier" tolerates the calendar's drift.
YEAR_TOL = timedelta(days=25)
DAYS = Decimal(365)
ONE = Decimal(1)


def fillers(concept: str) -> tuple[str, ...]:
    return FILLED_BY.get(concept.removesuffix("_fy"), ())


@dataclass(frozen=True)
class Ratio:
    """One computed figure with its inputs, or its absence with the reason."""

    name: str
    value: Decimal | None
    formula: str
    unit: str = "x"  # x | pct | days | ccy
    inputs: dict[str, Fact] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    note: str = ""

    @property
    def computable(self) -> bool:
        return self.value is not None

    @property
    def known_at(self) -> date | None:
        """The day the last input became knowable: the ratio's own known-at."""
        return max((f.known_at for f in self.inputs.values()), default=None)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({f.source_doc_id for f in self.inputs.values()}))

    def text(self) -> str:
        if self.value is None:
            who = sorted({s for c in self.missing for s in fillers(c)})
            filled = f" (filled by {', '.join(who)})" if who else ""
            return f"{self.name}: not computable, missing {', '.join(self.missing)}{filled}"
        if self.unit == "pct":
            shown = f"{self.value * 100:.1f}%"
        elif self.unit == "days":
            shown = f"{self.value:.0f} days"
        elif self.unit == "ccy":
            shown = f"{self.value:,.0f}"
        else:
            shown = f"{self.value:.2f}x"
        tail = f" - {self.note}" if self.note else ""
        return f"{self.name}: {shown} ({self.formula}; known {self.known_at}){tail}"


def _div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None or b == 0:
        return None
    try:
        return a / b
    except (InvalidOperation, ZeroDivisionError):
        return None


class Statements:
    """A name's stored statement lines as knowable on `asof`.

    Quarterly series come from the plain key, annual from `<key>_fy`; both are
    read through the point-in-time store so a row filed after `asof` is not
    there. Derived lines (gross profit from revenue and cost of revenue, total
    debt from its two halves, free cash flow from operations and capex) are
    computed only when the stored line is absent, so a reported figure always
    wins over a derived one.
    """

    def __init__(self, store: FactStore, instrument_id: str, asof: date) -> None:
        self.store = store
        self.instrument_id = instrument_id
        self.asof = asof
        self._cache: dict[str, list[Fact]] = {}

    @classmethod
    def from_store(cls, store: FactStore, instrument_id: str, asof: date) -> Statements:
        return cls(store, instrument_id, asof)

    # -- raw series ---------------------------------------------------------------------

    def series(self, concept: str) -> list[Fact]:
        if concept not in self._cache:
            self._cache[concept] = self.store.series_as_known_at(
                self.instrument_id, concept, self.asof
            )
        return self._cache[concept]

    def latest(self, concept: str) -> Fact | None:
        s = self.series(concept)
        return s[-1] if s else None

    def annual(self, concept: str) -> list[Fact]:
        return self.series(f"{concept}_fy")

    def latest_annual(self, concept: str) -> Fact | None:
        s = self.annual(concept)
        return s[-1] if s else None

    def prior_annual(self, concept: str) -> Fact | None:
        s = self.annual(concept)
        return s[-2] if len(s) >= 2 else None

    def year_earlier(self, concept: str, fact: Fact) -> Fact | None:
        """The quarter that ended about a year before `fact`."""
        target = fact.period_end - timedelta(days=365)
        best = None
        for f in self.series(concept):
            if abs(f.period_end - target) <= YEAR_TOL:
                if best is None or abs(f.period_end - target) < abs(best.period_end - target):
                    best = f
        return best

    # -- values with their inputs -------------------------------------------------------

    def ttm(self, concept: str) -> tuple[Decimal | None, dict[str, Fact]]:
        """Four quarters spanning about a year, else the latest annual figure."""
        s = self.series(concept)
        if len(s) >= 4:
            last4 = s[-4:]
            span = last4[-1].period_end - last4[0].period_end
            if TTM_SPAN[0] - timedelta(days=95) <= span <= TTM_SPAN[1] - timedelta(days=90):
                return sum((f.value for f in last4), Decimal(0)), {
                    f"{concept}@{f.period_end}": f for f in last4
                }
        fy = self.latest_annual(concept)
        if fy is not None:
            return fy.value, {f"{concept}_fy@{fy.period_end}": fy}
        return None, {}

    def instant(self, concept: str) -> tuple[Decimal | None, dict[str, Fact]]:
        f = self.latest(concept)
        return (f.value, {f"{concept}@{f.period_end}": f}) if f else (None, {})

    def flow(self, concept: str) -> tuple[Decimal | None, dict[str, Fact]]:
        """A flow on a trailing-twelve-month basis, with the derivations that stand in."""
        v, inp = self.ttm(concept)
        if v is not None:
            return v, inp
        if concept == "gross_profit":
            r, ri = self.ttm("revenue")
            c, ci = self.ttm("cost_of_revenue")
            if r is not None and c is not None:
                return r - c, {**ri, **ci}
        if concept == "free_cash_flow":
            o, oi = self.ttm("cash_from_operations")
            x, xi = self.ttm("capex")
            if o is not None and x is not None:
                return o - abs(x), {**oi, **xi}
        if concept == "ebitda":
            o, oi = self.ttm("operating_income")
            d, di = self.ttm("depreciation")
            if o is not None and d is not None:
                return o + d, {**oi, **di}
        return None, {}

    def balance(self, concept: str) -> tuple[Decimal | None, dict[str, Fact]]:
        """An instant, with the derivations that stand in for a missing line."""
        v, inp = self.instant(concept)
        if v is not None:
            return v, inp
        if concept == "total_debt":
            lt, li = self.instant("long_term_debt")
            st, si = self.instant("short_term_debt")
            if lt is not None:
                return lt + (st or Decimal(0)), {**li, **si}
        if concept == "total_liabilities":
            ta, ai = self.instant("total_assets")
            eq, ei = self.instant("equity")
            if ta is not None and eq is not None:
                return ta - eq, {**ai, **ei}
        return None, {}

    def capex(self) -> tuple[Decimal | None, dict[str, Fact]]:
        v, inp = self.ttm("capex")
        return (abs(v) if v is not None else None), inp

    def effective_tax_rate(self) -> tuple[Decimal | None, dict[str, Fact]]:
        t, ti = self.ttm("income_tax")
        p, pi = self.ttm("pretax_income")
        rate = _div(t, p)
        if rate is None or not (Decimal(0) <= rate <= Decimal("0.6")):
            return None, {}
        return rate, {**ti, **pi}


# --- the ratios --------------------------------------------------------------------------


def _ratio(name, formula, unit, num, den, inputs, needs, note="") -> Ratio:
    value = _div(num, den)
    if value is None:
        missing = tuple(c for c, v in needs if v is None)
        if not missing and den == 0:
            note = f"{note}; " if note else ""
            note += "denominator is zero"
        return Ratio(name, None, formula, unit, inputs, missing or ("zero denominator",), note)
    return Ratio(name, value, formula, unit, inputs, (), note)


def gross_margin(s: Statements) -> Ratio:
    gp, gi = s.flow("gross_profit")
    r, ri = s.flow("revenue")
    return _ratio(
        "gross_margin",
        "gross profit / revenue, TTM",
        "pct",
        gp,
        r,
        {**gi, **ri},
        [("gross_profit", gp), ("revenue", r)],
    )


def operating_margin(s: Statements) -> Ratio:
    o, oi = s.flow("operating_income")
    r, ri = s.flow("revenue")
    return _ratio(
        "operating_margin",
        "operating income / revenue, TTM",
        "pct",
        o,
        r,
        {**oi, **ri},
        [("operating_income", o), ("revenue", r)],
    )


def net_margin(s: Statements) -> Ratio:
    n, ni = s.flow("net_income")
    r, ri = s.flow("revenue")
    return _ratio(
        "net_margin",
        "net income / revenue, TTM",
        "pct",
        n,
        r,
        {**ni, **ri},
        [("net_income", n), ("revenue", r)],
    )


def fcf(s: Statements) -> Ratio:
    v, inp = s.flow("free_cash_flow")
    if v is None:
        return Ratio(
            "free_cash_flow",
            None,
            "cash from operations - |capex|, TTM",
            "ccy",
            {},
            ("cash_from_operations", "capex"),
        )
    return Ratio("free_cash_flow", v, "cash from operations - |capex|, TTM", "ccy", inp)


def fcf_margin(s: Statements) -> Ratio:
    f, fi = s.flow("free_cash_flow")
    r, ri = s.flow("revenue")
    return _ratio(
        "fcf_margin",
        "free cash flow / revenue, TTM",
        "pct",
        f,
        r,
        {**fi, **ri},
        [("free_cash_flow", f), ("revenue", r)],
    )


def capex_to_revenue(s: Statements) -> Ratio:
    x, xi = s.capex()
    r, ri = s.flow("revenue")
    return _ratio(
        "capex_to_revenue",
        "|capex| / revenue, TTM",
        "pct",
        x,
        r,
        {**xi, **ri},
        [("capex", x), ("revenue", r)],
    )


def capex_to_depreciation(s: Statements) -> Ratio:
    x, xi = s.capex()
    d, di = s.flow("depreciation")
    return _ratio(
        "capex_to_depreciation",
        "|capex| / depreciation, TTM",
        "x",
        x,
        d,
        {**xi, **di},
        [("capex", x), ("depreciation", d)],
        note="above 1 the asset base grows; well below 1 it is being run down",
    )


def accrual_ratio(s: Statements) -> Ratio:
    """Sloan (1996): (net income - cash from operations) / average total assets."""
    n, ni = s.flow("net_income")
    c, ci = s.flow("cash_from_operations")
    ta, ti = s.balance("total_assets")
    prior = s.prior_annual("total_assets") or None
    avg = None
    if ta is not None:
        avg = (ta + prior.value) / 2 if prior is not None else ta
        if prior is not None:
            ti = {**ti, f"total_assets_fy@{prior.period_end}": prior}
    num = None if n is None or c is None else n - c
    return _ratio(
        "accrual_ratio",
        "(net income - cash from operations) / average total assets, TTM",
        "pct",
        num,
        avg,
        {**ni, **ci, **ti},
        [("net_income", n), ("cash_from_operations", c), ("total_assets", ta)],
        note="Sloan: high positive accruals predict lower earnings and returns",
    )


def roe(s: Statements) -> Ratio:
    n, ni = s.flow("net_income")
    e, ei = s.balance("equity")
    return _ratio(
        "roe",
        "net income TTM / equity",
        "pct",
        n,
        e,
        {**ni, **ei},
        [("net_income", n), ("equity", e)],
    )


def roa(s: Statements) -> Ratio:
    n, ni = s.flow("net_income")
    a, ai = s.balance("total_assets")
    return _ratio(
        "roa",
        "net income TTM / total assets",
        "pct",
        n,
        a,
        {**ni, **ai},
        [("net_income", n), ("total_assets", a)],
    )


def roic(s: Statements, tax_rate: Decimal | None = None) -> Ratio:
    """NOPAT over invested capital (debt + equity - cash). Tax: effective, else the given rate."""
    o, oi = s.flow("operating_income")
    rate, ri = s.effective_tax_rate()
    note = "effective tax rate"
    if rate is None:
        rate, note = tax_rate, "statutory tax rate supplied"
    d, di = s.balance("total_debt")
    e, ei = s.balance("equity")
    c, ci = s.balance("cash")
    nopat = None if o is None or rate is None else o * (ONE - rate)
    ic = None if d is None or e is None else d + e - (c or Decimal(0))
    return _ratio(
        "roic",
        "operating income x (1 - tax) / (debt + equity - cash)",
        "pct",
        nopat,
        ic,
        {**oi, **ri, **di, **ei, **ci},
        [("operating_income", o), ("tax_rate", rate), ("total_debt", d), ("equity", e)],
        note=note,
    )


def net_debt(s: Statements) -> Ratio:
    d, di = s.balance("total_debt")
    c, ci = s.balance("cash")
    if d is None:
        return Ratio("net_debt", None, "total debt - cash", "ccy", {}, ("total_debt",))
    return Ratio("net_debt", d - (c or Decimal(0)), "total debt - cash", "ccy", {**di, **ci})


def net_debt_to_ebitda(s: Statements) -> Ratio:
    nd = net_debt(s)
    e, ei = s.flow("ebitda")
    return _ratio(
        "net_debt_to_ebitda",
        "net debt / EBITDA, TTM",
        "x",
        nd.value,
        e,
        {**nd.inputs, **ei},
        [("total_debt", nd.value), ("ebitda", e)],
        note="above 4x is the covenant-cliff region in the failure library",
    )


def debt_to_equity(s: Statements) -> Ratio:
    d, di = s.balance("total_debt")
    e, ei = s.balance("equity")
    return _ratio(
        "debt_to_equity",
        "total debt / equity",
        "x",
        d,
        e,
        {**di, **ei},
        [("total_debt", d), ("equity", e)],
    )


def interest_cover(s: Statements) -> Ratio:
    o, oi = s.flow("operating_income")
    i, ii = s.flow("interest_expense")
    i = abs(i) if i is not None else None
    return _ratio(
        "interest_cover",
        "operating income / interest expense, TTM",
        "x",
        o,
        i,
        {**oi, **ii},
        [("operating_income", o), ("interest_expense", i)],
    )


def current_ratio(s: Statements) -> Ratio:
    a, ai = s.balance("current_assets")
    b, bi = s.balance("current_liabilities")
    return _ratio(
        "current_ratio",
        "current assets / current liabilities",
        "x",
        a,
        b,
        {**ai, **bi},
        [("current_assets", a), ("current_liabilities", b)],
    )


def asset_turnover(s: Statements) -> Ratio:
    r, ri = s.flow("revenue")
    a, ai = s.balance("total_assets")
    return _ratio(
        "asset_turnover",
        "revenue TTM / total assets",
        "x",
        r,
        a,
        {**ri, **ai},
        [("revenue", r), ("total_assets", a)],
    )


def receivable_days(s: Statements) -> Ratio:
    rec, reci = s.balance("receivables")
    r, ri = s.flow("revenue")
    num = None if rec is None else rec * DAYS
    return _ratio(
        "receivable_days",
        "receivables x 365 / revenue TTM",
        "days",
        num,
        r,
        {**reci, **ri},
        [("receivables", rec), ("revenue", r)],
        note="rising faster than revenue is the receivables-run pattern",
    )


def yoy(s: Statements, concept: str) -> Ratio:
    """The latest quarter against the same quarter a year earlier."""
    f = s.latest(concept)
    prior = s.year_earlier(concept, f) if f else None
    name = f"{concept}_yoy"
    if f is None or prior is None:
        return Ratio(
            name,
            None,
            f"{concept} latest quarter / same quarter a year earlier - 1",
            "pct",
            {},
            (concept,),
        )
    if prior.value == 0:
        return Ratio(name, None, "year-earlier figure is zero", "pct", {}, ("zero denominator",))
    return Ratio(
        name,
        f.value / prior.value - ONE,
        f"{concept} latest quarter / same quarter a year earlier - 1",
        "pct",
        {f"{concept}@{f.period_end}": f, f"{concept}@{prior.period_end}": prior},
    )


def cagr(s: Statements, concept: str, years: int = 5) -> Ratio:
    """Compound annual growth over up to `years` annual figures."""
    fy = s.annual(concept)
    name = f"{concept}_cagr"
    if len(fy) < 2:
        return Ratio(name, None, f"{concept} annual compound growth", "pct", {}, (f"{concept}_fy",))
    window = fy[-(years + 1) :]
    first, last = window[0], window[-1]
    n = Decimal((last.period_end - first.period_end).days) / DAYS
    if first.value <= 0 or last.value <= 0 or n <= 0:
        return Ratio(
            name,
            None,
            "growth is undefined across a non-positive figure",
            "pct",
            {},
            ("non-positive base",),
        )
    growth = (last.value / first.value) ** (ONE / n) - ONE
    return Ratio(
        name,
        growth,
        f"{concept} compound annual growth over {n:.1f} years",
        "pct",
        {f"{concept}_fy@{first.period_end}": first, f"{concept}_fy@{last.period_end}": last},
    )


def dilution(s: Statements) -> Ratio:
    f = s.latest("shares_outstanding")
    prior = s.year_earlier("shares_outstanding", f) if f else None
    if f is None or prior is None or prior.value == 0:
        return Ratio(
            "dilution",
            None,
            "shares outstanding / a year earlier - 1",
            "pct",
            {},
            ("shares_outstanding",),
        )
    return Ratio(
        "dilution",
        f.value / prior.value - ONE,
        "shares outstanding / a year earlier - 1",
        "pct",
        {f"shares@{f.period_end}": f, f"shares@{prior.period_end}": prior},
        note="positive is dilution, negative is buybacks",
    )


# --- the sheet ---------------------------------------------------------------------------


@dataclass
class RatioSheet:
    instrument_id: str
    asof: date
    ratios: dict[str, Ratio]

    @property
    def computable(self) -> int:
        return sum(1 for r in self.ratios.values() if r.computable)

    @property
    def total(self) -> int:
        return len(self.ratios)

    @property
    def missing(self) -> dict[str, tuple[str, ...]]:
        return {n: r.missing for n, r in self.ratios.items() if not r.computable}

    @property
    def fillers(self) -> dict[str, tuple[str, ...]]:
        """Which collector would fill each missing concept."""
        out: dict[str, tuple[str, ...]] = {}
        for m in self.missing.values():
            for concept in m:
                who = fillers(concept)
                if who:
                    out[concept] = who
        return out

    def get(self, name: str) -> Decimal | None:
        r = self.ratios.get(name)
        return r.value if r else None


def ratio_sheet(
    store: FactStore, instrument_id: str, asof: date, tax_rate: Decimal | None = None
) -> RatioSheet:
    s = Statements.from_store(store, instrument_id, asof)
    ratios = [
        gross_margin(s),
        operating_margin(s),
        net_margin(s),
        fcf(s),
        fcf_margin(s),
        capex_to_revenue(s),
        capex_to_depreciation(s),
        accrual_ratio(s),
        roe(s),
        roa(s),
        roic(s, tax_rate),
        net_debt(s),
        net_debt_to_ebitda(s),
        debt_to_equity(s),
        interest_cover(s),
        current_ratio(s),
        asset_turnover(s),
        receivable_days(s),
        yoy(s, "revenue"),
        yoy(s, "net_income"),
        cagr(s, "revenue"),
        dilution(s),
    ]
    return RatioSheet(instrument_id, asof, {r.name: r for r in ratios})


def ratio_sheet_text(sheet: RatioSheet) -> str:
    rows = [
        f"{sheet.instrument_id} ratio sheet as of {sheet.asof}: "
        f"{sheet.computable} of {sheet.total} computable from what is stored"
    ]
    for r in sheet.ratios.values():
        rows.append(f"  {r.text()}")
    if sheet.fillers:
        rows.append(
            "  would fill the gaps: "
            + "; ".join(f"{c} <- {', '.join(w)}" for c, w in sorted(sheet.fillers.items()))
        )
    return "\n".join(rows)
