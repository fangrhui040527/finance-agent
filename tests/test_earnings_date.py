"""Which earnings date to believe: EDGAR's 8-K acceptance, then the vendors, and
"unconfirmed" with every claim when the top two disagree (gap 6, 2026-10-28)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from knowledge.digest import build_digest
from knowledge.earnings_date import earnings_dates
from knowledge.facts import EventRecord, FactBook
from knowledge.report import fact_snapshot
from knowledge.sources.edgar import EdgarFilings, acceptance_time
from tests.conftest import FakeResponse

NOW = datetime(2026, 10, 27, 22, 30, tzinfo=UTC)
SEEN = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def day(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


def finnhub(iid, when, hour="amc", seen=SEEN):
    return EventRecord(
        "finnhub",
        f"{iid}:earnings:{when.date()}",
        iid,
        "earnings_result",
        announced_at=seen,
        effective_at=when,
        title="Q3 2026 results after the close",
        payload={"hour": hour},
    )


def fmp(iid, when, seen=SEEN):
    return EventRecord(
        "fmp",
        f"{iid}:earnings:{when.date()}",
        iid,
        "earnings_result",
        announced_at=seen,
        effective_at=when,
        title="results",
    )


def edgar(iid, accepted):
    return EventRecord(
        "edgar",
        f"0000320193-26-000100:2.02:{accepted.isoformat()}",
        iid,
        "results_released",
        announced_at=accepted,
        title="results released: 8-K accepted",
    )


def book_with(tmp_path, *rows):
    book = FactBook(tmp_path / "f.db")
    book.add_events(rows, fetched_at=SEEN)
    return book


def resolve(book, iid):
    (d,) = earnings_dates(book, iid, NOW - timedelta(days=7), NOW + timedelta(days=60))
    return d


# --- the timezone of an acceptance ---------------------------------------------------


def test_an_acceptance_time_is_new_york_wall_clock_whatever_its_z_says():
    # Summer: EDT, UTC-4. A 4:05pm New York release is 20:05Z, not 16:05Z.
    assert acceptance_time("2026-07-30T16:05:12.000Z") == datetime(
        2026, 7, 30, 20, 5, 12, tzinfo=UTC
    )
    # Winter: EST, UTC-5.
    assert acceptance_time("2026-01-29T16:30:00.000Z") == datetime(2026, 1, 29, 21, 30, tzinfo=UTC)
    # An explicit offset is taken as given.
    assert acceptance_time("2026-07-30T16:05:12-04:00") == datetime(
        2026, 7, 30, 20, 5, 12, tzinfo=UTC
    )
    assert acceptance_time("") is None and acceptance_time(None) is None
    assert acceptance_time("not a time") is None and acceptance_time("Z") is None


# --- the collector stores the release ------------------------------------------------

SUBMISSIONS = {
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000101", "0000320193-26-000100", "x-3", "x-4"],
            "filingDate": ["2026-10-30", "2026-10-29", "2026-10-29", "2026-10-28"],
            "reportDate": ["2026-10-29", "2026-10-29", "2026-10-29", "2026-10-27"],
            "acceptanceDateTime": [
                "2026-10-30T09:00:00.000Z",
                "2026-10-29T16:30:41.000Z",
                "2026-10-29T17:00:00.000Z",
                "2026-10-28T08:00:00.000Z",
            ],
            "form": ["8-K/A", "8-K", "8-K", "8-K"],
            "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
            "primaryDocDescription": ["8-K/A", "8-K", "8-K", "8-K"],
            "items": ["2.02,9.01", "2.02,9.01", "5.02", "7.01,2.020"],
        }
    }
}


def test_edgar_stores_an_item_2_02_8k_as_a_release_known_at_its_acceptance():
    def opener(req, timeout=None):
        return FakeResponse(json.dumps(SUBMISSIONS))

    pull = EdgarFilings(clock=lambda: NOW, opener=opener).collect(day(2026, 10, 20), ("XNAS:AAPL",))
    released = [e for e in pull.events if e.kind == "results_released"]
    # Only the 8-K naming item 2.02: not the amendment, not 5.02, not "2.020".
    (r,) = released
    assert r.source == "edgar" and r.event_id == "0000320193-26-000100:2.02"
    assert r.announced_at == datetime(2026, 10, 29, 20, 30, 41, tzinfo=UTC)
    assert r.effective_at is None
    assert r.title == "results released: 8-K accepted 2026-10-29 16:30 ET"
    assert r.payload["url"].endswith("/000032019326000100/b.htm")
    # The filing row itself is unchanged.
    assert sum(e.kind == "filing" for e in pull.events) == 4


# --- resolution ----------------------------------------------------------------------


def test_msft_shaped_a_single_source_is_shown_plainly(tmp_path):
    with book_with(tmp_path, finnhub("XNAS:MSFT", day(2026, 10, 28))) as book:
        d = resolve(book, "XNAS:MSFT")
        assert d.confirmed and d.day == day(2026, 10, 28).date()
        assert d.label() == "2026-10-28 (finnhub)"


def test_msft_shaped_vendors_that_agree_resolve_to_their_date(tmp_path):
    with book_with(
        tmp_path, finnhub("XNAS:MSFT", day(2026, 10, 28)), fmp("XNAS:MSFT", day(2026, 10, 28))
    ) as book:
        assert resolve(book, "XNAS:MSFT").label() == "2026-10-28 (finnhub, fmp)"


def test_aapl_shaped_vendors_a_day_apart_are_unconfirmed_without_an_8k(tmp_path):
    with book_with(
        tmp_path, finnhub("XNAS:AAPL", day(2026, 10, 29)), fmp("XNAS:AAPL", day(2026, 10, 28))
    ) as book:
        d = resolve(book, "XNAS:AAPL")
        assert not d.confirmed and d.day is None
        assert d.label() == "unconfirmed (2026-10-29 finnhub / 2026-10-28 fmp)"


def test_a_one_day_gap_explained_by_an_after_close_listing_agrees(tmp_path):
    # Finnhub: after the close on 10-28. FMP dates it by the session it moves.
    with book_with(
        tmp_path, finnhub("XNAS:MSFT", day(2026, 10, 28)), fmp("XNAS:MSFT", day(2026, 10, 29))
    ) as book:
        assert resolve(book, "XNAS:MSFT").label() == "2026-10-28 (finnhub, fmp)"


def test_aapl_shaped_an_item_2_02_8k_resolves_to_its_date(tmp_path):
    accepted = acceptance_time("2026-11-02T16:30:41.000Z")
    assert accepted is not None
    with book_with(
        tmp_path,
        finnhub("XNAS:AAPL", day(2026, 10, 29)),
        fmp("XNAS:AAPL", day(2026, 10, 28)),
        edgar("XNAS:AAPL", accepted),
    ) as book:
        d = resolve(book, "XNAS:AAPL")
        assert d.released and d.confirmed and d.day == day(2026, 11, 2).date()
        assert d.label() == "2026-11-02 (edgar 8-K item 2.02, accepted 16:30 ET)"
        assert [c.source for c in d.claims] == ["edgar", "finnhub", "fmp"]


def test_a_late_evening_acceptance_keeps_its_new_york_date(tmp_path):
    # 20:15 New York is 00:15Z the next day; the release is still the 29th's.
    accepted = acceptance_time("2026-10-29T20:15:00.000Z")
    assert accepted is not None and accepted.date() == day(2026, 10, 30).date()
    with book_with(tmp_path, edgar("XNAS:AAPL", accepted)) as book:
        assert resolve(book, "XNAS:AAPL").day == day(2026, 10, 29).date()


def test_a_moved_calendar_date_counts_only_where_it_was_listed_last(tmp_path):
    with book_with(
        tmp_path,
        finnhub("XNAS:AAPL", day(2026, 10, 22), seen=SEEN - timedelta(days=10)),
        finnhub("XNAS:AAPL", day(2026, 10, 29), seen=SEEN),
        fmp("XNAS:AAPL", day(2026, 10, 29)),
    ) as book:
        assert resolve(book, "XNAS:AAPL").label() == "2026-10-29 (finnhub, fmp)"


def test_releases_a_quarter_apart_are_separate(tmp_path):
    with book_with(
        tmp_path,
        edgar("XNAS:MSFT", datetime(2026, 7, 29, 20, 5, tzinfo=UTC)),
        finnhub("XNAS:MSFT", day(2026, 10, 28)),
    ) as book:
        found = earnings_dates(book, "XNAS:MSFT", day(2026, 7, 1), day(2026, 11, 30))
        assert [d.label() for d in found] == [
            "2026-07-29 (edgar 8-K item 2.02, accepted 16:05 ET)",
            "2026-10-28 (finnhub)",
        ]


# --- printed where the date is read ---------------------------------------------------


def test_the_fact_snapshot_prints_the_resolved_date_or_unconfirmed(tmp_path):
    with book_with(
        tmp_path, finnhub("XNAS:AAPL", day(2026, 10, 29)), fmp("XNAS:AAPL", day(2026, 10, 28))
    ) as book:
        text = fact_snapshot(book, "XNAS:AAPL", now=NOW, days=7)
    assert "earnings date (edgar 8-K > company > finnhub > fmp > news)" in text
    assert "expected  unconfirmed (2026-10-29 finnhub / 2026-10-28 fmp)" in text


@dataclass
class Cfg:
    watchlist: tuple = ("XNAS:AAPL",)
    holdings: tuple = ()
    corpus_db: str = ":memory:"
    facts_db: str = ":memory:"


def test_the_digest_prints_the_resolved_date_beside_the_vendor_rows(tmp_path):
    facts_db = str(tmp_path / "f.db")
    with book_with(
        tmp_path, finnhub("XNAS:AAPL", day(2026, 10, 29)), fmp("XNAS:AAPL", day(2026, 10, 28))
    ):
        pass
    d = build_digest(
        Cfg(), NOW.date(), corpus_path=str(tmp_path / "c.db"), facts_path=facts_db, now=NOW
    )
    (aapl,) = d.names
    assert aapl.earnings == ["unconfirmed (2026-10-29 finnhub / 2026-10-28 fmp)"]
    assert "- earnings date: unconfirmed (2026-10-29 finnhub / 2026-10-28 fmp)" in (d.to_markdown())
