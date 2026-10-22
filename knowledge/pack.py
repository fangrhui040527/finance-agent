"""The feedback pack: the deterministic half of the nightly page.

docs/20 splits the night's work in two. Code prepares everything that is
arithmetic - returns from the cached bars, the decomposition, the day's digest,
the fact book's figures with the day they became knowable, the macro series -
and writes it as one markdown file. The routine then reasons over that file and
nothing else: the chain of *why*, what would confirm or refute each cause, what
the evidence does not reach. Nothing in the page may be a number the pack did
not carry, which is only enforceable if the pack carries every number.

Three properties of the measurement, the first and last from docs/03:

  * **Both legs measured, or neither.** A name's return against its market
    proxy's return over the same sessions, from the same cache. A name whose
    proxy cannot be read gets NO DATA, not a typed market leg.
  * **Total return, or said otherwise.** Closes are adjusted for the dividends
    the feed read (`core.market.feed.session_closes`); a leg with no dividend
    data is a price return and its row says NO DIVIDEND DATA.
  * **Betas estimated on the window BEFORE the day.** The event day is not in
    its own estimation window. One factor - the market proxy - because no
    sector proxy is cached; the sector beta is fixed at zero and the pack says
    so, rather than inventing one.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from core.market.feed import PriceFeedError, market_proxy_for, session_closes
from engines.attribution.decompose import (
    ESTIMATION_GAP,
    ESTIMATION_LOOKBACK,
    MIN_OBSERVATIONS,
    Verdict,
    decompose,
)
from engines.attribution.regression import Fit, huber_fit
from knowledge.corpus import SHORT_ID_CHARS, Corpus
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
    #: The standardised abnormal return - the "sigma" the reason text prints -
    #: as a number, so code can act on it (knowledge/evidence/web.py) rather
    #: than parse a sentence. None when there is no estimate.
    sar: float | None = None
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
    #: Set when either leg's bar for `last_day` was pulled before its market
    #: shut: the figures are the session so far, not the session. See
    #: `_provisional_note`.
    provisional: str = ""
    #: How many exchange sessions the "1d" return spans. More than one when a
    #: session between the last two common days has no row in one leg or in
    #: both: the return is then a k-session move, and it is tested against k
    #: sessions' sigma rather than labelled a day.
    span: int = 1
    #: Which leg the extra sessions are missing from, for the row's flag.
    span_cause: str = ""
    #: Sessions on the exchange calendar after `last_day`, up to the day asked
    #: for, that NEITHER leg has a usable bar for. See `blank`.
    blank_days: tuple[date, ...] = ()
    #: What the returns are adjusted for: a dividend that went ex inside the
    #: 1d window, and any leg whose feed read no dividends and is therefore a
    #: price return. See `_adjustment_note`.
    adjustment: str = ""

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

    @property
    def blank(self) -> bool:
        """The exchange held a session this measurement does not include, and
        neither leg has a bar for it.

        The case `_dating_note` used to call "correctly dated": with no later
        row in either leg it took the market to have been shut, and a blank
        bar the two share looks exactly like that. On 2026-09-22 the 22:44Z
        build did it to the three US rows - Tuesday's bars were blank for the
        names and SPY alike - while the same pack's macro table printed SP500
        for 09-22, and the 22:46Z refetch showed Tuesday traded. Only the
        exchange calendar can tell a closure from a blank, so it is asked.
        """
        return bool(self.blank_days) and not self.mis_dated and not self.stale

    def row(self) -> str:
        if self.error:
            return f"| {self.label} ({self.instrument_id}) | NO DATA | | | | {self.error[:90]} |"
        flag = ""
        if self.mis_dated:
            flag = f" **MIS-DATED: this is the {self.last_day} session**"
        elif self.stale:
            flag = f" **STALE NAME: this is the {self.last_day} session**"
        elif self.blank:
            flag = f" **BLANK SESSION: this is the {self.last_day} session**"
        if self.provisional:
            flag += f" **PROVISIONAL: the {self.last_day} session so far, not the close**"
        if self.span > 1:
            flag += f" **SPANS {self.span} SESSIONS: {self.span_cause or 'a leg has a gap'}**"
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
    # The closes are dividend-adjusted (`session_closes`): an ex-date's drop is
    # the dividend leaving the price, not a company-specific move.
    closes_i = session_closes(own)
    closes_m = session_closes(mkt)
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
    days_i, days_m = set(closes_i), set(closes_m)
    calendar = _exchange_calendar(instrument_id, day)
    move.blank_days = tuple(
        d
        for d in _calendar_sessions(calendar, move.last_day, day)
        if d not in days_i and d not in days_m
    )
    move.dating = _dating_note(move, own_days=days_i, mkt_days=days_m, calendar=calendar)
    # The "1d" return runs between the last two COMMON days. When a session in
    # between has no row in one leg, that is a k-session move: on 2026-09-23
    # ^KLSE had no 09-22 row, and IHH's +4.99% "1d" at 4.11 sigma was two
    # sessions, the second of them flat in the cache. Counted on the exchange
    # calendar as well as on the name's own rows, because the name's rows
    # cannot see a session missing from the NAME - Maybank's blank 10-12 row
    # from the 10-13 refetches made its 10-13 "1d" the 10-09 -> 10-13 move -
    # nor one missing from both legs.
    # Either leg's rows: a day only the proxy printed is a session the NAME
    # missed, and without a calendar to ask it is the only sign of one.
    printed = {d for d in days_i | days_m if common[-2] < d <= common[-1]}
    spanned = printed | set(_calendar_sessions(calendar, common[-2], common[-1]))
    move.span = len(spanned)
    if move.span > 1:
        inner = sorted(d for d in spanned if d < common[-1])
        no_proxy = [d for d in inner if d in days_i]
        no_name = [d for d in inner if d in days_m]
        neither = [d for d in inner if d not in days_i and d not in days_m]
        gaps, causes = [], []
        if no_proxy:
            gaps.append(f"{proxy} has no row for {_days(no_proxy)}")
            causes.append("the proxy has a gap")
        if no_name:
            gaps.append(f"{instrument_id} has no row for {_days(no_name)}")
            causes.append("the name has a gap")
        if neither:
            gaps.append(
                f"neither leg has a usable row for {_days(neither)}, a session on the "
                f"{mic_of(instrument_id)} calendar"
            )
            causes.append("both legs are blank on a session")
        move.span_cause = " and ".join(causes)
        note = (
            f"SPANS {move.span} SESSIONS: {'; '.join(gaps)}, so the 1d figures run from "
            f"{common[-2]} to {common[-1]} and are tested against {move.span} sessions' sigma"
        )
        move.dating = f"{move.dating}; {note}" if move.dating else note
    move.adjustment = _adjustment_note((own, mkt), common[-2], common[-1])
    move.r1, move.m1 = ri[-1], rm[-1]
    move.r5, move.m5 = ci[-1] / ci[-6] - 1.0, cm[-1] / cm[-6] - 1.0
    move.currency = market_currency(mic_of(instrument_id))
    move.provisional = _provisional_note(feed, (instrument_id, proxy), move.last_day)

    est = estimation_slice(len(ri))
    # ri[k] is the return INTO common[k + 1], so the window's sessions are the
    # same slice of the days one step along. A return across a proxy gap is a
    # two-session return, and a daily fit has no place for it: left out.
    own_days = sorted(closes_i)
    kept = [
        k
        for k in range(len(ri))[est]
        if bisect_right(own_days, common[k + 1]) - bisect_right(own_days, common[k]) == 1
    ]
    est_i, est_m = [ri[k] for k in kept], [rm[k] for k in kept]
    est_days = [common[k + 1] for k in kept]
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
        sessions=move.span,
    )
    move.verdict = exp.verdict.value
    if exp.significance is not None:
        move.sar = exp.significance.standardised_ar
    move.unexplained = exp.unexplained_share if fit is not None else None
    move.reason = exp.reason
    if exp.verdict is Verdict.NO_IDENTIFIED_CATALYST:
        # The page read this verdict as a search that found nothing - "no
        # catalyst matched" on Tenaga the one day the corpus held five rows about
        # the cause. Nothing here scores a candidate; the verdict is the
        # decomposition's alone.
        move.reason += (
            ". The pack runs no catalyst matcher: this verdict says a company-specific "
            "cause is warranted, not that none exists, and the stories and events below "
            "are unweighed"
        )
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


def _adjustment_note(legs, since: date, day: date) -> str:
    """Say what the row's returns are adjusted for, and where they cannot be.

    Returns are total return from 2026-10-10: each leg's closes are adjusted
    for the dividends its feed read. A dividend that went ex inside the 1d
    window is named, because it is exactly what the page would otherwise ask
    ("is it an ex-date?"). A leg whose feed read NO dividends - Stooq prints
    none, and a cache row fetched before 2026-10-10 was never asked - is a
    price return, and the row says so rather than assume it paid nothing.
    """
    notes = []
    for series in legs:
        if series.actions_source is None:
            notes.append(
                f"NO DIVIDEND DATA for {series.instrument_id}: its returns are price returns, "
                "and an ex-date drop would read as a move"
            )
            continue
        for a in series.dividends():
            if since < a.ex_date <= day:
                notes.append(
                    f"{series.instrument_id} went ex-dividend {a.amount:g} per share on "
                    f"{a.ex_date}; the 1d return is total return, the dividend added back"
                )
    return "; ".join(notes)


def _fetched_at(feed, instrument_id: str) -> datetime | None:
    """When the body behind an id's bars was pulled; None from a feed that
    keeps no such record (a fixture, a feed with no cache)."""
    ask = getattr(feed, "fetched_at", None)
    if ask is None:
        return None
    try:
        return ask(instrument_id)
    except Exception:  # a feed that cannot say is simply unknown
        return None


def _provisional_note(feed, legs: tuple[str, ...], day: date) -> str:
    """Say so when a leg's bar for `day` was pulled before its market shut.

    The paper book's fundable table has labelled such prices since 2026-09-16;
    this table did not. On 2026-09-17 Apple's published session moved 1.12pp
    between a build from a pre-close cache and one from the settled bars, and
    on 2026-09-22 the only US bars `main` held for the day were quotes pulled
    at 14:05 UTC, 35 minutes into the session: a decomposition of either reads
    exactly like one of a close. The rule is `core.market.calendar.price_state`,
    the same one the fundable table uses; a leg whose fetch time is unknown is
    not labelled, because `unknown` is not `provisional`.
    """
    from core.market.calendar import PROVISIONAL, price_state

    pulled = []
    for iid in legs:
        at = _fetched_at(feed, iid)
        if at is not None and price_state(mic_of(iid), day, at) == PROVISIONAL:
            pulled.append(f"{iid} pulled {at:%Y-%m-%d %H:%M}Z")
    if not pulled:
        return ""
    return (
        f"PROVISIONAL: {'; '.join(pulled)}, before its market's {day} close, so the "
        f"{day} figures in this row are the session so far, not the close"
    )


def _exchange_calendar(instrument_id: str, through: date):
    """The session calendar of the name's exchange, or None when there is none
    to trust through `through`.

    A calendar is only worth asking once it has been handed its closures: one
    with no table in `markets/holidays.py` is a weekday filter, and would call
    every holiday a blank session. Nor past the day its table expires: Bursa's
    2027 holidays are not entered yet, and a 2027 Chinese New Year read off it
    would be a "blank session". None means "cannot say", and the pack then
    falls back to what the bars alone show, as it always did.
    """
    from markets.holidays import closures
    from markets.registry import get as adapter_for

    try:
        mic = mic_of(instrument_id)
        calendar = adapter_for(mic).calendar
    except (KeyError, ValueError):
        return None
    expires = closures(mic).expires
    return calendar if expires is not None and through <= expires else None


def _days(days) -> str:
    return ", ".join(d.isoformat() for d in days)


def _calendar_sessions(calendar, after: date | None, through: date | None) -> list[date]:
    """The calendar's sessions in (after, through]; none without a calendar."""
    if calendar is None or after is None or through is None or through <= after:
        return []
    return [s.day for s in calendar.sessions_between(after + timedelta(days=1), through)]


def _dating_note(move: Move, *, own_days: set[date], mkt_days: set[date], calendar=None) -> str:
    """Say, in one line, which day this measurement is really about and why.

    A fallback to an earlier session is not itself a fault - markets close. The
    fault is a SILENT fallback, and the four cases have different cures: a
    quiet market needs nothing, a proxy that did not print needs a different
    proxy or a stated refusal, a name that did not print while its market
    did is halted or its feed has stopped and wants looking at, and a session
    neither leg has a bar for is a hole in the cache, not a holiday. They are
    told apart here so the page cannot confuse them. The holiday reading is the
    last resort, reached only when NEITHER leg has a later session and the
    exchange calendar has none either; the note used to reach it on the bars
    alone, so a blank bar both legs shared read as the market being shut.
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
    mic = mic_of(move.instrument_id)
    if move.blank_days:
        return (
            f"BLANK SESSION: {_days(move.blank_days)} is a session "
            f"on the {mic} calendar, but neither {move.instrument_id} nor {move.proxy} has a "
            f"usable bar for it (missing, blank or a carried filler), so every figure in this "
            f"row is the {last} session, not {asked}"
        )
    checked = (
        f": the {mic} calendar has no session in between"
        if calendar is not None
        else f", on the bars alone (no holiday table for {mic} covers {asked} to check them against)"
    )
    return (
        f"no session for {move.instrument_id} or {move.proxy} after {last}; the figures are "
        f"correctly dated to the last session on or before {asked}{checked}"
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
            + f". {m.reason.rstrip('.')}."
            + (
                f" Beta {m.beta:.2f}; {m.estimation}."
                if m.beta is not None
                else f" {m.estimation}."
            )
            + (f" Components: {comps}." if comps else "")
            + (f" {m.dating}." if m.dating else "")
            + (f" {m.adjustment}." if m.adjustment else "")
            + (f" {m.provisional}." if m.provisional else "")
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

    unsettled = [m for m in moves if m.provisional]
    if unsettled:
        out += [
            f"**{len(unsettled)} of {len(moves)} rows are PROVISIONAL.** A leg's bar for the "
            f"row's session was pulled while its market was still trading, so the return and "
            f"its decomposition are the session so far. Do not read them as {day}'s close; "
            f"the next close sweep refetches them:",
            "",
        ]
        out += [f"- {m.label} ({m.instrument_id}): {m.provisional}" for m in unsettled]
        out.append("")

    digest = build_digest(
        cfg, day, slot="pack", corpus_path=corpus_path, facts_path=facts_path, now=now
    )
    with Corpus(corpus_path or cfg.corpus_db) as corpus:
        refs = corpus.short_ids(s["doc_id"] for n in digest.names for s in n.stories)
    out += [
        "Each story line below carries its publication time in UTC and its corpus key: "
        f"cite it as the `doc_id`. A key ending in … is the first {SHORT_ID_CHARS} "
        f"characters of the only key in the corpus that begins with them "
        f"(`substr(doc_id, 1, {SHORT_ID_CHARS})`).",
        "",
    ]
    out.append(digest.to_markdown(refs=refs))
    out.append("")
    out += _web_evidence(cfg, corpus_path or cfg.corpus_db, book_ids, names, now)

    out += ["## Facts, per name (last 7 days of events; latest figures)", ""]
    with FactBook(facts_path or getattr(cfg, "facts_db", "data/facts.db")) as book:
        for iid in book_ids:
            out.append("```")
            out.append(fact_snapshot(book, iid, now=now, days=7))
            out.append(_dividends_held(feed, iid, day))
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


#: How far back the pack lists web evidence attempts: the ingest searches a
#: session up to three sessions after it, so a weekend plus a holiday fits.
WEB_EVIDENCE_DAYS = 5


def _web_evidence(cfg, corpus_path, book_ids, names, now: datetime) -> list[str]:
    """What the web evidence ingest searched for each name, and what it kept.

    Listed by the MOVE searched for, not by publication time: the digest above
    windows on when a story was published, and the page that explains a
    Thursday move may be dated Wednesday or Friday. Each kept row carries its
    own date and its doc_id; the model's summary is a pointer, not evidence."""
    from knowledge.evidence.web import for_pack
    from knowledge.graph.ids import instrument_id as canonical

    since = now - timedelta(days=WEB_EVIDENCE_DAYS)
    lines = [
        "## Web evidence (searched for the moves above that the stored news did not explain)",
        "",
    ]
    found = False
    with Corpus(corpus_path) as corpus:
        for iid in book_ids:
            attempts, rows = for_pack(corpus, iid, since)
            if not attempts and not rows:
                continue
            found = True
            label = names.get(canonical(iid) or iid) or iid
            lines.append(f"**{label}** ({iid})")
            refs = corpus.short_ids(r["doc_id"] for r in rows)
            # Each row under the search that found it: a row is first seen at
            # the instant its attempt was recorded (both are the retrieval).
            shown: set[str] = set()
            for a in attempts:
                lines.append(
                    f"- searched {a['at'][:16]}Z for the {a['session']} move "
                    f"({a['sar']:+.2f} sigma): {a['outcome']}, {a['stored']} stored, "
                    f"USD {Decimal(a['cost_usd']):.4f}. {a['detail']}"
                )
                for r in rows:
                    if r["first_seen_at"] == a["at"]:
                        lines.append(_web_row(r, refs, "  "))
                        shown.add(r["doc_id"])
            lines += [_web_row(r, refs) for r in rows if r["doc_id"] not in shown]
            lines.append("")
    if not found:
        lines += [f"None in the last {WEB_EVIDENCE_DAYS} days.", ""]
    return lines


def _web_row(r: dict, refs: dict, indent: str = "") -> str:
    """One stored web evidence row. A page dated by its day alone says so,
    rather than print the end-of-day stamp it is stored at as if a time."""
    from knowledge.evidence.web import DAY_ONLY

    when = (
        f"{r['published_at'][:10]} (date only)"
        if f", {DAY_ONLY})" in (r.get("body") or "")
        else f"{r['published_at'][:16]}Z"
    )
    return (
        f"{indent}- {when} {r['title']} ({r['source_domain']}) "
        f"`{refs.get(r['doc_id'], r['doc_id'])}`"
    )


#: How many of a name's ex-dates the facts block lists, newest first.
DIVIDENDS_LISTED = 3


def _dividends_held(feed, instrument_id: str, day: date) -> str:
    """The ex-dates the PRICE feed read for a name, under its fact-book block.

    The fact book holds nothing for a Bursa name - no free source there sends
    figures or entitlement dates, so its block reads NOTHING COLLECTED - and
    every page from 2026-09-23 carried the question "which source carries
    Bursa ex-dates?". One already did: the price feed reads each name's
    dividends with its bars (Yahoo's chart actions, `dividend` in
    price_cache.db), and the move rows and the paper book's dividend credits
    use them, but the facts block never showed them, so the page could not see
    an ex-date inside a window it was explaining. Only ex-dates on or before
    `day`: what the pack measures is what was knowable then.
    """
    head = f"\n  dividends the price feed read (ex-date, per share; newest {DIVIDENDS_LISTED})"
    try:
        series = feed.fetch(instrument_id, end=day)
    except PriceFeedError as e:
        return f"{head}\n    cannot read: {str(e).splitlines()[0][:120]}"
    source = getattr(series, "actions_source", None)
    if source is None:
        return f"{head}\n    NO DIVIDEND DATA: the feed read no dividends for this name"
    held = [a for a in series.dividends() if a.ex_date <= day][-DIVIDENDS_LISTED:]
    if not held:
        return f"{head}\n    none in the {source} history held for this name"
    ccy = market_currency(mic_of(instrument_id))
    rows = [f"    {a.ex_date}  {a.amount:g} {ccy}  ({source})" for a in reversed(held)]
    return "\n".join([head, *rows])


def write_pack(text: str, day: date, root: str | Path = PACK_DIR) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.pack.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path
