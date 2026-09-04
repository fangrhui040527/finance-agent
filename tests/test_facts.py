"""The structured store: append-only, vintaged, and a real FactStore for A1."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.facts import (
    FAILED,
    OK,
    Document,
    EventRecord,
    FactBook,
    Observation,
    SeriesPoint,
    as_decimal,
)

D = date(2026, 9, 1)
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def book(tmp_path):
    with FactBook(tmp_path / "facts.db") as b:
        yield b


def obs(value="4200", period=date(2026, 6, 30), known=D, concept="revenue"):
    return Observation(
        "fmp", "XNAS:AAPL", concept, known, Decimal(value), period_end=period, currency="USD"
    )


# --- append-only ------------------------------------------------------------------


def test_every_table_refuses_update_and_delete(book):
    book.add_observations([obs()])
    book.add_events([EventRecord("edgar", "e1", "XNAS:AAPL", "filing", NOW, "8-K")])
    book.add_series([SeriesPoint("fred", "DFF", D, Decimal("4.33"), D)])
    book.add_documents([Document("fmp", "t1", "XNAS:AAPL", "transcript", "Q3", "body", NOW)])
    book.record_pull("r1", "fmp", OK)
    for table in ("observations", "events", "series", "documents", "pulls"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            book.conn.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            book.conn.execute(f"UPDATE {table} SET source = 'x'")


def test_a_repeated_pull_stores_nothing_twice(book):
    first = book.add_observations([obs(), obs()])
    again = book.add_observations([obs()])
    assert (first.stored, first.duplicates) == (1, 1)
    assert (again.stored, again.duplicates) == (0, 1)


# --- point in time --------------------------------------------------------------


def test_a_restatement_is_a_new_row_and_the_old_figure_stays_knowable(book):
    book.add_observations([obs("4200", known=date(2026, 8, 1))])
    book.add_observations([obs("4150", known=date(2026, 9, 1))])
    assert book.latest("XNAS:AAPL", "revenue", asof=date(2026, 8, 15)).value == Decimal("4200")
    assert book.latest("XNAS:AAPL", "revenue").value == Decimal("4150")


def test_the_fact_store_bridge_keeps_the_lookahead_guard(book):
    book.add_observations([obs(known=date(2026, 8, 1))])
    store = book.as_fact_store(["XNAS:AAPL"])
    assert store.as_known_at("XNAS:AAPL", "revenue", date(2026, 7, 15)) is None
    fact = store.as_known_at("XNAS:AAPL", "revenue", date(2026, 8, 1))
    assert fact is not None and fact.value == Decimal("4200") and fact.currency == "USD"


def test_a_snapshot_without_a_period_is_not_a_fact(book):
    book.add_observations([Observation("finnhub", "XNAS:AAPL", "pe_ttm", D, Decimal("31.2"))])
    assert book.observations("XNAS:AAPL", "pe_ttm")[0].value == Decimal("31.2")
    assert book.as_fact_store(["XNAS:AAPL"]).as_known_at("XNAS:AAPL", "pe_ttm", D) is None


def test_text_observations_survive_the_round_trip(book):
    book.add_observations(
        [Observation("finnhub", "XNAS:AAPL", "next_earnings_hour", D, text="amc")]
    )
    (o,) = book.observations("XNAS:AAPL", "next_earnings_hour")
    assert o.value is None and o.text == "amc"


# --- series are vintaged ----------------------------------------------------------


def test_series_returns_the_vintage_knowable_on_the_day_asked(book):
    book.add_series([SeriesPoint("fred", "CPIAUCSL", date(2026, 7, 1), Decimal("320.1"), D)])
    book.add_series(
        [SeriesPoint("fred", "CPIAUCSL", date(2026, 7, 1), Decimal("320.4"), date(2026, 10, 1))]
    )
    (as_first_published,) = book.series("CPIAUCSL", asof=date(2026, 9, 15))
    (revised,) = book.series("CPIAUCSL")
    assert as_first_published.value == Decimal("320.1")
    assert revised.value == Decimal("320.4")


def test_a_non_finite_series_point_is_dropped_at_the_seam(book):
    stats = book.add_series([SeriesPoint("fred", "DFF", D, Decimal("NaN"), D)])
    assert stats.stored == 0 and book.series("DFF") == []


def test_as_decimal_refuses_the_fred_missing_marker():
    assert as_decimal(".") is None and as_decimal("") is None and as_decimal(None) is None
    assert as_decimal("4.33") == Decimal("4.33")
    assert as_decimal("inf") is None


# --- events -------------------------------------------------------------------------


def test_events_are_windowed_on_the_effective_date_when_there_is_one(book):
    scheduled = EventRecord(
        "finnhub",
        "aapl-2026q3",
        "XNAS:AAPL",
        "earnings_result",
        announced_at=NOW - timedelta(days=30),
        effective_at=NOW + timedelta(days=10),
        title="Q3 results",
    )
    book.add_events([scheduled])
    assert book.events("XNAS:AAPL", since=NOW) == [scheduled]
    assert book.events("XNAS:AAPL", until=NOW) == []


# --- pulls --------------------------------------------------------------------------


def test_a_failed_pull_does_not_move_the_watermark(book):
    book.record_pull("r1", "fred", OK, at=NOW - timedelta(days=1))
    book.record_pull("r2", "fred", FAILED, at=NOW, detail="429")
    assert book.last_success("fred") == NOW - timedelta(days=1)
    assert book.counts()["failed_pulls"] == 1


def test_counts_cover_every_table(book):
    assert set(book.counts()) == {
        "observations",
        "events",
        "series_points",
        "series",
        "documents",
        "pulls",
        "failed_pulls",
    }
