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


def test_a_forward_figure_keeps_its_label_through_the_store_and_the_bridge(book):
    """Consensus for FY27 is knowable today and describes a period that ends
    in a year. The label rides in payload_json, so the schema is the one every
    copy already has, and comes back as a field, so the payload is the
    vendor's extras again."""
    book.add_observations(
        [
            Observation(
                "fmp",
                "XNAS:AAPL",
                "est_eps",
                D,
                Decimal("9.538"),
                period_end=date(2027, 9, 27),
                currency="USD",
                payload={"analysts": 30},
                forward=True,
            )
        ]
    )
    (o,) = book.observations("XNAS:AAPL", "est_eps")
    assert o.forward and o.known_at == D and o.payload == {"analysts": 30}
    (raw,) = book.conn.execute("SELECT payload_json FROM observations").fetchone()
    assert raw == '{"analysts": 30, "forward": true}'
    store = book.as_fact_store(["XNAS:AAPL"])
    fact = store.as_known_at("XNAS:AAPL", "est_eps", D)
    assert fact is not None and fact.forward and fact.period_end == date(2027, 9, 27)
    assert store.as_known_at("XNAS:AAPL", "est_eps", D - timedelta(days=1)) is None


def test_the_bridge_refuses_a_reported_figure_stamped_before_its_period_ended(book):
    """Nothing is inferred from the dates. A row knowable before its period
    ended that does not say forward is a collector stamping a reported figure
    wrongly, and the guard refusing it is the point of the guard."""
    book.add_observations([obs("4200", period=date(2026, 12, 31), known=D)])
    with pytest.raises(ValueError, match="reported figure cannot be public before"):
        book.as_fact_store(["XNAS:AAPL"])


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


# --- a derived row is knowable no earlier than its inputs -------------------------
#
# Each test fails on the reads before 2026-10-17, which windowed every row on its
# stored known_at. Six fourth quarters stored before 2026-10-08 carry the 10-K's
# date although a quarter they were computed from was filed later, and the store
# is append-only, so the read is where the stamp is corrected.

MSFT = "XNAS:MSFT"
LEGACY_RUN = datetime(2026, 9, 6, 12, 48, tzinfo=UTC)
Q4 = {"derived": "FY - Q1..Q3", "fp": "Q4", "form": "10-K", "tag": "Depreciation"}


def xbrl(concept: str, period: date, known: date, value: str, **payload) -> Observation:
    return Observation(
        "sec_xbrl",
        MSFT,
        concept,
        known,
        Decimal(value),
        period_end=period,
        unit="USD",
        currency="USD",
        payload={"tag": "Depreciation", **payload},
    )


def test_a_legacy_fourth_quarter_is_knowable_when_its_last_quarter_was_filed(book):
    # MSFT's FY25 depreciation as the 2026-09-06 run stored it: 22.0B for the year in
    # the 10-K of 2025-07-30, its three quarters filed only as FY26 comparatives, and
    # the Q4 of 6.3B stamped with the 10-K's date and naming no inputs.
    q4_end = date(2025, 6, 30)
    book.add_observations(
        [
            xbrl("depreciation_fy", q4_end, date(2025, 7, 30), "22000"),
            xbrl("depreciation", date(2024, 9, 30), date(2025, 10, 29), "4700"),
            xbrl("depreciation", date(2024, 12, 31), date(2026, 1, 28), "5200"),
            xbrl("depreciation", date(2025, 3, 31), date(2026, 4, 29), "5800"),
            xbrl("depreciation", q4_end, date(2025, 7, 30), "6300", **Q4),
        ],
        fetched_at=LEGACY_RUN,
    )
    assert book.latest(MSFT, "depreciation", asof=date(2025, 8, 15)) is None, (
        "nothing public on 2025-08-15 implied 6.3B"
    )
    assert [o.value for o in book.observations(MSFT, asof=date(2026, 4, 28))] == [
        Decimal(22000),
        Decimal(5200),
        Decimal(4700),
    ]
    row = book.latest(MSFT, "depreciation", asof=date(2026, 4, 29))
    assert row is not None and row.value == Decimal(6300) and row.known_at == date(2026, 4, 29)

    store = book.as_fact_store([MSFT])
    assert store.as_known_at(MSFT, "depreciation", date(2026, 4, 28), q4_end) is None
    fact = store.as_known_at(MSFT, "depreciation", date(2026, 4, 29), q4_end)
    assert fact is not None and fact.known_at == date(2026, 4, 29)
    early = book.as_fact_store([MSFT], asof=date(2026, 4, 28))
    assert early.as_known_at(MSFT, "depreciation", date(2026, 10, 17), q4_end) is None

    (stored,) = book.conn.execute(
        "SELECT known_at FROM observations WHERE concept = 'depreciation' AND period_end = ?",
        (q4_end.isoformat(),),
    ).fetchone()
    assert stored == "2025-07-30", "the row on disk is untouched; only the read moves"


def test_a_legacy_fourth_quarter_keeps_the_10k_date_when_its_quarters_came_first(book):
    # 303 of the 309 legacy rows: every quarter was filed before the 10-K, so the
    # 10-K's date was right. A quarter restated in a later run was not what that
    # run read, and must not move the row either.
    q4_end = date(2024, 6, 30)
    book.add_observations(
        [
            xbrl("depreciation_fy", q4_end, date(2024, 7, 30), "20000"),
            xbrl("depreciation", date(2023, 9, 30), date(2023, 10, 24), "4500"),
            xbrl("depreciation", date(2023, 12, 31), date(2024, 1, 30), "5000"),
            xbrl("depreciation", date(2024, 3, 31), date(2024, 4, 25), "5100"),
            xbrl("depreciation", q4_end, date(2024, 7, 30), "5400", **Q4),
        ],
        fetched_at=LEGACY_RUN,
    )
    book.add_observations(
        [xbrl("depreciation", date(2023, 12, 31), date(2026, 10, 1), "4900")],
        fetched_at=LEGACY_RUN + timedelta(days=35),
    )
    row = book.latest(MSFT, "depreciation", asof=date(2024, 7, 30))
    assert row is not None and row.value == Decimal(5400) and row.known_at == date(2024, 7, 30)
    fact = book.as_fact_store([MSFT]).as_known_at(MSFT, "depreciation", date(2024, 7, 30), q4_end)
    assert fact is not None and fact.value == Decimal(5400)


def test_a_derived_row_that_names_its_inputs_is_never_knowable_before_them(book):
    inputs = [
        {"span": "6M", "end": "2024-12-31", "value": "250", "filed": "2025-01-29"},
        {"span": "3M", "end": "2024-09-30", "value": "100", "filed": "2025-10-29"},
    ]
    book.add_observations(
        [
            xbrl(
                "cash_from_operations",
                date(2024, 12, 31),
                date(2025, 1, 29),
                "150",
                derived="6M - 3M",
                inputs=inputs,
            )
        ]
    )
    assert book.latest(MSFT, "cash_from_operations", asof=date(2025, 10, 28)) is None
    row = book.latest(MSFT, "cash_from_operations")
    assert row is not None and row.known_at == date(2025, 10, 29)


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


def test_a_limit_is_not_spent_on_brokers_restating_their_opinion(book):
    """NVIDIA's 30-day page on 2026-09-06: 28 rows of "Rosenblatt maintain Buy"
    and 2 rows of what the company actually did. Ratings arrive by the dozen
    and an 8-K arrives once, so any read with a limit shows the brokers and
    loses the record."""
    filed = EventRecord(
        "edgar", "8k", "XNAS:NVDA", "filing", NOW - timedelta(days=20), "8-K: items 8.01"
    )
    book.add_events(
        [filed]
        + [
            EventRecord(
                "fmp",
                f"grade-{i}",
                "XNAS:NVDA",
                "rating_reiteration",
                NOW - timedelta(days=i),
                f"House {i} maintain Buy",
            )
            for i in range(1, 15)
        ]
    )
    uncapped = book.events("XNAS:NVDA", limit=5)
    assert filed not in uncapped, "the case this exists for"

    capped = book.events("XNAS:NVDA", limit=5, opinions=2)
    assert filed in capped
    assert sum(1 for e in capped if e.kind == "rating_reiteration") == 2
    # Still newest first, as the store promises.
    assert [e.announced_at for e in capped] == sorted(
        (e.announced_at for e in capped), reverse=True
    )


def test_opinions_zero_leaves_only_what_the_company_did(book):
    book.add_events(
        [
            EventRecord("edgar", "8k", "XNAS:NVDA", "filing", NOW, "8-K"),
            EventRecord("fmp", "g", "XNAS:NVDA", "rating_change", NOW, "House upgrade Buy"),
        ]
    )
    assert [e.kind for e in book.events("XNAS:NVDA", opinions=0)] == ["filing"]


def test_the_store_itself_still_keeps_every_opinion(book):
    """The cap is a READER's policy. Nothing a source said is dropped - that
    would be the store deciding what the record is."""
    book.add_events(
        [
            EventRecord("fmp", f"g{i}", "XNAS:NVDA", "rating_reiteration", NOW, f"House {i}")
            for i in range(6)
        ]
    )
    assert len(book.events("XNAS:NVDA")) == 6


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
