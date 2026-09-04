"""The dated rate log: what it is for, and the three ways it must not drift.

`fx_spread_per_side` is the largest unmeasured number in the system. Measuring
it has needed two figures at one moment - the rate the broker gave and the
official rate right then - and needing both at once is why it has not happened.
Recording the official rate daily removes the timing problem.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from core.market.fxlog import FxLog

DAY = date(2026, 9, 3)
ROWS = [
    ("USD", DAY, Decimal("4.0550")),
    ("SGD", DAY, Decimal("3.1600")),
    ("JPY", DAY, Decimal("0.0271")),
]


@pytest.fixture
def log(tmp_path):
    with FxLog(tmp_path / "fx.db") as f:
        yield f


# --- append-only -------------------------------------------------------------------


def test_a_recorded_rate_cannot_be_edited(log):
    """A rate you can change afterwards proves nothing about what was knowable
    on the day, which is the only thing this log is for."""
    log.record("USD", DAY, Decimal("4.0550"))
    with pytest.raises(sqlite3.IntegrityError, match="not editable"):
        log.conn.execute("UPDATE fx_rates SET rate = '9.99'")


def test_a_recorded_rate_cannot_be_deleted(log):
    log.record("USD", DAY, Decimal("4.0550"))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log.conn.execute("DELETE FROM fx_rates")


# --- idempotence -------------------------------------------------------------------


def test_recording_the_same_day_twice_changes_nothing(log):
    """The collector runs daily and may be run by hand as well. A second run
    must not double-count, and must not fail either."""
    assert log.record("USD", DAY, Decimal("4.0550")) is True
    assert log.record("USD", DAY, Decimal("4.0550")) is False
    assert log.counts()["rates"] == 1


def test_a_second_run_does_not_overwrite_the_first_reading(log):
    """INSERT OR IGNORE, not upsert. The first reading of a day is the one that
    was knowable when it was taken; a later correction is a different fact and
    silently replacing one with the other loses both."""
    log.record("USD", DAY, Decimal("4.0550"))
    log.record("USD", DAY, Decimal("4.9999"))
    assert log.rate_on("USD", DAY) == Decimal("4.0550")


# --- what is kept ------------------------------------------------------------------


def test_only_the_currencies_the_book_touches_are_kept(log):
    """BNM publishes thirty-odd currencies and this book names one or two.
    Storing the rest daily is thirty times the rows to answer one question."""
    new, held = log.record_all(ROWS, only=("USD",))
    assert (new, held) == (1, 0)
    assert log.counts() == {"rates": 1, "currencies": 1, "days": 1}
    assert log.rate_on("SGD", DAY) is None


def test_no_filter_keeps_everything(log):
    assert log.record_all(ROWS)[0] == 3


def test_a_repeat_run_reports_what_was_already_held(log):
    log.record_all(ROWS, only=("USD", "SGD"))
    assert log.record_all(ROWS, only=("USD", "SGD")) == (0, 2)


# --- reads -------------------------------------------------------------------------


def test_the_lookup_is_exact_and_never_the_nearest_day():
    """The point of this log is comparing against a conversion made on a KNOWN
    date. Answering with a neighbouring day's rate would put an unknown error
    into the one number it exists to measure - so a missing day says so."""
    with FxLog(":memory:") as log:
        log.record("USD", DAY, Decimal("4.0550"))
        assert log.rate_on("USD", DAY) == Decimal("4.0550")
        assert log.rate_on("USD", date(2026, 9, 4)) is None
        assert log.rate_on("USD", date(2026, 9, 2)) is None


def test_history_is_newest_first(log):
    for d, r in (
        (date(2026, 9, 1), "4.01"),
        (date(2026, 9, 3), "4.03"),
        (date(2026, 9, 2), "4.02"),
    ):
        log.record("USD", d, Decimal(r))
    assert [dict(r)["rate_date"] for r in log.history("USD")] == [
        "2026-09-03",
        "2026-09-02",
        "2026-09-01",
    ]


def test_the_rate_survives_the_round_trip_exactly(log):
    """Stored as text, not a float. A rate that gains a rounding error in the
    database cannot measure a spread of half a percent."""
    log.record("USD", DAY, Decimal("4.05500"))
    assert log.rate_on("USD", DAY) == Decimal("4.05500")
    assert str(log.rate_on("USD", DAY)) == "4.05500"


def test_the_reading_time_is_kept_apart_from_the_rate_date(log):
    """Two different facts: the day BNM published the rate, and the moment this
    system read it. A sweep that runs late must not look like a later rate."""
    seen = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    log.record("USD", DAY, Decimal("4.0550"), seen_at=seen)
    row = dict(log.history("USD")[0])
    assert row["rate_date"] == "2026-09-03"
    assert row["seen_at"].startswith("2026-09-04T10:00")


# --- the CLI -----------------------------------------------------------------------


def test_fx_records_what_the_source_published(tmp_path, capsys, monkeypatch):
    """Driven through the opener seam every feed here takes, so a refactor that
    stops honouring it fails loudly rather than silently reaching the network."""
    import ask
    import core.market.fx as fx
    from tests.conftest import opener_for
    from tests.test_fx_rss import BNM_BODY

    # the real class captured BEFORE patching; a lambda that reads fx.BnmFxFeed
    # at call time would find itself
    real = fx.BnmFxFeed
    monkeypatch.setattr(fx, "BnmFxFeed", lambda: real(opener=opener_for(BNM_BODY)))
    db = tmp_path / "fx.db"
    assert ask.main(["fx", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "1 USD = MYR 4.1520" in out
    assert "1 new" in out

    # a second run is a no-op, not a duplicate and not a failure
    assert ask.main(["fx", "--db", str(db)]) == 0
    assert "0 new, 1 already held" in capsys.readouterr().out

    with FxLog(db) as log:
        assert log.rate_on("USD", date(2026, 8, 29)) == Decimal("4.1520")
        assert log.rate_on("JPY", date(2026, 8, 29)) is None, "only what the book touches"


def test_fx_show_reads_the_log_back(tmp_path, capsys):
    import ask

    db = tmp_path / "fx.db"
    with FxLog(db) as log:
        log.record("USD", DAY, Decimal("4.0550"))
    assert ask.main(["fx", "--show", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "2026-09-03  1 USD = MYR 4.0550" in out
    assert "1 rates" in out


def test_a_source_that_fails_exits_three_and_says_why(tmp_path, capsys, monkeypatch):
    """Same posture as a failed sweep: a silence here would later read as a day
    the rate did not move."""
    import ask
    import core.market.fx as fx

    def _boom():
        raise fx.FxFeedError("BNM fetch failed: refused")

    monkeypatch.setattr(
        fx, "BnmFxFeed", lambda: type("F", (), {"fetch_rates": staticmethod(_boom)})()
    )
    assert ask.main(["fx", "--db", str(tmp_path / "fx.db")]) == 3
    assert "BNM fetch failed" in capsys.readouterr().err


def test_a_failed_fetch_writes_nothing_at_all(tmp_path, monkeypatch):
    """A partial write would be worse than none: a rate row that is really an
    error is indistinguishable from a rate later on."""
    import ask
    import core.market.fx as fx

    def _boom():
        raise fx.FxFeedError("refused")

    monkeypatch.setattr(
        fx, "BnmFxFeed", lambda: type("F", (), {"fetch_rates": staticmethod(_boom)})()
    )
    db = tmp_path / "fx.db"
    ask.main(["fx", "--db", str(db)])
    with FxLog(db) as log:
        assert log.counts()["rates"] == 0
