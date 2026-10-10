"""Vendor facts are dated by the day they became knowable, and the newest reading wins.

What now holds:

  * a filing or an insider trade is windowed on the day it was FILED - a
    Form 4 filed today is in today's window, and an as-of read never sees it
    before it was made - for new rows and for rows stored with the report or
    trade date in `effective_at`, which are read the same way, unrewritten;
  * a day's Alpha Vantage sentiment and article count are over the whole day
    as far as each pull saw it, so a later pull that catches the day's last
    two stories never replaces 28 articles with 2;
  * a vendor snapshot (a price target, a beta, a market cap) is dated by the
    day it was read, so a value that returns to an earlier one is stored and
    read as the newest - by latest(), the fact page, the digest and the cost
    of capital - and never reaches A1 as a reported figure;
  * a FRED release, which has a day and no time, is listed under "releases,
    next 7 days" all through its own day.

Each test fails on the code before 2026-10-10:

  * sources-1 - EDGAR and Finnhub put the report or trade date in
    `effective_at` and `FactBook.events` windowed on it: today's filings never
    reached "recent" and `until=` reads saw them days early;
  * sources-3 - each Alpha Vantage pull aggregated only the stories since its
    watermark, and the partial tail of a day superseded the full day;
  * sources-4 - snapshots had no period_end, so A -> B -> A dropped the second
    A as a duplicate and latest() stayed on B;
  * sources-7 - FRED releases were stamped 00:00 UTC and left the list at
    20:00 ET the evening before they printed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from engines.fundamentals.ratios import Statements
from engines.valuation.cost_of_capital import derive, load_table
from knowledge.digest import _name_digest
from knowledge.facts import EventRecord, FactBook, Observation
from knowledge.report import fact_snapshot, macro_calendar
from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.base import Pull
from knowledge.sources.edgar import EdgarFilings
from knowledge.sources.finnhub import FinnhubCollector
from knowledge.sources.fmp import FmpCollector
from knowledge.sources.fred import FredCollector
from tests.conftest import FakeResponse


def _at(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def _router(routes: Mapping[str, object]):
    def open_(req, timeout=None):
        for needle, body in routes.items():
            if needle in req.full_url:
                return FakeResponse(body if isinstance(body, str) else json.dumps(body))
        raise AssertionError(f"unexpected request {req.full_url}")

    return open_


# --- sources-1: filings and insider trades, windowed on the day they were filed -------------

DIGEST_NOW = _at(2026, 10, 5, 22)
DIGEST_SINCE = DIGEST_NOW - timedelta(days=2)  # digest.py's "last 2 days"
BEFORE_FILED = _at(2026, 10, 2, 23)

FORM4 = {
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000111"],
            "filingDate": ["2026-10-05"],
            "reportDate": ["2026-10-01"],
            "form": ["4"],
            "primaryDocument": ["xslF345X05/form4.xml"],
            "primaryDocDescription": ["FORM 4"],
            "items": [""],
        }
    }
}

FINNHUB_INSIDER = {
    "company-news": [],
    "insider-transactions": {
        "data": [
            {
                "name": "Ternus John",
                "share": 100000,
                "change": -3754,
                "filingDate": "2026-10-05",
                "transactionDate": "2026-10-02",
                "transactionCode": "S",
                "transactionPrice": 334.05,
            }
        ]
    },
    "calendar/earnings": {"earningsCalendar": []},
    "stock/earnings": [],
    "stock/recommendation": [],
    "stock/metric": {"metric": {}},
}


def test_a_form4_filed_today_is_in_todays_window_and_not_in_an_earlier_as_of_read():
    book = FactBook(":memory:")
    pull = EdgarFilings(clock=lambda: DIGEST_NOW, opener=_router({"CIK": FORM4})).collect(
        DIGEST_SINCE, ("XNAS:AAPL",)
    )
    (filing,) = pull.events
    assert filing.effective_at is None and filing.payload["report_date"] == "2026-10-01"
    book.add_events(pull.events)
    recent = book.events("XNAS:AAPL", since=DIGEST_SINCE, until=DIGEST_NOW, opinions=2)
    assert [e.event_id for e in recent] == ["0000320193-26-000111"]
    assert book.events("XNAS:AAPL", until=BEFORE_FILED) == []


def test_a_finnhub_insider_sale_is_windowed_on_its_filing_date():
    book = FactBook(":memory:")
    pull = FinnhubCollector(
        key="k", clock=lambda: DIGEST_NOW, opener=_router(FINNHUB_INSIDER)
    ).collect(DIGEST_SINCE, ("XNAS:AAPL",))
    (sale,) = pull.events
    assert sale.effective_at is None and sale.payload["trade_date"] == "2026-10-02"
    book.add_events(pull.events)
    assert len(book.events("XNAS:AAPL", since=DIGEST_SINCE, until=DIGEST_NOW)) == 1
    assert book.events("XNAS:AAPL", until=BEFORE_FILED) == []


def test_rows_stored_with_the_report_or_trade_date_are_read_on_the_filing_date():
    """The 180-odd rows written before the fix keep their `effective_at` on
    disk; every read windows, orders and hands them back as new rows are."""
    book = FactBook(":memory:")
    book.add_events(
        [
            EventRecord(
                "edgar",
                "legacy-4",
                "XNAS:AAPL",
                "insider_filing",
                _at(2026, 10, 5),
                "4: FORM 4",
                effective_at=_at(2026, 10, 1),
            ),
            EventRecord(
                "finnhub",
                "legacy-sale",
                "XNAS:AAPL",
                "insider_sell",
                _at(2026, 10, 5),
                "Ternus John sold 3,754 shares at 334.05",
                effective_at=_at(2026, 10, 2),
            ),
            EventRecord(
                "edgar",
                "legacy-10q",
                "XNAS:AAPL",
                "filing",
                _at(2026, 8, 1),
                "10-Q",
                effective_at=_at(2026, 6, 28),
            ),
            # A scheduled date is still what a result is windowed on.
            EventRecord(
                "finnhub",
                "AAPL:earnings:2026-10-29",
                "XNAS:AAPL",
                "earnings_result",
                DIGEST_NOW,
                "results",
                effective_at=_at(2026, 10, 29),
            ),
        ]
    )
    recent = book.events("XNAS:AAPL", since=DIGEST_SINCE, until=DIGEST_NOW, opinions=2)
    assert {e.event_id for e in recent} == {"legacy-4", "legacy-sale"}
    assert all(e.effective_at is None for e in recent)
    by_id = {e.event_id: e for e in recent}
    assert by_id["legacy-4"].payload["report_date"] == "2026-10-01"
    assert by_id["legacy-sale"].payload["trade_date"] == "2026-10-02"
    assert [e.event_id for e in book.events("XNAS:AAPL", until=BEFORE_FILED)] == ["legacy-10q"]
    assert [e.event_id for e in book.events("XNAS:AAPL", until=_at(2026, 7, 31))] == []
    # The 10-Q was "recent" on the day it was filed, not on its quarter end.
    assert [
        e.event_id for e in book.events("XNAS:AAPL", since=_at(2026, 7, 31), until=_at(2026, 8, 2))
    ] == ["legacy-10q"]
    (ahead,) = book.events("XNAS:AAPL", since=DIGEST_NOW, until=DIGEST_NOW + timedelta(days=30))
    assert ahead.effective_at == _at(2026, 10, 29)


# --- sources-3: a day's vendor sentiment over the whole day, not a pull's tail -----------------


def _story(n: int, when: datetime, score: str) -> dict:
    return {
        "title": f"story {n}",
        "url": f"https://example.com/av/{n}",
        "time_published": when.strftime("%Y%m%dT%H%M%S"),
        "summary": "",
        "source_domain": "example.com",
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.5", "ticker_sentiment_score": score}
        ],
    }


#: 28 stories on 2026-10-01 before the first pull at 22:00, 2 after it.
DAY = [_story(i, _at(2026, 10, 1, 1) + timedelta(minutes=40 * i), "0.5") for i in range(28)] + [
    _story(100, _at(2026, 10, 1, 22, 30), "-0.3"),
    _story(101, _at(2026, 10, 1, 23, 10), "-0.3"),
]


def _vendor(now: datetime):
    """NEWS_SENTIMENT as the vendor answers it: stories from `time_from` up to now."""

    def open_(req, timeout=None):
        q = parse_qs(urlparse(req.full_url).query)
        start = datetime.strptime(q["time_from"][0], "%Y%m%dT%H%M").replace(tzinfo=UTC)
        feed = [
            s
            for s in DAY
            if start
            <= datetime.strptime(s["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            <= now
        ]
        return FakeResponse(json.dumps({"feed": feed[::-1]}))

    return open_


def test_a_later_pull_that_sees_a_days_last_two_stories_does_not_replace_its_28():
    book = FactBook(":memory:")
    first_at, second_at = _at(2026, 10, 1, 22), _at(2026, 10, 3, 22)
    first = AlphaVantageNews(key="k", clock=lambda: first_at, opener=_vendor(first_at)).collect(
        _at(2026, 9, 30, 22), ("XNAS:AAPL",)
    )
    book.add_observations(first.observations)
    full = book.latest("XNAS:AAPL", "av_news_count")
    assert full is not None and full.value == 28
    # The next pull resumes from the first one's watermark, mid-day.
    second = AlphaVantageNews(key="k", clock=lambda: second_at, opener=_vendor(second_at)).collect(
        first_at, ("XNAS:AAPL",)
    )
    assert len(second.articles) == 2, "only the stories since the watermark are returned"
    book.add_observations(second.observations)
    count = book.latest("XNAS:AAPL", "av_news_count", asof=second_at.date())
    sent = book.latest("XNAS:AAPL", "av_news_sentiment", asof=second_at.date())
    assert count is not None and count.period_end == date(2026, 10, 1)
    assert count.value == 30, "the whole day, not the 2-story tail"
    assert sent is not None and sent.value == Decimal("0.4467")  # (28 x 0.5 - 2 x 0.3) / 30


def test_a_reply_cut_at_its_limit_does_not_aggregate_its_oldest_day(monkeypatch):
    monkeypatch.setattr(AlphaVantageNews, "LIMIT", 5)
    now = _at(2026, 10, 2, 22)

    def open_(req, timeout=None):
        return FakeResponse(json.dumps({"feed": DAY[-5:][::-1]}))

    pull = AlphaVantageNews(key="k", clock=lambda: now, opener=open_).collect(
        _at(2026, 10, 1, 12), ("XNAS:AAPL",)
    )
    assert not [o for o in pull.observations if o.period_end == date(2026, 10, 1)]
    assert any("not aggregated" in n for n in pull.notes)


# --- sources-4: a snapshot value that comes back is the newest ---------------------------------


def _targets(day: date, low: int) -> Pull:
    reply = [{"targetConsensus": 520, "targetMedian": 515, "targetHigh": 600, "targetLow": low}]
    c = FmpCollector(
        key="k",
        clock=lambda: datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        opener=_router({"price-target-consensus": reply}),
    )
    pull = Pull()
    c._targets(pull, "XNAS:MSFT", "MSFT", _at(2026, 9, 1), "k")
    return pull


def _metrics(day: date, beta: str, cap: str) -> Pull:
    c = FinnhubCollector(
        key="k",
        clock=lambda: datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        opener=_router({"stock/metric": {"metric": {"beta": beta, "marketCapitalization": cap}}}),
    )
    pull = Pull()
    c._metrics(pull, "XNAS:MSFT", "MSFT", _at(2026, 9, 1), "k")
    return pull


DAYS = (date(2026, 9, 5), date(2026, 9, 23), date(2026, 10, 6))


def _reverting_book() -> FactBook:
    book = FactBook(":memory:")
    # A row stored before the fix: no period_end. Every dated reading outranks it.
    book.add_observations(
        [
            Observation(
                "fmp",
                "XNAS:MSFT",
                "price_target_low",
                known_at=date(2026, 9, 1),
                value=Decimal(500),
            )
        ]
    )
    for day, low, beta, cap in zip(
        DAYS, (490, 440, 490), ("0.90", "1.10", "0.90"), ("3000000", "3100000", "3000000")
    ):
        book.add_observations(_targets(day, low).observations)
        book.add_observations(_metrics(day, beta, cap).observations)
    return book


def test_a_target_that_returns_to_an_earlier_value_is_the_latest_and_never_a_reported_fact():
    book = _reverting_book()
    low = book.latest("XNAS:MSFT", "price_target_low", asof=DAYS[2])
    assert low is not None and low.value == 490 and low.known_at == DAYS[2] and low.snapshot
    mid = book.latest("XNAS:MSFT", "price_target_low", asof=DAYS[1])
    assert mid is not None and mid.value == 440
    legacy = book.latest("XNAS:MSFT", "price_target_low", asof=date(2026, 9, 2))
    assert legacy is not None and legacy.value == 500
    store = book.as_fact_store(["XNAS:MSFT"])
    assert store.as_known_at("XNAS:MSFT", "price_target_low", DAYS[2]) is None
    assert store.as_known_at("XNAS:MSFT", "beta", DAYS[2]) is None


def test_the_readers_of_snapshots_get_the_newest_value():
    book = _reverting_book()
    page = fact_snapshot(book, "XNAS:MSFT", now=_at(2026, 10, 6, 12))
    line = next(x for x in page.splitlines() if "price_target_low" in x)
    assert " 490 " in line and "knowable 2026-10-06" in line and " for " not in line
    beta_line = next(x for x in page.splitlines() if x.strip().startswith("beta "))
    assert "0.9" in beta_line and "knowable 2026-10-06" in beta_line

    nd = _name_digest("XNAS:MSFT", [], book, _at(2026, 10, 6, 12), None, None, 8, set())
    snap = {s["concept"]: s for s in nd.snapshot}
    assert snap["beta"]["value"] == "0.90"
    assert snap["beta"]["known_at"] == "2026-10-06" and snap["beta"]["period_end"] is None

    coc = derive("XNAS:MSFT", book, load_table(), DAYS[2])
    assert coc.beta == Decimal("0.90") and "known 2026-10-06" in coc.beta_source
    assert coc.e_value == Decimal("3000000") * Decimal(1_000_000)

    from engines.analysis.workup import _market_cap

    s = Statements.from_store(book.as_fact_store(["XNAS:MSFT"], DAYS[2]), "XNAS:MSFT", DAYS[2])
    cap, why = _market_cap(book, s, "XNAS:MSFT", DAYS[2])
    assert cap == Decimal("3000000") * Decimal(1_000_000) and "known 2026-10-06" in why


# --- sources-7: a FRED release is upcoming all through its own day -----------------------------

PREOPEN = _at(2026, 10, 2, 12, 30)  # us_preopen, an hour before the 08:30 ET payrolls print

FRED_RELEASES = {
    "releases/dates": {
        "release_dates": [
            {"release_id": 50, "release_name": "Employment Situation", "date": "2026-10-02"}
        ]
    }
}


def test_a_fred_release_is_listed_on_the_morning_it_prints():
    book = FactBook(":memory:")
    c = FredCollector(
        series={}, key="k", clock=lambda: _at(2026, 10, 1, 12), opener=_router(FRED_RELEASES)
    )
    pull = Pull()
    c._release_dates(pull, "k")
    (release,) = pull.events
    assert release.announced_at.date() == date(2026, 10, 2)
    book.add_events(pull.events)
    listed = "\n".join(macro_calendar(book, PREOPEN))
    assert "Employment Situation" in listed and "10-02" in listed
    assert "Employment Situation" not in "\n".join(macro_calendar(book, _at(2026, 10, 3, 0, 1)))


def test_a_release_stored_at_midnight_utc_is_still_listed_that_day_and_a_timed_one_is_not():
    book = FactBook(":memory:")
    book.add_events(
        [
            EventRecord(
                "fred",
                "fred:50:2026-10-02",
                "MACRO:US",
                "macro_release",
                _at(2026, 10, 2),
                "Employment Situation",
                payload={"time": "not published by FRED"},
            ),
            # A calendar with real times (jin10): a print earlier today is past.
            EventRecord(
                "jin10_calendar",
                "x:scheduled",
                "MACRO:CN",
                "macro_release",
                _at(2026, 10, 2, 1, 30),
                "CN PMI",
            ),
        ]
    )
    listed = "\n".join(macro_calendar(book, PREOPEN))
    assert "Employment Situation" in listed
    assert "CN PMI" not in listed
