"""The discount rate, built from stored inputs and a dated table; every input labelled.

A cost of capital stated as "10%" is a number picked to make the answer come
out. This module builds it and says where each piece came from:

  risk-free rate      the ten-year Treasury yield in the fact book as of the
                      valuation date (FRED DGS10, vintage-safe); a Malaysian
                      government yield when a series for it is stored
  equity risk premium Damodaran's dated table (knowledge/method/data/
                      cost_of_capital.yaml): the country's total premium, or the
                      mature-market premium with the omission stated when the
                      country row has not been transcribed
  beta                a stored vendor beta for the name, else the industry beta
                      the archetype borrows, each labelled as such
  cost of debt        risk-free plus a synthetic-rating spread read from the
                      name's interest cover, when the ladder is transcribed
  tax                 the effective rate from the statements, else statutory
  weights             market capitalisation and book debt

Anything not available is `None` and named in `missing`; the WACC is reported
only when its parts are, and the text says which approximation, if any, the
number rests on. A stale table (past `stale_after_days`) is a caveat on every
figure derived from it, never a silent default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from engines.fundamentals.ratios import Statements, interest_cover
from knowledge.retrieval.method import COC_TABLE, load_cost_of_capital

ONE = Decimal(1)
HUNDRED = Decimal(100)

#: Which sovereign a market's names earn their cash under, for the country row.
COUNTRY_OF_MIC = {
    "XKLS": "MY",
    "XNAS": "US",
    "XNYS": "US",
    "XTAI": "TW",
    "XHKG": "HK",
    "XSES": "SG",
}
#: The risk-free series to read per country, in order of preference.
RISK_FREE_SERIES = {
    "US": ("DGS10",),
    "MY": ("DBN:GOVT_YIELD_MY", "DGS10"),
    # No free upstream carries a Taiwanese government yield (Taiwan is outside
    # the IMF tables); DGS10 stands in and the caveat says so.
    "TW": ("DGS10",),
}


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


@dataclass(frozen=True)
class CostOfCapitalTable:
    as_of: date
    stale_after_days: int
    source: str
    mature_erp: Decimal | None
    implied_erp: dict[str, Decimal]
    multiplier: Decimal | None
    country: dict[str, dict[str, Any]]
    industry_betas: dict[str, dict[str, Decimal | None]]
    archetype_industry: dict[str, str]
    spreads: tuple[dict[str, Any], ...]
    statutory_tax: dict[str, Decimal]
    long_run_growth: dict[str, Decimal]

    @property
    def present(self) -> bool:
        return self.mature_erp is not None


def load_table(path: str | Path = COC_TABLE) -> CostOfCapitalTable:
    raw = load_cost_of_capital(path)
    as_of_raw = raw.get("as_of")
    as_of = as_of_raw if isinstance(as_of_raw, date) else date(1970, 1, 1)
    return CostOfCapitalTable(
        as_of=as_of,
        stale_after_days=int(raw.get("stale_after_days") or 400),
        source=str(raw.get("source") or "cost-of-capital table"),
        mature_erp=_dec(raw.get("mature_market_erp")),
        implied_erp={
            k: d for k, v in (raw.get("implied_erp") or {}).items() if (d := _dec(v)) is not None
        },
        multiplier=_dec(raw.get("equity_volatility_multiplier")),
        country={k: dict(v) for k, v in (raw.get("country") or {}).items() if isinstance(v, dict)},
        industry_betas={
            k: {"unlevered": _dec(v.get("unlevered")), "levered": _dec(v.get("levered"))}
            for k, v in (raw.get("industry_betas") or {}).items()
            if isinstance(v, dict)
        },
        archetype_industry=dict(raw.get("archetype_industry") or {}),
        spreads=tuple(
            s for s in (raw.get("synthetic_rating_spreads") or []) if isinstance(s, dict)
        ),
        statutory_tax={
            k: d for k, v in (raw.get("statutory_tax") or {}).items() if (d := _dec(v)) is not None
        },
        long_run_growth={
            k: d
            for k, v in (raw.get("long_run_nominal_growth") or {}).items()
            if (d := _dec(v)) is not None
        },
    )


def staleness(table: CostOfCapitalTable, today: date) -> str | None:
    """A caveat once the table is older than its own refresh promise."""
    age = today - table.as_of
    if age > timedelta(days=table.stale_after_days):
        return (
            f"cost-of-capital table is {age.days} days old (as of {table.as_of}); premiums "
            "refresh each January and July, so these figures may be stale"
        )
    return None


def cost_of_equity(rf: Decimal, beta: Decimal, erp: Decimal, crp: Decimal = Decimal(0)) -> Decimal:
    """CAPM with a country premium added to the whole, Damodaran's default treatment."""
    return rf + beta * erp + crp


def synthetic_spread(
    table: CostOfCapitalTable, cover: Decimal | None
) -> tuple[Decimal | None, str]:
    """The default spread a name's interest cover implies on the large-firm ladder."""
    if cover is None:
        return None, "interest cover not computable"
    for row in table.spreads:
        min_cover = _dec(row.get("min_cover"))
        if min_cover is None:
            continue
        if cover >= min_cover:
            spread = _dec(row.get("spread"))
            if spread is None:
                return None, f"synthetic rating {row.get('rating')} (spread not transcribed)"
            return spread, f"synthetic rating {row.get('rating')}"
    return None, "interest cover below the ladder"


def cost_of_debt(
    rf: Decimal, cover: Decimal | None, table: CostOfCapitalTable, crp: Decimal = Decimal(0)
) -> tuple[Decimal | None, str]:
    spread, label = synthetic_spread(table, cover)
    if spread is None:
        return None, label
    return rf + spread + crp, label


def wacc(
    ke: Decimal, kd_pre_tax: Decimal, tax: Decimal, e_value: Decimal, d_value: Decimal
) -> Decimal | None:
    total = e_value + d_value
    if total <= 0:
        return None
    return ke * (e_value / total) + kd_pre_tax * (ONE - tax) * (d_value / total)


@dataclass(frozen=True)
class CostOfCapital:
    instrument_id: str
    asof: date
    country: str
    rf: Decimal | None
    rf_source: str
    beta: Decimal | None
    beta_source: str
    erp: Decimal | None
    erp_source: str
    crp: Decimal
    ke: Decimal | None
    kd: Decimal | None
    kd_source: str
    tax_rate: Decimal | None
    tax_source: str
    e_value: Decimal | None
    d_value: Decimal | None
    wacc: Decimal | None
    caveats: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    citations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def discount(self) -> tuple[Decimal | None, str]:
        """The rate a DCF should use and which one it is."""
        if self.wacc is not None:
            return self.wacc, "wacc"
        if self.ke is not None:
            return self.ke, "cost of equity (WACC unavailable; discount levered cash flows)"
        return None, "no discount rate: " + ", ".join(self.missing)


def market_of(instrument_id: str) -> str:
    """The MIC behind an instrument id: MYX:1155 trades on XKLS."""
    from markets.registry import mic_of

    try:
        return mic_of(instrument_id)
    except ValueError:
        return instrument_id.split(":", 1)[0].upper()


def country_of(instrument_id: str) -> str:
    return COUNTRY_OF_MIC.get(market_of(instrument_id), "US")


def _risk_free(book, country: str, asof: date) -> tuple[Decimal | None, str]:
    """The sovereign yield to discount at, and the label that qualifies it.

    The label is the whole point. A Malaysian name is discounted off
    DBN:GOVT_YIELD_MY, whose upstream stopped in 2025-05 (see
    knowledge/sources/freshness.py ENDED): the figure is still the best stored
    reading of the ringgit risk-free rate and still what the arithmetic uses,
    but a rate sixteen months old is an assumption, not an observation, and
    every WACC built on it says so rather than printing a date the reader has
    to age themselves.
    """
    from knowledge.sources.freshness import ended, has_resumed

    for sid in RISK_FREE_SERIES.get(country, ("DGS10",)):
        points = book.series(sid, asof=asof, limit=1)
        if points:
            p = points[-1]
            dead = ended(sid)
            note = (
                f" (that series ENDED: {dead.upstream} stopped publishing at "
                f"{dead.last_period:%Y-%m}, so this rate is {(asof - p.obs_date).days} days "
                f"old and will not refresh)"
                if dead is not None and not has_resumed(sid, p.obs_date)
                else ""
            )
            approx = (
                ""
                if country == "US" or not sid.startswith("DGS")
                else f" (no {country} government yield stored; US ten-year as the stated approximation)"
            )
            return p.value / HUNDRED, f"{sid} {p.obs_date}, known {p.known_at}{approx}{note}"
    return None, "no risk-free series stored (FRED DGS10 is the collector)"


def derive(
    instrument_id: str,
    book,
    table: CostOfCapitalTable,
    asof: date,
    archetype: str | None = None,
    statements: Statements | None = None,
    beta: Decimal | None = None,
) -> CostOfCapital:
    """Build the rate from what is stored, labelling every input and every gap."""
    country = country_of(instrument_id)
    caveats: list[str] = []
    missing: list[str] = []
    citations: list[str] = []
    stale = staleness(table, asof)
    if stale:
        caveats.append(stale)
    if not table.present:
        missing.append("cost-of-capital table")

    rf, rf_source = _risk_free(book, country, asof)
    if rf is None:
        missing.append("risk-free rate")
    elif "ENDED" in rf_source:
        # A caveat, not a `missing`: the rate is there and the WACC is still
        # computable off it. What a reader must not do is take it for current.
        caveats.append(f"the risk-free rate rests on a stopped series - {rf_source}")

    row = table.country.get(country) or {}
    erp = _dec(row.get("erp"))
    crp = _dec(row.get("crp")) or Decimal(0)
    if erp is not None:
        erp_source = f"{table.source}: {country} total equity risk premium"
        citations.append(f"cost_of_capital#country:{country}")
        crp = Decimal(0)  # the country's total premium already carries it
    elif table.mature_erp is not None:
        erp, erp_source = table.mature_erp, f"{table.source}: mature-market premium"
        citations.append("cost_of_capital#erp")
        if country != "US":
            caveats.append(
                f"{country} country row not transcribed: no country risk premium applied, "
                "so the cost of equity is understated for a non-US name"
            )
    else:
        erp, erp_source = None, "no premium: table absent"
        missing.append("equity risk premium")

    beta_source = "supplied"
    if beta is None:
        obs = book.latest(instrument_id, "beta", asof=asof)
        if obs is not None and obs.value is not None:
            beta, beta_source = obs.value, f"{obs.source} beta, known {obs.known_at}"
        else:
            industry = table.archetype_industry.get(archetype or "", "")
            lev = (table.industry_betas.get(industry) or {}).get("levered")
            if lev is not None:
                beta, beta_source = lev, f"industry beta: {industry} ({table.source})"
                citations.append(f"cost_of_capital#industry:{industry}")
            else:
                beta_source = (
                    f"no vendor beta stored and industry row {industry!r} not transcribed"
                    if industry
                    else "no vendor beta stored and no archetype to borrow an industry beta from"
                )
                missing.append("beta")

    ke = cost_of_equity(rf, beta, erp, crp) if None not in (rf, beta, erp) else None  # type: ignore[arg-type]

    tax, tax_source = None, ""
    if statements is not None:
        tax, _ = statements.effective_tax_rate()
        if tax is not None:
            tax_source = "effective rate from the statements"
    if tax is None:
        mic = market_of(instrument_id)
        tax = table.statutory_tax.get(mic)
        tax_source = f"statutory rate for {mic}" if tax is not None else "no tax rate"
        if tax is None:
            missing.append("tax rate")

    cover = interest_cover(statements).value if statements is not None else None
    kd, kd_source = (
        cost_of_debt(rf, cover, table, crp) if rf is not None else (None, "no risk-free rate")
    )
    if kd is None:
        caveats.append(f"cost of debt unavailable: {kd_source}")

    e_value: Decimal | None = None
    d_value: Decimal | None = None
    cap = book.latest(instrument_id, "market_cap_musd", asof=asof)
    if cap is not None and cap.value is not None:
        e_value = cap.value * Decimal(1_000_000)
    else:
        caveats.append("no market capitalisation stored: equity weight unknown")
    if statements is not None:
        d_value = statements.balance("total_debt")[0]
    if d_value is None:
        caveats.append("no total debt stored: debt weight unknown")

    w = None
    if None not in (ke, kd, tax, e_value, d_value):
        w = wacc(ke, kd, tax, e_value, d_value)  # type: ignore[arg-type]
    else:
        caveats.append(
            "WACC not computed: "
            + ", ".join(
                n
                for n, v in (
                    ("cost of equity", ke),
                    ("cost of debt", kd),
                    ("tax", tax),
                    ("equity value", e_value),
                    ("debt value", d_value),
                )
                if v is None
            )
        )

    return CostOfCapital(
        instrument_id,
        asof,
        country,
        rf,
        rf_source,
        beta,
        beta_source,
        erp,
        erp_source,
        crp,
        ke,
        kd,
        kd_source,
        tax,
        tax_source,
        e_value,
        d_value,
        w,
        tuple(caveats),
        tuple(dict.fromkeys(missing)),
        tuple(citations),
    )


def _pct(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v * HUNDRED:.2f}%"


def cost_of_capital_text(c: CostOfCapital) -> str:
    rows = [
        f"{c.instrument_id} cost of capital as of {c.asof} ({c.country})",
        f"  risk-free {_pct(c.rf)}  <- {c.rf_source}",
        f"  beta {'n/a' if c.beta is None else f'{c.beta:.2f}'}  <- {c.beta_source}",
        f"  equity risk premium {_pct(c.erp)}"
        + (f" + country premium {_pct(c.crp)}" if c.crp else "")
        + f"  <- {c.erp_source}",
        f"  cost of equity {_pct(c.ke)}",
        f"  cost of debt {_pct(c.kd)}  <- {c.kd_source}",
        f"  tax {_pct(c.tax_rate)}  <- {c.tax_source}",
        f"  WACC {_pct(c.wacc)}",
    ]
    rate, which = c.discount
    rows.append(f"  discount rate for a DCF: {_pct(rate)} ({which})")
    for m in c.missing:
        rows.append(f"  missing: {m}")
    for cv in c.caveats:
        rows.append(f"  caveat: {cv}")
    return "\n".join(rows)
