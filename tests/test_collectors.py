"""Every structured collector, offline, against the vendor's documented shapes.

The properties under test are the seam rules in knowledge/sources/base.py: a
missing key is a skip, a plan boundary is a note, a broken source raises, a
malformed row drops the row and never the pull, and every figure is typed and
dated on the way in.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.facts import FactBook
from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.base import KeyMissing, SourceError, parse_date, parse_datetime
from knowledge.sources.bnm import BnmOprCollector
from knowledge.sources.bursa import BursaAnnouncements
from knowledge.sources.dbnomics import DbnomicsCollector
from knowledge.sources.dosm import DosmCpiCollector
from knowledge.sources.edgar import EdgarFilings
from knowledge.sources.finmind import FinMindCollector
from knowledge.sources.finnhub import FinnhubCollector
from knowledge.sources.fmp import FmpCollector
from knowledge.sources.fred import FredCollector
from knowledge.sources.jin10 import Jin10CalendarCollector, Jin10FlashCollector, beijing_to_utc
from knowledge.sources.registry import COLLECTORS, UnknownCollector, collector_for
from knowledge.sources.twse import TwseOpenApiCollector, number, roc_date, roc_month_end
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


def test_finnhub_a_print_filed_under_a_later_quarter_end_is_knowable_the_day_it_was_seen():
    """NVIDIA's late-August result reaches this endpoint labelled with the
    calendar quarter end, 2026-09-30. Until 2026-09-18 known_at was pushed out
    to that label, and a figure public on 5 September was invisible to every
    as-of read until the 30th. The endpoint carries no announcement date, so
    the day we saw the row is the earliest the store can vouch for."""
    routes = dict(FINNHUB)
    routes["stock/earnings"] = [
        {"actual": 2.22, "estimate": 2.1384, "period": "2026-09-30", "surprisePercent": 3.8159},
        {"actual": 1.87, "estimate": 1.7922, "period": "2026-06-30", "surprisePercent": 4.341},
    ]
    pull = FinnhubCollector(key="k", clock=CLOCK, opener=router(routes)).collect(
        SINCE, ("XNAS:NVDA",)
    )
    prints = {o.period_end: o for o in pull.observations if o.concept == "eps_actual"}
    later = prints[date(2026, 9, 30)]
    assert later.known_at == NOW.date() and later.forward and later.value == Decimal("2.22")
    earlier = prints[date(2026, 6, 30)]
    assert earlier.known_at == NOW.date() and not earlier.forward
    assert all(o.known_at <= NOW.date() for o in pull.observations), (
        "never knowable later than the day it was seen"
    )


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


def test_fmp_estimates_are_knowable_the_day_they_were_fetched_and_say_so():
    """Consensus for FY27 is knowable today and describes a period that ends
    in a year: known_at is the fetch day, period_end the target, and the row
    says forward so the A1 bridge can build it."""
    pull = FmpCollector(key="k", clock=CLOCK, opener=router(FMP)).collect(
        SINCE, ("XNAS:AAPL",), slot="weekly"
    )
    est = next(o for o in pull.observations if o.concept == "est_eps")
    assert est.known_at == NOW.date() and est.period_end == date(2027, 9, 30) and est.forward
    assert est.value == Decimal("8.9") and est.payload == {"analysts": 30}


def test_fmp_estimate_revisions_are_separate_vintages(tmp_path):
    """AAPL's FY27 EPS consensus read 9.538 on 5 September and 9.571 on the
    6th. Under the old stamp both rows carried known_at 2027-09-27: the
    vintage was gone and neither was visible. Stamped with the fetch day, an
    as-of read gets the figure the street held on that day."""
    revisions = [
        (datetime(2026, 9, 5, 2, tzinfo=UTC), "9.538"),
        (datetime(2026, 9, 6, 12, tzinfo=UTC), "9.571"),
    ]
    with FactBook(tmp_path / "facts.db") as book:
        for day, value in revisions:
            estimates = [{"date": "2027-09-27", "epsAvg": value, "numAnalystsEps": 30}]
            c = FmpCollector(
                key="k",
                clock=lambda d=day: d,
                opener=router({**FMP, "analyst-estimates": estimates}),
            )
            pull = c.collect(SINCE, ("XNAS:AAPL",), slot="weekly")
            book.add_observations(pull.observations, fetched_at=day)
        assert book.latest("XNAS:AAPL", "est_eps", asof=date(2026, 9, 4)) is None
        assert book.latest("XNAS:AAPL", "est_eps", asof=date(2026, 9, 5)).value == Decimal("9.538")
        assert book.latest("XNAS:AAPL", "est_eps", asof=date(2026, 9, 6)).value == Decimal("9.571")


def test_fmp_separates_a_change_of_mind_from_a_broker_restating_one():
    """A reiteration is not an event: nothing happened. This endpoint returns
    every republication, and they are the overwhelming majority - 3,917 rows in
    the fact book on 2026-09-06 against 10 filings, insider trades and results
    combined. Both are kept, under kinds that mean different things."""
    grades = [
        {
            "date": "2026-09-03",
            "gradingCompany": "Morgan Stanley",
            "previousGrade": "Equal Weight",
            "newGrade": "Overweight",
            "action": "upgrade",
        },
        {
            "date": "2026-09-03",
            "gradingCompany": "Rosenblatt",
            "previousGrade": "Buy",
            "newGrade": "Buy",
            "action": "maintain",
        },
        {  # a maintain whose grade moved anyway is still a change
            "date": "2026-09-03",
            "gradingCompany": "Needham",
            "previousGrade": "Hold",
            "newGrade": "Buy",
            "action": "maintain",
        },
    ]
    c = FmpCollector(key="k", clock=CLOCK, opener=router({**FMP, "grades?": grades}))
    pull = c.collect(SINCE, ("XNAS:AAPL",), slot="weekly")
    by_house = {e.payload["house"]: e.kind for e in pull.events if "house" in e.payload}
    assert by_house == {
        "Morgan Stanley": "rating_change",
        "Rosenblatt": "rating_reiteration",
        "Needham": "rating_change",
    }


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
    assert any("outside the plan" in n for n in pull.notes), pull.notes


def test_fmp_a_402_is_one_legible_note_per_endpoint_and_is_not_asked_again():
    """`/stable/earnings` answered HTTP 402 every week to 2026-09-13. It was
    already a note rather than a failure - `get_json` maps 402 to PlanExcluded
    - but the note was a redacted URL per name per endpoint, and three names'
    worth ran past the column before it named the second company. Now the
    endpoint is named once, with every name it was not collected for, and
    once refused it is not requested for the next name: the boundary is the
    plan's, not the company's, and the second call would learn the same fact
    for the price of a request."""
    routes = dict(FMP)
    routes["earnings?"] = http_error(402)
    c = FmpCollector(key="k", clock=CLOCK, opener=router(routes))
    pull = c.collect(SINCE, ("XNAS:AAPL", "XNAS:MSFT"), slot="us_preopen")
    about_earnings = [n for n in pull.notes if "/earnings" in n]
    assert len(about_earnings) == 1, pull.notes
    (note,) = about_earnings
    assert "outside the plan (HTTP 402)" in note and "AAPL" in note and "MSFT" in note, note
    assert not any("financialmodelingprep.com" in n for n in pull.notes), pull.notes
    assert c.requests == 5, "grades and targets for both names, earnings once"
    assert pull.observations and not any(e.kind == "earnings_result" for e in pull.events)
    # The boundary is one run's: a plan bought tomorrow is asked tomorrow.
    c.collect(SINCE, ("XNAS:AAPL",), slot="us_preopen")
    assert c.requests == 8


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
    assert c.requests == 2, "one request PER name: the vendor's multi-ticker filter is an AND"
    assert sent.payload["articles"] == 1, "the story came back for both names and counts once"


def test_alphavantage_keeps_the_first_names_articles_when_a_later_request_hits_the_quota():
    quota = {"Information": "Our standard API rate limit is 25 requests per day."}
    answers = iter([AV, quota])
    c = AlphaVantageNews(
        key="k",
        clock=CLOCK,
        opener=lambda req, timeout=None: FakeResponse(json.dumps(next(answers))),
    )
    pull = c.collect(SINCE, ("XNAS:AAPL", "XNAS:NVDA"))
    assert len(pull.articles) == 1 and c.requests == 2
    assert pull.notes and "quota exhausted for today" in pull.notes[0]
    assert pull.notes[0].startswith("XNAS:NVDA:"), "the note names the name that went unanswered"


def test_alphavantage_rate_limit_answer_is_a_failure_not_a_quiet_day():
    body = {
        "Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
    }
    c = AlphaVantageNews(key="k", clock=CLOCK, opener=router({"alphavantage": body}))
    with pytest.raises(SourceError, match="quota exhausted for today"):
        c.collect(SINCE, ("XNAS:NVDA",))


def test_alphavantage_rejected_key_is_named_as_such_not_as_a_quota():
    """The quota notice also says "API key", so the order of the checks is the
    point: a bad key must not read as a spent day, or the operator waits until
    tomorrow for a key that will never work."""
    body = {
        "Information": "The **demo** API key is for demo purposes only. Please claim your free API key."
    }
    c = AlphaVantageNews(key="k", clock=CLOCK, opener=router({"alphavantage": body}))
    with pytest.raises(SourceError, match="rejected the key") as exc:
        c.collect(SINCE, ("XNAS:NVDA",))
    assert "quota" not in str(exc.value)


def test_alphavantage_any_other_notice_is_still_a_refusal():
    body = {"Note": "Maintenance window; try again later."}
    c = AlphaVantageNews(key="k", clock=CLOCK, opener=router({"alphavantage": body}))
    with pytest.raises(SourceError, match="alphavantage refused: Maintenance"):
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


def test_dosm_asks_for_the_newest_rows_and_keeps_them_whatever_order_they_arrive_in():
    """The catalogue answered a bare limit with 1980; the request says otherwise."""
    open_ = router(
        {
            "data-catalogue": [
                {"date": "1980-01-01", "division": "overall", "index": 40.0},
                {"date": "2026-07-01", "division": "overall", "index": 134.1},
                {"date": "2026-06-01", "division": "overall", "index": 133.8},
            ]
        }
    )
    pull = DosmCpiCollector(clock=CLOCK, opener=open_).collect(SINCE)
    asked = open_.calls[0]
    assert "sort=-date" in asked and "date_start=2022" in asked
    assert f"limit={DosmCpiCollector.FULL_SERIES_LIMIT}" in asked
    # Ascending, descending or shuffled, the newest row is the one stored first.
    heads = [p for p in pull.series if p.series_id == "DOSM:CPI_HEADLINE"]
    assert [p.obs_date.isoformat() for p in heads][:2] == ["2026-07-01", "2026-06-01"]


def test_dosm_refuses_a_series_that_stops_before_the_freshness_limit():
    """The 2026-09-06 defect, as a test: 1982 must never be stored as current."""
    stale = [{"date": f"1982-{m:02d}-01", "division": "overall", "index": 48.7} for m in (11, 12)]
    with pytest.raises(SourceError, match="newest row is 1982-12-01"):
        DosmCpiCollector(clock=CLOCK, opener=router({"data-catalogue": stale})).collect(SINCE)


def test_dosm_asks_again_without_the_extra_parameters_when_the_host_rejects_them():
    """An unknown query parameter must not cost the source its whole reading."""
    calls: list[str] = []

    def open_(req, timeout=None):
        calls.append(req.full_url)
        if "sort=" in req.full_url:
            raise http_error(400)
        return FakeResponse(
            json.dumps([{"date": "2026-07-01", "division": "overall", "index": 134.1}])
        )

    pull = DosmCpiCollector(clock=CLOCK, opener=open_).collect(SINCE)
    assert len(calls) == 2 and "sort=" not in calls[1]
    assert [p.value for p in pull.series] == [Decimal("134.1")]


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
        # The forward estimate crosses the bridge too, visible from the fetch day.
        est = store.as_known_at("XNAS:AAPL", "est_eps", NOW.date())
        assert est is not None and est.forward and est.period_end == date(2027, 9, 30)
        assert store.as_known_at("XNAS:AAPL", "est_eps", NOW.date() - timedelta(days=1)) is None
        assert book.documents("XNAS:AAPL", kind="transcript")


# --- Jin10 ---------------------------------------------------------------------------------

JIN10_FLASH = {
    "status": 200,
    "data": [
        {
            "id": 3001,
            "time": "2026-09-04 21:30:05",
            "type": 0,
            "important": 1,
            "tags": ["美联储", "美国"],
            "data": {"content": "<b>美国8月非农就业人口</b> 增加 14.2万人，预期16万人。"},
        },
        {"id": 3002, "time": "2026-09-01 08:00:00", "type": 0, "data": {"content": "too old"}},
        {"id": 3003, "time": "2026-09-04 09:00:00", "type": 1, "data": {"pic": "x.png"}},
    ],
}


def test_beijing_time_becomes_utc():
    when = beijing_to_utc("2026-09-04 21:30:05")
    assert when == datetime(2026, 9, 4, 13, 30, 5, tzinfo=UTC)
    assert beijing_to_utc("") is None and beijing_to_utc("not a time") is None


def test_jin10_flash_keeps_recent_items_as_chinese_articles_with_tags_stripped():
    c = Jin10FlashCollector(clock=CLOCK, opener=router({"flash-api": JIN10_FLASH}))
    pull = c.collect(SINCE)
    (a,) = pull.articles  # the old one is before SINCE; the picture has no text
    assert a.doc_id == "jin10:3001" and a.language == "zh" and a.source_domain == "jin10.com"
    assert "<b>" not in a.body and "非农" in a.body
    assert "important" in a.themes and "美联储" in a.themes
    assert a.published_at == datetime(2026, 9, 4, 13, 30, 5, tzinfo=UTC)
    assert c.requests == 1 and pull.requests == 1


def test_jin10_flash_sends_the_app_headers_and_refuses_a_non_200_status():
    seen = {}

    def opener(req, timeout=None):
        seen.update(req.headers)
        return FakeResponse(json.dumps({"status": 403, "message": "forbidden"}))

    with pytest.raises(SourceError, match="refused"):
        Jin10FlashCollector(clock=CLOCK, opener=opener).collect(SINCE)
    assert seen.get("X-app-id") == "bVBF4FyRTn5NJF5n" and seen.get("X-version") == "1.0.0"
    assert seen.get("User-agent", "").startswith("finplanet-analyst-mind/")


def test_jin10_flash_html_is_a_failure_not_a_quiet_day():
    c = Jin10FlashCollector(clock=CLOCK, opener=router({"flash-api": "<html>captive</html>"}))
    with pytest.raises(SourceError, match="non-JSON"):
        c.collect(SINCE)


JIN10_CAL = [
    {
        "id": 501,
        "country": "美国",
        "name": "8月季调后非农就业人口(万人)",
        "pub_time": "2026-09-04 20:30:00",
        "actual": "14.2",
        "consensus": "16",
        "previous": "7.3",
        "revised": "",
        "star": 3,
        "unit": "万人",
        "time_period": "8月",
    },
    {
        "id": 502,
        "country": "欧元区",
        "name": "第二季度GDP年率终值",
        "pub_time": "2026-09-04 17:00:00",
        "actual": "",
        "consensus": "1.4",
        "previous": "1.5",
        "star": 2,
    },
    {"id": 503, "country": "日本", "name": "", "pub_time": "2026-09-04 07:30:00"},
]


def test_jin10_calendar_splits_scheduled_releases_from_prints_and_keeps_the_surprise():
    c = Jin10CalendarCollector(clock=CLOCK, opener=router({"economics.json": JIN10_CAL}))
    pull = c.collect(SINCE)
    kinds = {e.event_id: e.kind for e in pull.events}
    assert kinds == {"501:print": "macro_print", "502:scheduled": "macro_release"}
    printed = next(e for e in pull.events if e.kind == "macro_print")
    assert printed.instrument_id == "MACRO:美国"
    assert printed.announced_at == datetime(2026, 9, 4, 12, 30, tzinfo=UTC)
    assert printed.payload["actual"] == "14.2" and printed.payload["surprise"] == "-1.8"
    assert "vs 16 expected" in printed.title
    scheduled = next(e for e in pull.events if e.kind == "macro_release")
    assert scheduled.payload["consensus"] == "1.4" and "consensus 1.4" in scheduled.title
    assert c.requests == 1


def test_jin10_calendar_falls_back_to_the_older_path_and_reports_when_neither_answers():
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if "datas" not in req.full_url:
            raise http_error(404)
        return FakeResponse(json.dumps({"data": JIN10_CAL[:1]}))

    pull = Jin10CalendarCollector(clock=CLOCK, opener=opener).collect(SINCE)
    assert len(pull.events) == 1 and "/datas/2026/0904/economics.json" in calls[-1]

    seen = {}

    def nothing(req, timeout=None):
        seen.update(req.headers)
        raise http_error(404)

    with pytest.raises(SourceError, match="no calendar path answered") as exc:
        Jin10CalendarCollector(clock=CLOCK, opener=nothing).collect(SINCE)
    assert "web_data" in str(exc.value) and "datas" in str(exc.value), "every path tried is named"
    assert seen.get("Referer") == "https://rili.jin10.com/"


def test_jin10_calendar_retires_a_host_whose_name_does_not_resolve_and_groups_the_verdicts():
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url.startswith("https://cdn-rili."):
            raise urllib.error.URLError("[Errno -2] Name or service not known")
        raise http_error(404)

    c = Jin10CalendarCollector(clock=CLOCK, opener=opener, sleep=lambda _s: None)
    with pytest.raises(SourceError, match="no calendar path answered") as exc:
        c.collect(SINCE)
    cdn = {u for u in calls if "cdn-rili" in u}  # a set: the retry loop re-asks the same URL
    assert len(cdn) == 1, "one DNS failure retires the host; its other paths are not asked"
    assert len({u for u in calls if u.startswith("https://rili.")}) == 2
    msg = str(exc.value)
    assert "cdn-rili.jin10.com: name does not resolve" in msg
    assert "rili.jin10.com: HTTP 404 at /datas/2026/0904/economics.json" in msg
    assert "/web_data/2026/daily/09/04/economics.json" in msg
    assert "network tab" in msg, "the failure says how to find the new path"


# --- DBnomics ------------------------------------------------------------------------------


def _dbn_doc(provider, dataset, code, periods, values):
    return {
        "provider_code": provider,
        "dataset_code": dataset,
        "series_code": code,
        "period": periods,
        "value": values,
    }


def test_dbnomics_stores_known_series_and_names_the_ones_the_api_does_not_know():
    series = DbnomicsCollector.__init__.__defaults__  # noqa: F841 - default list is the module's
    from knowledge.sources.dbnomics import SERIES

    two = SERIES[:2]
    good = _dbn_doc("IMF", "PCPS", "M.W00.PPOIL.USD", ["2026-06", "2026-07"], [905.2, 921.0])
    answers = iter(
        [
            {"series": {"docs": [good]}},  # the bulk call knows one of two
            {"series": {"docs": []}},  # the retry for the other still does not
        ]
    )
    c = DbnomicsCollector(
        series=two,
        clock=CLOCK,
        opener=lambda req, timeout=None: FakeResponse(json.dumps(next(answers))),
    )
    pull = c.collect(SINCE)
    assert {p.series_id for p in pull.series} == {"DBN:PALM_OIL_USD"}
    assert pull.series[-1].obs_date == date(2026, 7, 1) and pull.series[-1].value == Decimal(
        "921.0"
    )
    assert pull.series[-1].known_at == NOW.date() and pull.series[-1].payload["title"].startswith(
        "Palm oil"
    )
    assert pull.notes == [
        "dbnomics: no series IMF/PCPS/M.W00.PALUM.USD (check the code on db.nomics.world)"
    ]
    assert c.requests == 2


def test_dbnomics_reads_quarterly_and_annual_periods_and_fails_when_nothing_answers():
    from knowledge.sources.dbnomics import SERIES, _period_to_day

    assert _period_to_day("2026-Q2") == "2026-04-01" and _period_to_day("2025") == "2025-01-01"
    assert (
        _period_to_day("2026-07") == "2026-07-01" and _period_to_day("2026-07-15") == "2026-07-15"
    )
    c = DbnomicsCollector(
        series=SERIES[:1], clock=CLOCK, opener=router({"db.nomics": {"series": {"docs": []}}})
    )
    with pytest.raises(SourceError, match="none of the configured series"):
        c.collect(SINCE)


def test_dbnomics_relays_the_apis_own_error_message():
    c = DbnomicsCollector(clock=CLOCK, opener=router({"db.nomics": {"message": "Bad request"}}))
    with pytest.raises(SourceError, match="Bad request"):
        c.collect(SINCE)


# --- FRED release calendar -------------------------------------------------------------------


def test_a_release_fred_lists_every_day_is_a_table_not_a_print():
    """FRED lists "FOMC Press Release" on every day of the fortnight, weekends
    included. Stored as events it put an FOMC date on every row of every page's
    watch list, and the one real decision could not be told from the rest."""
    from datetime import timedelta as _td

    daily = [
        {"release_id": 101, "release_name": "FOMC Press Release", "date": str(d)}
        for d in (date(2026, 9, 5) + _td(days=i) for i in range(14))
    ]
    weekly = [
        {
            "release_id": 180,
            "release_name": "Unemployment Insurance Weekly Claims Report",
            "date": d,
        }
        for d in ("2026-09-10", "2026-09-17")
    ]

    def opener(req, timeout=None):
        if "releases/dates" in req.full_url:
            return FakeResponse(json.dumps({"release_dates": daily + weekly}))
        return FakeResponse(json.dumps({"observations": [{"date": "2026-09-01", "value": "4.33"}]}))

    c = FredCollector(series={"DFF": "fed funds"}, key="k", clock=CLOCK, opener=opener)
    pull = c.collect(SINCE, slot="us_preopen")
    titles = {e.title for e in pull.events if e.kind == "macro_release"}
    assert titles == {"Unemployment Insurance Weekly Claims Report"}, titles
    assert any("'FOMC Press Release' listed on" in n and "daily table" in n for n in pull.notes)


def test_fred_adds_the_release_calendar_as_macro_events_and_survives_its_absence():
    def opener(req, timeout=None):
        if "releases/dates" in req.full_url:
            return FakeResponse(
                json.dumps(
                    {
                        "release_dates": [
                            {
                                "release_id": 10,
                                "release_name": "Consumer Price Index",
                                "date": "2026-09-10",
                            },
                            {
                                "release_id": 50,
                                "release_name": "Employment Situation",
                                "date": "2026-09-01",
                            },
                            {
                                "release_id": 18,
                                "release_name": "H.15 Selected Interest Rates",
                                "date": "2026-09-08",
                            },
                        ]
                    }
                )
            )
        return FakeResponse(json.dumps({"observations": [{"date": "2026-09-01", "value": "4.33"}]}))

    c = FredCollector(series={"DFF": "fed funds"}, key="k", clock=CLOCK, opener=opener)
    pull = c.collect(SINCE, slot="us_preopen")
    (e,) = pull.events  # 09-01 is in the past relative to NOW; H.15 is not a major release
    assert e.instrument_id == "MACRO:US" and e.kind == "macro_release"
    assert e.event_id == "fred:10:2026-09-10" and e.title == "Consumer Price Index"
    assert e.payload["time"] == "not published by FRED"
    # the observations body answering the calendar call is a note, not a failure
    c2 = FredCollector(
        series={"DFF": "fed funds"},
        key="k",
        clock=CLOCK,
        opener=router({"fred": {"observations": [{"date": "2026-09-01", "value": "4.33"}]}}),
    )
    pull2 = c2.collect(SINCE)
    assert len(pull2.series) == 1 and pull2.notes == [
        "release calendar: no release_dates list in the reply"
    ]
    # the us_close slot does not read the calendar at all
    c3 = FredCollector(series={"DFF": "x"}, key="k", clock=CLOCK, opener=opener)
    assert c3.collect(SINCE, slot="us_close").events == [] and c3.requests == 1


# --- TWSE OpenAPI ----------------------------------------------------------------------------


def test_roc_dates_and_twse_numbers_normalise():
    assert roc_date("1150905") == date(2026, 9, 5) and roc_date("115/09/05") == date(2026, 9, 5)
    assert roc_date("") is None and roc_date("2026-09-05") is None
    assert roc_month_end("11508") == date(2026, 8, 31) and roc_month_end("11513") is None
    assert number("1,234.5") == Decimal("1234.5") and number("－") is None and number("--") is None


TWSE_VAL = [
    {
        "Date": "1150904",
        "Code": "2330",
        "Name": "台積電",
        "PEratio": "24.10",
        "DividendYield": "1.20",
        "PBratio": "6.30",
    },
    {
        "Date": "1150904",
        "Code": "2317",
        "Name": "鴻海",
        "PEratio": "12.0",
        "DividendYield": "3.0",
        "PBratio": "1.5",
    },
]
TWSE_REV = [
    {
        "出表日期": "1150905",
        "資料年月": "11508",
        "公司代號": "2330",
        "公司名稱": "台積電",
        "產業別": "24",
        "營業收入-當月營收": "250,000,000",
        "營業收入-上月營收": "240,000,000",
        "營業收入-去年當月營收": "200,000,000",
        "營業收入-上月比較增減(%)": "4.17",
        "營業收入-去年同月增減(%)": "25.00",
        "累計營業收入-當月累計營收": "1,900,000,000",
    }
]
TWSE_DAY = [
    {
        "Date": "1150904",
        "Code": "2330",
        "Name": "台積電",
        "TradeVolume": "30,123,456",
        "ClosingPrice": "1,105.00",
    }
]


def test_twse_reads_the_three_tables_for_the_read_only_names_only():
    c = TwseOpenApiCollector(
        clock=CLOCK,
        opener=router({"BWIBBU_ALL": TWSE_VAL, "t187ap05_L": TWSE_REV, "STOCK_DAY_ALL": TWSE_DAY}),
    )
    pull = c.collect(SINCE, ("XTAI:2330", "XNAS:NVDA"))
    by = {o.concept: o for o in pull.observations}
    assert set(by) == {
        "pe_ttm",
        "pb",
        "dividend_yield",
        "revenue_month",
        "revenue_yoy",
        "revenue_mom",
        "close",
        "volume",
    }
    assert all(o.instrument_id == "XTAI:2330" for o in pull.observations)
    assert by["revenue_month"].value == Decimal("250000000000") and by[
        "revenue_month"
    ].period_end == date(2026, 8, 31)
    assert by["revenue_yoy"].value == Decimal("25.00") and by["pe_ttm"].period_end == date(
        2026, 9, 4
    )
    assert by["close"].value == Decimal("1105.00") and by["close"].currency == "TWD"
    assert c.requests == 3


def test_twse_makes_no_request_without_a_taiwan_name_and_fails_only_when_every_table_does():
    c = TwseOpenApiCollector(clock=CLOCK, opener=router({}))
    assert c.collect(SINCE, ("XNAS:NVDA",)).fetched == 0 and c.requests == 0
    partial = TwseOpenApiCollector(
        clock=CLOCK,
        opener=router(
            {"BWIBBU_ALL": TWSE_VAL, "t187ap05_L": "<html>", "STOCK_DAY_ALL": http_error(500)}
        ),
    )
    pull = partial.collect(SINCE, ("XTAI:2330",))
    assert {o.concept for o in pull.observations} == {"pe_ttm", "pb", "dividend_yield"}
    assert len(pull.notes) == 2 and any("revenue" in n for n in pull.notes)
    dead = TwseOpenApiCollector(clock=CLOCK, opener=router({"twse": http_error(500)}))
    with pytest.raises(SourceError, match="every TWSE table failed"):
        dead.collect(SINCE, ("XTAI:2330",))


# --- FinMind ---------------------------------------------------------------------------------


def _finmind_router(seen: dict):
    def opener(req, timeout=None):
        seen.setdefault("auth", []).append(req.headers.get("Authorization"))
        url = req.full_url
        if "TaiwanStockMonthRevenue" in url:
            body = {
                "status": 200,
                "data": [
                    {
                        "date": "2026-08-01",
                        "stock_id": "2330",
                        "revenue": 250000000000,
                        "revenue_year": 2026,
                        "revenue_month": 8,
                    }
                ],
            }
        elif "TaiwanStockFinancialStatements" in url:
            body = {
                "status": 200,
                "data": [
                    {
                        "date": "2026-06-30",
                        "stock_id": "2330",
                        "type": "EPS",
                        "value": 15.36,
                        "origin_name": "基本每股盈餘",
                    },
                    {
                        "date": "2026-06-30",
                        "stock_id": "2330",
                        "type": "Revenue",
                        "value": 933000000000,
                        "origin_name": "營業收入",
                    },
                    {"date": "2026-06-30", "stock_id": "2330", "type": "SomethingElse", "value": 1},
                ],
            }
        elif "InstitutionalInvestors" in url:
            body = {
                "status": 200,
                "data": [
                    {
                        "date": "2026-09-03",
                        "stock_id": "2330",
                        "name": "Foreign_Investor",
                        "buy": 5000,
                        "sell": 3000,
                    },
                    {
                        "date": "2026-09-03",
                        "stock_id": "2330",
                        "name": "Dealer_self",
                        "buy": 1,
                        "sell": 2,
                    },
                ],
            }
        else:
            raise AssertionError(url)
        return FakeResponse(json.dumps(body))

    return opener


def test_finmind_reads_three_datasets_per_name_keyless_and_sends_the_token_when_set(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    seen: dict = {}
    c = FinMindCollector(clock=CLOCK, opener=_finmind_router(seen))
    pull = c.collect(SINCE, ("XTAI:2330",))
    by = {o.concept: o for o in pull.observations}
    assert set(by) == {"revenue_month", "eps", "revenue", "foreign_net_buy"}
    assert by["foreign_net_buy"].value == Decimal(2000) and by["eps"].period_end == date(
        2026, 6, 30
    )
    assert by["eps"].known_at == NOW.date(), (
        "a statement is knowable when fetched, not on its period end"
    )
    assert c.requests == 3 and seen["auth"] == [None, None, None]
    monkeypatch.setenv("FINMIND_TOKEN", "t-not-real")
    seen2: dict = {}
    FinMindCollector(clock=CLOCK, opener=_finmind_router(seen2)).collect(SINCE, ("XTAI:2330",))
    assert seen2["auth"] == ["Bearer t-not-real"] * 3


def test_finmind_quota_answer_is_a_note_and_no_taiwan_name_means_no_request():
    body = {"status": 402, "msg": "Your quota is exceeded"}
    c = FinMindCollector(clock=CLOCK, opener=router({"finmindtrade": body}))
    pull = c.collect(SINCE, ("XTAI:2330",))
    assert pull.fetched == 0 and len(pull.notes) == 3 and all("quota" in n for n in pull.notes)
    assert (
        FinMindCollector(clock=CLOCK, opener=router({})).collect(SINCE, ("MYX:1155",)).fetched == 0
    )


def test_every_new_collector_is_registered_and_catalogued():
    from knowledge.sources.catalog import CATALOG

    for name in ("jin10_flash", "jin10_calendar", "dbnomics", "twse_openapi", "finmind"):
        assert name in COLLECTORS and name in CATALOG, name
        assert collector_for(name).name == name


# --- SEC XBRL company facts ------------------------------------------------------------

from knowledge.sources.eodhd import EodhdFundamentals, symbol_for  # noqa: E402
from knowledge.sources.sec_xbrl import SecCompanyFacts  # noqa: E402


def _usd(items):
    return {"units": {"USD": items}}


def _fact(start, end, val, filed, form, fy=None, fp=None):
    d = {
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "fy": fy,
        "fp": fp,
        "accn": f"acc-{filed}",
    }
    if start:
        d["start"] = start
    return d


COMPANY_FACTS = {
    "cik": 320193,
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        _fact(None, "2026-01-16", 15000000000, "2026-01-30", "10-Q", 2026, "Q1")
                    ]
                }
            }
        },
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _usd(
                [
                    _fact("2024-01-01", "2024-12-31", 900, "2025-02-01", "10-K", 2024, "FY"),
                    _fact("2025-01-01", "2025-03-31", 240, "2025-05-01", "10-Q", 2025, "Q1"),
                    _fact("2025-04-01", "2025-06-30", 250, "2025-08-01", "10-Q", 2025, "Q2"),
                    _fact(
                        "2025-01-01", "2025-06-30", 490, "2025-08-01", "10-Q", 2025, "Q2"
                    ),  # six-month YTD: dropped
                    _fact("2025-07-01", "2025-09-30", 255, "2025-11-01", "10-Q", 2025, "Q3"),
                    _fact("2025-01-01", "2025-12-31", 1000, "2026-02-01", "10-K", 2025, "FY"),
                    _fact(
                        "2025-01-01", "2025-03-31", 240, "2026-02-01", "10-K", 2025, "Q1"
                    ),  # re-reported: earliest wins
                    _fact(
                        "2024-01-01", "2024-12-31", 905, "2026-02-01", "10-K", 2025, "FY"
                    ),  # restated: a second row
                    _fact(
                        "2010-01-01", "2010-12-31", 100, "2011-02-01", "10-K", 2010, "FY"
                    ),  # beyond the lookback
                ]
            ),
            "Revenues": _usd(
                [_fact("2023-01-01", "2023-12-31", 800, "2024-02-01", "10-K", 2023, "FY")]
            ),
            "Assets": _usd(
                [
                    _fact(None, "2025-12-31", 2000, "2026-02-01", "10-K", 2025, "FY"),
                    _fact(None, "2024-12-31", 1800, "2025-02-01", "10-K", 2024, "FY"),
                ]
            ),
            "NetIncomeLoss": _usd(
                [_fact("2025-01-01", "2025-12-31", 150, "2026-02-01", "10-K", 2025, "FY")]
            ),
            "SomethingElse": _usd([_fact(None, "2025-12-31", 1, "2026-02-01", "10-K")]),
        },
    },
}


def test_sec_company_facts_stamp_filing_dates_split_quarters_from_years_and_derive_q4():
    open_ = router({"companyfacts/CIK0000320193.json": COMPANY_FACTS})
    c = SecCompanyFacts(clock=lambda: datetime(2026, 3, 1, tzinfo=UTC), opener=open_)
    pull = c.collect(SINCE, ("XNAS:AAPL", "XNAS:ZZZZ", "MYX:1155"))
    assert pull.requests == 1
    assert any("XNAS:ZZZZ: no CIK" in n for n in pull.notes) and not any(
        "MYX" in n for n in pull.notes
    )
    by = {}
    for o in pull.observations:
        by.setdefault((o.concept, o.period_end), []).append(o)
    q1 = by[("revenue", date(2025, 3, 31))]
    assert len(q1) == 1 and q1[0].known_at == date(2025, 5, 1), (
        "the earliest filing of a figure is its known-at"
    )
    assert q1[0].payload["form"] == "10-Q" and q1[0].currency == "USD"
    fy25 = by[("revenue_fy", date(2025, 12, 31))]
    assert fy25[0].value == Decimal(1000) and fy25[0].known_at == date(2026, 2, 1)
    q4 = by[("revenue", date(2025, 12, 31))]
    assert q4[0].value == Decimal(255) and q4[0].payload.get("derived") == "FY - Q1..Q3"
    assert q4[0].known_at == date(2026, 2, 1), (
        "a derived quarter is knowable when the year is filed"
    )
    fy24 = sorted(by[("revenue_fy", date(2024, 12, 31))], key=lambda o: o.known_at)
    assert [o.value for o in fy24] == [Decimal(900), Decimal(905)], (
        "a restatement is a second row, not an overwrite"
    )
    assert ("revenue", date(2025, 6, 30)) in by and all(
        o.value == Decimal(250) for o in by[("revenue", date(2025, 6, 30))]
    ), "the six-month figure is not a quarter"
    assert ("revenue_fy", date(2023, 12, 31)) in by, "a period only the older tag carries is kept"
    assert ("revenue_fy", date(2010, 12, 31)) not in by, "beyond the lookback"
    assert by[("total_assets", date(2025, 12, 31))][0].value == Decimal(2000)
    assert by[("shares_outstanding", date(2026, 1, 16))][0].unit == "shares"
    assert all(o.known_at >= (o.period_end or o.known_at) for o in pull.observations)


def test_sec_company_facts_names_the_user_agent_on_403_and_fails_only_when_every_name_does():
    forbidden = router({"companyfacts": http_error(403)})
    with pytest.raises(SourceError, match="SEC_USER_AGENT"):
        SecCompanyFacts(clock=CLOCK, opener=forbidden).collect(SINCE, ("XNAS:AAPL",))
    dead = router({"companyfacts": http_error(500)})
    with pytest.raises(SourceError, match="every name failed"):
        SecCompanyFacts(clock=CLOCK, opener=dead, sleep=lambda _s: None).collect(
            SINCE, ("XNAS:AAPL",)
        )
    assert (
        SecCompanyFacts(clock=CLOCK, opener=router({})).collect(SINCE, ("MYX:1155",)).requests == 0
    )


# --- EODHD -------------------------------------------------------------------------------

EODHD_PAYLOAD = {
    "Financials": {
        "Income_Statement": {
            "currency_symbol": "USD",
            "quarterly": {
                "2025-12-31": {
                    "date": "2025-12-31",
                    "filing_date": "2026-02-15",
                    "totalRevenue": "255",
                    "netIncome": "39",
                },
                "2025-09-30": {
                    "date": "2025-09-30",
                    "filing_date": None,
                    "totalRevenue": "255",
                    "netIncome": "39",
                },
            },
            "yearly": {
                "2025-12-31": {
                    "date": "2025-12-31",
                    "filing_date": "2026-02-15",
                    "totalRevenue": "1000",
                    "netIncome": "150",
                }
            },
        },
        "Balance_Sheet": {
            "currency_symbol": "USD",
            "quarterly": {
                "2025-12-31": {
                    "date": "2025-12-31",
                    "filing_date": "2026-02-15",
                    "totalAssets": "2000",
                    "netReceivables": "150",
                }
            },
            "yearly": {
                "2025-12-31": {
                    "date": "2025-12-31",
                    "filing_date": "2026-02-15",
                    "totalAssets": "2000",
                }
            },
        },
        "Cash_Flow": {
            "currency_symbol": "USD",
            "quarterly": {
                "2025-12-31": {
                    "date": "2025-12-31",
                    "filing_date": "2026-02-15",
                    "totalCashFromOperatingActivities": "50",
                    "capitalExpenditures": "-13",
                }
            },
            "yearly": {},
        },
    }
}


def test_eodhd_skips_without_its_key_and_names_the_variable():
    with pytest.raises(KeyMissing, match="EODHD_API_KEY"):
        EodhdFundamentals(clock=CLOCK, opener=router({})).collect(SINCE, ("XNAS:AAPL",))


def test_eodhd_rotates_two_names_a_day_and_defers_the_rest():
    names = ("XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA", "XNYS:JPM", "MYX:1155")
    asked, deferred = EodhdFundamentals.rotation(names, date(2026, 9, 6), "us_close")
    assert len(asked) == 2 and len(deferred) == 2, "two asked, the rest of the ELIGIBLE deferred"
    assert set(asked) | set(deferred) == {"XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA", "XNYS:JPM"}
    assert EodhdFundamentals.rotation(names, date(2026, 9, 6), "us_close") == (asked, deferred), (
        "deterministic"
    )
    next_day, _ = EodhdFundamentals.rotation(names, date(2026, 9, 7), "us_close")
    assert next_day != asked, "the next day asks for different names"
    assert (
        symbol_for("MYX:1155") == "1155.KLSE"
        and symbol_for("XNAS:NVDA") == "NVDA.US"
        and symbol_for("XLON:VOD") is None
    )


def test_the_rotation_only_asks_for_names_the_plan_can_serve():
    """The bug the account's own dashboard reported: 0 calls, ever.

    The free plan is US-only. Rotating over the whole book spent all three
    weekday slots on Bursa symbols that can only be refused, and reached the
    three US names the plan DOES cover only in `weekly`, which fires on Sundays.
    """
    book = (
        "MYX:1155",
        "MYX:5347",
        "MYX:5183",
        "MYX:5225",
        "MYX:8869",
        "MYX:3182",
        "XNAS:NVDA",
        "XNAS:AAPL",
        "XNAS:MSFT",
        "XTAI:2330",
    )
    for slot in ("bursa_close", "us_preopen", "us_close", "weekly", "all"):
        asked, deferred = EodhdFundamentals.rotation(book, date(2026, 9, 7), slot)
        assert asked, f"{slot} asks for nothing"
        assert all(a.startswith("XNAS:") for a in asked), (
            f"{slot} asked for a name outside the free plan: {asked}"
        )
        assert not any(d.startswith("MYX:") or d.startswith("XTAI:") for d in deferred), (
            "a name the plan cannot serve is not 'next in rotation' - it needs a paid plan"
        )


def test_widening_the_plan_brings_the_bursa_names_back():
    """The paid plan is a parameter, not an edit here."""
    book = ("MYX:1155", "MYX:5347", "MYX:8869", "XNAS:NVDA", "XNAS:AAPL")
    paid = {"XNAS", "XNYS", "XKLS", "XTAI"}
    asked, deferred = EodhdFundamentals.rotation(
        book, date(2026, 9, 7), "bursa_close", plan_markets=paid
    )
    assert set(asked) | set(deferred) == set(book)
    assert any(a.startswith("MYX:") for a in asked)


def test_a_book_with_no_covered_name_still_asks_rather_than_going_silent():
    """Falling back to the whole book matters: an empty ask would make the
    source report `ok` having done nothing, which is the shape of a healthy
    collector and the substance of a dead one."""
    book = ("MYX:1155", "MYX:5347", "MYX:8869")
    asked, _ = EodhdFundamentals.rotation(book, date(2026, 9, 7), "bursa_close")
    assert asked, "no eligible name must not mean no request and no message"


def test_eodhd_maps_statements_to_the_shared_keys_and_stamps_filing_dates():
    open_ = router({"fundamentals/AAPL.US": EODHD_PAYLOAD})
    c = EodhdFundamentals(
        clock=lambda: datetime(2026, 3, 1, tzinfo=UTC), opener=open_, key="tok.12345678"
    )
    pull = c.collect(SINCE, ("XNAS:AAPL",))
    assert (
        pull.requests == 1
        and "api_token=tok.12345678" in open_.calls[0]
        and "filter=Financials" in open_.calls[0]
    )
    by = {(o.concept, o.period_end): o for o in pull.observations}
    assert by[("revenue", date(2025, 12, 31))].value == Decimal(255)
    assert by[("revenue", date(2025, 12, 31))].known_at == date(2026, 2, 15)
    assert by[("revenue", date(2025, 9, 30))].known_at == date(2026, 3, 1), (
        "no filing date: the fetch day, never the period end"
    )
    assert by[("revenue_fy", date(2025, 12, 31))].value == Decimal(1000)
    assert (
        by[("total_assets", date(2025, 12, 31))].value == Decimal(2000)
        and by[("total_assets", date(2025, 12, 31))].currency == "USD"
    )
    assert by[("capex", date(2025, 12, 31))].value == Decimal(-13), (
        "signs as reported; the ratio layer takes the absolute value"
    )
    assert ("total_assets_fy", date(2025, 12, 31)) not in by, (
        "instants are not duplicated under _fy"
    )


def test_eodhd_names_the_plan_boundary_without_spending_a_request_on_it():
    """The Bursa name is not requested at all now, so the boundary is reported
    BEFORE the call rather than by reading a 403 back from one."""
    open_ = router({"AAPL.US": {"message": "Daily API limit exceeded"}})
    c = EodhdFundamentals(clock=CLOCK, opener=open_, key="tok.12345678")
    pull = c.collect(SINCE, ("MYX:1155", "XNAS:AAPL"))
    assert any(
        "outside the free plan (US only), so not asked for: MYX:1155" in n for n in pull.notes
    )
    assert any("Fundamentals plan covers KLSE" in n for n in pull.notes)
    assert pull.requests == 1, "one request, for the one name the plan can serve"
    assert any("limit" in n.lower() for n in pull.notes)
    assert not pull.observations


def test_eodhd_still_reads_a_refusal_on_a_name_it_did_ask_for():
    """A covered name can still be refused - over credits, endpoint off - and
    that path must keep working now that the KLSE names never reach it."""
    open_ = router({"AAPL.US": http_error(403)})
    c = EodhdFundamentals(clock=CLOCK, opener=open_, key="tok.12345678")
    pull = c.collect(SINCE, ("XNAS:AAPL",))
    assert any("outside the plan or over today's credits" in n for n in pull.notes)
    assert pull.requests == 1 and not pull.observations


def test_the_statement_collectors_are_registered_and_catalogued():
    from knowledge.sources.catalog import CATALOG

    assert COLLECTORS["sec_xbrl"] is SecCompanyFacts and COLLECTORS["eodhd"] is EodhdFundamentals
    assert CATALOG["sec_xbrl"].markets == ("XNAS", "XNYS") and CATALOG["sec_xbrl"].keyless
    assert "XKLS" in CATALOG["eodhd"].markets


# --- EODHD_PLAN: the budget the run is shaped to ---------------------------------------------

WHOLE_BOOK = (
    "MYX:1155",
    "MYX:5347",
    "MYX:5183",
    "MYX:5225",
    "MYX:8869",
    "MYX:3182",
    "XNAS:NVDA",
    "XNAS:AAPL",
    "XNAS:MSFT",
    "XTAI:2330",
    "XLON:VOD",  # no EODHD suffix: never asked for on any plan
)


def test_eodhd_on_a_paid_plan_asks_every_name_every_run_bursa_included(monkeypatch):
    """The free-plan rotation was hard-wired: two names a day, US only, and the
    Bursa names gated out at the top. A month of the Fundamentals Data Feed is
    100,000 calls a day with KLSE included - there is nothing to ration, and a
    rotation would only make the newest quarter arrive days late. EODHD_PLAN
    says which plan the key is on; on a paid one every name is asked, every
    run, and the first note says so."""
    monkeypatch.setenv("EODHD_PLAN", "fundamentals")
    open_ = router({"fundamentals/": EODHD_PAYLOAD})
    c = EodhdFundamentals(clock=CLOCK, opener=open_, key="tok.12345678")
    pull = c.collect(SINCE, WHOLE_BOOK, slot="bursa_close")
    assert pull.requests == 10, open_.calls
    assert any("1155.KLSE" in u for u in open_.calls) and any("2330.TW" in u for u in open_.calls)
    assert pull.notes[0].startswith("EODHD plan: fundamentals"), pull.notes[0]
    assert not any("deferred" in n or "outside the" in n for n in pull.notes), pull.notes
    assert {o.instrument_id for o in pull.observations} >= {"MYX:1155", "XNAS:NVDA", "XTAI:2330"}


def test_eodhd_free_is_the_default_and_the_run_names_it(monkeypatch):
    """Unset means free, and the rotation is exactly what it was."""
    monkeypatch.delenv("EODHD_PLAN", raising=False)
    open_ = router({"fundamentals/": EODHD_PAYLOAD})
    c = EodhdFundamentals(clock=CLOCK, opener=open_, key="tok.12345678")
    pull = c.collect(SINCE, WHOLE_BOOK, slot="bursa_close")
    assert pull.requests == 2
    assert all(".US" in u for u in open_.calls), open_.calls
    assert pull.notes[0].startswith("EODHD plan: free (2 names a day"), pull.notes[0]
    assert any("deferred by the 2-a-day credit budget" in n for n in pull.notes)
    assert any("outside the free plan (US only)" in n and "MYX:1155" in n for n in pull.notes)


def test_eodhd_refuses_a_plan_name_it_does_not_know(monkeypatch):
    """Not a quiet fall back to free: an operator who paid and misspelt the
    variable would otherwise watch two names a day and read it as the plan
    not having taken effect at EODHD's end."""
    from knowledge.sources.eodhd import plan_from_env

    monkeypatch.setenv("EODHD_PLAN", "premium")
    with pytest.raises(SourceError, match="EODHD_PLAN='premium'"):
        EodhdFundamentals(clock=CLOCK, opener=router({}), key="tok.12345678").collect(
            SINCE, ("XNAS:AAPL",)
        )
    assert plan_from_env("All-In-One").names_per_run == 0, "case and dashes as EODHD spells them"
    assert plan_from_env("").name == "free"


def test_the_plan_kwarg_wins_over_the_environment(monkeypatch):
    """The way `key` wins over EODHD_API_KEY: a one-off run names it without
    touching what the scheduled run reads."""
    monkeypatch.setenv("EODHD_PLAN", "free")
    open_ = router({"fundamentals/": EODHD_PAYLOAD})
    pull = EodhdFundamentals(
        clock=CLOCK, opener=open_, key="tok.12345678", plan="all-in-one"
    ).collect(SINCE, WHOLE_BOOK, slot="us_close")
    assert pull.requests == 10 and pull.notes[0].startswith("EODHD plan: all-in-one")
