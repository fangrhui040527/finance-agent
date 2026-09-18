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

from core.market.bars import drop_carried_rows
from core.market.feed import PriceFeedError, market_proxy_for
from engines.attribution.decompose import (
    ESTIMATION_GAP,
    ESTIMATION_LOOKBACK,
    MIN_OBSERVATIONS,
    decompose,
)
from engines.attribution.regression import Fit, huber_fit
from knowledge.digest import build_digest
from knowledge.facts import FactBook
from knowledge.report import fact_snapshot, macro_context
from markets.registry import market_currency, mic_of

PACK_DIR = Path("knowledge/feedback")
#: The most sessions of returns the market beta is estimated on. The window
#: actually fitted is `estimation_slice`: it ends ESTIMATION_GAP sessions before
#: the event and shortens to what the pair has, down to MIN_OBSERVATIONS.
EST_WINDOW = ESTIMATION_LOOKBACK
#: Pull of the estimated beta toward 1.0. docs/03: robust estimators on a few
#: hundred sessions still carry noise; a fifth of the way to the structural prior.
SHRINK = 0.2


def estimation_slice(n_returns: int) -> slice:
    """Which of `n_returns` session returns, the last being the event's, the beta is fitted on.

    docs/03 section 2.2 rule 1: the window is [t-260, t-11]. It ends
    ESTIMATION_GAP sessions before the event so that a move which took several
    sessions to play out is in neither its own benchmark nor its own sigma.
    Until now the pack fitted the 120 returns immediately before the event -
    equal to MIN_OBSERVATIONS, so every row said "short window; betas
    unstable", and with no gap at all. The lookback shortens to what the pair
    has; below MIN_OBSERVATIONS `market_fit` refuses, and that is the floor.
    """
    available = max(n_returns - ESTIMATION_GAP - 1, 0)
    lookback = min(ESTIMATION_LOOKBACK, available)
    return slice(-(lookback + ESTIMATION_GAP + 1), -(ESTIMATION_GAP + 1))


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
    asked_for: date | None = None
    own_last: date | None = None
    mkt_last: date | None = None
    dating: str = ""
    #: What the returns are denominated in: the instrument's market currency,
    #: read off its adapter. Not the book's currency, which the pack does not
    #: convert to because no FX return is measured.
    currency: str = ""

    @property
    def mis_dated(self) -> bool:
        """The name printed a session this measurement does not include.

        Not the same as a quiet market. If neither the name nor its proxy has a
        bar for the day asked for, the last common session IS the last session
        and the figure is correctly dated. If the NAME printed and the pair has
        no common bar for it, the proxy is the reason and the figure belongs to
        an earlier day than the page it appears on - which is how the six Bursa
        names on the 2026-09-07 page came to report Friday's move, two of them
        sign-flipped.
        """
        return bool(
            self.asked_for
            and self.last_day
            and self.own_last
            and self.last_day < self.own_last <= self.asked_for
        )

    @property
    def stale(self) -> bool:
        """The PROXY printed a session this measurement does not include.

        The mirror of `mis_dated`. When the market traded and the name did not,
        the name is halted, delisted or its feed has stopped, and every figure
        in the row belongs to its last real session. The note used to call this
        "no session for either" whenever the NAME lacked a later bar, without
        looking at the proxy - a stale name labelled as a market holiday.
        `mis_dated` takes precedence when both legs printed sessions the other
        did not, because that is the fault it already names.
        """
        return bool(
            self.asked_for
            and self.last_day
            and self.mkt_last
            and self.last_day < self.mkt_last <= self.asked_for
            and not self.mis_dated
        )

    def row(self) -> str:
        if self.error:
            return f"| {self.label} ({self.instrument_id}) | NO DATA | | | | {self.error[:90]} |"
        flag = ""
        if self.mis_dated:
            flag = f" **MIS-DATED: this is the {self.last_day} session**"
        elif self.stale:
            flag = f" **STALE NAME: this is the {self.last_day} session**"
        return (
            f"| {self.label} ({self.instrument_id}) | {self.r1:+.2%} | {self.m1:+.2%} | "
            f"{self.r5:+.2%} | {self.m5:+.2%} | {self.verdict}"
            + (f", {self.unexplained:.0%} unexplained" if self.unexplained is not None else "")
            + flag
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
        # Widening the shape must not drop a field. The intercept's standard
        # error is what lets the page say whether a drift is a drift or noise,
        # and the one-factor fit is where it was computed.
        intercept_se=one.intercept_se,
    )


def measure(feed, instrument_id: str, label: str, day: date, base_currency: str = "MYR") -> Move:
    """One name against its proxy, from whatever the feed serves for `day`.

    `base_currency` is the book's. The returns are NOT converted to it: no FX
    return is measured, so the row is in the instrument's own market currency
    and says so when that differs. Passing the book's currency into the
    decomposition with a zero FX leg labelled a USD return as MYR with
    "currency 0.00%", which is a conversion that never happened.
    """
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
    # The vendor writes a Bursa holiday as a row - the previous close carried
    # forward on zero volume for a share, the previous row repeated whole for
    # the index - and both pass the bar parser. Dropped before the intersection
    # so the day is missing from BOTH legs and reaches `_dating_note` as a
    # closed market, instead of printing a 0.00% session with a verdict.
    closes_i = {b.day: b.close for b in drop_carried_rows(own.raw())}
    closes_m = {b.day: b.close for b in drop_carried_rows(mkt.raw())}
    common = sorted(set(closes_i) & set(closes_m))
    if len(common) < 6:
        move.error = f"only {len(common)} common sessions with {proxy}; need 6 for a 5-bar return"
        return move
    ci = [closes_i[d] for d in common]
    cm = [closes_m[d] for d in common]
    ri = [b / a - 1.0 for a, b in zip(ci, ci[1:])]
    rm = [b / a - 1.0 for a, b in zip(cm, cm[1:])]
    move.last_day = common[-1]
    move.asked_for = day
    move.own_last = max(closes_i) if closes_i else None
    move.mkt_last = max(closes_m) if closes_m else None
    move.sessions = len(common)
    move.dating = _dating_note(move, own_days=set(closes_i), mkt_days=set(closes_m))
    move.r1, move.m1 = ri[-1], rm[-1]
    move.r5, move.m5 = ci[-1] / ci[-6] - 1.0, cm[-1] / cm[-6] - 1.0
    move.currency = market_currency(mic_of(instrument_id))

    est = estimation_slice(len(ri))
    est_i, est_m = ri[est], rm[est]
    # ri[k] is the return INTO common[k + 1], so the window's sessions are the
    # same slice of the days one step along.
    est_days = common[1:][est]
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
        base_currency=move.currency,
    )
    move.verdict = exp.verdict.value
    move.unexplained = exp.unexplained_share if fit is not None else None
    move.reason = exp.reason
    move.estimation = (
        f"{exp.estimation_note}; window {est_days[0]}..{est_days[-1]} "
        f"({ESTIMATION_GAP} sessions before the event); "
        "sector beta fixed at 0 (no sector proxy cached)"
        if fit is not None
        else (
            f"no estimate: {len(est_i)} sessions before the {ESTIMATION_GAP}-session gap, "
            f"{MIN_OBSERVATIONS} needed"
        )
    )
    if move.currency != base_currency:
        move.estimation += f"; returns in {move.currency}, {base_currency} leg not measured"
    move.beta = fit.coefficients[1] if fit is not None else None
    move.components = {c.component.value: c.contribution for c in exp.components}
    return move


def _dating_note(move: Move, *, own_days: set[date], mkt_days: set[date]) -> str:
    """Say, in one line, which day this measurement is really about and why.

    A fallback to an earlier session is not itself a fault - markets close. The
    fault is a SILENT fallback, and the three cases have different cures: a
    quiet market needs nothing, a proxy that did not print needs a different
    proxy or a stated refusal, and a name that did not print while its market
    did is halted or its feed has stopped and wants looking at. They are told
    apart here so the page cannot confuse them. The holiday reading is the
    last resort, reached only when NEITHER leg has a later session; the note
    used to reach it whenever the name lacked one.
    """
    asked, last = move.asked_for, move.last_day
    if asked is None or last is None or last >= asked:
        return ""
    missed = sorted(d for d in own_days - mkt_days if last < d <= asked)
    if missed:
        return (
            f"MIS-DATED: {move.instrument_id} printed {', '.join(d.isoformat() for d in missed)} "
            f"but the proxy {move.proxy} did not, so every figure in this row is the "
            f"{last} session, not {asked}"
        )
    proxy_only = sorted(d for d in mkt_days - own_days if last < d <= asked)
    if proxy_only:
        return (
            f"STALE NAME: {move.proxy} printed {', '.join(d.isoformat() for d in proxy_only)} "
            f"but {move.instrument_id} did not; every figure in this row is the {last} session"
        )
    return (
        f"no session for {move.instrument_id} or {move.proxy} after {last}; the figures are "
        f"correctly dated to the last session on or before {asked}"
    )


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
            + (f" {m.dating}." if m.dating else "")
        )
    out.append("")

    stale = [m for m in moves if m.mis_dated]
    if stale:
        out += [
            f"**{len(stale)} of {len(moves)} rows are MIS-DATED.** The name printed a session "
            f"its market proxy did not, so the decomposition fell back to the last session "
            f"they share. Read these as that day's move, and do not date them {day}:",
            "",
        ]
        out += [f"- {m.dating}" for m in stale]
        out.append("")

    halted = [m for m in moves if m.stale]
    if halted:
        out += [
            f"**{len(halted)} of {len(moves)} rows are STALE NAMES.** The market proxy printed a "
            f"session the name did not - a halt, a delisting or a feed that has stopped - so the "
            f"decomposition is the name's last real session. Do not date these {day}, and treat "
            f"the name's price feed as suspect until it prints again:",
            "",
        ]
        out += [f"- {m.dating}" for m in halted]
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
