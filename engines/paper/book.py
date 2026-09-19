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


def grade_date(made: datetime, horizon_days: int) -> date:
    """Sessions -> calendar days, the same arithmetic predict.py uses."""
    return (made + timedelta(days=horizon_days / 5 * 7)).date()


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
        grade_on = grade_date(nominal, horizon)
        replayed = grade_on <= now.date()
        made = nominal if replayed else now
        for i, t in enumerate(rows):
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
) -> PositionChange:
    iid = t.instrument_id
    ccy = currency_of(iid)
    mic = mic_of(iid)
    state = store.state(book)
    pos = state.position(iid)
    held = pos.units if pos else 0
    avg = pos.avg_cost if pos else Decimal(0)
    units = min(units, held)
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
    for t in store.pending_targets(book):
        assert t.target_id is not None
        if t.phase in (OBSERVE, PRE) and t.reason == "decision":
            if up_to > t.decided_on:
                store.resolve(
                    t.target_id, up_to, "observed", "observe phase: logged, never applied"
                )
                out.append(Applied(t, "observed"))
            continue
        bar = first_bar_after(feed, t.instrument_id, t.decided_on, up_to)
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


# -- mark ------------------------------------------------------------------------------------


def mark_book(
    store: PaperStore, cfg, feed, fx: UsdMyr, *, book: str, day: date, slot: str, now: datetime
) -> tuple[MarkRow, list[str]]:
    settings = settings_of(cfg, store)
    state = store.state(book)
    quote = fx.asof(day)
    prev = store.latest_mark(book)
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
    peak = max(prev.peak_usd if prev else store.initial_cash(book), equity)
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
        for p in self.problems:
            lines.append(f"  PROBLEM: {p}")
        return "\n".join(lines)


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
    if session != day:
        result.notes.append(
            f"marked as {session}: the session of the last cached bar on or before {day} "
            f"for the market(s) the {slot} slot marks"
        )
        day = result.day = session
    phase = phase_for(day, settings)
    for book in (DECIDED, CONTROL):
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
            result.stops = stop_checks(store, cfg, m, now)
        elif rebalance_due(store, day, phase):
            quote = fx.asof(day)
            funds = fundables(feed, cfg, m.equity_usd, quote, day, settings)
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
                store.record_targets(rows)
            result.control_targets = rows
    return result


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
    """
    moves: list[tuple[int, date]] = []
    notes: list[str] = []
    for m in store.all_marks():
        if slot_is_session(cfg, m.slot, m.day):
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
        notes.append(f"{m.book}: the {m.day} {m.slot} mark {what}")
    return notes


def _unused(_: Fundable | BookState | Refusal) -> None:  # keeps the imports honest for pyright
    return None
