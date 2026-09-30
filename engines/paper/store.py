"""The paper ledger: append-only SQLite, replayed rather than updated.

Five tables, ten triggers. Nothing is ever UPDATEd or DELETEd: a target is
written once and *resolved* once by a row in `applications`; a position change
is a fact about a bar; a mark is a fact about a session. The book's state at
any moment is replayed from the initial cash and every position change since,
which is what makes the record auditable to the cent - and what makes a
"reset" impossible short of a new file.

Marks carry the one qualification. A slot marks a book at most once per
session day, and a later mark of the same session replaces the earlier one,
because a later fetch is closer to the close: the 21:15 UTC run and the
catch-up dispatched after it are two readings of one close, and the record
wants the better one, not both. That replacement is the only UPDATE the
ledger's own guard lets through, and it may change nothing but the reading.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import _enable_wal, apply_schema
from engines.paper.settings import PaperSettings

DECIDED = "decided"
CONTROL = "control"
#: The benchmark: a passive, equal-weight basket of a whole market index
#: (engines/paper/index.py). Opened on its own day with its own notional, never
#: by `init_books`, so a ledger that has not asked for it carries two books.
INDEX = "index"

#: The instrument id of an all-cash night. Not a tradable symbol and never
#: routed to a market: it is the subject of the one row that says a decision to
#: hold nothing was taken. `mic_of` would reject it, which is why every path
#: that prices a target checks the reason before the id.
CASH = "CASH"

#: `reason` on that row. Recorded, then immediately resolved - there is no
#: position to apply, so it must never sit in the pending queue.
ALL_CASH = "all_cash"
#: The books `init_books` opens. The index is opened separately, by `open_book`.
BOOKS = (DECIDED, CONTROL)
#: Every book a mark may carry, in the order a page lists them.
ALL_BOOKS = (DECIDED, CONTROL, INDEX)

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book             TEXT PRIMARY KEY,
    opened_on        TEXT NOT NULL,
    initial_cash_usd TEXT NOT NULL,
    base_currency    TEXT NOT NULL DEFAULT 'USD',
    caps_json        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS targets (
    target_id     INTEGER PRIMARY KEY,
    book          TEXT NOT NULL,
    decided_on    TEXT NOT NULL,
    decided_at    TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    weight        TEXT NOT NULL,
    target_units  INTEGER,
    reason        TEXT NOT NULL,
    phase         TEXT NOT NULL,
    thesis        TEXT NOT NULL DEFAULT '',
    prediction_id TEXT
);
CREATE INDEX IF NOT EXISTS targets_book_day ON targets(book, decided_on);
CREATE TABLE IF NOT EXISTS applications (
    target_id   INTEGER PRIMARY KEY REFERENCES targets(target_id),
    resolved_on TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS position_changes (
    change_id           INTEGER PRIMARY KEY,
    book                TEXT NOT NULL,
    target_id           INTEGER REFERENCES targets(target_id),
    day                 TEXT NOT NULL,
    instrument_id       TEXT NOT NULL,
    currency            TEXT NOT NULL,
    action              TEXT NOT NULL,
    units_delta         INTEGER NOT NULL,
    units_after         INTEGER NOT NULL,
    bar_open            TEXT NOT NULL,
    slippage_bps        INTEGER NOT NULL,
    price_local         TEXT NOT NULL,
    consideration_local TEXT NOT NULL,
    consideration_usd   TEXT NOT NULL,
    fee_local           TEXT NOT NULL,
    fee_usd             TEXT NOT NULL,
    fx_rate             TEXT NOT NULL,
    fx_date             TEXT NOT NULL,
    fx_source           TEXT NOT NULL,
    fx_spread_usd       TEXT NOT NULL,
    slippage_usd        TEXT NOT NULL,
    cash_delta_usd      TEXT NOT NULL,
    avg_cost_after      TEXT NOT NULL,
    realised_pnl_usd    TEXT NOT NULL DEFAULT '0'
);
CREATE INDEX IF NOT EXISTS changes_book_day ON position_changes(book, day);
CREATE TABLE IF NOT EXISTS marks (
    mark_id        INTEGER PRIMARY KEY,
    book           TEXT NOT NULL,
    day            TEXT NOT NULL,
    slot           TEXT NOT NULL,
    marked_at      TEXT NOT NULL,
    cash_usd       TEXT NOT NULL,
    positions_usd  TEXT NOT NULL,
    equity_usd     TEXT NOT NULL,
    peak_usd       TEXT NOT NULL,
    drawdown       TEXT NOT NULL,
    halted         INTEGER NOT NULL,
    phase          TEXT NOT NULL,
    fx_rate        TEXT NOT NULL,
    fx_date        TEXT NOT NULL,
    fx_source      TEXT NOT NULL,
    positions_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS marks_book_day ON marks(book, day);
"""

#: One mark per (book, session day, slot). Created by `_migrate_marks` rather
#: than in SCHEMA: a ledger written before the rule can hold two marks for one
#: session, and the index has to follow the de-duplication, not precede it.
#: Its presence is also the marker that the migration has run.
MARKS_UNIQUE_INDEX = "marks_book_day_slot"
MARKS_UNIQUE_SQL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {MARKS_UNIQUE_INDEX} ON marks(book, day, slot);"
)

MARK_GUARD = (
    "a mark is a fact about a session day: only a later mark of the same session replaces it"
)

_GUARDS = {
    "books": (
        "a paper book is opened once; there is no reset, only a new file",
        "the paper record is append-only",
    ),
    "targets": (
        "a target is written once: edit it and the record becomes a memory",
        "targets are never deleted: a record missing its refused ideas proves nothing",
    ),
    "applications": ("a target is resolved once", "a resolution is never deleted"),
    "position_changes": (
        "a position change you can edit proves nothing about what the book did",
        "position changes are never deleted: a book missing its losers is a story",
    ),
    "marks": (MARK_GUARD, "marks are never deleted"),
}

#: The UPDATE a table's guard lets through. Only marks have one: the upsert
#: that re-marks a session may change every reading on the row and nothing
#: about which session it is - not the id, the book, the day or the slot -
#: and it may never put an earlier reading over a later one.
_UPDATE_ALLOWED_UNLESS = {
    "marks": (
        "NEW.mark_id IS NOT OLD.mark_id OR NEW.book IS NOT OLD.book "
        "OR NEW.day IS NOT OLD.day OR NEW.slot IS NOT OLD.slot "
        "OR NEW.marked_at < OLD.marked_at"
    ),
}


def _trigger_statements(table: str, on_update: str, on_delete: str) -> tuple[str, str]:
    when = _UPDATE_ALLOWED_UNLESS.get(table)
    guard = f"WHEN {when}\n" if when else ""
    return (
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}\n"
        f"{guard}BEGIN SELECT RAISE(ABORT, '{on_update}'); END",
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}\n"
        f"BEGIN SELECT RAISE(ABORT, '{on_delete}'); END",
    )


def _trigger_sql(table: str, on_update: str, on_delete: str) -> str:
    return ";\n".join(_trigger_statements(table, on_update, on_delete)) + ";\n"


def _guards_sql() -> list[str]:
    return [_trigger_sql(table, upd, dele) for table, (upd, dele) in _GUARDS.items()]


#: Rows that share (book, day, slot) with a later mark - later by `marked_at`,
#: then by id when two runs stamped the same instant. Text comparison is safe:
#: every `marked_at` is written by `_iso`, UTC and one layout.
_DUPLICATE_MARKS = (
    "SELECT m.mark_id FROM marks m JOIN marks o "
    "ON o.book = m.book AND o.day = m.day AND o.slot = m.slot "
    "WHERE o.marked_at > m.marked_at OR (o.marked_at = m.marked_at AND o.mark_id > m.mark_id)"
)


def _d(text: str | None) -> Decimal:
    return Decimal(text) if text not in (None, "") else Decimal(0)


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


# -- rows -------------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetRow:
    book: str
    decided_on: date
    decided_at: datetime
    instrument_id: str
    weight: Decimal
    reason: str  # decision | stop | control_rebalance | all_cash
    phase: str
    thesis: str = ""
    prediction_id: str | None = None
    target_units: int | None = None
    target_id: int | None = None


@dataclass(frozen=True)
class Application:
    target_id: int
    resolved_on: date
    status: str  # applied | observed | no_change | skipped | superseded | expired
    detail: str = ""


@dataclass(frozen=True)
class PositionChange:
    book: str
    target_id: int | None
    day: date
    instrument_id: str
    currency: str
    action: str  # open | add | trim | exit
    units_delta: int
    units_after: int
    bar_open: Decimal
    slippage_bps: int
    price_local: Decimal
    consideration_local: Decimal
    consideration_usd: Decimal
    fee_local: Decimal
    fee_usd: Decimal
    fx_rate: Decimal
    fx_date: date
    fx_source: str
    fx_spread_usd: Decimal
    slippage_usd: Decimal
    cash_delta_usd: Decimal
    avg_cost_after: Decimal
    realised_pnl_usd: Decimal = Decimal(0)
    change_id: int | None = None


@dataclass(frozen=True)
class PaperPosition:
    """The shape of core/broker/account.Position, copied rather than imported:
    the paper module may not depend on anything that can see a broker."""

    instrument_id: str
    units: int
    avg_cost: Decimal
    currency: str
    last_close: Decimal | None = None
    last_close_day: date | None = None
    value_usd: Decimal = Decimal(0)
    stale: bool = False


@dataclass(frozen=True)
class BookState:
    book: str
    cash_usd: Decimal
    positions: tuple[PaperPosition, ...]
    realised_pnl_usd: Decimal
    peak_usd: Decimal

    def position(self, instrument_id: str) -> PaperPosition | None:
        for p in self.positions:
            if p.instrument_id == instrument_id:
                return p
        return None


@dataclass(frozen=True)
class MarkRow:
    book: str
    day: date
    slot: str
    marked_at: datetime
    cash_usd: Decimal
    positions_usd: Decimal
    equity_usd: Decimal
    peak_usd: Decimal
    drawdown: Decimal
    halted: bool
    phase: str
    fx_rate: Decimal
    fx_date: date
    fx_source: str
    positions: list[dict] = field(default_factory=list)
    mark_id: int | None = None


@dataclass(frozen=True)
class CostDrag:
    fees_usd: Decimal
    fx_spread_usd: Decimal
    slippage_usd: Decimal
    initial_cash_usd: Decimal

    @property
    def total(self) -> Decimal:
        return self.fees_usd + self.fx_spread_usd + self.slippage_usd

    @property
    def pct_of_initial(self) -> Decimal:
        if self.initial_cash_usd <= 0:
            return Decimal(0)
        return self.total / self.initial_cash_usd


# -- the store ---------------------------------------------------------------------------


class PaperStore:
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str | Path = "data/paper.db") -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=self.BUSY_TIMEOUT_MS / 1000)
        self.conn.row_factory = sqlite3.Row
        _enable_wal(self.conn, path, self.BUSY_TIMEOUT_MS)
        apply_schema(self.conn, SCHEMA, *_guards_sql(), timeout_ms=self.BUSY_TIMEOUT_MS)
        #: Duplicate marks this open removed from a ledger written before the
        #: one-mark-per-session rule; zero on every open after the first.
        self.marks_deduplicated = self._migrate_marks()
        self.conn.commit()

    def _migrate_marks(self) -> int:
        """One mark per (book, session day, slot), on a ledger written before the rule.

        Manual catch-up dispatches marked the same slot twice on one day and
        the schema let them: sixteen (book, day, slot) groups in data/paper.db
        carried two marks. The later one is kept - a later fetch is closer to
        the close - and the earlier one goes, under the ledger's own delete
        guard lifted for that one statement and put back in the same
        transaction. The update guard is swapped at the same time, because the
        one written before this rule refused every UPDATE and would refuse the
        upsert that now records a mark. The unique index comes last and is
        the marker: a ledger that has it has been through this, so every later
        open pays one read of the schema and nothing else.
        """
        have = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                (MARKS_UNIQUE_INDEX,),
            )
        }
        if MARKS_UNIQUE_INDEX in have:
            return 0
        before = self.conn.total_changes
        apply_schema(
            self.conn,
            "DROP TRIGGER IF EXISTS marks_no_update; DROP TRIGGER IF EXISTS marks_no_delete;",
            f"DELETE FROM marks WHERE mark_id IN ({_DUPLICATE_MARKS});",
            MARKS_UNIQUE_SQL,
            _trigger_sql("marks", *_GUARDS["marks"]),
            timeout_ms=self.BUSY_TIMEOUT_MS,
        )
        return self.conn.total_changes - before

    @classmethod
    def open_existing(cls, path: str | Path) -> PaperStore | None:
        """The store if the file exists; None otherwise, and nothing created."""
        if str(path) != ":memory:" and not Path(path).exists():
            return None
        return cls(path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> PaperStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- books ----------------------------------------------------------------------------

    def has_books(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] > 0

    def has_book(self, book: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM books WHERE book = ?", (book,)).fetchone() is not None
        )

    def open_books(self) -> tuple[str, ...]:
        """The books this ledger carries, decided and control first."""
        held = {r["book"] for r in self.conn.execute("SELECT book FROM books")}
        return tuple(b for b in ALL_BOOKS if b in held)

    def book_opened_on(self, book: str) -> date | None:
        row = self.conn.execute("SELECT opened_on FROM books WHERE book = ?", (book,)).fetchone()
        return date.fromisoformat(row["opened_on"]) if row else None

    def book_terms(self, book: str) -> dict:
        """What the book was opened with, as `caps_json` froze it."""
        row = self.conn.execute("SELECT caps_json FROM books WHERE book = ?", (book,)).fetchone()
        return json.loads(row["caps_json"]) if row else {}

    def opened_on(self) -> date | None:
        row = self.conn.execute("SELECT opened_on FROM books WHERE book = ?", (DECIDED,)).fetchone()
        return date.fromisoformat(row["opened_on"]) if row else None

    def initial_cash(self, book: str = DECIDED) -> Decimal:
        row = self.conn.execute(
            "SELECT initial_cash_usd FROM books WHERE book = ?", (book,)
        ).fetchone()
        return _d(row["initial_cash_usd"]) if row else Decimal(0)

    def settings_frozen(self) -> dict:
        row = self.conn.execute("SELECT caps_json FROM books WHERE book = ?", (DECIDED,)).fetchone()
        return json.loads(row["caps_json"]) if row else {}

    def init_books(self, settings: PaperSettings, opened_on: date) -> None:
        if self.has_books():
            raise ValueError(
                f"the paper book was opened on {self.opened_on()}; it is append-only and "
                "has no reset. Point `[paper] database` at a new file to start another."
            )
        caps = json.dumps(settings.as_dict(), sort_keys=True)
        for book in BOOKS:
            self.conn.execute(
                "INSERT INTO books VALUES (?,?,?,?,?)",
                (book, opened_on.isoformat(), str(settings.initial_cash_usd), "USD", caps),
            )
        self.conn.commit()

    def open_book(self, book: str, opened_on: date, initial_cash_usd: Decimal, terms: dict) -> None:
        """Open one more book beside the two `init_books` opened.

        Once, like every book: the row is the book's opening cash, and a second
        opening would be a reset under another name. Refused before the two
        main books exist, because the phases every book trades by are theirs.
        """
        if book in BOOKS:
            raise ValueError(f"the {book} book is opened by `ask.py paper init`, not here")
        if not self.has_book(DECIDED):
            raise ValueError("no paper book yet: run `ask.py paper init` first")
        if self.has_book(book):
            raise ValueError(
                f"the {book} book was opened on {self.book_opened_on(book)}; it is append-only "
                "and has no reset"
            )
        if initial_cash_usd <= 0:
            raise ValueError(f"a book opens with cash above zero, not USD {initial_cash_usd}")
        self.conn.execute(
            "INSERT INTO books VALUES (?,?,?,?,?)",
            (
                book,
                opened_on.isoformat(),
                str(initial_cash_usd),
                "USD",
                json.dumps(terms, sort_keys=True),
            ),
        )
        self.conn.commit()

    # -- targets ----------------------------------------------------------------------------

    def record_targets(self, rows: list[TargetRow]) -> list[int]:
        ids: list[int] = []
        for t in rows:
            cur = self.conn.execute(
                "INSERT INTO targets (book, decided_on, decided_at, instrument_id, weight, "
                "target_units, reason, phase, thesis, prediction_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    t.book,
                    t.decided_on.isoformat(),
                    _iso(t.decided_at),
                    t.instrument_id,
                    str(t.weight),
                    t.target_units,
                    t.reason,
                    t.phase,
                    t.thesis,
                    t.prediction_id,
                ),
            )
            ids.append(int(cur.lastrowid or 0))
        self.conn.commit()
        return ids

    def _target(self, r: sqlite3.Row) -> TargetRow:
        return TargetRow(
            book=r["book"],
            decided_on=date.fromisoformat(r["decided_on"]),
            decided_at=datetime.fromisoformat(r["decided_at"]),
            instrument_id=r["instrument_id"],
            weight=_d(r["weight"]),
            reason=r["reason"],
            phase=r["phase"],
            thesis=r["thesis"],
            prediction_id=r["prediction_id"],
            target_units=r["target_units"],
            target_id=r["target_id"],
        )

    def pending_targets(self, book: str) -> list[TargetRow]:
        rows = self.conn.execute(
            "SELECT t.* FROM targets t LEFT JOIN applications a USING (target_id) "
            "WHERE t.book = ? AND a.target_id IS NULL ORDER BY t.decided_on, t.target_id",
            (book,),
        ).fetchall()
        return [self._target(r) for r in rows]

    def targets_on(self, book: str, decided_on: date, reason: str | None = None) -> list[TargetRow]:
        sql = "SELECT * FROM targets WHERE book = ? AND decided_on = ?"
        args: list = [book, decided_on.isoformat()]
        if reason:
            sql += " AND reason = ?"
            args.append(reason)
        return [self._target(r) for r in self.conn.execute(sql + " ORDER BY target_id", args)]

    def targets(self, book: str, since: date | None = None) -> list[TargetRow]:
        sql = "SELECT * FROM targets WHERE book = ?"
        args: list = [book]
        if since is not None:
            sql += " AND decided_on >= ?"
            args.append(since.isoformat())
        return [self._target(r) for r in self.conn.execute(sql + " ORDER BY target_id", args)]

    def target_by_prediction(self, prediction_id: str) -> TargetRow | None:
        r = self.conn.execute(
            "SELECT * FROM targets WHERE prediction_id = ?", (prediction_id,)
        ).fetchone()
        return self._target(r) if r else None

    def last_control_rebalance(self) -> date | None:
        return self.last_rebalance(CONTROL, "control_rebalance")

    def last_rebalance(self, book: str, reason: str) -> date | None:
        r = self.conn.execute(
            "SELECT MAX(decided_on) AS d FROM targets WHERE book = ? AND reason = ?",
            (book, reason),
        ).fetchone()
        return date.fromisoformat(r["d"]) if r and r["d"] else None

    def resolve(self, target_id: int, resolved_on: date, status: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO applications VALUES (?,?,?,?)",
            (target_id, resolved_on.isoformat(), status, detail),
        )
        self.conn.commit()

    def applications(
        self, book: str, since: date | None = None
    ) -> list[tuple[TargetRow, Application]]:
        sql = (
            "SELECT t.*, a.resolved_on, a.status, a.detail FROM targets t "
            "JOIN applications a USING (target_id) WHERE t.book = ?"
        )
        args: list = [book]
        if since is not None:
            sql += " AND a.resolved_on >= ?"
            args.append(since.isoformat())
        out = []
        for r in self.conn.execute(sql + " ORDER BY a.resolved_on, t.target_id", args):
            out.append(
                (
                    self._target(r),
                    Application(
                        r["target_id"],
                        date.fromisoformat(r["resolved_on"]),
                        r["status"],
                        r["detail"],
                    ),
                )
            )
        return out

    # -- position changes -------------------------------------------------------------------

    def record_change(self, c: PositionChange) -> int:
        cur = self.conn.execute(
            "INSERT INTO position_changes (book, target_id, day, instrument_id, currency, action, "
            "units_delta, units_after, bar_open, slippage_bps, price_local, consideration_local, "
            "consideration_usd, fee_local, fee_usd, fx_rate, fx_date, fx_source, fx_spread_usd, "
            "slippage_usd, cash_delta_usd, avg_cost_after, realised_pnl_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                c.book,
                c.target_id,
                c.day.isoformat(),
                c.instrument_id,
                c.currency,
                c.action,
                c.units_delta,
                c.units_after,
                str(c.bar_open),
                c.slippage_bps,
                str(c.price_local),
                str(c.consideration_local),
                str(c.consideration_usd),
                str(c.fee_local),
                str(c.fee_usd),
                str(c.fx_rate),
                c.fx_date.isoformat(),
                c.fx_source,
                str(c.fx_spread_usd),
                str(c.slippage_usd),
                str(c.cash_delta_usd),
                str(c.avg_cost_after),
                str(c.realised_pnl_usd),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def _change(self, r: sqlite3.Row) -> PositionChange:
        return PositionChange(
            book=r["book"],
            target_id=r["target_id"],
            day=date.fromisoformat(r["day"]),
            instrument_id=r["instrument_id"],
            currency=r["currency"],
            action=r["action"],
            units_delta=r["units_delta"],
            units_after=r["units_after"],
            bar_open=_d(r["bar_open"]),
            slippage_bps=r["slippage_bps"],
            price_local=_d(r["price_local"]),
            consideration_local=_d(r["consideration_local"]),
            consideration_usd=_d(r["consideration_usd"]),
            fee_local=_d(r["fee_local"]),
            fee_usd=_d(r["fee_usd"]),
            fx_rate=_d(r["fx_rate"]),
            fx_date=date.fromisoformat(r["fx_date"]),
            fx_source=r["fx_source"],
            fx_spread_usd=_d(r["fx_spread_usd"]),
            slippage_usd=_d(r["slippage_usd"]),
            cash_delta_usd=_d(r["cash_delta_usd"]),
            avg_cost_after=_d(r["avg_cost_after"]),
            realised_pnl_usd=_d(r["realised_pnl_usd"]),
            change_id=r["change_id"],
        )

    def changes(
        self, book: str, start: date | None = None, end: date | None = None
    ) -> list[PositionChange]:
        sql = "SELECT * FROM position_changes WHERE book = ?"
        args: list = [book]
        if start is not None:
            sql += " AND day >= ?"
            args.append(start.isoformat())
        if end is not None:
            sql += " AND day <= ?"
            args.append(end.isoformat())
        return [self._change(r) for r in self.conn.execute(sql + " ORDER BY change_id", args)]

    def change_for_target(self, target_id: int) -> PositionChange | None:
        r = self.conn.execute(
            "SELECT * FROM position_changes WHERE target_id = ? ORDER BY change_id LIMIT 1",
            (target_id,),
        ).fetchone()
        return self._change(r) if r else None

    def state(self, book: str) -> BookState:
        """Replayed from the initial cash and every change, never stored."""
        cash = self.initial_cash(book)
        units: dict[str, int] = {}
        cost: dict[str, Decimal] = {}
        ccy: dict[str, str] = {}
        realised = Decimal(0)
        for c in self.changes(book):
            cash += c.cash_delta_usd
            units[c.instrument_id] = c.units_after
            cost[c.instrument_id] = c.avg_cost_after
            ccy[c.instrument_id] = c.currency
            realised += c.realised_pnl_usd
        positions = tuple(
            PaperPosition(iid, units[iid], cost[iid], ccy[iid])
            for iid in sorted(units)
            if units[iid] > 0
        )
        last = self.latest_mark(book)
        peak = max(last.peak_usd if last else Decimal(0), self.initial_cash(book))
        return BookState(book, cash, positions, realised, peak)

    def cost_to_date(self, book: str) -> CostDrag:
        fees = fx = slip = Decimal(0)
        for c in self.changes(book):
            fees += c.fee_usd
            fx += c.fx_spread_usd
            slip += c.slippage_usd
        return CostDrag(fees, fx, slip, self.initial_cash(book))

    # -- marks -------------------------------------------------------------------------------

    #: Every reading on a mark row; what a re-mark of the same session replaces.
    _MARK_READINGS = (
        "marked_at",
        "cash_usd",
        "positions_usd",
        "equity_usd",
        "peak_usd",
        "drawdown",
        "halted",
        "phase",
        "fx_rate",
        "fx_date",
        "fx_source",
        "positions_json",
    )

    def record_mark(self, m: MarkRow) -> int:
        """The row's id: a new row, or the session's existing row re-read.

        One row per (book, session day, slot). The close run and the catch-up
        dispatched after it both mark the same session, and the record wants
        the later reading and exactly one of them - so a later mark replaces
        the earlier in place, keeping its id, and an earlier mark arriving
        after a later one (a replay) changes nothing. The id is read back
        rather than taken from `lastrowid`, which the update path of an upsert
        does not set.
        """
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in self._MARK_READINGS)
        self.conn.execute(
            "INSERT INTO marks (book, day, slot, marked_at, cash_usd, positions_usd, equity_usd, "
            "peak_usd, drawdown, halted, phase, fx_rate, fx_date, fx_source, positions_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            f"ON CONFLICT(book, day, slot) DO UPDATE SET {set_clause} "
            "WHERE excluded.marked_at >= marks.marked_at",
            (
                m.book,
                m.day.isoformat(),
                m.slot,
                _iso(m.marked_at),
                str(m.cash_usd),
                str(m.positions_usd),
                str(m.equity_usd),
                str(m.peak_usd),
                str(m.drawdown),
                int(m.halted),
                m.phase,
                str(m.fx_rate),
                m.fx_date.isoformat(),
                m.fx_source,
                json.dumps(m.positions, default=str),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT mark_id FROM marks WHERE book = ? AND day = ? AND slot = ?",
            (m.book, m.day.isoformat(), m.slot),
        ).fetchone()
        return int(row["mark_id"]) if row else 0

    def mark_for(self, book: str, day: date, slot: str) -> MarkRow | None:
        """The one mark a slot holds for a session, if it has marked it."""
        r = self.conn.execute(
            "SELECT * FROM marks WHERE book = ? AND day = ? AND slot = ?",
            (book, day.isoformat(), slot),
        ).fetchone()
        return self._mark(r) if r else None

    def all_marks(self) -> list[MarkRow]:
        """Every mark row, every book, oldest first - the scan a re-stamp reads."""
        return [
            self._mark(r) for r in self.conn.execute("SELECT * FROM marks ORDER BY day, mark_id")
        ]

    def sessions_marked(self, book: str) -> int:
        """Distinct session days with a mark, whatever the slots and however many runs."""
        return int(
            self.conn.execute(
                "SELECT COUNT(DISTINCT day) FROM marks WHERE book = ?", (book,)
            ).fetchone()[0]
        )

    def restamp_marks(self, moves: list[tuple[int, date]]) -> list[tuple[MarkRow, date, str]]:
        """Re-date marks onto the session they were marked from.

        `moves` pairs a mark id with the day it should carry. This is the one
        place a mark's day changes, and only from a day its market never
        traded to the session of the bars that priced it: a mark stamped with
        the wall-clock Saturday of a catch-up run is Friday's mark filed under
        the wrong day, not a different fact. Both guards are lifted for the
        transaction and put back inside it. Where the session already has a
        mark for that slot the later `marked_at` stays, so a move can replace
        the session's earlier mark or be dropped in favour of a later one; the
        return says which happened to each, oldest first.
        """
        if not moves:
            return []
        out: list[tuple[MarkRow, date, str]] = []
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("DROP TRIGGER IF EXISTS marks_no_update")
            self.conn.execute("DROP TRIGGER IF EXISTS marks_no_delete")
            for mark_id, new_day in moves:
                r = self.conn.execute(
                    "SELECT * FROM marks WHERE mark_id = ?", (mark_id,)
                ).fetchone()
                if r is None:
                    continue
                m = self._mark(r)
                other = self.conn.execute(
                    "SELECT mark_id, marked_at FROM marks WHERE book = ? AND day = ? AND slot = ?",
                    (m.book, new_day.isoformat(), m.slot),
                ).fetchone()
                if other is not None and other["marked_at"] >= r["marked_at"]:
                    self.conn.execute("DELETE FROM marks WHERE mark_id = ?", (mark_id,))
                    out.append((m, new_day, "dropped"))
                    continue
                outcome = "moved"
                if other is not None:
                    self.conn.execute("DELETE FROM marks WHERE mark_id = ?", (other["mark_id"],))
                    outcome = "replaced"
                self.conn.execute(
                    "UPDATE marks SET day = ? WHERE mark_id = ?", (new_day.isoformat(), mark_id)
                )
                out.append((m, new_day, outcome))
            for statement in _trigger_statements("marks", *_GUARDS["marks"]):
                self.conn.execute(statement)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return out

    def _mark(self, r: sqlite3.Row) -> MarkRow:
        return MarkRow(
            book=r["book"],
            day=date.fromisoformat(r["day"]),
            slot=r["slot"],
            marked_at=datetime.fromisoformat(r["marked_at"]),
            cash_usd=_d(r["cash_usd"]),
            positions_usd=_d(r["positions_usd"]),
            equity_usd=_d(r["equity_usd"]),
            peak_usd=_d(r["peak_usd"]),
            drawdown=_d(r["drawdown"]),
            halted=bool(r["halted"]),
            phase=r["phase"],
            fx_rate=_d(r["fx_rate"]),
            fx_date=date.fromisoformat(r["fx_date"]),
            fx_source=r["fx_source"],
            positions=json.loads(r["positions_json"]),
            mark_id=r["mark_id"],
        )

    def latest_mark(self, book: str, on_or_before: date | None = None) -> MarkRow | None:
        sql = "SELECT * FROM marks WHERE book = ?"
        args: list = [book]
        if on_or_before is not None:
            sql += " AND day <= ?"
            args.append(on_or_before.isoformat())
        r = self.conn.execute(sql + " ORDER BY day DESC, mark_id DESC LIMIT 1", args).fetchone()
        return self._mark(r) if r else None

    def mark_before(self, book: str, day: date) -> MarkRow | None:
        r = self.conn.execute(
            "SELECT * FROM marks WHERE book = ? AND day < ? ORDER BY day DESC, mark_id DESC LIMIT 1",
            (book, day.isoformat()),
        ).fetchone()
        return self._mark(r) if r else None

    def marks(self, book: str, since: date | None = None) -> list[MarkRow]:
        """The last mark per day, ascending - the equity series."""
        sql = (
            "SELECT m.* FROM marks m JOIN (SELECT book, day, MAX(mark_id) AS mid FROM marks "
            "WHERE book = ? GROUP BY book, day) x ON x.mid = m.mark_id"
        )
        args: list = [book]
        if since is not None:
            sql += " WHERE m.day >= ?"
            args.append(since.isoformat())
        return [self._mark(r) for r in self.conn.execute(sql + " ORDER BY m.day", args)]

    def counts(self) -> dict[str, int]:
        q = lambda t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        return {t: q(t) for t in ("books", "targets", "applications", "position_changes", "marks")}
