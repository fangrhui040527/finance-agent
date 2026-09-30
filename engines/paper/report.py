"""The status page: one reading of the book for the CLI, the MCP tool and the pack.

Everything the decider needs before recording a target book is on this page,
in this order: where the book stands, what it holds, what is pending, which
caps have headroom, which names are fundable at today's equity, and what the
day's decision will cost. A page that omitted the fundable table would invite
a target no lot can fill.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from engines.backtest.metrics import drawdown_profile
from engines.paper.book import current_weights, settings_of, turnover_used
from engines.paper.fx import UsdMyr
from engines.paper.rules import Fundable, Phase, fundables, min_names, names_rule, phase_for
from engines.paper.store import CONTROL, DECIDED, INDEX, MarkRow, PaperStore, TargetRow


@dataclass
class CapRow:
    cap: str
    value: str
    limit: str
    breached: bool


@dataclass
class IndexLine:
    """The index book in one reading, and the other two books over its window.

    Its window, because it opened later than they did: a return "to date" for
    each book would compare different stretches of the market. `since` is the
    index book's opening day, and every return here runs from the last mark on
    or before it.
    """

    opened_on: date
    notional_usd: Decimal
    universe: str
    members: int
    marked_on: date | None
    equity_usd: Decimal
    cash_usd: Decimal
    names_held: int
    pending: int
    cost_pct: Decimal
    index_return: Decimal | None
    decided_return: Decimal | None
    control_return: Decimal | None

    def as_json(self) -> dict:
        def f(x: Decimal | None) -> float | None:
            return float(x) if x is not None else None

        return {
            "opened_on": self.opened_on.isoformat(),
            "notional_usd": float(self.notional_usd),
            "universe": self.universe,
            "members": self.members,
            "marked_on": self.marked_on.isoformat() if self.marked_on else None,
            "equity_usd": float(self.equity_usd),
            "cash_usd": float(self.cash_usd),
            "names_held": self.names_held,
            "pending": self.pending,
            "cost_pct_of_notional": float(self.cost_pct),
            "since": self.opened_on.isoformat(),
            "index_return": f(self.index_return),
            "decided_return": f(self.decided_return),
            "control_return": f(self.control_return),
        }

    def render(self) -> list[str]:
        L = [
            f"  index book: {self.universe}, equal weight, opened {self.opened_on} at notional "
            f"USD {self.notional_usd:,.0f} - a scale for percentages, not money"
        ]
        if self.marked_on is None:
            L.append("    not yet marked")
            return L
        L.append(
            f"    equity USD {self.equity_usd:,.2f}  cash {self.cash_usd:,.2f}  "
            f"{self.names_held} of {self.members} names held  {self.pending} pending  "
            f"cost to date {self.cost_pct:.2%} of notional"
        )

        def pct(x: Decimal | None) -> str:
            return "-" if x is None else f"{x:+.2%}"

        L.append(
            f"    since {self.opened_on}: index {pct(self.index_return)}  decided "
            f"{pct(self.decided_return)}  control {pct(self.control_return)}"
        )
        return L


def _since(store: PaperStore, book: str, start: date, day: date) -> Decimal | None:
    """A book's return from its last mark on or before `start` to its last on or before `day`."""
    now = store.latest_mark(book, on_or_before=day)
    base = store.latest_mark(book, on_or_before=start)
    base_equity = base.equity_usd if base else store.initial_cash(book)
    if now is None or base_equity <= 0:
        return None
    return now.equity_usd / base_equity - 1


def index_line(store: PaperStore, day: date) -> IndexLine | None:
    if not store.has_book(INDEX):
        return None
    opened = store.book_opened_on(INDEX)
    assert opened is not None
    terms = store.book_terms(INDEX)
    notional = store.initial_cash(INDEX)
    m = store.latest_mark(INDEX, on_or_before=day)
    cost = store.cost_to_date(INDEX)
    name = str(terms.get("universe") or "index")
    label = Path(name).stem.upper() if name else "INDEX"
    return IndexLine(
        opened_on=opened,
        notional_usd=notional,
        universe=label,
        members=int(terms.get("universe_names") or 0),
        marked_on=m.day if m else None,
        equity_usd=m.equity_usd if m else notional,
        cash_usd=m.cash_usd if m else notional,
        names_held=len(m.positions) if m else 0,
        pending=len(store.pending_targets(INDEX)),
        cost_pct=cost.pct_of_initial,
        index_return=(m.equity_usd / notional - 1) if (m and notional > 0) else None,
        decided_return=_since(store, DECIDED, opened, day) if m else None,
        control_return=_since(store, CONTROL, opened, day) if m else None,
    )


@dataclass
class Status:
    day: date
    phase: Phase
    started: date | None
    equity_usd: Decimal
    cash_usd: Decimal
    peak_usd: Decimal
    drawdown: Decimal
    halted: bool
    control_equity_usd: Decimal | None
    initial_cash_usd: Decimal
    marked_on: date | None
    marks_count: int
    positions: list[dict]
    pending: list[TargetRow]
    caps: list[CapRow]
    fundable: list[Fundable]
    turnover_used_usd: Decimal
    turnover_cap_usd: Decimal
    fx_rate: Decimal
    fx_date: date
    fx_source: str
    cost: dict[str, str]
    max_drawdown: Decimal
    return_to_date: Decimal | None
    control_return_to_date: Decimal | None
    notes: list[str] = field(default_factory=list)
    #: The index book (engines/paper/index.py), or None while it is not open.
    index: IndexLine | None = None

    def as_json(self) -> dict:
        d = {
            "day": self.day.isoformat(),
            "phase": self.phase.name,
            "week": self.phase.week,
            "phase_note": self.phase.describe(),
            "started": self.started.isoformat() if self.started else None,
            "equity_usd": float(self.equity_usd),
            "cash_usd": float(self.cash_usd),
            "peak_usd": float(self.peak_usd),
            "drawdown": float(self.drawdown),
            "max_drawdown": float(self.max_drawdown),
            "halted": self.halted,
            "control_equity_usd": float(self.control_equity_usd)
            if self.control_equity_usd is not None
            else None,
            "initial_cash_usd": float(self.initial_cash_usd),
            "return_to_date": float(self.return_to_date)
            if self.return_to_date is not None
            else None,
            "control_return_to_date": (
                float(self.control_return_to_date)
                if self.control_return_to_date is not None
                else None
            ),
            "marked_on": self.marked_on.isoformat() if self.marked_on else None,
            "marks": self.marks_count,
            "positions": [
                {**p, "value_usd": float(p["value_usd"]), "weight": float(p.get("weight", 0))}
                for p in self.positions
            ],
            "pending": [
                {
                    "instrument_id": t.instrument_id,
                    "weight": float(t.weight),
                    "reason": t.reason,
                    "decided_on": t.decided_on.isoformat(),
                }
                for t in self.pending
            ],
            "caps": [asdict(c) for c in self.caps],
            "fundable": [
                {
                    "instrument_id": f.instrument_id,
                    "lot": f.lot,
                    "lot_usd": float(f.lot_usd),
                    "lot_weight": float(f.lot_weight),
                    "max_lots": f.max_lots,
                    "round_trip": float(f.round_trip),
                    "fundable": f.fundable,
                    "price_local": float(f.price_local),
                    "price_day": f.close_day.isoformat() if f.close_day else None,
                    "price_state": f.price_state,
                    "newer_session": f.newer_session.isoformat() if f.newer_session else None,
                    "error": f.error,
                }
                for f in self.fundable
            ],
            "turnover_used_usd": float(self.turnover_used_usd),
            "turnover_cap_usd": float(self.turnover_cap_usd),
            "fx": {
                "rate": float(self.fx_rate),
                "date": self.fx_date.isoformat(),
                "source": self.fx_source,
            },
            "cost": self.cost,
            "notes": self.notes,
            "index": self.index.as_json() if self.index else None,
        }
        return d

    def _price_basis(self) -> str:
        """What the fundable table's prices actually are.

        The old header said "the last close" unconditionally, over a table whose
        newest row may have been pulled mid-session. Say which, or say nothing:
        a heading that is right on most nights and silently wrong on the rest is
        worse than one that names the mixture.
        """
        priced = [f for f in self.fundable if not f.error]
        if priced and all(f.provisional for f in priced):
            return "the session so far - no price here is a close"
        if any(f.provisional or f.newer_session for f in priced):
            return "the last close, except where marked"
        return "the last close"

    def render(self) -> str:
        L: list[str] = []
        L.append(f"PAPER BOOK  {self.day}  {self.phase.describe()}")
        if self.started:
            L.append(
                f"  opened {self.started} with USD {self.initial_cash_usd:,.2f}; {self.marks_count} marks"
            )
        if self.marked_on is None:
            L.append("  not yet marked: run `ask.py paper mark`")
        else:
            flag = "  HALTED: no new entries" if self.halted else ""
            L.append(
                f"  equity USD {self.equity_usd:,.2f}  cash {self.cash_usd:,.2f}  peak {self.peak_usd:,.2f}"
                f"  drawdown {self.drawdown:.2%} (max {self.max_drawdown:.2%}){flag}"
            )
            if self.return_to_date is not None:
                ctl = (
                    f"  control {self.control_return_to_date:+.2%}"
                    if self.control_return_to_date is not None
                    else ""
                )
                L.append(f"  return to date {self.return_to_date:+.2%}{ctl}")
            if self.control_equity_usd is not None:
                L.append(f"  control book equity USD {self.control_equity_usd:,.2f}")
        if self.index is not None:
            L += self.index.render()
        L.append("")
        L.append("  positions")
        if not self.positions:
            L.append("    none (cash)")
        for p in self.positions:
            stale = "  STALE" if p.get("stale") else ""
            L.append(
                f"    {p['instrument_id']:<10} {p['units']:>6} @ {Decimal(p['avg_cost']):.4f} {p['currency']}"
                f"  close {Decimal(p['close']):.4f} ({p['close_day']})  USD {Decimal(p['value_usd']):>9,.2f}"
                f"  {Decimal(p.get('weight', '0')):.1%}  open P&L {Decimal(p['pnl_open_usd']):+,.2f}{stale}"
            )
        L.append("")
        L.append("  pending targets")
        if not self.pending:
            L.append("    none")
        for t in self.pending:
            L.append(
                f"    {t.instrument_id:<10} {t.weight:>7.2%}  {t.reason}  decided {t.decided_on}"
            )
        L.append("")
        L.append("  caps (value / limit)")
        for c in self.caps:
            L.append(
                f"    {c.cap:<28} {c.value:>12} / {c.limit:<12} {'BREACHED' if c.breached else 'ok'}"
            )
        L.append(
            f"    turnover used, 5 weekdays   USD {self.turnover_used_usd:>9,.2f} / {self.turnover_cap_usd:,.2f}"
        )
        L.append("")
        L.append(f"  fundable at this equity (one lot at {self._price_basis()})")
        for f in self.fundable:
            L.append(f.row())
        provisional = [f.instrument_id for f in self.fundable if f.provisional]
        if provisional:
            L.append(
                f"  {len(provisional)} price(s) above are the session so far, not a close - "
                f"{', '.join(provisional)} were pulled while their market was still trading"
            )
        behind = [f.instrument_id for f in self.fundable if f.newer_session]
        if behind:
            L.append(
                f"  {len(behind)} price(s) above are an older session's close - "
                f"{', '.join(behind)}: the cache holds no bar for the session their market "
                f"has closed since"
            )
        L.append("")
        L.append(
            f"  fx MYR per USD {self.fx_rate} ({self.fx_source}, {self.fx_date}); marks use the mid"
        )
        L.append(
            f"  cost to date USD {self.cost['total']} = fees {self.cost['fees']} + fx spread "
            f"{self.cost['fx_spread']} + slippage {self.cost['slippage']} ({self.cost['pct_of_initial']} of opening cash)"
        )
        for n in self.notes:
            L.append(f"  note: {n}")
        L.append(
            "  a decision recorded today applies at each market's first cached bar after today"
        )
        return "\n".join(L)


def status(store: PaperStore, cfg, feed, fx: UsdMyr, *, day: date | None = None) -> Status:
    settings = settings_of(cfg, store)
    day = day or datetime.now(UTC).date()
    phase = phase_for(day, settings)
    m = store.latest_mark(DECIDED, on_or_before=day)
    mc = store.latest_mark(CONTROL, on_or_before=day)
    initial = store.initial_cash(DECIDED)
    equity = m.equity_usd if m else initial
    quote = fx.asof(day)
    funds = fundables(feed, cfg, equity, quote, day, settings)
    weights = current_weights(m)
    invested = sum(weights.values(), Decimal(0))
    used = turnover_used(store, DECIDED, day)
    cash = m.cash_usd if m else initial
    notes: list[str] = []

    caps = [
        CapRow(
            "largest name",
            f"{max(weights.values()):.2%}" if weights else "0.00%",
            f"{settings.max_weight_per_name:.0%}",
            bool(weights) and max(weights.values()) > settings.max_weight_per_name,
        ),
        CapRow(
            "invested",
            f"{invested:.2%}",
            f"{phase.max_invested:.0%}",
            invested > phase.max_invested + Decimal("0.0001"),
        ),
        CapRow(
            "cash floor",
            f"{(cash / equity) if equity > 0 else Decimal(1):.2%}",
            f"{settings.cash_floor:.0%}",
            equity > 0 and cash / equity < settings.cash_floor - Decimal("0.0001"),
        ),
        CapRow(
            "names held",
            str(len(weights)),
            f">= {min_names(settings, invested)}" if invested > 0 else "0",
            0 < len(weights) < min_names(settings, invested),
        ),
        CapRow(
            "drawdown from peak",
            f"{(m.drawdown if m else Decimal(0)):.2%}",
            f"{settings.drawdown_halt:.0%} halts entries",
            bool(m and m.halted),
        ),
    ]
    if invested > 0:
        notes.append(f"names rule in force: {names_rule(settings, invested)}")
    marks = store.marks(DECIDED)
    rets = [
        float(b.equity_usd / a.equity_usd - 1) for a, b in zip(marks, marks[1:]) if a.equity_usd > 0
    ]
    max_dd = Decimal(str(round(drawdown_profile(rets)[0], 6))) if rets else Decimal(0)
    cost = store.cost_to_date(DECIDED)
    return Status(
        day=day,
        phase=phase,
        started=store.opened_on(),
        equity_usd=equity,
        cash_usd=cash,
        peak_usd=m.peak_usd if m else initial,
        drawdown=m.drawdown if m else Decimal(0),
        halted=bool(m and m.halted),
        control_equity_usd=mc.equity_usd if mc else None,
        initial_cash_usd=initial,
        marked_on=m.day if m else None,
        marks_count=len(marks),
        positions=list(m.positions) if m else [],
        pending=store.pending_targets(DECIDED),
        caps=caps,
        fundable=funds,
        turnover_used_usd=used,
        turnover_cap_usd=settings.weekly_turnover_cap * equity,
        fx_rate=quote.rate,
        fx_date=quote.rate_date,
        fx_source=quote.source,
        cost={
            "fees": f"{cost.fees_usd:.2f}",
            "fx_spread": f"{cost.fx_spread_usd:.2f}",
            "slippage": f"{cost.slippage_usd:.2f}",
            "total": f"{cost.total:.2f}",
            "pct_of_initial": f"{cost.pct_of_initial:.2%}",
        },
        max_drawdown=max_dd,
        return_to_date=(equity / initial - 1) if (m and initial > 0) else None,
        control_return_to_date=(mc.equity_usd / initial - 1) if (mc and initial > 0) else None,
        notes=notes,
        index=index_line(store, day),
    )


def status_json(s: Status) -> str:
    return json.dumps(s.as_json(), indent=2, default=str)


def _unused(_: MarkRow) -> None:
    return None
