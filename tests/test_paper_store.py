"""The paper ledger is append-only and replays to the cent."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from engines.paper.settings import PaperSettings
from engines.paper.store import CONTROL, DECIDED, MarkRow, PaperStore, PositionChange, TargetRow

NOW = datetime(2026, 3, 2, 22, 30, tzinfo=UTC)


def _change(day, iid, delta, after, price, cash, avg, ccy="MYR", fee="1.00"):
    return PositionChange(
        book=DECIDED,
        target_id=None,
        day=day,
        instrument_id=iid,
        currency=ccy,
        action="open" if delta > 0 else "exit",
        units_delta=delta,
        units_after=after,
        bar_open=Decimal(price),
        slippage_bps=10,
        price_local=Decimal(price),
        consideration_local=Decimal(price) * abs(delta),
        consideration_usd=Decimal(price) * abs(delta) / 4,
        fee_local=Decimal(fee),
        fee_usd=Decimal(fee) / 4,
        fx_rate=Decimal(4),
        fx_date=day,
        fx_source="config",
        fx_spread_usd=Decimal("0.5"),
        slippage_usd=Decimal("0.1"),
        cash_delta_usd=Decimal(cash),
        avg_cost_after=Decimal(avg),
    )


def test_init_opens_both_books_once_and_refuses_a_reset(tmp_path):
    with PaperStore(tmp_path / "p.db") as s:
        assert not s.has_books()
        s.init_books(PaperSettings(), date(2026, 3, 2))
        assert s.has_books() and s.opened_on() == date(2026, 3, 2)
        assert s.initial_cash(DECIDED) == Decimal(1000) == s.initial_cash(CONTROL)
        assert s.settings_frozen()["start_date"] == "2026-09-08"  # the settings, not the override
        with pytest.raises(ValueError, match="no reset"):
            s.init_books(PaperSettings(), date(2026, 3, 9))


def test_open_existing_creates_nothing(tmp_path):
    assert PaperStore.open_existing(tmp_path / "absent.db") is None
    assert not (tmp_path / "absent.db").exists()
    with PaperStore(":memory:") as s:
        assert not s.has_books()


@pytest.mark.parametrize(
    "table, msg",
    [
        ("books", "no reset"),
        ("targets", "becomes a memory"),
        ("applications", "resolved once"),
        ("position_changes", "proves nothing"),
        ("marks", "fact about a session day"),
    ],
)
def test_every_table_refuses_update_and_delete(tmp_path, table, msg):
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        (tid,) = s.record_targets(
            [
                TargetRow(
                    DECIDED,
                    date(2026, 3, 16),
                    NOW,
                    "MYX:5183",
                    Decimal("0.1"),
                    "decision",
                    "ramp",
                    "t",
                )
            ]
        )
        s.resolve(tid, date(2026, 3, 17), "applied", "x")
        s.record_change(_change(date(2026, 3, 17), "MYX:5183", 100, 100, "4.16", "-105.00", "4.16"))
        s.record_mark(
            MarkRow(
                DECIDED,
                date(2026, 3, 17),
                "us_close",
                NOW,
                Decimal(895),
                Decimal(104),
                Decimal(999),
                Decimal(1000),
                Decimal("0.001"),
                False,
                "ramp",
                Decimal(4),
                date(2026, 3, 17),
                "config",
                [],
            )
        )
        with pytest.raises(sqlite3.IntegrityError, match=msg):
            s.conn.execute(f"UPDATE {table} SET rowid = rowid + 100")
        with pytest.raises(sqlite3.IntegrityError):
            s.conn.execute(f"DELETE FROM {table}")


def test_state_replays_cash_positions_and_realised_from_the_changes(tmp_path):
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        s.record_change(_change(date(2026, 3, 17), "MYX:5183", 100, 100, "4.16", "-105.00", "4.16"))
        s.record_change(_change(date(2026, 3, 18), "MYX:5183", 100, 200, "4.20", "-106.00", "4.18"))
        exit_ = _change(date(2026, 3, 25), "MYX:5183", -100, 100, "4.40", "+109.00", "4.18")
        exit_ = PositionChange(
            **{**exit_.__dict__, "realised_pnl_usd": Decimal("5.50"), "action": "trim"}
        )
        s.record_change(exit_)
        st = s.state(DECIDED)
        assert st.cash_usd == Decimal("898.00")
        assert [(p.instrument_id, p.units, p.avg_cost) for p in st.positions] == [
            ("MYX:5183", 100, Decimal("4.18"))
        ]
        assert st.realised_pnl_usd == Decimal("5.50")
        cost = s.cost_to_date(DECIDED)
        assert cost.fees_usd == Decimal("0.75") and cost.fx_spread_usd == Decimal("1.5")
        assert s.state(CONTROL).positions == () and s.state(CONTROL).cash_usd == Decimal(1000)


def test_marks_series_is_the_last_mark_per_day(tmp_path):
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        for day, slot, eq in (
            (date(2026, 3, 17), "bursa_close", 990),
            (date(2026, 3, 17), "us_close", 995),
            (date(2026, 3, 18), "us_close", 1001),
        ):
            s.record_mark(
                MarkRow(
                    DECIDED,
                    day,
                    slot,
                    NOW,
                    Decimal(900),
                    Decimal(eq - 900),
                    Decimal(eq),
                    Decimal(max(eq, 1000)),
                    Decimal(0),
                    False,
                    "ramp",
                    Decimal(4),
                    day,
                    "config",
                    [],
                )
            )
        series = s.marks(DECIDED)
        assert [(m.day, m.slot, m.equity_usd) for m in series] == [
            (date(2026, 3, 17), "us_close", Decimal(995)),
            (date(2026, 3, 18), "us_close", Decimal(1001)),
        ]
        assert s.latest_mark(DECIDED, on_or_before=date(2026, 3, 17)).slot == "us_close"
        assert s.mark_before(DECIDED, date(2026, 3, 18)).equity_usd == Decimal(995)


def test_pending_targets_exclude_the_resolved_and_keep_decision_order(tmp_path):
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        ids = s.record_targets(
            [
                TargetRow(
                    DECIDED, date(2026, 3, 16), NOW, "MYX:5183", Decimal("0.1"), "decision", "ramp"
                ),
                TargetRow(
                    DECIDED, date(2026, 3, 16), NOW, "MYX:3182", Decimal("0.05"), "decision", "ramp"
                ),
            ]
        )
        s.resolve(ids[0], date(2026, 3, 17), "applied")
        pending = s.pending_targets(DECIDED)
        assert [t.instrument_id for t in pending] == ["MYX:3182"]
        assert (
            s.targets_on(DECIDED, date(2026, 3, 16), reason="decision")[0].instrument_id
            == "MYX:5183"
        )
        assert s.counts()["applications"] == 1


# -- one mark per session ----------------------------------------------------------------


def _mark_row(day, slot, at, equity, book=DECIDED):
    return MarkRow(
        book,
        day,
        slot,
        at,
        Decimal(equity),
        Decimal(0),
        Decimal(equity),
        Decimal(max(equity, 1000)),
        Decimal(0),
        False,
        "observe",
        Decimal(4),
        day,
        "config",
        [],
    )


def test_a_slot_marks_a_session_once_and_the_later_reading_replaces_the_earlier(tmp_path):
    fri = date(2026, 3, 6)
    t1, t2, t3 = (datetime(2026, 3, 6, 21, 15, tzinfo=UTC) + timedelta(hours=h) for h in (0, 9, 2))
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        first = s.record_mark(_mark_row(fri, "us_close", t1, 1000))
        again = s.record_mark(_mark_row(fri, "us_close", t2, 1001))  # the catch-up, later
        assert again == first, "the session's row is re-read in place, keeping its id"
        assert s.counts()["marks"] == 1 and s.sessions_marked(DECIDED) == 1
        kept = s.mark_for(DECIDED, fri, "us_close")
        assert kept is not None and kept.equity_usd == Decimal(1001) and kept.marked_at == t2
        # an earlier reading arriving after a later one changes nothing
        assert s.record_mark(_mark_row(fri, "us_close", t3, 999)) == first
        kept = s.mark_for(DECIDED, fri, "us_close")
        assert kept is not None and kept.equity_usd == Decimal(1001) and kept.marked_at == t2
        # a different slot on the same session is its own row; a re-date by hand is refused
        s.record_mark(_mark_row(fri, "bursa_close", t1, 998))
        assert s.counts()["marks"] == 2 and s.sessions_marked(DECIDED) == 1
        with pytest.raises(sqlite3.IntegrityError, match="fact about a session day"):
            s.conn.execute("UPDATE marks SET day = '2026-03-07' WHERE day = '2026-03-06'")


_OLD_MARK_GUARDS = (
    "CREATE TRIGGER marks_no_update BEFORE UPDATE ON marks "
    "BEGIN SELECT RAISE(ABORT, 'a mark is a fact about a date'); END;"
    "CREATE TRIGGER marks_no_delete BEFORE DELETE ON marks "
    "BEGIN SELECT RAISE(ABORT, 'marks are never deleted'); END;"
)


def _ledger_written_before_the_rule(path, rows):
    """A paper.db as the schema of 2026-09-04 wrote it: no unique index, an
    update guard that refused everything, and whatever rows the runs left."""
    from engines.paper.store import SCHEMA

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA + _OLD_MARK_GUARDS)
    for book, day, slot, at, equity in rows:
        conn.execute(
            "INSERT INTO marks (book, day, slot, marked_at, cash_usd, positions_usd, equity_usd, "
            "peak_usd, drawdown, halted, phase, fx_rate, fx_date, fx_source, positions_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                book,
                day,
                slot,
                at,
                str(equity),
                "0",
                str(equity),
                "1000",
                "0",
                0,
                "observe",
                "4",
                day,
                "config",
                "[]",
            ),
        )
    conn.commit()
    conn.close()


def test_a_ledger_with_duplicate_marks_is_deduplicated_once_on_open(tmp_path):
    path = tmp_path / "old.db"
    rows = [
        # 2026-09-04 us_close marked twice by two dispatches: the later stays
        (DECIDED, "2026-09-04", "us_close", "2026-09-04T16:17:45+00:00", 1000),
        (CONTROL, "2026-09-04", "us_close", "2026-09-04T16:17:45+00:00", 1000),
        (DECIDED, "2026-09-04", "us_close", "2026-09-04T23:03:57+00:00", 1001),
        (CONTROL, "2026-09-04", "us_close", "2026-09-04T23:03:57+00:00", 1001),
        # a different slot the same day is not a duplicate
        (DECIDED, "2026-09-04", "bursa_close", "2026-09-04T09:20:00+00:00", 999),
        # two runs stamped the same instant: the higher id stays
        (DECIDED, "2026-09-08", "us_close", "2026-09-08T22:38:02+00:00", 1000),
        (DECIDED, "2026-09-08", "us_close", "2026-09-08T22:38:02+00:00", 1002),
    ]
    _ledger_written_before_the_rule(path, rows)
    with PaperStore(path) as s:
        assert s.marks_deduplicated == 3
        assert s.counts()["marks"] == 4
        kept = s.mark_for(DECIDED, date(2026, 9, 4), "us_close")
        assert kept is not None and kept.equity_usd == Decimal(1001)
        kept = s.mark_for(DECIDED, date(2026, 9, 8), "us_close")
        assert kept is not None and kept.equity_usd == Decimal(1002)
        names = {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master")}
        assert "marks_book_day_slot" in names
        # the swapped guard lets the session be re-read and nothing else
        s.record_mark(
            _mark_row(date(2026, 9, 4), "us_close", datetime(2026, 9, 5, 6, tzinfo=UTC), 1003)
        )
        kept = s.mark_for(DECIDED, date(2026, 9, 4), "us_close")
        assert kept is not None and kept.equity_usd == Decimal(1003)
        with pytest.raises(sqlite3.IntegrityError, match="fact about a session day"):
            s.conn.execute("UPDATE marks SET slot = 'manual'")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            s.conn.execute("DELETE FROM marks")
    with PaperStore(path) as s:
        assert s.marks_deduplicated == 0, "the index is the marker; a second open changes nothing"
        assert s.counts()["marks"] == 4


def test_restamping_moves_a_mark_onto_its_session_and_the_later_reading_stays(tmp_path):
    fri, sat, sun = date(2026, 3, 6), date(2026, 3, 7), date(2026, 3, 8)
    at = lambda d, h: datetime(d.year, d.month, d.day, h, tzinfo=UTC)  # noqa: E731
    with PaperStore(tmp_path / "p.db") as s:
        s.init_books(PaperSettings(), date(2026, 3, 2))
        s.record_mark(_mark_row(fri, "us_close", at(fri, 21), 1000))
        sat_id = s.record_mark(_mark_row(sat, "us_close", at(sat, 6), 1001))  # the catch-up
        sun_id = s.record_mark(_mark_row(sun, "us_close", at(sun, 22), 1002))
        early_id = s.record_mark(_mark_row(sat, "bursa_close", at(fri, 8), 990))  # before Friday's
        s.record_mark(_mark_row(fri, "bursa_close", at(fri, 9), 991))
        outcomes = [
            (m.day, new, how)
            for m, new, how in s.restamp_marks([(sat_id, fri), (sun_id, fri), (early_id, fri)])
        ]
        assert outcomes == [(sat, fri, "replaced"), (sun, fri, "replaced"), (sat, fri, "dropped")]
        assert s.sessions_marked(DECIDED) == 1 and s.counts()["marks"] == 2
        us = s.mark_for(DECIDED, fri, "us_close")
        assert us is not None and us.equity_usd == Decimal(1002) and us.mark_id == sun_id
        bursa = s.mark_for(DECIDED, fri, "bursa_close")
        assert bursa is not None and bursa.equity_usd == Decimal(991)
        # the guards are back once the transaction closes
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            s.conn.execute("DELETE FROM marks")
        assert s.restamp_marks([]) == []
