"""Several connections opening one fresh store together must all succeed.

The stress suite's eighth writer failed on Windows with "database is locked"
despite a ten-second busy timeout and WAL. The cause is SQLite's snapshot
rule: a statement that began as a reader (CREATE ... IF NOT EXISTS reads the
schema first) cannot become a writer once another connection has committed,
and SQLite answers BUSY at once without consulting the busy handler.
`apply_schema` takes the write lock first and retries a lock that outlasts
the handler; these tests pin both halves and the constructors that use it.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from agents.learning.reflection import Horizon, Prediction
from agents.learning.store import LearningStore
from core.provenance.ledger import apply_schema

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _hold_write_lock(path, release_after: float | None):
    """A connection holding the write lock, released after a delay or never."""
    # The release happens on a timer thread, so the connection must allow it.
    holder = sqlite3.connect(path, timeout=0.05, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("CREATE TABLE IF NOT EXISTS held(x)")
    if release_after is not None:
        threading.Timer(release_after, holder.commit).start()
    return holder


def test_schema_waits_for_a_writer_that_lets_go(tmp_path):
    path = tmp_path / "s.db"
    holder = _hold_write_lock(path, release_after=0.3)
    other = sqlite3.connect(path, timeout=0.05)
    t0 = time.monotonic()
    apply_schema(other, "CREATE TABLE IF NOT EXISTS t(x);", timeout_ms=5000)
    assert 0.2 <= time.monotonic() - t0 < 4
    names = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"held", "t"} <= names
    holder.close()
    other.close()


def test_schema_gives_up_when_the_lock_never_clears(tmp_path):
    path = tmp_path / "s.db"
    holder = _hold_write_lock(path, release_after=None)
    other = sqlite3.connect(path, timeout=0.05)
    t0 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        apply_schema(other, "CREATE TABLE IF NOT EXISTS t(x);", timeout_ms=300)
    assert 0.25 <= time.monotonic() - t0 < 3
    assert not other.in_transaction, "a failed attempt leaves no transaction open"
    holder.rollback()
    holder.close()
    other.close()


def test_a_non_lock_error_is_raised_at_once_not_retried(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    t0 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="syntax"):
        apply_schema(conn, "CREATE TABL nope(x);", timeout_ms=5000)
    assert time.monotonic() - t0 < 0.5
    assert not conn.in_transaction


def test_several_scripts_commit_together_or_not_at_all(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_schema(conn, "CREATE TABLE a(x);", "CREATE TABL b(x);", timeout_ms=1000)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" not in names, "the first script must not survive the second's failure"
    apply_schema(conn, "CREATE TABLE a(x);", "CREATE TABLE b(x);", timeout_ms=1000)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"a", "b"} <= names


@pytest.mark.parametrize("round_", range(3))
def test_eight_learning_stores_open_one_fresh_file_together(tmp_path, round_):
    path = tmp_path / f"concurrent-{round_}.db"
    errors: list[str] = []

    def writer(worker: int) -> None:
        try:
            with LearningStore(path) as s:
                for i in range(5):
                    s.record(
                        Prediction(
                            f"w{worker}-{i}",
                            f"S{i}",
                            "human",
                            NOW,
                            Horizon.D21,
                            "test",
                            1,
                            0.6,
                            grade_on=date(2026, 10, 4) + timedelta(days=i),
                        )
                    )
        except Exception as e:  # noqa: BLE001 - the point is to see any error at all
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with LearningStore(path) as s:
        assert s.counts()["logged"] == 40
