"""The one-off repair for rows the collectors stamped knowable in the future.

Rewriting what was known is exactly what the store's append-only triggers
exist to refuse, so the script is held to a narrow contract: touch only rows
whose known_at lies after the day they were fetched, move them to that day and
no earlier, label them forward so the A1 bridge can build them, leave every
other row as it was, put the guard back, and say what it did - on stdout and
inside the store."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from knowledge.facts import FactBook, Observation

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "repair_known_at.py"
spec = importlib.util.spec_from_file_location("repair_known_at", SCRIPT)
assert spec is not None and spec.loader is not None
repair_known_at = importlib.util.module_from_spec(spec)
# Registered before it runs: a dataclass under `from __future__ import
# annotations` resolves its field types through sys.modules, and a module
# loaded from a path is not there unless the loader puts it there.
sys.modules[spec.name] = repair_known_at
spec.loader.exec_module(repair_known_at)

FETCHED = datetime(2026, 9, 5, 2, 29, tzinfo=UTC)


def seed(path: Path) -> None:
    """Three rows from the fact book of 2026-09-18, stamped the way they were."""
    with FactBook(path) as book:
        book.add_observations(
            [
                # NVIDIA's real print, filed under the calendar quarter end and
                # clamped to it: public on 5 September, dated the 30th.
                Observation(
                    "finnhub",
                    "XNAS:NVDA",
                    "eps_actual",
                    date(2026, 9, 30),
                    Decimal("2.22"),
                    period_end=date(2026, 9, 30),
                    currency="USD",
                    payload={"quarter": 2, "year": 2027},
                ),
                # A consensus figure dated a year out.
                Observation(
                    "fmp",
                    "XNAS:AAPL",
                    "est_eps",
                    date(2027, 9, 27),
                    Decimal("9.53806"),
                    period_end=date(2027, 9, 27),
                    currency="USD",
                    payload={"analysts": 30},
                ),
                # The quarter before, stamped correctly. It must not move.
                Observation(
                    "finnhub",
                    "XNAS:NVDA",
                    "eps_actual",
                    date(2026, 9, 5),
                    Decimal("1.87"),
                    period_end=date(2026, 6, 30),
                    currency="USD",
                    payload={"quarter": 1, "year": 2027},
                ),
            ],
            fetched_at=FETCHED,
        )


def test_mis_stamped_rows_move_to_the_fetch_day_and_nothing_else_moves(tmp_path):
    path = tmp_path / "facts.db"
    seed(path)
    changes = repair_known_at.repair(path, now=FETCHED)
    assert [(c.source, c.concept, c.known_at, c.fetched_on) for c in changes] == [
        ("finnhub", "eps_actual", "2026-09-30", "2026-09-05"),
        ("fmp", "est_eps", "2027-09-27", "2026-09-05"),
    ]
    with FactBook(path) as book:
        assert repair_known_at.mis_stamped(book) == []
        by_period = {o.period_end: o for o in book.observations("XNAS:NVDA", "eps_actual")}
        moved = by_period[date(2026, 9, 30)]
        assert moved.known_at == date(2026, 9, 5) and moved.forward
        assert moved.payload == {"quarter": 2, "year": 2027}, "the vendor's extras are kept"
        kept = by_period[date(2026, 6, 30)]
        assert kept.known_at == date(2026, 9, 5) and not kept.forward
        (est,) = book.observations("XNAS:AAPL", "est_eps")
        assert est.known_at == date(2026, 9, 5) and est.forward


def test_the_repaired_rows_cross_the_bridge_from_the_day_they_were_fetched(tmp_path):
    """The reason the label is set and not only the date: without it the A1
    bridge builds a Fact whose known_at precedes its period end and the guard
    refuses the whole store."""
    path = tmp_path / "facts.db"
    seed(path)
    repair_known_at.repair(path, now=FETCHED)
    with FactBook(path) as book:
        store = book.as_fact_store()
        print_ = store.as_known_at("XNAS:NVDA", "eps_actual", date(2026, 9, 5))
        assert print_ is not None and print_.value == Decimal("2.22") and print_.forward
        kept = store.as_known_at(
            "XNAS:NVDA", "eps_actual", date(2026, 9, 5), period_end=date(2026, 6, 30)
        )
        assert kept is not None and kept.value == Decimal("1.87") and not kept.forward, (
            "the earlier quarter, correctly stamped, is untouched"
        )
        assert store.as_known_at("XNAS:NVDA", "eps_actual", date(2026, 9, 4)) is None
        est = store.as_known_at("XNAS:AAPL", "est_eps", date(2026, 9, 5))
        assert est is not None and est.value == Decimal("9.53806")
        assert store.as_known_at("XNAS:AAPL", "est_eps", date(2026, 9, 4)) is None


def test_the_guard_is_back_and_the_store_records_the_repair(tmp_path):
    path = tmp_path / "facts.db"
    seed(path)
    repair_known_at.repair(path, now=FETCHED)
    with FactBook(path) as book:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            book.conn.execute("UPDATE observations SET known_at = '2020-01-01'")
        (run,) = book.pulls(source=repair_known_at.SOURCE)
        assert run["stored"] == 2 and run["fetched"] == 2
        assert "XNAS:NVDA eps_actual 2026-09-30 2026-09-30->2026-09-05" in run["detail"]
        assert book.last_success(repair_known_at.SOURCE) == FETCHED


def test_a_dry_run_reports_and_touches_nothing(tmp_path):
    path = tmp_path / "facts.db"
    seed(path)
    changes = repair_known_at.repair(path, dry_run=True)
    assert len(changes) == 2
    with FactBook(path) as book:
        assert len(repair_known_at.mis_stamped(book)) == 2
        assert book.pulls(source=repair_known_at.SOURCE) == []


def test_a_second_run_finds_nothing_and_records_nothing(tmp_path):
    path = tmp_path / "facts.db"
    seed(path)
    repair_known_at.repair(path, now=FETCHED)
    assert repair_known_at.repair(path) == []
    with FactBook(path) as book:
        assert len(book.pulls(source=repair_known_at.SOURCE)) == 1


def test_main_prints_each_row_and_the_counts_before_and_after(tmp_path, capsys):
    path = tmp_path / "facts.db"
    seed(path)
    assert repair_known_at.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "eps_actual" in out and "known_at 2026-09-30 -> 2026-09-05" in out
    assert out.rstrip().endswith(
        "2 rows had known_at after the day they were fetched; moved 2 to that day; 0 remain"
    )
    assert repair_known_at.main([str(path), "--dry-run"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("would move 0 to that day; 0 remain")
