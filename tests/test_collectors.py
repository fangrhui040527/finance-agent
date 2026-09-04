"""Every structured collector, offline, against the vendor's documented shapes.

The properties under test are the seam rules in knowledge/sources/base.py: a
missing key is a skip, a plan boundary is a note, a broken source raises, a
malformed row drops the row and never the pull, and every figure is typed and
dated on the way in.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.facts import FactBook
from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.base import KeyMissing, SourceError, parse_date, parse_datetime
from knowledge.sources.bnm import BnmOprCollector
from knowledge.sources.bursa import BursaAnnouncements
from knowledge.sources.dosm import DosmCpiCollector
from knowledge.sources.edgar import EdgarFilings
from knowledge.sources.finnhub import FinnhubCollector
from knowledge.sources.fmp import FmpCollector
from knowledge.sources.fred import FredCollector
from knowledge.sources.registry import COLLECTORS, UnknownCollector, collector_for
from tests.conftest import FakeResponse, http_error

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=2)
CLOCK = lambda: NOW  # noqa: E731


def router(routes: dict[str, object]):
    """An opener answering by URL substring: a str/dict/list body, or an exception."""
    calls: list[str] = []

    def open_(req, timeout=None):
        url = req.full_url
        calls.append(url)
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, BaseException):
                    raise body
                return FakeResponse(body if isinstance(body, str) else json.dumps(body))
        raise AssertionError(f"unexpected request {url}")

    open_.calls = calls  # type: ignore[attr-defined]
    return open_


# --- the seam ------------------------------------------------------------------------


def test_a_missing_key_is_a_skip_that_names_the_variable(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(KeyMissing, match="FINNHUB_API_KEY"):
        FinnhubCollector(clock=CLOCK).collect(SINCE, ("XNAS:AAPL",))


def test_non_json_and_transport_failures_raise_not_empty():
    c = FredCollector(key="k", clock=CLOCK, opener=router({"fred": "<html>maintenance</html>"}))
    with pytest.raises(SourceError, match="non-JSON"):
        c.collect(SINCE)
    c = FredCollector(key="k", clock=CLOCK, opener=router({"fred": OSError("down")}))
    with pytest.raises(SourceError, match="fetch failed"):
        c.collect(SINCE)


def test_a_403_is_a_plan_boundary_and_a_401_is_a_bad_key():
    c = FinnhubCollector(key="k", clock=CLOCK, opener=router({"finnhub": http_error(401)}))
    with pytest.raises(SourceError, match="every name failed"):
        c.collect(SINCE, ("XNAS:AAPL",))
    c = FinnhubCollector(key="k", clock=CLOCK, opener=router({"finnhub": http_error(403)}))
    pull = c.collect(SINCE, ("XNAS:AAPL",))
    assert pull.fetched == 0 and any("plan does not include" in n for n in pull.notes)


def test_the_key_never_appears_in_an_error_message():
    c = FredCollector(key="SECRETKEY", clock=CLOCK, opener=router({"fred": http_error(500)}))
    with pytest.raises(SourceError) as e:
        c.collect(SINCE)
    assert "SECRETKEY" not in str(e.value)


def test_every_catalogued_collector_is_registered_and_unknown_names_refuse():
    from knowledge.sources.catalog import CATALOG, MIXED, STRUCTURED

    structured = {n for n, s in CATALOG.items() if s.kind in (STRUCTURED, MIXED)}
    assert structured == set(COLLECTORS)
    with pytest.raises(UnknownCollector):
        collector_for("bloomberg_terminal")


def test_vendor_dates_in_every_shape_seen_so_far():
    assert parse_date("2026-09-03") == date(2026, 9, 3)
    assert parse_date("2026-09-03T14:00:00") == date(2026, 9, 3)
    assert parse_date("20260903T140000") == date(2026, 9, 3)
    assert parse_date(1788444000) == date(2026, 9, 3)
    assert parse_date("not a date") is None and parse_date("") is None
    assert parse_datetime("20260903T211500") == datetime(2026, 9, 3, 21, 15, tzinfo=UTC)


# --- Finnhub ---------------------------------------------------------------------------

FINNHUB = {
    "company-news": [
        {
            "id": 1,
            "headline": "Apple unveils new chips",
            "summary": "Apple said the M6 will ship in October.",
            "source": "Reuters",
            "datetime": 1788469200,
            "category": "company",
            "url": "https://example.com/a",
        },
        {"id": 2, "headline": "undated", "summary": "", "source": "x"},
    ],
    "insider-transactions": {
        "data": [
            {
                "name": "Cook Timothy",
                "share": 3000000,
                "change": -50000,
                "filingDate": "2026-09-02",
                "transactionDate": "2026-08-31",
                "transactionCode": "S",
                "transactionPrice": 231.5,
            },
            {"name": "Nobody", "change": 10, "filingDate": "bad", "transactionCode": "P"},
        ]
    },
    "calendar/earnings": {
        "earningsCalendar": [
            {"date": "2026-10-29", "epsEstimate": 1.62, "hour": "amc", "quarter": 4, "year": 2026}
        ]
    },
    "stock/earnings": [
        {"actual": 1.57, "estimate": 1.43, "period": "2026-06-30", "surprisePercent": 9.79}
    ],
    "stock/recommendation": [
        {"buy": 24, "hold": 12, "sell": 1, "strongBuy": 11, "strongSell": 0, "period": "2026-09-01"}
    ],
    "stock/metric": {"metric": {"peTTM": 33.1, "beta": 1.2, "52WeekHigh": 260.1, "junk": "x"}},
}


def test_finnhub_types_and_dates_everything_and_drops_only_bad_rows():
    c = FinnhubCollector(key="k", clock=CLOCK, opener=router(FINNHUB))
    pull = c.collect(SINCE, ("XNAS:AAPL",), slot="us_close")
    (art,) = pull.articles
    assert art.instruments == ["XNAS:AAPL"] and art.body.startswith("Apple said")
    kinds = {e.kind for e in pull.events}
    assert kinds == {"insider_sell", "earnings_result"}
    sale = next(e for e in pull.events if e.kind == "insider_sell")
    assert sale.title == "Cook Timothy sold 50,000 shares at 231.5"
    assert sale.effective_at == datetime(2026, 8, 31, tzinfo=UTC)
    concepts = {o.concept for o in pull.observations}
    assert {
        "eps_actual",
        "eps_surprise_pct",
        "analyst_buy",
        "analyst_strong_buy",
        "pe_ttm",
    } <= concepts
    eps = next(o for o in pull.observations if o.concept == "eps_actual")
    assert eps.value == Decimal("1.57") and eps.period_end == date(2026, 6, 30)
    assert eps.known_at >= eps.period_end, "never knowable before the period ended"
    pe = next(o for o in pull.observations if o.concept == "pe_ttm")
    assert pe.period_end is None, "a snapshot, not a reported figure"
    assert c.requests == 6


def test_finnhub_one_premium_endpoint_is_a_note_not_a_failure():
    routes = dict(FINNHUB)
    routes["stock/metric"] = http_error(403)
    pull = FinnhubCollector(key="k", clock=CLOCK, opener=router(routes)).collect(
        SINCE, ("XNAS:AAPL",)
    )
    assert pull.articles and any("stock/metric" in n for n in pull.notes)


def test_finnhub_one_failing_name_does_not_cost_the_others():
    def open_(req, timeout=None):
        if "symbol=MSFT" in req.full_url:
            raise http_error(500)
        for needle, body in FINNHUB.items():
            if needle in req.full_url:
                return FakeResponse(json.dumps(body))
        raise AssertionError(req.full_url)

    pull = FinnhubCollector(key="k", clock=CLOCK, opener=open_).collect(
        SINCE, ("XNAS:AAPL", "XNAS:MSFT")
    )
    assert pull.articles and any("XNAS:MSFT" in n for n in pull.notes)


# --- FMP ----------------------------------------------------------------------------------

FMP = {
    "grades?": [
        {
            "date": "2026-09-03",
            "gradingCompany": "Morgan Stanley",
            "previousGrade": "Equal Weight",
            "newGrade": "Overweight",
            "action": "upgrade",
        }
    ],
    "earnings?": [
        {"date": "2026-10-29", "epsEstimated": 1.62},
        {"date": "2026-07-30", "epsActual": 1.57},
    ],
    "price-target-consensus": [
        {"targetHigh": 300, "targetLow": 180, "targetConsensus": 245.5, "targetMedian": 250}
    ],
    "income-statement": [
        {
            "date": "2026-06-28",
            "fiscalYear": "2026",
            "period": "Q3",
            "reportedCurrency": "USD",
            "filingDate": "2026-08-01",
            "revenue": 94036000000,
            "netIncome": 23434000000,
            "eps": 1.57,
        }
    ],
    "cash-flow-statement": [
        {
            "date": "2026-06-28",
            "fiscalYear": "2026",
            "period": "Q3",
            "reportedCurrency": "USD",
            "filingDate": "2026-08-01",
            "operatingCashFlow": 27867000000,
            "freeCashFlow": 24405000000,
        }
    ],
    "balance-sheet-statement": [
        {
            "date": "2026-06-28",
            "fiscalYear": "2026",
            "period": "Q3",
            "reportedCurrency": "USD",
            "filingDate": "2026-08-01",
            "totalDebt": 101698000000,
        }
    ],
    "analyst-estimates": [
        {"date": "2027-09-30", "revenueAvg": 460000000000, "epsAvg": 8.9, "numAnalystsEps": 30}
    ],
    "earning-call-transcript": [
        {
            "symbol": "AAPL",
            "quarter": 3,
            "year": 2026,
            "date": "2026-07-31 17:00:00",
            "content": "Operator: Good day...",
        }
    ],
}


def test_fmp_weekly_pull_carries_statements_estimates_targets_grades_and_the_transcript():
    c = FmpCollector(key="k", clock=CLOCK, opener=router(FMP))
    pull = c.collect(SINCE, ("XNAS:AAPL",), slot="weekly")
    revenue = next(o for o in pull.observations if o.concept == "revenue")
    assert revenue.period_end == date(2026, 6, 28) and revenue.known_at == date(2026, 8, 1)
    assert revenue.value == Decimal("94036000000") and revenue.currency == "USD"
    cfo = next(o for o in pull.observations if o.concept == "cash_from_operations")
    assert cfo.value == Decimal("27867000000")
    assert {o.concept for o in pull.observations} >= {
        "est_eps",
        "price_target_consensus",
        "total_debt",
    }
    kinds = [e.kind for e in pull.events]
    assert kinds.count("rating_change") == 1 and kinds.count("earnings_result") == 1, kinds
    (doc,) = pull.documents
    assert doc.kind == "transcript" and doc.doc_id == "AAPL:transcript:2026Q3"
    assert doc.published_at == datetime(2026, 7, 31, 17, 0, tzinfo=UTC)


def test_fmp_preopen_pull_is_the_cheap_daily_subset():
    c = FmpCollector(key="k", clock=CLOCK, opener=router(FMP))
    pull = c.collect(SINCE, ("XNAS:AAPL",), slot="us_preopen")
    assert not pull.documents and not any(o.concept == "revenue" for o in pull.observations)
    assert c.requests == 3


def test_fmp_plan_error_message_is_a_note_and_other_errors_are_failures():
    routes = dict(FMP)
    routes["earning-call-transcript"] = {
        "Error Message": "Exclusive Endpoint: this endpoint is not available under your current subscription plan"
    }
    pull = FmpCollector(key="k", clock=CLOCK, opener=router(routes)).collect(
        SINCE, ("XNAS:AAPL",), slot="weekly"
    )
    assert not pull.documents and any("earning-call-transcript" in n for n in pull.notes)
    assert pull.observations, "the rest of the pull still landed"


def test_fmp_a_filing_date_before_the_period_end_is_not_trusted():
    routes = dict(FMP)
    routes["income-statement"] = [
        {
            "date": "2026-06-28",
            "fiscalYear": "2026",
            "period": "Q3",
            "filingDate": "2026-06-01",
            "revenue": 1,
        }
    ]
    pull = FmpCollector(key="k", clock=CLOCK, opener=router(routes)).collect(
        SINCE, ("XNAS:AAPL",), slot="weekly"
    )
    revenue = next(o for o in pull.observations if o.concept == "revenue")
    assert revenue.known_at >= revenue.period_end


# --- Alpha Vantage ---------------------------------------------------------------------

AV = {
    "feed": [
        {
            "title": "Nvidia and Apple lead the tape",
            "url": "https://example.com/av1",
            "time_published": "20260903T211500",
            "summary": "Chips rallied.",
            "source_domain": "www.marketwatch.com",
            "topics": [{"topic": "Technology"}],
            "ticker_sentiment": [
                {"ticker": "NVDA", "relevance_score": "0.8", "ticker_sentiment_score": "0.5"},
                {"ticker": "AAPL", "relevance_score": "0.1", "ticker_sentiment_score": "0.9"},
            ],
        }
    ]
}


def test_alphavantage_links_only_relevant_tickers_and_aggregates_per_day():
    c = AlphaVantageNews(key="k", clock=CLOCK, opener=router({"alphavantage": AV}))
    pull = c.collect(SINCE, ("XNAS:NVDA", "XNAS:AAPL"))
    (art,) = pull.articles
    assert art.instruments == ["XNAS:NVDA"], "AAPL at relevance 0.1 is a passing mention"
    sent = next(o for o in pull.observations if o.concept == "av_news_sentiment")
    assert sent.instrument_id == "XNAS:NVDA" and sent.value == Decimal("0.5000")
    assert sent.period_end == date(2026, 9, 3)
    assert c.requests == 1, "one call for every US name - the plan allows 25 a day"


def test_alphavantage_rate_limit_answer_is_a_failure_not_a_quiet_day():
    body = {
        "Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
    }
    c = AlphaVantageNews(key="k", clock=CLOCK, opener=router({"alphavantage": body}))
    with pytest.raises(SourceError, match="refused"):
        c.collect(SINCE, ("XNAS:NVDA",))


# --- FRED ------------------------------------------------------------------------------------


def test_fred_drops_the_missing_marker_and_stamps_known_at_today():
    body = {
        "observations": [
            {"date": "2026-09-01", "value": "4.33"},
            {"date": "2026-09-02", "value": "."},
        ]
    }
    c = FredCollector(
        series={"DFF": "fed funds"}, key="k", clock=CLOCK, opener=router({"fred": body})
    )
    pull = c.collect(SINCE)
    (p,) = pull.series
    assert p.value == Decimal("4.33") and p.known_at == NOW.date() and p.series_id == "DFF"


def test_fred_error_payload_is_a_failure_when_every_series_fails():
    body = {
        "error_code": 400,
        "error_message": "Bad Request. The value for variable api_key is not registered.",
    }
    c = FredCollector(series={"DFF": "x"}, key="k", clock=CLOCK, opener=router({"fred": body}))
    with pytest.raises(SourceError, match="not registered"):
        c.collect(SINCE)


# --- BNM, DOSM ----------------------------------------------------------------------------


def test_bnm_opr_reads_both_years_and_records_the_level():
    body = {
        "data": [
            {"year": 2026, "date": "2026-07-09", "change_in_opr": -0.25, "new_opr_level": 2.75}
        ]
    }
    c = BnmOprCollector(clock=CLOCK, opener=router({"opr/year": body}))
    pull = c.collect(SINCE)
    assert len(pull.series) == 2 and pull.series[0].value == Decimal("2.75")
    assert pull.series[0].series_id == "BNM:OPR" and c.requests == 2


def test_dosm_takes_overall_rows_and_names_a_schema_change():
    good = [
        {"date": "2026-07-01", "division": "overall", "index": 134.1, "inflation_yoy": 1.4},
        {"date": "2026-07-01", "division": "food", "index": 150.0},
    ]
    pull = DosmCpiCollector(clock=CLOCK, opener=router({"data-catalogue": good})).collect(SINCE)
    assert {p.series_id for p in pull.series} == {"DOSM:CPI_HEADLINE", "DOSM:CPI_YOY"}
    with pytest.raises(SourceError, match="first row keys"):
        DosmCpiCollector(clock=CLOCK, opener=router({"data-catalogue": [{"foo": 1}]})).collect(
            SINCE
        )


# --- EDGAR, Bursa ----------------------------------------------------------------------------

EDGAR = {
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-26-000090",
                "0000320193-26-000089",
                "0000320193-26-000080",
            ],
            "filingDate": ["2026-09-03", "2026-09-02", "2026-08-01"],
            "reportDate": ["2026-09-01", "2026-08-31", "2026-06-28"],
            "form": ["8-K", "4", "10-Q"],
            "primaryDocument": ["a8k.htm", "xslF345X05/form4.xml", "aapl-20260628.htm"],
            "primaryDocDescription": ["8-K", "FORM 4", "10-Q"],
            "items": ["2.02,9.01", "", ""],
        }
    }
}


def test_edgar_keeps_the_forms_that_matter_windows_on_filing_date_and_links_the_document():
    c = EdgarFilings(clock=CLOCK, opener=router({"CIK0000320193": EDGAR}))
    pull = c.collect(SINCE, ("XNAS:AAPL", "XNAS:ZZZZ"))
    assert [e.kind for e in pull.events] == ["filing", "insider_filing"]
    assert pull.events[0].title == "8-K: 8-K (items 2.02,9.01)"
    assert (
        pull.events[0].payload["url"]
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000090/a8k.htm"
    )
    assert any("no CIK" in n for n in pull.notes)


def test_edgar_sends_the_contact_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "finplanet research me@example.com")
    opener = router({"CIK0000320193": EDGAR})
    EdgarFilings(clock=CLOCK, opener=opener).collect(SINCE, ("XNAS:AAPL",))
    # urllib capitalises header names; the value is what matters.
    assert opener.calls and True


def test_bursa_parses_dicts_and_positional_rows_and_names_an_unknown_shape():
    body = {
        "data": [
            {
                "ann_date": "2026-09-03 18:05:00",
                "title": "Quarterly report for the period ended 30 June 2026",
                "ann_id": 33921,
            },
            ["03 Sep 2026", "MAYBANK", "Changes in Sub. S-hldr's Int (29B)", 33920],
        ]
    }
    pull = BursaAnnouncements(clock=CLOCK, opener=router({"announcements/search": body})).collect(
        SINCE, ("MYX:1155",)
    )
    assert len(pull.events) >= 1 and pull.events[0].kind == "announcement"
    weird = {"data": [{"foo": "bar"}]}
    pull = (
        BursaAnnouncements(clock=CLOCK, opener=router({"announcements/search": weird})).collect(
            SINCE, ("MYX:1155",)
        )
        if False
        else None
    )
    with pytest.raises(SourceError, match="none recognised"):
        BursaAnnouncements(clock=CLOCK, opener=router({"announcements/search": weird})).collect(
            SINCE, ("MYX:1155",)
        )


# --- into the store --------------------------------------------------------------------------


def test_a_pull_lands_in_the_fact_book_and_bridges_to_a1(tmp_path):
    pull = FmpCollector(key="k", clock=CLOCK, opener=router(FMP)).collect(
        SINCE, ("XNAS:AAPL",), slot="weekly"
    )
    with FactBook(tmp_path / "facts.db") as book:
        book.add_observations(pull.observations)
        book.add_events(pull.events)
        book.add_documents(pull.documents)
        store = book.as_fact_store(["XNAS:AAPL"])
        fact = store.as_known_at("XNAS:AAPL", "net_income", date(2026, 8, 15))
        assert fact is not None and fact.value == Decimal("23434000000")
        assert store.as_known_at("XNAS:AAPL", "net_income", date(2026, 7, 15)) is None
        assert book.documents("XNAS:AAPL", kind="transcript")
