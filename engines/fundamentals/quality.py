"""Earnings-quality and distress scores, each saying how many of its inputs it has.

Three published models and one house rule, computed from the statement lines
the fact book holds as of a date:

  Beneish M-score (1999)   eight indices on two fiscal years; M above -1.78 flags
                           a likely manipulator. Value only when all eight compute:
                           a seven-index M is a different, uncalibrated number.
  Piotroski F-score (2000) nine binary signals on two fiscal years; reported as
                           "j of k computable signals", never as j of 9 when k < 9.
  Altman Z (1968)          five ratios; below 1.8 distress, above 3.0 safe; refused
                           for banks and insurers, whose balance sheets it was not
                           built for.
  Accruals                 Sloan's ratio, and the 30 percent net-income-to-cash-flow
                           gap the fundamentals agent has always flagged.

Every score names the concepts it lacked and, through FILLED_BY, the collector
that would supply them. The point is the honest partial: a Bursa name with no
statement source scores nothing and says why, which is the true state of the
evidence and the one thing a manufactured number can never say.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from core.market.pointintime import Fact
from engines.fundamentals.ratios import Statements, fillers

ZERO = Decimal(0)
ONE = Decimal(1)
BENEISH_THRESHOLD = Decimal("-1.78")


@dataclass(frozen=True)
class Score:
    name: str
    value: Decimal | None
    components: dict[str, Decimal | None]
    computable: int
    needed: int
    missing: tuple[str, ...]
    threshold: str
    verdict: str
    caveats: tuple[str, ...] = ()
    inputs: dict[str, Fact] = field(default_factory=dict)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({f.source_doc_id for f in self.inputs.values()}))

    def text(self) -> str:
        head = f"{self.name}: {self.verdict}"
        if self.value is not None:
            head += f" (value {self.value:.2f}; {self.threshold})"
        parts = [head]
        if self.missing:
            who = sorted({s for c in self.missing for s in fillers(c)})
            parts.append(
                f"  {self.computable} of {self.needed} inputs computable; missing "
                f"{', '.join(self.missing)}" + (f" (filled by {', '.join(who)})" if who else "")
            )
        for c in self.caveats:
            parts.append(f"  caveat: {c}")
        return "\n".join(parts)


def _d(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None or b == 0:
        return None
    try:
        return a / b
    except (InvalidOperation, ZeroDivisionError):
        return None


class _Years:
    """The two most recent fiscal years' figures, t and t-1, with their facts."""

    def __init__(self, s: Statements) -> None:
        self.s = s
        self.inputs: dict[str, Fact] = {}
        self.missing: list[str] = []

    def pair(self, concept: str) -> tuple[Decimal | None, Decimal | None]:
        t, t1 = self.s.latest_annual(concept), self.s.prior_annual(concept)
        if t is None or t1 is None:
            self.missing.append(f"{concept}_fy")
            return None, None
        self.inputs[f"{concept}_fy@{t.period_end}"] = t
        self.inputs[f"{concept}_fy@{t1.period_end}"] = t1
        return t.value, t1.value

    def one(self, concept: str) -> Decimal | None:
        t = self.s.latest_annual(concept)
        if t is None:
            self.missing.append(f"{concept}_fy")
            return None
        self.inputs[f"{concept}_fy@{t.period_end}"] = t
        return t.value


def beneish_m_score(s: Statements) -> Score:
    """Eight indices, two years. Value only at eight of eight."""
    y = _Years(s)
    rev, rev1 = y.pair("revenue")
    rec, rec1 = y.pair("receivables")
    cogs, cogs1 = y.pair("cost_of_revenue")
    ca, ca1 = y.pair("current_assets")
    ppe, ppe1 = y.pair("ppe_net")
    ta, ta1 = y.pair("total_assets")
    dep, dep1 = y.pair("depreciation")
    sga, sga1 = y.pair("sga")
    ltd, ltd1 = y.pair("long_term_debt")
    cl, cl1 = y.pair("current_liabilities")
    ni = y.one("net_income")
    cfo = y.one("cash_from_operations")

    comp: dict[str, Decimal | None] = {}
    comp["DSRI"] = _d(_d(rec, rev), _d(rec1, rev1))
    gm = None if rev is None or cogs is None else _d(rev - cogs, rev)
    gm1 = None if rev1 is None or cogs1 is None else _d(rev1 - cogs1, rev1)
    comp["GMI"] = _d(gm1, gm)
    aq = None if None in (ca, ppe, ta) else ONE - _d((ca or ZERO) + (ppe or ZERO), ta)  # type: ignore[operator]
    aq1 = None if None in (ca1, ppe1, ta1) else ONE - _d((ca1 or ZERO) + (ppe1 or ZERO), ta1)  # type: ignore[operator]
    comp["AQI"] = _d(aq, aq1)
    comp["SGI"] = _d(rev, rev1)
    dr = None if dep is None or ppe is None else _d(dep, dep + ppe)
    dr1 = None if dep1 is None or ppe1 is None else _d(dep1, dep1 + ppe1)
    comp["DEPI"] = _d(dr1, dr)
    comp["SGAI"] = _d(_d(sga, rev), _d(sga1, rev1))
    lv = None if None in (ltd, cl, ta) else _d((ltd or ZERO) + (cl or ZERO), ta)
    lv1 = None if None in (ltd1, cl1, ta1) else _d((ltd1 or ZERO) + (cl1 or ZERO), ta1)
    comp["LVGI"] = _d(lv, lv1)
    comp["TATA"] = None if ni is None or cfo is None else _d(ni - cfo, ta)

    have = sum(1 for v in comp.values() if v is not None)
    missing = tuple(dict.fromkeys(y.missing))
    if have < 8:
        return Score(
            "beneish_m",
            None,
            comp,
            have,
            8,
            missing,
            "M above -1.78 flags likely manipulation",
            f"{have} of 8 indices computable; no score",
            inputs=y.inputs,
            caveats=("a partial M-score is a different, uncalibrated number and is not reported",),
        )
    m = (
        Decimal("-4.84")
        + Decimal("0.920") * comp["DSRI"]  # type: ignore[operator]
        + Decimal("0.528") * comp["GMI"]  # type: ignore[operator]
        + Decimal("0.404") * comp["AQI"]  # type: ignore[operator]
        + Decimal("0.892") * comp["SGI"]  # type: ignore[operator]
        + Decimal("0.115") * comp["DEPI"]  # type: ignore[operator]
        - Decimal("0.172") * comp["SGAI"]  # type: ignore[operator]
        + Decimal("4.679") * comp["TATA"]  # type: ignore[operator]
        - Decimal("0.327") * comp["LVGI"]  # type: ignore[operator]
    )
    verdict = (
        "flag: above the -1.78 threshold" if m > BENEISH_THRESHOLD else "below the -1.78 threshold"
    )
    return Score(
        "beneish_m",
        m,
        comp,
        8,
        8,
        (),
        "M above -1.78 flags likely manipulation",
        verdict,
        inputs=y.inputs,
        caveats=("a screen for further work, not a finding of manipulation",),
    )


def piotroski_f_score(s: Statements) -> Score:
    """Nine binary signals, two years. Reported over the signals that compute."""
    y = _Years(s)
    ni, ni1 = y.pair("net_income")
    ta, ta1 = y.pair("total_assets")
    cfo = y.one("cash_from_operations")
    ltd, ltd1 = y.pair("long_term_debt")
    ca, ca1 = y.pair("current_assets")
    cl, cl1 = y.pair("current_liabilities")
    sh, sh1 = y.pair("shares_outstanding")
    rev, rev1 = y.pair("revenue")
    cogs, cogs1 = y.pair("cost_of_revenue")

    roa, roa1 = _d(ni, ta), _d(ni1, ta1)
    gm = None if rev is None or cogs is None else _d(rev - cogs, rev)
    gm1 = None if rev1 is None or cogs1 is None else _d(rev1 - cogs1, rev1)
    signals: dict[str, Decimal | None] = {
        "roa_positive": None if roa is None else Decimal(roa > 0),
        "cfo_positive": None if cfo is None else Decimal(cfo > 0),
        "roa_improving": None if roa is None or roa1 is None else Decimal(roa > roa1),
        "cfo_exceeds_ni": None if cfo is None or ni is None else Decimal(cfo > ni),
        "leverage_falling": None
        if None in (ltd, ta, ltd1, ta1)
        else Decimal(_d(ltd, ta) < _d(ltd1, ta1)),  # type: ignore[operator]
        "liquidity_improving": None
        if None in (ca, cl, ca1, cl1)
        else Decimal(_d(ca, cl) > _d(ca1, cl1)),  # type: ignore[operator]
        "no_new_shares": None if sh is None or sh1 is None else Decimal(sh <= sh1),
        "gross_margin_improving": None if gm is None or gm1 is None else Decimal(gm > gm1),
        "asset_turnover_improving": None
        if None in (rev, ta, rev1, ta1)
        else Decimal(_d(rev, ta) > _d(rev1, ta1)),  # type: ignore[operator]
    }
    k = sum(1 for v in signals.values() if v is not None)
    j = sum(int(v) for v in signals.values() if v is not None)
    missing = tuple(dict.fromkeys(y.missing))
    if k == 0:
        return Score(
            "piotroski_f",
            None,
            signals,
            0,
            9,
            missing,
            "8-9 strong, 0-2 weak",
            "0 of 9 signals computable; no score",
            inputs=y.inputs,
        )
    verdict = f"{j} of {k} computable signals" + ("" if k == 9 else f" ({9 - k} not computable)")
    caveats = (
        ()
        if k == 9
        else ("a score over fewer than nine signals is not comparable with the published bands",)
    )
    return Score(
        "piotroski_f",
        Decimal(j),
        signals,
        k,
        9,
        missing,
        "8-9 strong, 0-2 weak",
        verdict,
        caveats,
        y.inputs,
    )


def altman_z(s: Statements, market_cap: Decimal | None, archetype: str | None = None) -> Score:
    """1.2 WC/TA + 1.4 RE/TA + 3.3 EBIT/TA + 0.6 MVE/TL + 1.0 Sales/TA."""
    if archetype in ("bank", "insurer", "reit"):
        return Score(
            "altman_z",
            None,
            {},
            0,
            5,
            (),
            "below 1.8 distress, above 3.0 safe",
            f"not applicable to a {archetype}: the model was built for industrial balance sheets",
        )
    ta, tai = s.balance("total_assets")
    ca, cai = s.balance("current_assets")
    cl, cli = s.balance("current_liabilities")
    re_, rei = s.balance("retained_earnings")
    tl, tli = s.balance("total_liabilities")
    ebit, ei = s.flow("operating_income")
    sales, si = s.flow("revenue")
    inputs = {**tai, **cai, **cli, **rei, **tli, **ei, **si}
    wc = None if ca is None or cl is None else ca - cl
    comp: dict[str, Decimal | None] = {
        "wc_ta": _d(wc, ta),
        "re_ta": _d(re_, ta),
        "ebit_ta": _d(ebit, ta),
        "mve_tl": _d(market_cap, tl),
        "sales_ta": _d(sales, ta),
    }
    needs = {
        "wc_ta": ("current_assets", ca, "current_liabilities", cl, "total_assets", ta),
        "re_ta": ("retained_earnings", re_, "total_assets", ta),
        "ebit_ta": ("operating_income", ebit, "total_assets", ta),
        "mve_tl": ("market_cap", market_cap, "total_liabilities", tl),
        "sales_ta": ("revenue", sales, "total_assets", ta),
    }
    missing: list[str] = []
    for key, spec in needs.items():
        if comp[key] is None:
            for name, val in zip(spec[0::2], spec[1::2]):
                if val is None and name not in missing:
                    missing.append(str(name))
    have = sum(1 for v in comp.values() if v is not None)
    if have < 5:
        return Score(
            "altman_z",
            None,
            comp,
            have,
            5,
            tuple(missing),
            "below 1.8 distress, above 3.0 safe",
            f"{have} of 5 components computable; no score",
            inputs=inputs,
        )
    z = (
        Decimal("1.2") * comp["wc_ta"]  # type: ignore[operator]
        + Decimal("1.4") * comp["re_ta"]  # type: ignore[operator]
        + Decimal("3.3") * comp["ebit_ta"]  # type: ignore[operator]
        + Decimal("0.6") * comp["mve_tl"]  # type: ignore[operator]
        + comp["sales_ta"]  # type: ignore[operator]
    )
    zone = (
        "distress zone"
        if z < Decimal("1.8")
        else ("grey zone" if z < Decimal("3.0") else "safe zone")
    )
    return Score(
        "altman_z", z, comp, 5, 5, (), "below 1.8 distress, above 3.0 safe", zone, inputs=inputs
    )


def accruals(s: Statements) -> Score:
    """Sloan's accrual ratio and the 30 percent net-income-to-cash gap."""
    ni, nii = s.flow("net_income")
    cfo, ci = s.flow("cash_from_operations")
    ta, ti = s.balance("total_assets")
    inputs = {**nii, **ci, **ti}
    if ni is None or cfo is None:
        missing = tuple(
            c for c, v in (("net_income", ni), ("cash_from_operations", cfo)) if v is None
        )
        return Score(
            "accruals",
            None,
            {},
            0,
            2,
            missing,
            "gap above 30% of net income is a flag",
            "not computable",
            inputs=inputs,
        )
    gap = ni - cfo
    ratio = _d(gap, ta)
    comp = {"ni_minus_cfo": gap, "sloan_ratio": ratio}
    flagged = ni != 0 and abs(gap) > abs(ni) * Decimal("0.30") and gap > 0
    verdict = (
        (
            f"flag: net income exceeds operating cash flow by {gap / abs(ni) * 100:.0f}% of net income"
            if flagged
            else "cash from operations covers net income"
            if gap <= 0
            else f"gap {gap / abs(ni) * 100:.0f}% of net income, inside the 30% rule"
        )
        if ni != 0
        else "net income is zero"
    )
    caveats = () if ratio is not None else ("Sloan ratio needs total assets",)
    return Score(
        "accruals",
        ratio if ratio is not None else gap,
        comp,
        2 if ratio is not None else 1,
        2,
        () if ratio is not None else ("total_assets",),
        "gap above 30% of net income is a flag",
        verdict,
        caveats,
        inputs,
    )


def quality_report(
    s: Statements, market_cap: Decimal | None = None, archetype: str | None = None
) -> list[Score]:
    return [
        accruals(s),
        beneish_m_score(s),
        piotroski_f_score(s),
        altman_z(s, market_cap, archetype),
    ]


def quality_text(scores: list[Score]) -> str:
    return "\n".join(sc.text() for sc in scores)
