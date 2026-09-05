"""The feedback pack: the deterministic half of the nightly page.

docs/20 splits the night's work in two. Code prepares everything that is
arithmetic - returns from the cached bars, the decomposition, the day's digest,
the fact book's figures with the day they became knowable, the macro series -
and writes it as one markdown file. The routine then reasons over that file and
nothing else: the chain of *why*, what would confirm or refute each cause, what
the evidence does not reach. Nothing in the page may be a number the pack did
not carry, which is only enforceable if the pack carries every number.

Two properties of the measurement, both from docs/03:

  * **Both legs measured, or neither.** A name's return against its market
    proxy's return over the same sessions, from the same cache. A name whose
    proxy cannot be read gets NO DATA, not a typed market leg.
  * **Betas estimated on the window BEFORE the day.** The event day is not in
    its own estimation window. One factor - the market proxy - because no
    sector proxy is cached; the sector beta is fixed at zero and the pack says
    so, rather than inventing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from core.market.feed import PriceFeedError, market_proxy_for
from engines.attribution.decompose import MIN_OBSERVATIONS, decompose
from engines.attribution.regression import Fit, huber_fit
from knowledge.digest import build_digest
from knowledge.facts import FactBook
from knowledge.report import fact_snapshot, macro_context

PACK_DIR = Path("knowledge/feedback")
#: Sessions of returns the market beta is estimated on, ending the day before.
EST_WINDOW = 120
#: Pull of the estimated beta toward 1.0. docs/03: robust estimators on 120
#: sessions still carry noise; a fifth of the way to the structural prior.
SHRINK = 0.2


@dataclass
class Move:
    instrument_id: str
    label: str
    proxy: str
    last_day: date | None = None
    r1: float | None = None
    m1: float | None = None
    r5: float | None = None
    m5: float | None = None
    sessions: int = 0
    beta: float | None = None
    verdict: str = ""
    unexplained: float | None = None
    reason: str = ""
    estimation: str = ""
    served_from: str = ""
    error: str = ""
    components: dict[str, float] = field(default_factory=dict)

    def row(self) -> str:
        if self.error:
            return f"| {self.label} ({self.instrument_id}) | NO DATA | | | | {self.error[:90]} |"
        return (
            f"| {self.label} ({self.instrument_id}) | {self.r1:+.2%} | {self.m1:+.2%} | "
            f"{self.r5:+.2%} | {self.m5:+.2%} | {self.verdict}"
            + (f", {self.unexplained:.0%} unexplained" if self.unexplained is not None else "")
            + " |"
        )


def market_fit(inst: list[float], mkt: list[float]) -> Fit | None:
    """A one-factor robust fit widened to the two-factor shape `decompose` reads.

    The second slope - the sector beta - is fixed at 0.0 because no sector
    proxy is cached. Zero is stated in the estimation note; it is not an
    estimate, and the pack never presents it as one.
    """
    n = min(len(inst), len(mkt))
    if n < MIN_OBSERVATIONS:
        return None
    one = huber_fit([[m] for m in mkt[-n:]], inst[-n:], shrink_to=[1.0], shrink_lambda=SHRINK)
    return Fit(
        coefficients=[one.coefficients[0], one.coefficients[1], 0.0],
        residuals=one.residuals,
        residual_sigma=one.residual_sigma,
        r_squared=one.r_squared,
        n=one.n,
        shrinkage=one.shrinkage,
        dof=one.dof,
    )


def measure(feed, instrument_id: str, label: str, day: date, base_currency: str = "MYR") -> Move:
    """One name against its proxy, from whatever the feed serves for `day`."""
    proxy = market_proxy_for(instrument_id)
    move = Move(instrument_id, label, proxy or "")
    if proxy is None:
        move.error = "no market proxy registered for this market (core/market/feed.MARKET_PROXIES)"
        return move
    try:
        own = feed.fetch(instrument_id, end=day)
        mkt = feed.fetch(proxy, end=day)
    except PriceFeedError as e:
        move.error = str(e).splitlines()[0]
        return move
    move.served_from = getattr(feed, "source_used", "") or ""
    closes_i = {b.day: b.close for b in own.raw()}
    closes_m = {b.day: b.close for b in mkt.raw()}
    common = sorted(set(closes_i) & set(closes_m))
    if len(common) < 6:
        move.error = f"only {len(common)} common sessions with {proxy}; need 6 for a 5-bar return"
        return move
    ci = [closes_i[d] for d in common]
    cm = [closes_m[d] for d in common]
    ri = [b / a - 1.0 for a, b in zip(ci, ci[1:])]
    rm = [b / a - 1.0 for a, b in zip(cm, cm[1:])]
    move.last_day = common[-1]
    move.sessions = len(common)
    move.r1, move.m1 = ri[-1], rm[-1]
    move.r5, move.m5 = ci[-1] / ci[-6] - 1.0, cm[-1] / cm[-6] - 1.0

    est_i, est_m = ri[-EST_WINDOW - 1 : -1], rm[-EST_WINDOW - 1 : -1]
    fit = market_fit(est_i, est_m)
    exp = decompose(
        instrument_id,
        (common[-2], common[-1]),
        rm[-1],
        0.0,
        {},
        ri[-1],
        0.0,
        fit,
        base_currency=base_currency,
    )
    move.verdict = exp.verdict.value
    move.unexplained = exp.unexplained_share if fit is not None else None
    move.reason = exp.reason
    move.estimation = (
        (exp.estimation_note + "; sector beta fixed at 0 (no sector proxy cached)")
        if fit is not None
        else f"no estimate: {len(est_i)} sessions, {MIN_OBSERVATIONS} needed"
    )
    move.beta = fit.coefficients[1] if fit is not None else None
    move.components = {c.component.value: c.contribution for c in exp.components}
    return move


def build_pack(
    cfg,
    day: date | None = None,
    *,
    corpus_path: str | None = None,
    facts_path: str | None = None,
    feed=None,
    now: datetime | None = None,
    previous_dir: str | Path | None = PACK_DIR,
) -> str:
    """The pack for `day`, as markdown. Never raises for a name; NO DATA is a row."""
    from core.market.feed import default_feed
    from knowledge.graph.ids import display_names
    from knowledge.graph.ids import instrument_id as canonical

    now = now or datetime.now(UTC)
    day = day or (now.date() - timedelta(days=1))
    feed = feed or default_feed()
    names = display_names()
    book_ids = tuple(dict.fromkeys(tuple(cfg.watchlist) + tuple(cfg.holdings)))
    base = getattr(cfg, "base_currency", "MYR")

    out = [
        f"# Feedback pack {day}",
        "",
        f"Prepared {now:%Y-%m-%d %H:%M} UTC by `ask.py pack`. Every number below is measured "
        "or copied from the stores; the page written from it copies them with their ids and "
        "estimates nothing. The contract is knowledge/feedback/README.md.",
        "",
        "## Moves",
        "",
        "Returns from the cached daily bars, each name against its market proxy over the same "
        "sessions. Decomposition per engines/attribution: the verdict says whether a company "
        "story is even warranted.",
        "",
        "| name | 1d | market 1d | 5d | market 5d | decomposition |",
        "|---|---|---|---|---|---|",
    ]
    moves = [
        measure(feed, iid, names.get(canonical(iid) or iid) or iid, day, base) for iid in book_ids
    ]
    out += [m.row() for m in moves]
    out.append("")
    for m in moves:
        if m.error:
            continue
        comps = ", ".join(f"{k} {v:+.2%}" for k, v in m.components.items() if abs(v) > 0)
        out.append(
            f"- **{m.label}** ({m.instrument_id}) vs {m.proxy}, last session {m.last_day}, "
            f"{m.sessions} common sessions"
            + (f", source {m.served_from}" if m.served_from else "")
            + f". {m.reason}"
            + (
                f" Beta {m.beta:.2f}; {m.estimation}."
                if m.beta is not None
                else f" {m.estimation}."
            )
            + (f" Components: {comps}." if comps else "")
        )
    out.append("")

    digest = build_digest(
        cfg, day, slot="pack", corpus_path=corpus_path, facts_path=facts_path, now=now
    )
    out.append(digest.to_markdown())
    out.append("")

    out += ["## Facts, per name (last 7 days of events; latest figures)", ""]
    with FactBook(facts_path or getattr(cfg, "facts_db", "data/facts.db")) as book:
        for iid in book_ids:
            out.append("```")
            out.append(fact_snapshot(book, iid, now=now, days=7))
            out.append("```")
            out.append("")
        read_only: tuple[str, ...] = tuple(getattr(cfg, "read_only", None) or ())
        if read_only:
            out += ["### Watched, not held (read-only names; never in the book)", ""]
            for iid in read_only:
                out.append("```")
                out.append(fact_snapshot(book, iid, now=now, days=7))
                out.append("```")
                out.append("")
        out += ["## Macro", "", "```", macro_context(book), "```", ""]

    if previous_dir is not None:
        prev = sorted(
            p.name for p in Path(previous_dir).glob("????-??-??.md") if p.name < f"{day}.md"
        )[-3:]
        out += ["## Previous pages", ""]
        out += [f"- knowledge/feedback/{p}" for p in prev] or ["- none yet"]
        out.append("")
    return "\n".join(out)


def write_pack(text: str, day: date, root: str | Path = PACK_DIR) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.pack.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path
