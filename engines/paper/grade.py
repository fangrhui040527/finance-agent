"""Grade the book's predictions against the control, at their horizon.

Every raised or exited weight was logged as a prediction when it was decided
(`agents/learning/store.py`, agent `paper`). This grades the ones whose date
has come: realised is the name's USD return from the price the book actually
got (or the decision-day close, in the observe weeks when nothing was
applied), benchmark is the control book's return over the same days. The
queue refuses anything before its date; that refusal is kept, not caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from core.market.feed import PriceFeedError
from engines.paper.fx import UsdMyr
from engines.paper.pricing import currency_of, last_close
from engines.paper.store import CASH, CONTROL, DECIDED, PaperStore


@dataclass(frozen=True)
class Graded:
    prediction_id: str
    instrument_id: str
    realised: float
    benchmark: float
    correct: bool
    note: str


def grade_due(
    store: PaperStore, cfg, feed, fx: UsdMyr, *, day: date, learning, dry_run: bool = False
) -> list[Graded]:
    out: list[Graded] = []
    due = [p for p in learning.pending() if p.agent == "paper" and p.grade_on <= day]
    if not due:
        return out
    queue = learning.load_queue()
    for p in due:
        iid = p.instrument_id
        t = store.target_by_prediction(p.prediction_id)
        if iid == CASH:
            graded = _grade_all_cash(store, p, day=day, learning=learning, dry_run=dry_run)
            if graded is not None:
                out.append(graded)
            continue
        ccy = currency_of(iid)
        change = store.change_for_target(t.target_id) if t and t.target_id is not None else None
        notes: list[str] = []
        try:
            if change is not None:
                ref_usd = (
                    change.price_local / change.fx_rate if ccy == "MYR" else change.price_local
                )
                ref_day = change.day
            else:
                decided = t.decided_on if t else p.made_at.date()
                close, ref_day = last_close(feed, iid, decided)
                q = fx.asof(decided)
                ref_usd = close / q.rate if ccy == "MYR" else close
                notes.append("not applied; graded from the decision-day close")
            close_now, _ = last_close(feed, iid, day)
            q_now = fx.asof(day)
            now_usd = close_now / q_now.rate if ccy == "MYR" else close_now
        except PriceFeedError as e:
            notes.append(f"cannot grade: {str(e).splitlines()[0]}")
            continue
        realised = float(now_usd / ref_usd - 1)
        m_ref = store.latest_mark(CONTROL, on_or_before=ref_day)
        m_now = store.latest_mark(CONTROL, on_or_before=day)
        if m_ref and m_now and m_ref.equity_usd > 0:
            benchmark = float(m_now.equity_usd / m_ref.equity_usd - 1)
        else:
            benchmark = 0.0
            notes.append("control book unmarked over the window; benchmark 0")
        stopped = [
            c
            for c in store.changes("decided", start=ref_day, end=day)
            if c.instrument_id == iid and c.action == "exit"
        ]
        if stopped and change is not None:
            notes.append(
                f"position exited on {stopped[-1].day}; graded on the name to {day} regardless"
            )
        o = queue.grade(p.prediction_id, day, realised, benchmark, "; ".join(notes))
        if not dry_run:
            learning.record_outcome(o)
        out.append(Graded(p.prediction_id, iid, realised, benchmark, o.correct, o.note))
    return out


def _grade_all_cash(store: PaperStore, p, *, day: date, learning, dry_run: bool) -> Graded | None:
    """An all-cash night, settled by the two books rather than by a price.

    There is no instrument to price - that is the whole content of the claim -
    so realised is the deciding book's own return over the window and benchmark
    is the control's. Holding nothing was right exactly when the control lost
    ground over the same days.
    """
    decided = p.context.get("decided_on")
    ref_day = date.fromisoformat(decided) if decided else p.made_at.date()
    notes: list[str] = ["all-cash; graded on the book against the control, not on a price"]
    windows: dict[str, tuple[Decimal, Decimal]] = {}
    for book in (DECIDED, CONTROL):
        m_ref = store.latest_mark(book, on_or_before=ref_day)
        m_now = store.latest_mark(book, on_or_before=day)
        if m_ref is None or m_now is None or m_ref.equity_usd <= 0:
            # Unmarked is not zero: leave it pending rather than score a window
            # the book cannot see.
            return None
        windows[book] = (m_ref.equity_usd, m_now.equity_usd)
    realised = float(windows[DECIDED][1] / windows[DECIDED][0] - 1)
    benchmark = float(windows[CONTROL][1] / windows[CONTROL][0] - 1)
    o = learning.load_queue().grade(p.prediction_id, day, realised, benchmark, "; ".join(notes))
    if not dry_run:
        learning.record_outcome(o)
    return Graded(p.prediction_id, CASH, realised, benchmark, o.correct, o.note)


def realised_vs_control(store: PaperStore, since: date | None = None) -> dict[str, Decimal]:
    """Both books' return from their first mark (or `since`) to their last."""
    out: dict[str, Decimal] = {}
    for book in ("decided", CONTROL):
        marks = store.marks(book, since=since)
        if len(marks) >= 2 and marks[0].equity_usd > 0:
            out[book] = (marks[-1].equity_usd / marks[0].equity_usd - 1).quantize(Decimal("0.0001"))
    return out
