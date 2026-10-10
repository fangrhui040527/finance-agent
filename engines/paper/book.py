"""The ledger arithmetic: decide, apply at the next open, mark, stop, halt.

Two entry points. `decide` validates a whole target book against the caps and
records it, with one prediction per raised or exited name, and writes nothing
if any cap is breached. `mark` applies whatever is pending at each market's
first cached bar after the decision, marks both books at their last cached
closes, raises stop targets, and re-targets the control when its month turns.

Both legs of every figure come from the same cache. A position whose market
has not produced a bar since the decision stays pending; one that has not
produced a bar in five weekdays expires unapplied. Nothing is ever priced at
the time of the decision, because a daily-bar system does not know that price.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from core.market.feed import PriceFeedError
from core.market.prices import Bar
from engines.paper.fx import FxQuote, UsdMyr
from engines.paper.pricing import (
    ENTRY,
    EXIT,
    currency_of,
    dec,
    first_bar_after,
    last_close,
    leg_fee,
    leg_price,
    lot_size,
    tick_for,
    weekdays_back,
    weekdays_between,
)
from engines.paper.rules import (
    OBSERVE,
    PRE,
    Fundable,
    Phase,
    Refusal,
    fundables,
    phase_for,
    validate_targets,
)
from engines.paper.settings import PaperSettings
from engines.paper.store import (
    ALL_CASH,
    CASH,
    CONTROL,
    DECIDED,
    INDEX,
    BookState,
    MarkRow,
    PaperStore,
    PositionChange,
    TargetRow,
)
from markets.registry import get as market_get
from markets.registry import mic_of

CENT = Decimal("0.01")
TENTH_BP = Decimal("0.0001")
EXPIRE_AFTER_WEEKDAYS = 5
STALE_AFTER_WEEKDAYS = 3
HORIZONS = (1, 5, 21, 63, 252)

#: The hour the nightly Routine decides (docs/22 section 8). A prediction's
#: grading date counts from the decision day at this hour, so the date is a
#: function of the day decided and the horizon, not of when the row was
#: written: a decision for Monday's session recorded at 02:42 UTC on Tuesday
#: grades on the same date as one recorded at 22:30 UTC on Monday.
DECISION_NIGHT = time(22, 30)

#: The market a close slot marks from. `manual` and `all` are absent on
#: purpose: they mark from every market the book trades. XNYS is named for
#: the day a NYSE name joins the watchlist; the registry has no adapter for it
#: yet, and `slot_is_session` skips a MIC it cannot look up.
SLOT_MARKETS: dict[str, frozenset[str]] = {
    "bursa_close": frozenset({"XKLS"}),
    "us_close": frozenset({"XNAS", "XNYS"}),
}


def _cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _fine(x: Decimal) -> Decimal:
    return x.quantize(TENTH_BP, rounding=ROUND_HALF_UP)


def settings_of(cfg, store: PaperStore | None = None) -> PaperSettings:
    """The book's own settings once it is open; the file's until then.

    A book opened with `--start 2026-03-02` for a replay must run its phases
    from that date, whatever config.toml says today - the settings it was
    opened with are frozen in `books.caps_json` for exactly this reason.
    """
    base = getattr(cfg, "paper", None) or PaperSettings()
    if store is not None and store.has_books():
        return PaperSettings.from_dict(store.settings_frozen(), base)
    return base


def fx_for(cfg, fx_db: str | None = None) -> UsdMyr:
    return UsdMyr(
        fx_db or "data/fx.db",
        getattr(cfg, "fx_myr_per_usd", Decimal("4.055")),
        getattr(cfg, "fx_spread_per_side", Decimal("0.005")),
    )


def grade_date(cfg, instrument_id: str, day: date, horizon_days: int) -> date:
    """The `horizon_days`-th session after the decision day, on the name's own
    exchange calendar; an all-cash row's on every market the book trades, so
    the window holds the full horizon on each (`core.market.calendar`).

    Until 2026-10-10 this was calendar days at seven for every five, so a
    "21d" call covered 19 to 22 Bursa sessions depending on the weekday it was
    decided, and graded on a Sunday as often as not. Predictions logged before
    then keep the grade_on they were written with; only new ones count sessions.
    """
    from core.market.calendar import horizon_end

    mics = slot_markets(cfg, "all") if instrument_id == CASH else {mic_of(instrument_id)}
    return horizon_end(mics, day, horizon_days)


def current_weights(mark: MarkRow | None) -> dict[str, Decimal]:
    if mark is None or mark.equity_usd <= 0:
        return {}
    return {p["instrument_id"]: dec(p["value_usd"]) / mark.equity_usd for p in mark.positions}


def equity_seen(store: PaperStore, book: str, before: date) -> Decimal:
    """What the decider saw: the latest mark before the bar day, else the opening cash."""
    m = store.mark_before(book, before)
    return m.equity_usd if m else store.initial_cash(book)


def turnover_used(store: PaperStore, book: str, on: date, sessions: int = 5) -> Decimal:
    start = weekdays_back(on, sessions)
    return sum(
        (c.consideration_usd for c in store.changes(book, end=on) if c.day > start), Decimal(0)
    )


# -- which session a mark belongs to -------------------------------------------------------


def slot_names(cfg, slot: str) -> tuple[str, ...]:
    """The watchlist names whose market the slot closes; all of them for manual/all."""
    names = tuple(cfg.watchlist)
    mics = SLOT_MARKETS.get(slot)
    if mics is None:
        return names
    return tuple(i for i in names if mic_of(i) in mics)


def slot_markets(cfg, slot: str) -> frozenset[str]:
    mics = SLOT_MARKETS.get(slot)
    if mics is not None:
        return mics
    return frozenset(mic_of(i) for i in cfg.watchlist) or frozenset({"XKLS", "XNAS"})


def slot_is_session(cfg, slot: str, day: date) -> bool:
    """Whether a market the slot marks from calls `day` a session.

    The calendars as they stand: weekends only, until the holiday tables land.
    A Labor Day us_close mark therefore passes here and is caught, if at all,
    by the bars - there is no XNAS bar that day, so `session_day` stamps the
    Friday before it.
    """
    for mic in slot_markets(cfg, slot):
        try:
            calendar = market_get(mic).calendar
        except KeyError:
            continue
        if calendar.is_session(day):
            return True
    return False


def session_open(cfg, slot: str, day: date) -> datetime | None:
    """When the first market the slot marks from opens on `day`, UTC.

    None when no market the slot marks from calls `day` a session, or none of
    them can be looked up. A mark taken before this instant cannot carry the
    day's bars, provisional or settled: the market had not opened.
    """
    opens: list[datetime] = []
    for mic in slot_markets(cfg, slot):
        try:
            calendar = market_get(mic).calendar
        except KeyError:
            continue
        session = calendar.session(day)
        if session is not None:
            opens.append(session.open_utc())
    return min(opens) if opens else None


def bars_session(feed, cfg, slot: str, on_or_before: date) -> date | None:
    """The day of the last cached bar, on or before the day, on any name the slot marks from."""
    seen: date | None = None
    for iid in slot_names(cfg, slot):
        try:
            _, close_day = last_close(feed, iid, on_or_before)
        except PriceFeedError:
            continue
        if seen is None or close_day > seen:
            seen = close_day
    return seen


def session_day(feed, cfg, slot: str, day: date) -> date | None:
    """The session a mark asked for on `day` belongs to; None if there is none to mark.

    A mark is a fact about the bars it is marked from, so its day is the
    session those bars closed - the last cached bar for the slot's market on
    or before the day asked for - never the wall clock. A catch-up run on a
    Saturday marks Friday's session; a run on a holiday marks the session
    before it; each replaces that session's earlier mark rather than adding a
    day the market never traded. Until 2026-09-19 the wall clock was the
    stamp, which put sixteen marks on Saturdays and Sundays and filed the run
    that priced Friday 2026-09-11's close under the 12th. Without a cached bar
    to say otherwise, the day asked for stands if the calendar calls it a
    session, and nothing is marked if it does not.
    """
    from_bars = bars_session(feed, cfg, slot, day)
    if from_bars is not None:
        return from_bars
    return day if slot_is_session(cfg, slot, day) else None


# -- decide ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoggedPrediction:
    prediction_id: str
    instrument_id: str
    direction: int
    horizon_days: int
    grade_on: date


@dataclass
class DecisionResult:
    day: date
    phase: Phase
    refusals: list[Refusal] = field(default_factory=list)
    targets: list[TargetRow] = field(default_factory=list)
    predictions: list[LoggedPrediction] = field(default_factory=list)
    superseded: int = 0
    all_cash: bool = False
    dry_run: bool = False
    equity_usd: Decimal = Decimal(0)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def render(self) -> str:
        if self.refused:
            lines = [f"REFUSED: {len(self.refusals)} cap(s) breached; nothing recorded."]
            lines += [f"  - [{r.code}] {r.message}" for r in self.refusals]
            return "\n".join(lines)
        head = "DRY RUN - would record" if self.dry_run else "recorded"
        lines = [
            f"{head} {len(self.targets)} target(s) for {self.day} ({self.phase.describe()}), "
            f"sized against equity USD {self.equity_usd:,.2f}"
        ]
        for t in self.targets:
            lines.append(f"  {t.instrument_id:<12} {t.weight:>7.2%}  {t.reason}")
        if self.all_cash:
            lines.append("  all-cash: one row and one prediction, graded against the control book")
        if self.predictions:
            lines.append(f"  {len(self.predictions)} prediction(s) logged:")
            lines += [
                f"    {p.prediction_id}  {'+' if p.direction > 0 else '-'}{p.horizon_days}d  grades on {p.grade_on}"
                for p in self.predictions
            ]
        if self.superseded:
            lines.append(
                f"  superseded {self.superseded} pending target(s) from an earlier decision today"
            )
        if self.phase.name == OBSERVE:
            lines.append("  observe phase: this is logged and will be graded; it is not applied")
        else:
            lines.append("  applies at each market's first cached bar after this day")
        return "\n".join(lines)


def decide(
    store: PaperStore,
    cfg,
    feed,
    fx: UsdMyr,
    *,
    day: date,
    weights: dict[str, Decimal],
    thesis: str,
    horizon: int = 21,
    confidence: float = 0.55,
    learning=None,
    supersede: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> DecisionResult:
    now = now or datetime.now(UTC)
    settings = settings_of(cfg, store)
    phase = phase_for(day, settings)
    result = DecisionResult(day, phase, dry_run=dry_run)

    if not store.has_books():
        result.refusals.append(Refusal("book", "NO BOOK: run `ask.py paper init` first"))
        return result
    if not thesis.strip():
        result.refusals.append(
            Refusal("thesis", "a decision without a thesis cannot be graded; --thesis is required")
        )
    if not 0.0 < confidence < 1.0:
        result.refusals.append(Refusal("confidence", "confidence must be strictly between 0 and 1"))
    if horizon not in HORIZONS:
        result.refusals.append(
            Refusal("horizon", f"horizon must be one of {list(HORIZONS)} sessions")
        )

    mark = store.latest_mark(DECIDED, on_or_before=day)
    equity = mark.equity_usd if mark else store.initial_cash(DECIDED)
    result.equity_usd = equity
    current = current_weights(mark)
    state = store.state(DECIDED)
    quote = fx.asof(day)
    funds = fundables(feed, cfg, equity, quote, day, settings)
    pending_stops = {t.instrument_id for t in store.pending_targets(DECIDED) if t.reason == "stop"}

    result.refusals += validate_targets(
        weights,
        state=state,
        current=current,
        equity=equity,
        fundable=funds,
        phase=phase,
        settings=settings,
        halted=bool(mark and mark.halted),
        pending_stops=pending_stops,
        turnover_used=turnover_used(store, DECIDED, day),
        watchlist=tuple(cfg.watchlist),
    )

    recorded_today = [
        t for t in store.targets_on(DECIDED, day) if t.reason in ("decision", ALL_CASH)
    ]
    todays = [
        t for t in store.pending_targets(DECIDED) if t.decided_on == day and t.reason == "decision"
    ]
    if recorded_today and not supersede:
        result.refusals.append(
            Refusal(
                "duplicate",
                f"a decision for {day} is already pending; pass --supersede to replace it",
            )
        )
    if result.refused:
        return result

    held = {p.instrument_id for p in state.positions}
    rows: list[TargetRow] = []
    preds: list[LoggedPrediction] = []
    for iid, w in weights.items():
        if w <= 0 and iid not in held:
            continue
        rows.append(TargetRow(DECIDED, day, now, iid, w, "decision", phase.name, thesis))
    for iid in sorted(held - set(weights)):
        rows.append(
            TargetRow(
                DECIDED,
                day,
                now,
                iid,
                Decimal(0),
                "decision",
                phase.name,
                thesis + " (omitted: exit)",
            )
        )

    # AN ALL-CASH NIGHT IS A DECISION. Until 2026-09-09 it was the one decision
    # that left no trace: no weight raised and no name held is zero rows, so the
    # book could not tell "held nothing on purpose" from "was never asked", and
    # `decide` printed "this is logged and will be graded" over an empty write.
    # It is also a CLAIM - that nothing in the fundable universe beats cash over
    # the horizon - and the control book is the counterfactual that settles it.
    # So it gets one row and one prediction, graded against the control instead
    # of against a price.
    all_cash = not rows
    if all_cash:
        rows.append(TargetRow(DECIDED, day, now, CASH, Decimal(0), ALL_CASH, phase.name, thesis))

    if learning is not None:
        from agents.learning.reflection import Horizon, Prediction

        # `made_at` is the clock. The row says when the call was actually
        # made; the day decided lives in decided_on, in the id and in the
        # context, and the grading date counts from that day (DECISION_NIGHT),
        # so a decision recorded after midnight neither claims hours it did
        # not have nor lengthens its horizon. Until 2026-09-19 such a decision
        # was stamped 22:30Z of its day: paper-2026-09-14-allcash said made
        # 22:30Z on the 14th and was decided at 02:42Z on the 15th.
        #
        # A replay is the one case that keeps the nominal stamp: a decision
        # for a day whose horizon has already run out in real time. The log
        # refuses a prediction whose grading date is behind it - a horizon set
        # after the fact is not a horizon - so the row carries the decision
        # night as its clock and says in its context when it was written.
        nominal = datetime.combine(day, DECISION_NIGHT, tzinfo=UTC)
        for i, t in enumerate(rows):
            grade_on = grade_date(cfg, t.instrument_id, day, horizon)
            replayed = grade_on <= now.date()
            made = nominal if replayed else now
            cur = current.get(t.instrument_id, Decimal(0))
            if t.reason == ALL_CASH:
                # +1 because the claim is "this book beats the control by
                # holding nothing". `correct` is then direction * (realised -
                # benchmark) > 0, which is exactly that question.
                direction = 1
                slug = "allcash"
                statement = (
                    f"all-cash: nothing in the fundable universe beats cash over "
                    f"{horizon} sessions: {thesis}"
                )
            else:
                direction = 1 if t.weight > cur else (-1 if (t.weight == 0 and cur > 0) else 0)
                if direction == 0:
                    continue
                slug = t.instrument_id.replace(":", "").lower()
                statement = f"{t.instrument_id} target {t.weight:.2%} (from {cur:.2%}): {thesis}"
            prior = len(store.targets_on(DECIDED, day))
            salt = hashlib.sha256(
                f"{thesis}|{t.weight}|{now.isoformat()}|{prior}".encode()
            ).hexdigest()[:4]
            pid = f"paper-{day}-{slug}-{horizon}d-{salt}"
            p = Prediction(
                prediction_id=pid,
                instrument_id=t.instrument_id,
                agent="paper",
                made_at=made,
                horizon=Horizon(f"{horizon}d"),
                statement=statement,
                direction=direction,
                confidence=confidence,
                grade_on=grade_on,
                context={
                    "paper": True,
                    "decided_on": day.isoformat(),
                    "from_weight": str(cur),
                    "to_weight": str(t.weight),
                    "phase": phase.name,
                    "all_cash": t.reason == ALL_CASH,
                    **({"replayed_at": now.isoformat()} if replayed else {}),
                },
            )
            if not dry_run:
                for attempt in range(1, 6):
                    try:
                        learning.record(p)
                        break
                    except ValueError:
                        # the same thesis, weight and clock twice: a different id, not a crash
                        pid = f"paper-{day}-{slug}-{horizon}d-{salt}-{attempt}"
                        p = replace(p, prediction_id=pid)
                else:
                    raise
            rows[i] = TargetRow(
                t.book,
                t.decided_on,
                t.decided_at,
                t.instrument_id,
                t.weight,
                t.reason,
                t.phase,
                t.thesis,
                pid,
            )
            preds.append(LoggedPrediction(pid, t.instrument_id, direction, horizon, p.grade_on))

    if not dry_run:
        if todays and supersede:
            for t in todays:
                assert t.target_id is not None
                store.resolve(
                    t.target_id, day, "superseded", f"replaced by a later decision on {day}"
                )
            result.superseded = len(todays)
        if supersede and learning is not None:
            # A SUPERSEDED DECISION WITHDRAWS ITS CALLS. Until 2026-10-10 its
            # predictions stayed pending in the learning log: the withdrawn names
            # were graded, and a name kept in both decisions was graded twice
            # from two reference prices, all of it in calibration. The day's
            # earlier targets - the pending ones resolved above and an all-cash
            # row, which is resolved the moment it is written - now mark their
            # predictions "withdrawn" (an append-only row; the prediction is
            # untouched): never graded, out of calibration. Rows superseded
            # before then were not withdrawn and keep whatever grade they got.
            replaced = todays + [t for t in recorded_today if t.reason == ALL_CASH]
            for t in replaced:
                if not t.prediction_id:
                    continue
                try:
                    learning.withdraw(
                        t.prediction_id, day, f"superseded by a later decision on {day}"
                    )
                except ValueError:
                    pass  # graded already (a replayed day), or not in this log: it stays
        # ONE LIVE DECISION. A decision is the whole target book, so every
        # older decision still waiting for its bar is replaced by this one - for
        # the names it repeats and for the names it leaves out. Left pending,
        # Friday's, Saturday's and Sunday's books all filled at Monday's open,
        # each against the same holding (see `_one_live_target_per_name`).
        result.superseded += supersede_pending(
            store, DECIDED, on=day, why=f"replaced by the decision of {day}", reasons=("decision",)
        )
        ids = store.record_targets(rows)
        for tid, t in zip(ids, rows, strict=True):
            if t.reason == ALL_CASH:
                # Resolved on the spot: there is no position to open, and a row
                # left pending would be picked up by `apply_pending`, which
                # would ask a market for the price of CASH.
                store.resolve(
                    tid, day, ALL_CASH, "held nothing; the row is the record that it was decided"
                )
    result.targets = rows
    result.all_cash = all_cash
    result.predictions = preds
    return result


# -- apply ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Applied:
    target: TargetRow
    status: str
    detail: str = ""
    change: PositionChange | None = None


def _price_usd(price_local: Decimal, ccy: str, quote: FxQuote) -> Decimal:
    return price_local / quote.rate if ccy == "MYR" else price_local


def _entry(
    store: PaperStore,
    cfg,
    fx: UsdMyr,
    *,
    book: str,
    t: TargetRow,
    bar,
    units: int,
    quote: FxQuote,
    settings: PaperSettings,
    equity: Decimal,
) -> tuple[PositionChange | None, str]:
    """Build an entry of `units`, shrinking by lots until cash stays above the floor."""
    iid = t.instrument_id
    ccy = currency_of(iid)
    lot = lot_size(iid)
    mic = mic_of(iid)
    state = store.state(book)
    pos = state.position(iid)
    held = pos.units if pos else 0
    avg = pos.avg_cost if pos else Decimal(0)
    bar_open = dec(bar.open)
    price = leg_price(bar_open, ENTRY, settings.slippage_bps(mic), tick_for(iid, bar_open))
    floor = settings.cash_floor * equity
    used = turnover_used(store, book, bar.day) if book == DECIDED else Decimal(0)
    cap = settings.weekly_turnover_cap * equity

    while units > 0:
        consideration = price * units
        fee = _cents(leg_fee(mic, getattr(cfg, "broker", None), consideration, price, ENTRY))
        gross = consideration + fee
        if ccy == "MYR":
            cash_out = fx.usd_needed_to_buy_myr(gross, quote)
            spread = cash_out - gross / quote.rate
        else:
            cash_out, spread = gross, Decimal(0)
        consideration_usd = _price_usd(consideration, ccy, quote)
        if book == DECIDED and used + consideration_usd > cap:
            units -= lot
            reason = "turnover cap"
            continue
        if state.cash_usd - cash_out < floor:
            units -= lot
            reason = "cash floor"
            continue
        new_units = held + units
        new_avg = ((avg * held) + (price * units)) / new_units
        change = PositionChange(
            book=book,
            target_id=t.target_id,
            day=bar.day,
            instrument_id=iid,
            currency=ccy,
            action="add" if held else "open",
            units_delta=units,
            units_after=new_units,
            bar_open=bar_open,
            slippage_bps=settings.slippage_bps(mic),
            price_local=price,
            consideration_local=consideration,
            consideration_usd=_fine(consideration_usd),
            fee_local=fee,
            fee_usd=_fine(_price_usd(fee, ccy, quote)),
            fx_rate=quote.rate if ccy == "MYR" else Decimal(1),
            fx_date=quote.rate_date,
            fx_source=quote.source if ccy == "MYR" else "n/a",
            fx_spread_usd=_fine(spread),
            slippage_usd=_fine(_price_usd(abs(price - bar_open) * units, ccy, quote)),
            cash_delta_usd=-_cents(cash_out),
            avg_cost_after=new_avg,
        )
        return change, ""
    return None, reason if units <= 0 else "below one lot"  # type: ignore[possibly-undefined]


def _exit(
    store: PaperStore,
    cfg,
    fx: UsdMyr,
    *,
    book: str,
    t: TargetRow,
    bar,
    units: int,
    quote: FxQuote,
    settings: PaperSettings,
) -> PositionChange | None:
    iid = t.instrument_id
    ccy = currency_of(iid)
    mic = mic_of(iid)
    state = store.state(book)
    pos = state.position(iid)
    held = pos.units if pos else 0
    avg = pos.avg_cost if pos else Decimal(0)
    units = min(units, held)
    if units <= 0:
        # Nothing left to sell. Recording an `exit 0` charged the Bursa card's
        # flat RM3 platform fee plus SST on zero consideration - a phantom trade
        # in the cost to date - whenever a stop and a decision exit met.
        return None
    bar_open = dec(bar.open)
    price = leg_price(bar_open, EXIT, settings.slippage_bps(mic), tick_for(iid, bar_open))
    consideration = price * units
    fee = _cents(leg_fee(mic, getattr(cfg, "broker", None), consideration, price, EXIT))
    net = consideration - fee
    if ccy == "MYR":
        cash_in = fx.usd_received_for_myr(net, quote)
        spread = net / quote.rate - cash_in
    else:
        cash_in, spread = net, Decimal(0)
    remaining = held - units
    return PositionChange(
        book=book,
        target_id=t.target_id,
        day=bar.day,
        instrument_id=iid,
        currency=ccy,
        action="exit" if remaining == 0 else "trim",
        units_delta=-units,
        units_after=remaining,
        bar_open=bar_open,
        slippage_bps=settings.slippage_bps(mic),
        price_local=price,
        consideration_local=consideration,
        consideration_usd=_fine(_price_usd(consideration, ccy, quote)),
        fee_local=fee,
        fee_usd=_fine(_price_usd(fee, ccy, quote)),
        fx_rate=quote.rate if ccy == "MYR" else Decimal(1),
        fx_date=quote.rate_date,
        fx_source=quote.source if ccy == "MYR" else "n/a",
        fx_spread_usd=_fine(spread),
        slippage_usd=_fine(_price_usd(abs(price - bar_open) * units, ccy, quote)),
        cash_delta_usd=_cents(cash_in),
        avg_cost_after=avg if remaining else Decimal(0),
        realised_pnl_usd=_cents(_price_usd((price - avg) * units, ccy, quote)),
    )


def apply_pending(
    store: PaperStore, cfg, feed, fx: UsdMyr, *, book: str, up_to: date
) -> list[Applied]:
    """Resolve every pending target whose market has produced a bar since the decision."""
    settings = settings_of(cfg, store)
    out: list[Applied] = []
    ready: list[tuple[TargetRow, Bar]] = []
    live = _one_live_target_per_name(store, store.pending_targets(book), up_to, out)
    for t in live:
        assert t.target_id is not None
        if t.phase in (OBSERVE, PRE) and t.reason == "decision":
            if up_to > t.decided_on:
                store.resolve(
                    t.target_id, up_to, "observed", "observe phase: logged, never applied"
                )
                out.append(Applied(t, "observed"))
            continue
        bar = _fill_bar(feed, t, up_to)
        if bar is None:
            if weekdays_between(t.decided_on, up_to) > EXPIRE_AFTER_WEEKDAYS:
                store.resolve(t.target_id, up_to, "expired", "no cached bar within five weekdays")
                out.append(Applied(t, "expired"))
            continue
        ready.append((t, bar))

    # Size everything against the same equity the decider saw, then exits first.
    plans = []
    for t, bar in ready:
        iid = t.instrument_id
        ccy = currency_of(iid)
        lot = lot_size(iid)
        quote = fx.asof(bar.day)
        equity = equity_seen(store, book, bar.day)
        held = (store.state(book).position(iid) or PositionChangeStub()).units
        if t.target_units is not None:
            target_units = int(t.target_units)
        else:
            bar_open = dec(bar.open)
            entry_px = leg_price(
                bar_open, ENTRY, settings.slippage_bps(mic_of(iid)), tick_for(iid, bar_open)
            )
            lot_usd = _price_usd(entry_px, ccy, quote) * lot
            target_units = (
                int((t.weight * equity / lot_usd).quantize(Decimal(1), rounding=ROUND_DOWN)) * lot
                if lot_usd > 0
                else 0
            )
            if t.weight > 0 and target_units == 0 and held > 0:
                # The weight still says "hold this name"; a lot that outgrew the
                # weight between the decision and the open is not an exit call.
                target_units = held
        plans.append((t, bar, target_units - held, quote, equity))

    exits = sorted(
        (p for p in plans if p[2] < 0), key=lambda p: (p[0].reason != "stop", p[0].target_id)
    )
    entries = sorted((p for p in plans if p[2] > 0), key=lambda p: (-p[0].weight, p[0].target_id))
    flat = [p for p in plans if p[2] == 0]

    for t, bar, _delta, _q, _e in flat:
        assert t.target_id is not None
        store.resolve(t.target_id, bar.day, "no_change", "target equals the held units")
        out.append(Applied(t, "no_change"))

    for t, bar, delta, quote, _equity in exits:
        assert t.target_id is not None
        change = _exit(
            store, cfg, fx, book=book, t=t, bar=bar, units=-delta, quote=quote, settings=settings
        )
        if change is None:
            store.resolve(t.target_id, bar.day, "no_change", "nothing held to exit")
            out.append(Applied(t, "no_change"))
            continue
        store.record_change(change)
        store.resolve(
            t.target_id,
            bar.day,
            "applied",
            f"{change.action} {abs(change.units_delta)} @ {change.price_local}",
        )
        out.append(Applied(t, "applied", change=change))

    for t, bar, delta, quote, equity in entries:
        assert t.target_id is not None
        if book == DECIDED:
            m = store.mark_before(DECIDED, bar.day)
            if m is not None and m.halted:
                store.resolve(
                    t.target_id,
                    bar.day,
                    "skipped",
                    "halted: drawdown at or past the halt line at the last mark",
                )
                out.append(Applied(t, "skipped", "halted"))
                continue
        change, why = _entry(
            store,
            cfg,
            fx,
            book=book,
            t=t,
            bar=bar,
            units=delta,
            quote=quote,
            settings=settings,
            equity=equity,
        )
        if change is None:
            store.resolve(t.target_id, bar.day, "skipped", why)
            out.append(Applied(t, "skipped", why))
            continue
        store.record_change(change)
        store.resolve(
            t.target_id,
            bar.day,
            "applied",
            f"{change.action} {change.units_delta} @ {change.price_local}",
        )
        out.append(Applied(t, "applied", change=change))
    return out


class PositionChangeStub:
    units = 0


def _target_order(t: TargetRow) -> tuple:
    at = t.decided_at if t.decided_at.tzinfo else t.decided_at.replace(tzinfo=UTC)
    return (t.decided_on, at, t.target_id or 0)


def _one_live_target_per_name(
    store: PaperStore, pending: list[TargetRow], up_to: date, out: list[Applied]
) -> list[TargetRow]:
    """The newest pending target per instrument; the older ones resolved as superseded.

    Every target is sized as `target units - held`, with `held` read before
    any of the run's changes. Two pending targets for one name each bought the
    whole difference: three identical weekend decisions waiting for Monday's
    first bar would take a 21% weight to 42%, past the 25% cap and the ramp's
    40% ceiling. `decide` and the rebalance writers now retire the older
    targets when they write; this is the same rule at the point of use, for
    anything already in the ledger.

    One exception: a pending STOP beats a later decision for the same name.
    `validate_targets` already refuses to target a stopped name above zero, so
    the later decision can only be an exit too - and one exit is the trade.
    """
    by_name: dict[str, list[TargetRow]] = {}
    for t in pending:
        by_name.setdefault(t.instrument_id, []).append(t)
    live: list[TargetRow] = []
    for rows in by_name.values():
        if len(rows) == 1:
            live.append(rows[0])
            continue
        stops = [t for t in rows if t.reason == "stop"]
        keep = max(stops or rows, key=_target_order)
        for t in rows:
            if t is keep:
                continue
            assert t.target_id is not None
            why = (
                "the pending stop exits this name"
                if keep.reason == "stop"
                else f"replaced by the {keep.reason} target of {keep.decided_on}"
            )
            store.resolve(t.target_id, up_to, "superseded", why)
            out.append(Applied(t, "superseded", why))
        live.append(keep)
    return sorted(live, key=lambda t: (t.decided_on, t.target_id or 0))


def _fill_bar(feed, t: TargetRow, up_to: date):
    """The bar a target fills at: the first session that OPENED after the decision.

    `first_bar_after(decided_on)` alone is the next calendar session, and a
    decision recorded after that session had opened - the nightly routine
    running at 02:42Z, after Bursa's 01:00Z open - filled at a price printed
    before it was decided. Such a target waits for the session after.
    """
    bar = first_bar_after(feed, t.instrument_id, t.decided_on, up_to)
    decided_at = t.decided_at if t.decided_at.tzinfo else t.decided_at.replace(tzinfo=UTC)
    while bar is not None:
        try:
            session = market_get(mic_of(t.instrument_id)).calendar.session(bar.day)
        except (KeyError, ValueError):
            return bar
        if session is None or session.open_utc() > decided_at:
            return bar
        bar = first_bar_after(feed, t.instrument_id, bar.day, up_to)
    return None


def supersede_pending(
    store: PaperStore, book: str, *, on: date, why: str, reasons: tuple[str, ...] | None = None
) -> int:
    """Retire a book's pending targets before a newer set is written. Returns how many.

    `reasons` limits it to those kinds (the decided book keeps a pending stop
    through a new decision; the stop exits at the next open whatever is decided).
    """
    n = 0
    for t in store.pending_targets(book):
        if reasons is not None and t.reason not in reasons:
            continue
        assert t.target_id is not None
        store.resolve(t.target_id, on, "superseded", why)
        n += 1
    return n


# -- mark ------------------------------------------------------------------------------------


def mark_book(
    store: PaperStore, cfg, feed, fx: UsdMyr, *, book: str, day: date, slot: str, now: datetime
) -> tuple[MarkRow, list[str]]:
    settings = settings_of(cfg, store)
    # The book as it stood at THIS session's close, and the mark before it on
    # or before this day: a slot that falls back to an earlier session must not
    # value later fills at earlier closes, or take its peak from a later day.
    state = store.state(book, end=day)
    quote = fx.asof(day)
    prev = store.latest_mark(book, on_or_before=day)
    prev_close = {p["instrument_id"]: p for p in (prev.positions if prev else [])}
    problems: list[str] = []
    rows: list[dict] = []
    positions_usd = Decimal(0)
    for p in state.positions:
        try:
            close, close_day = last_close(feed, p.instrument_id, day)
            stale = weekdays_between(close_day, day) > STALE_AFTER_WEEKDAYS
            unpriced = False
        except PriceFeedError as e:
            old = prev_close.get(p.instrument_id)
            if old is None:
                problems.append(
                    f"{p.instrument_id}: {str(e).splitlines()[0]}; carried at average cost"
                )
                close, close_day, stale, unpriced = p.avg_cost, day, True, True
            else:
                problems.append(
                    f"{p.instrument_id}: no cached bar; carried at the {old['close_day']} close"
                )
                close, close_day, stale, unpriced = (
                    dec(old["close"]),
                    date.fromisoformat(old["close_day"]),
                    True,
                    True,
                )
        value = _cents(_price_usd(close * p.units, p.currency, quote))
        positions_usd += value
        rows.append(
            {
                "instrument_id": p.instrument_id,
                "units": p.units,
                "avg_cost": str(p.avg_cost),
                "currency": p.currency,
                "close": str(close),
                "close_day": close_day.isoformat(),
                "stale": stale,
                "unpriced": unpriced,
                "value_usd": str(value),
                "pnl_open_usd": str(
                    _cents(_price_usd((close - p.avg_cost) * p.units, p.currency, quote))
                ),
            }
        )
    equity = _cents(state.cash_usd + positions_usd)
    # From the marks that REMAIN: the (day, slot) reading this mark replaces is
    # left out, so a provisional spike it carried does not outlive it.
    peak = max(store.peak_equity(book, day, excluding=(day, slot)), equity)
    drawdown = (Decimal(1) - equity / peak) if peak > 0 else Decimal(0)
    drawdown = max(Decimal(0), drawdown).quantize(TENTH_BP)
    for r in rows:
        r["weight"] = str((dec(r["value_usd"]) / equity).quantize(TENTH_BP)) if equity > 0 else "0"
    halted = drawdown >= settings.drawdown_halt
    m = MarkRow(
        book=book,
        day=day,
        slot=slot,
        marked_at=now,
        cash_usd=_cents(state.cash_usd),
        positions_usd=positions_usd,
        equity_usd=equity,
        peak_usd=peak,
        drawdown=drawdown,
        halted=halted,
        phase=phase_for(day, settings).name,
        fx_rate=quote.rate,
        fx_date=quote.rate_date,
        fx_source=quote.source,
        positions=rows,
    )
    mid = store.record_mark(m)
    return MarkRow(**{**m.__dict__, "mark_id": mid}), problems


def stop_checks(store: PaperStore, cfg, mark: MarkRow, now: datetime) -> list[TargetRow]:
    """Positions whose close breached the stop get a weight-0 target for the next open."""
    settings = settings_of(cfg, store)
    pending = {t.instrument_id for t in store.pending_targets(DECIDED) if t.weight == 0}
    rows: list[TargetRow] = []
    for p in mark.positions:
        if p.get("unpriced"):
            continue
        close, avg = dec(p["close"]), dec(p["avg_cost"])
        line = avg * (Decimal(1) - settings.stop_loss)
        if close <= line and p["instrument_id"] not in pending:
            rows.append(
                TargetRow(
                    DECIDED,
                    mark.day,
                    now,
                    p["instrument_id"],
                    Decimal(0),
                    "stop",
                    mark.phase,
                    f"stop: close {close} at or below {line:.4f} ({settings.stop_loss:.0%} under average cost {avg})",
                    target_units=0,
                )
            )
    if rows:
        store.record_targets(rows)
    return rows


@dataclass
class MarkResult:
    day: date
    slot: str
    marks: dict[str, MarkRow] = field(default_factory=dict)
    applied: dict[str, list[Applied]] = field(default_factory=dict)
    stops: list[TargetRow] = field(default_factory=list)
    control_targets: list[TargetRow] = field(default_factory=list)
    #: The index book's rebalance rows, and the line that says what they left
    #: out. A hundred rows are summarised on the page, never listed.
    index_targets: list[TargetRow] = field(default_factory=list)
    index_summary: str = ""
    problems: list[str] = field(default_factory=list)
    #: What the run did to the record besides marking: the session it stamped
    #: when that is not the day asked for, the earlier mark it replaced, the
    #: marks it re-dated. Said, not counted as problems.
    notes: list[str] = field(default_factory=list)
    #: Why nothing was marked - the day is no session and the cache holds no
    #: bar to mark from. Exit 2, the code for "refused", like every refusal.
    refusal: str = ""

    @property
    def exit_code(self) -> int:
        if self.refusal:
            return 2
        return 3 if self.problems else 0

    def render(self) -> str:
        lines = [f"paper mark {self.day} ({self.slot})"]
        if self.refusal:
            lines.append(f"  REFUSED: {self.refusal}")
        for n in self.notes:
            lines.append(f"  note: {n}")
        for book, m in self.marks.items():
            flag = "  HALTED" if m.halted else ""
            lines.append(
                f"  {book:<8} equity USD {m.equity_usd:>9,.2f}  cash {m.cash_usd:>9,.2f}  "
                f"positions {m.positions_usd:>9,.2f}  peak {m.peak_usd:,.2f}  drawdown {m.drawdown:.2%}"
                f"  fx {m.fx_rate} ({m.fx_source} {m.fx_date}){flag}"
            )
            if book == INDEX:
                lines += _index_applied_lines(self.applied.get(book, []))
                continue
            for a in self.applied.get(book, []):
                if a.change:
                    c = a.change
                    lines.append(
                        f"    {c.action:<5} {c.instrument_id:<10} {c.units_delta:+6d} @ {c.price_local} {c.currency}"
                        f"  fee {c.fee_usd} USD  cash {c.cash_delta_usd:+,.2f}"
                    )
                else:
                    lines.append(f"    {a.status:<9} {a.target.instrument_id:<10} {a.detail}")
        for t in self.stops:
            lines.append(f"  stop raised: {t.instrument_id} - {t.thesis}")
        for t in self.control_targets:
            lines.append(f"  control target: {t.instrument_id} {t.target_units} units")
        if self.index_summary:
            lines.append(f"  {self.index_summary}")
        for p in self.problems:
            lines.append(f"  PROBLEM: {p}")
        return "\n".join(lines)


def _index_applied_lines(applied: list[Applied]) -> list[str]:
    """The index book's day in counts: a rebalance resolves a hundred targets."""
    if not applied:
        return []
    counts: dict[str, int] = {}
    fees = cash = Decimal(0)
    for a in applied:
        key = a.change.action if a.change else a.status
        counts[key] = counts.get(key, 0) + 1
        if a.change:
            fees += a.change.fee_usd
            cash += a.change.cash_delta_usd
    lines = [
        "    "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + f"  fees USD {fees:,.2f}  cash {cash:+,.2f}"
    ]
    for a in applied:
        if a.status == "skipped":
            lines.append(f"    skipped   {a.target.instrument_id:<10} {a.detail}")
    return lines


def index_rebalance(store: PaperStore, cfg, feed, fx: UsdMyr, *, mark_row: MarkRow, now: datetime):
    """Write the index book's rebalance targets for the next open, if one is due."""
    from engines.paper.control import rebalance_due
    from engines.paper.index import REASON, rebalance_targets
    from engines.paper.universe import check, load_universe

    settings = settings_of(cfg, store)
    day = mark_row.day
    phase = phase_for(day, settings)
    if not rebalance_due(store, day, phase, book=INDEX, reason=REASON):
        return None
    universe = load_universe(index_universe_path(store))
    quote = fx.asof(day)
    funds = fundables(
        feed, cfg, mark_row.equity_usd, quote, day, settings, names=universe.ids, now=now
    )
    res = rebalance_targets(
        store,
        funds,
        check(universe, feed),
        universe,
        equity=mark_row.equity_usd,
        day=day,
        now=now,
        phase=phase,
        settings=settings,
    )
    if any((t.target_units or 0) > 0 for t in res.targets):
        supersede_pending(store, INDEX, on=day, why=f"replaced by the index rebalance of {day}")
        store.record_targets(res.targets)
    else:
        # Nothing priced, nothing to hold: write nothing, so the next mark
        # tries again instead of waiting for the month to turn.
        res.targets = []
    return res


def index_universe_path(store: PaperStore):
    """The universe file the index book was opened with, resolved from the repository root."""
    from pathlib import Path

    from engines.paper.universe import DEFAULT_UNIVERSE

    named = store.book_terms(INDEX).get("universe")
    if not named:
        return DEFAULT_UNIVERSE
    p = Path(named)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


def mark(
    store: PaperStore,
    cfg,
    feed,
    fx: UsdMyr,
    *,
    day: date,
    slot: str = "manual",
    now: datetime | None = None,
) -> MarkResult:
    from engines.paper.control import control_units, rebalance_due

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)  # the store writes UTC; compare like with like
    settings = settings_of(cfg, store)
    result = MarkResult(day, slot)
    if not store.has_books():
        result.problems.append("NO BOOK: run `ask.py paper init` first")
        return result
    if store.marks_deduplicated:
        result.notes.append(
            f"removed {store.marks_deduplicated} duplicate mark(s) on opening the ledger: "
            "a slot marks a session once, and the later reading stays"
        )
        store.marks_deduplicated = 0
    result.notes += restamp_marks(store, feed, cfg)
    session = session_day(feed, cfg, slot, day)
    if session is None:
        result.refusal = (
            f"{day} is not a session of the {slot} market and the cache holds no earlier "
            "bar to mark from; nothing marked"
        )
        return result
    missing = _closed_session_without_a_bar(cfg, slot, day, session, now)
    if missing:
        # The day traded and has closed, and the cache holds no usable bar for
        # it - on 2026-10-05 Yahoo served Monday's row with a blank close. Marking
        # the session before it instead replaced Friday's marks with a later
        # reading and left Monday with no mark at all, at exit 0. Say so, exit 3,
        # and leave the earlier session's marks alone; the next run retries.
        result.problems.append(missing)
        return result
    if session != day:
        result.notes.append(
            f"marked as {session}: the session of the last cached bar on or before {day} "
            f"for the market(s) the {slot} slot marks"
        )
        day = result.day = session
    phase = phase_for(day, settings)
    # The index book from its opening day on: a replay marking an earlier
    # session must not write marks for a book that did not exist yet.
    index_open = store.book_opened_on(INDEX)
    books = (DECIDED, CONTROL) + ((INDEX,) if index_open and day >= index_open else ())
    for book in books:
        result.applied[book] = apply_pending(store, cfg, feed, fx, book=book, up_to=day)
        prior = store.mark_for(book, day, slot)
        m, problems = mark_book(store, cfg, feed, fx, book=book, day=day, slot=slot, now=now)
        if prior is not None:
            if m.marked_at >= prior.marked_at:
                result.notes.append(
                    f"{book}: replaces the {slot} mark of {day} taken at {prior.marked_at:%H:%M}Z"
                )
            else:
                # A replay clocked before the mark on record: the store kept
                # the later reading, so that is the one this run reports.
                m = prior
                result.notes.append(
                    f"{book}: the {slot} mark of {day} taken at {prior.marked_at:%H:%M}Z is "
                    "later than this run's clock and stays"
                )
        result.marks[book] = m
        result.problems += [f"{book}: {p}" for p in problems]
        if book == DECIDED:
            if prior is not None and m is not prior:
                result.notes += _withdraw_stale_stops(store, cfg, prior, m)
            result.stops = stop_checks(store, cfg, m, now)
        elif book == INDEX:
            try:
                res = index_rebalance(store, cfg, feed, fx, mark_row=m, now=now)
            except (OSError, ValueError) as e:  # a broken universe file must not cost the marks
                result.problems.append(f"index: rebalance not written: {e}")
            else:
                if res is not None:
                    result.index_targets = res.targets
                    result.index_summary = (
                        res.summary()
                        if res.targets
                        else "index rebalance due and not written: no member is priced and "
                        "fundable yet; the next mark tries again"
                    )
        elif book == CONTROL and rebalance_due(store, day, phase):
            quote = fx.asof(day)
            funds = fundables(feed, cfg, m.equity_usd, quote, day, settings, now=now)
            units = control_units(funds, m.equity_usd, phase, settings)
            held = {p.instrument_id: p.units for p in store.state(CONTROL).positions}
            rows = []
            for iid in sorted(set(units) | set(held)):
                u = units.get(iid, 0)
                f = next((x for x in funds if x.instrument_id == iid), None)
                w = (
                    (f.lot_usd / f.lot * u / m.equity_usd)
                    if f and f.lot and m.equity_usd > 0
                    else Decimal(0)
                )
                rows.append(
                    TargetRow(
                        CONTROL,
                        day,
                        now,
                        iid,
                        w.quantize(TENTH_BP),
                        "control_rebalance",
                        phase.name,
                        f"equal-lot control, {phase.name} phase",
                        target_units=u,
                    )
                )
            if rows:
                supersede_pending(
                    store, CONTROL, on=day, why=f"replaced by the control rebalance of {day}"
                )
                store.record_targets(rows)
            result.control_targets = rows
    return result


#: How long after a session's close the vendor is given to serve its bar before
#: a missing one is a problem rather than a wait. The Bursa catch-up runs 65
#: minutes after the close and has found the day's bar every time it ran.
BAR_GRACE = timedelta(minutes=30)


def _closed_session_without_a_bar(
    cfg, slot: str, day: date, session: date | None, now: datetime
) -> str:
    """Why the run cannot mark, or "" when it can or no closed session is owed a bar.

    Owed: a session of a market the slot marks, after the newest usable bar
    (`session`) and on or before `day`, that closed more than `BAR_GRACE` before
    `now`. Every such day is checked, not only `day`: the us_close run that
    lands at 01:00Z is dated the NEXT UTC day, and on 2026-10-08 that run found
    the 10-07 US rows with blank closes and quietly re-marked 10-06. A holiday
    is not a session, so it never alarms; a session still trading is not owed.
    """
    if session is None or session >= day:
        return ""
    for mic in slot_markets(cfg, slot):
        try:
            calendar = market_get(mic).calendar
        except (KeyError, ValueError):
            continue
        d = session + timedelta(days=1)
        while d <= day:
            owed = calendar.session(d)
            if owed is not None and owed.close_utc() + BAR_GRACE <= now:
                return (
                    f"{d} is a {mic} session that closed at {owed.close_utc():%Y-%m-%d %H:%M}Z "
                    f"and the cache holds no usable bar for it on any name the {slot} slot "
                    f"marks (the newest is {session}); nothing marked, so the {session} marks "
                    "stay as they were. A blank close from the vendor looks like this; the "
                    "next run retries"
                )
            d += timedelta(days=1)
    return ""


def _withdraw_stale_stops(
    store: PaperStore, cfg, replaced: MarkRow, mark_row: MarkRow
) -> list[str]:
    """Withdraw stops the replaced reading raised that the new reading does not breach.

    A stop is a fact about a close. When a later run of the same slot replaces
    a provisional reading - an intraday dip that touched the line - with a
    settled close above it, the stop raised by the dip would still sell the
    position at the next open. It is resolved `withdrawn` instead, and says why.
    """
    settings = settings_of(cfg, store)
    closes = {p["instrument_id"]: p for p in mark_row.positions if not p.get("unpriced")}
    raised_at = (
        replaced.marked_at if replaced.marked_at.tzinfo else replaced.marked_at.replace(tzinfo=UTC)
    )
    notes: list[str] = []
    for t in store.pending_targets(DECIDED):
        at = t.decided_at if t.decided_at.tzinfo else t.decided_at.replace(tzinfo=UTC)
        if t.reason != "stop" or t.decided_on != replaced.day or at != raised_at:
            continue
        p = closes.get(t.instrument_id)
        if p is None:
            continue
        close, avg = dec(p["close"]), dec(p["avg_cost"])
        line = avg * (Decimal(1) - settings.stop_loss)
        if close > line:
            assert t.target_id is not None
            why = (
                f"re-marked: the {mark_row.slot} reading of {mark_row.day} that raised it was "
                f"replaced, and the close {close} is above the stop line {line:.4f}"
            )
            store.resolve(t.target_id, mark_row.day, "withdrawn", why)
            notes.append(f"{t.instrument_id}: stop withdrawn - {why}")
    return notes


def restamp_marks(store: PaperStore, feed, cfg) -> list[str]:
    """Move marks stamped with a day their market never traded onto the session of their bars.

    Before the session-day rule a catch-up run on a Saturday wrote a Saturday
    mark: sixteen such rows sat in data/paper.db, and Friday 2026-09-11 had
    no us_close mark while the 06:07Z run that priced its close was filed
    under the 12th. Each mark on a non-session day is re-dated to the last
    cached bar for its slot's market on or before the day it carries - the
    bars it was marked from - and where that session already has a mark the
    later reading stays. A mark whose bars are not in the cache is left where
    it is, and the run says so. Cheap on every run: one scan of the marks,
    and nothing to do once the ledger carries no such day.

    The second shape of the same fault is a mark on a session day that had not
    OPENED when the mark was taken. On 2026-09-22 Monday's 21:15Z us_close
    collector arrived at 00:09Z Tuesday and a build that still stamped the
    wall clock filed a mark under Tuesday holding Monday's closes - thirteen
    hours before Tuesday's US session opened. Such a mark cannot carry the
    bars of the day it names, provisional or settled, so it is re-dated to
    the last cached bar before that day, under the same later-reading-stays
    rule. The test is the open, not the close, on purpose: a mark taken
    during the session from a provisional bar is that day's mark, and a later
    run of the same slot replaces it.
    """
    moves: list[tuple[int, date]] = []
    notes: list[str] = []
    because: dict[int, str] = {}
    for m in store.all_marks():
        if slot_is_session(cfg, m.slot, m.day):
            opened = session_open(cfg, m.slot, m.day)
            taken = m.marked_at if m.marked_at.tzinfo else m.marked_at.replace(tzinfo=UTC)
            if opened is None or taken >= opened:
                continue
            session = bars_session(feed, cfg, m.slot, m.day - timedelta(days=1))
            if session is None:
                notes.append(
                    f"{m.book}: the {m.day} {m.slot} mark was taken at {taken:%Y-%m-%d %H:%M}Z, "
                    "before that session opened, and the cache holds no earlier bar to say "
                    "which one it priced; left as it is"
                )
                continue
            if m.mark_id is not None:
                moves.append((m.mark_id, session))
                because[m.mark_id] = (
                    f", taken at {taken:%Y-%m-%d %H:%M}Z before that session opened,"
                )
            continue
        session = bars_session(feed, cfg, m.slot, m.day)
        if session is None:
            notes.append(
                f"{m.book}: the {m.day} {m.slot} mark is on no session and the cache holds no "
                "bar on or before it to say which one it priced; left as it is"
            )
            continue
        if session != m.day and m.mark_id is not None:
            moves.append((m.mark_id, session))
    for m, new_day, outcome in store.restamp_marks(moves):
        what = {
            "moved": f"re-dated to its session {new_day}",
            "replaced": f"re-dated to its session {new_day}, replacing that session's earlier mark",
            "dropped": f"dropped: a later mark of its session {new_day} already exists",
        }[outcome]
        notes.append(
            f"{m.book}: the {m.day} {m.slot} mark{because.get(m.mark_id or -1, '')} {what}"
        )
    return notes


def _unused(_: Fundable | BookState | Refusal) -> None:  # keeps the imports honest for pyright
    return None
