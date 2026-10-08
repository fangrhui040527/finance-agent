"""A sweep records what each source actually did, and one source cannot sink the run.

Each test here fails on the code before 2026-10-08:

  * a structured source that read one name of three was stored `ok`, exactly
    like one that read all three (Alpha Vantage refused MSFT and NVDA on 30 of
    31 runs, every one `ok`);
  * EODHD was refused on every name of 63 runs and every one was `ok`, with 0
    rows - and in bursa_close it asked two KLSE names the plan always refuses;
  * a collector raising anything but SourceError ended the process with exit 1,
    and collect.yml commits only on 0 or 3, so the run's prices, marks and
    every source already read were discarded;
  * a reply cut off mid-body (`http.client.IncompleteRead`) was not an OSError
    and escaped the transport's handlers;
  * Jin10's one page covers under an hour of a 3-15 hour window, and NST's
    50-item feed overflows between daily polls; both were recorded `ok`.
"""

from __future__ import annotations

import http.client
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from knowledge.corpus import Corpus
from knowledge.facts import FactBook
from knowledge.feeds.adapter import FeedError
from knowledge.feeds.rss import RssFeed
from knowledge.sources.alphavantage import AlphaVantageNews, classify_notice
from knowledge.sources.base import Collector, PlanExcluded, Pull, SourceError
from knowledge.sources.eodhd import EodhdFundamentals
from knowledge.sources.jin10 import Jin10FlashCollector
from knowledge.sweep import run_sweep
from tests.conftest import FakeResponse
from tests.test_sweep import INDEX, CannedCollector, Cfg, RecordingAdapters, collectors_returning

NOW = datetime(2026, 10, 6, 15, 59, tzinfo=UTC)


@pytest.fixture
def stores(tmp_path):
    return str(tmp_path / "corpus.db"), str(tmp_path / "facts.db")


def _sweep(cfg, slot, stores, *, collectors=None, adapters=None, sources=None):
    corpus_db, facts_db = stores
    return run_sweep(
        cfg,
        slot,
        sources=sources,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters or RecordingAdapters({}),
        collector_for=collectors,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
        force=True,
    )


def _rows(stores, source):
    corpus_db, facts_db = stores
    with Corpus(corpus_db) as c, FactBook(facts_db) as f:
        sweeps = [r for r in c.sweeps(limit=50) if r["source"] == source]
        pulls = [p for p in f.pulls() if p["source"] == source]
    return sweeps, pulls


def _canned(name, pull=None, raise_=None):
    c = CannedCollector(pull=pull, raise_=raise_)
    c.name = name
    return c


# --- structured sources: per-name outcomes ---------------------------------------------------


def test_a_structured_source_that_read_one_name_of_three_is_degraded_not_ok(stores):
    pull = Pull(
        asked=["XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA"],
        failed=[
            ("XNAS:MSFT", "alphavantage throttled (more than 1 request per second)"),
            ("XNAS:NVDA", "alphavantage throttled (more than 1 request per second)"),
        ],
        notes=["XNAS:MSFT: throttled", "XNAS:NVDA: throttled"],
    )
    av = _canned("alphavantage_news", pull)
    report = _sweep(
        Cfg(sources=("alphavantage_news",)),
        "us_close",
        stores,
        collectors=collectors_returning(alphavantage_news=av),
    )
    sweeps, pulls = _rows(stores, "alphavantage_news")
    assert pulls[0]["status"] == "degraded" and sweeps[0]["status"] == "degraded"
    assert "2 of 3 names could not be read" in sweeps[0]["detail"]
    assert report.exit_code == 3, "a run that lost two of three names is not a clean run"


def test_a_structured_source_that_read_most_names_stays_ok(stores):
    pull = Pull(
        asked=["XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA"],
        failed=[("XNAS:NVDA", "HTTP Error 500")],
    )
    av = _canned("alphavantage_news", pull)
    report = _sweep(
        Cfg(sources=("alphavantage_news",)),
        "us_close",
        stores,
        collectors=collectors_returning(alphavantage_news=av),
    )
    sweeps, pulls = _rows(stores, "alphavantage_news")
    assert pulls[0]["status"] == "ok" and sweeps[0]["status"] == "ok"
    assert report.exit_code == 0


def test_every_asked_name_refused_by_the_plan_is_a_skip_with_its_reason(stores):
    pull = Pull(
        asked=["XNAS:NVDA", "XNAS:AAPL"],
        refused=[
            ("XNAS:NVDA", "outside the plan or over today's credits"),
            ("XNAS:AAPL", "outside the plan or over today's credits"),
        ],
    )
    eodhd = _canned("eodhd", pull)
    report = _sweep(
        Cfg(sources=("eodhd",)), "us_close", stores, collectors=collectors_returning(eodhd=eodhd)
    )
    sweeps, pulls = _rows(stores, "eodhd")
    assert pulls[0]["status"] == "skipped"
    assert "every asked name was refused" in pulls[0]["detail"]
    assert sweeps[0]["detail"].startswith("skipped:"), "sweep_silence reads the sweeps table"
    assert report.results[0].status == "skipped" and report.exit_code == 0
    with FactBook(stores[1]) as f:
        assert f.last_success("eodhd") is None, "a run that read nothing moves no watermark"


def test_a_plan_boundary_raised_before_any_request_also_leaves_a_sweep_row(stores):
    eodhd = _canned("eodhd", raise_=PlanExcluded("eodhd: no name in this slot is on the plan"))
    _sweep(
        Cfg(sources=("eodhd",)), "us_close", stores, collectors=collectors_returning(eodhd=eodhd)
    )
    sweeps, pulls = _rows(stores, "eodhd")
    assert pulls[0]["status"] == "skipped"
    assert sweeps and sweeps[0]["detail"].startswith("skipped:")


def test_a_degraded_structured_pull_still_counts_as_read_for_the_watermark(stores):
    """The corpus's rule for news, now the fact book's too: the names that were
    reached were read for the whole window, so the next run starts here."""
    av = _canned("alphavantage_news", Pull(asked=["A", "B", "C"], failed=[("B", "x"), ("C", "x")]))
    _sweep(
        Cfg(sources=("alphavantage_news",)),
        "us_close",
        stores,
        collectors=collectors_returning(alphavantage_news=av),
    )
    with FactBook(stores[1]) as f:
        assert f.last_success("alphavantage_news") == NOW


# --- one source cannot sink the run ----------------------------------------------------------


def test_a_collector_that_raises_an_unexpected_error_fails_alone(stores):
    broken = _canned("fred", raise_=ValueError("could not convert string to float: 'n/a'"))
    after = _canned("dbnomics", Pull(notes=["read"]))
    report = _sweep(
        Cfg(sources=("fred", "dbnomics")),
        "all",
        stores,
        collectors=collectors_returning(fred=broken, dbnomics=after),
        sources=("fred", "dbnomics"),
    )
    by = {r.name: r for r in report.results}
    assert by["fred"].status == "failed" and "ValueError" in by["fred"].detail
    assert by["dbnomics"].status == "ok" and after.seen, "the source after it still ran"
    assert report.exit_code == 3, "3 commits what was collected; 1 discarded all of it"
    sweeps, pulls = _rows(stores, "fred")
    assert sweeps[0]["status"] == "failed" and "crashed: ValueError" in sweeps[0]["detail"]
    assert pulls[0]["status"] == "failed"


def test_a_news_adapter_that_raises_an_unexpected_error_fails_alone(stores):
    class Exploding(RecordingAdapters):
        def __call__(self, name, **kw):
            if name == "nst_business":
                raise KeyError("feed registry lost its row")
            return super().__call__(name, **kw)

    report = _sweep(
        Cfg(sources=("nst_business", "fmt_business")),
        "bursa_close",
        stores,
        adapters=Exploding({}),
    )
    by = {r.name: r for r in report.results}
    assert by["nst_business"].status == "failed" and "KeyError" in by["nst_business"].detail
    assert by["fmt_business"].status in ("ok", "degraded")
    assert report.exit_code == 3


def test_a_reply_cut_off_mid_body_is_a_source_error_not_a_crash():
    class Probe(Collector):
        name = "probe"

        def collect(self, since, instruments=(), slot="all"):
            return Pull()

    def opener(req, timeout=None):
        raise http.client.IncompleteRead(b"partial", 1024)

    with pytest.raises(SourceError, match="IncompleteRead"):
        Probe(opener=opener, sleep=lambda s: None).get_json("https://example.test/x")


def test_an_rss_reply_cut_off_mid_body_is_a_feed_error():
    def opener(req, timeout=None):
        raise http.client.IncompleteRead(b"partial", 1024)

    feed = RssFeed("https://x.my/feed", name="x", opener=opener, sleep=lambda s: None)
    with pytest.raises(FeedError, match="IncompleteRead"):
        feed.fetch(NOW - timedelta(days=1))


# --- EODHD: no fallback to names the plan cannot serve --------------------------------------


def test_eodhd_with_only_bursa_names_on_the_free_plan_asks_nothing_and_says_why(monkeypatch):
    monkeypatch.delenv("EODHD_PLAN", raising=False)
    asked, deferred = EodhdFundamentals.rotation(
        ("MYX:1155", "MYX:5347"), NOW.date(), "bursa_close"
    )
    assert (asked, deferred) == ([], [])

    def opener(req, timeout=None):
        raise AssertionError("a request was spent on a name the plan always refuses")

    c = EodhdFundamentals(clock=lambda: NOW, opener=opener, key="tok.12345678")
    with pytest.raises(PlanExcluded, match="nothing was asked"):
        c.collect(NOW - timedelta(days=1), ("MYX:1155", "MYX:5347"), slot="bursa_close")
    assert c.requests == 0


def test_eodhd_records_each_refused_name(monkeypatch):
    from tests.conftest import http_error

    monkeypatch.delenv("EODHD_PLAN", raising=False)

    def opener(req, timeout=None):
        raise http_error(403)

    c = EodhdFundamentals(clock=lambda: NOW, opener=opener, key="tok.12345678")
    pull = c.collect(NOW - timedelta(days=1), ("XNAS:AAPL",), slot="us_close")
    assert pull.asked == ["XNAS:AAPL"]
    assert [n for n, _ in pull.refused] == ["XNAS:AAPL"]


# --- Alpha Vantage: pacing and what its notices mean -----------------------------------------


def test_alphavantage_burst_notice_is_a_throttle_not_a_spent_day():
    burst = (
        "Thank you for using Alpha Vantage! Please consider spreading out your free API "
        "requests more sparingly (1 request per second). You may subscribe to any of the "
        "premium plans at https://www.alphavantage.co/premium/ to lift the free key rate limit"
    )
    assert classify_notice(burst).startswith("throttled")
    premium = (
        "Thank you for using Alpha Vantage! This is a premium endpoint. You may subscribe to "
        "any of the premium plans at https://www.alphavantage.co/premium/ to instantly unlock"
    )
    assert "premium endpoint" in classify_notice(premium)
    daily = "Our standard API rate limit is 25 requests per day. Please subscribe to premium plans"
    assert classify_notice(daily) == "quota exhausted for today"


def test_alphavantage_waits_between_names_and_records_who_failed():
    waits: list[float] = []
    feed = {"feed": []}
    burst = {"Information": "Please consider spreading out your requests (1 request per second)."}
    answers = iter([feed, burst, feed])
    c = AlphaVantageNews(
        key="k",
        clock=lambda: NOW,
        sleep=waits.append,
        opener=lambda req, timeout=None: FakeResponse(json.dumps(next(answers))),
    )
    pull = c.collect(NOW - timedelta(days=1), ("XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA"))
    assert len(waits) == 2 and all(0 < w <= 1.2 for w in waits), "paced between request starts"
    assert pull.asked == ["XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA"]
    assert [n for n, _ in pull.failed] == ["XNAS:MSFT"]
    assert "throttled" in pull.failed[0][1]


# --- windows a single reply cannot cover -----------------------------------------------------


def _beijing(dt: datetime) -> str:
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def test_jin10_records_the_part_of_the_window_its_page_never_reached():
    since = datetime(2026, 10, 6, 1, 47, tzinfo=UTC)
    page = {
        "status": 200,
        "data": [
            {
                "id": i,
                "time": _beijing(datetime(2026, 10, 6, 15, 37 + i, tzinfo=UTC)),
                "data": {"content": f"flash {i}"},
            }
            for i in range(8)
        ],
    }
    c = Jin10FlashCollector(
        clock=lambda: NOW, opener=lambda req, timeout=None: FakeResponse(json.dumps(page))
    )
    pull = c.collect(since)
    assert len(pull.articles) == 8
    assert "window not covered back to 2026-10-06 01:47Z" in pull.gap and "13.8h" in pull.gap


def test_jin10_page_that_reaches_back_to_since_has_no_gap():
    since = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    page = {
        "status": 200,
        "data": [
            {
                "id": 1,
                "time": _beijing(datetime(2026, 10, 6, 15, 30, tzinfo=UTC)),
                "data": {"content": "a"},
            },
            {
                "id": 2,
                "time": _beijing(datetime(2026, 10, 6, 14, 50, tzinfo=UTC)),
                "data": {"content": "b"},
            },
        ],
    }
    c = Jin10FlashCollector(
        clock=lambda: NOW, opener=lambda req, timeout=None: FakeResponse(json.dumps(page))
    )
    assert c.collect(since).gap == ""


def test_a_jin10_gap_makes_the_run_degraded(stores):
    pull = Pull(gap="window not covered back to 2026-10-06 01:47Z; 13.8h never served")
    jin = _canned("jin10_flash", pull)
    report = _sweep(
        Cfg(sources=("jin10_flash",)),
        "bursa_close",
        stores,
        collectors=collectors_returning(jin10_flash=jin),
    )
    sweeps, pulls = _rows(stores, "jin10_flash")
    assert sweeps[0]["status"] == "degraded" and "never served" in sweeps[0]["detail"]
    assert pulls[0]["status"] == "degraded" and report.exit_code == 3


def _rss(items: list[datetime]) -> str:
    body = "".join(
        f"<item><title>Story {i}</title><link>https://www.nst.com.my/{i}</link>"
        f"<pubDate>{format_datetime(when)}</pubDate></item>"
        for i, when in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'


def test_a_full_fixed_length_feed_that_starts_after_the_window_opened_overflowed():
    since = datetime(2026, 10, 5, 18, 31, tzinfo=UTC)
    newest = datetime(2026, 10, 6, 15, 50, tzinfo=UTC)
    items = [newest - timedelta(minutes=7 * i) for i in range(50)]  # back to 10:07Z
    feed = RssFeed(
        "https://www.nst.com.my/feed",
        name="nst_business",
        opener=lambda req, timeout=None: FakeResponse(_rss(items)),
        sleep=lambda s: None,
    )
    assert len(feed.fetch(since)) == 50
    assert feed.overflow.startswith("window overflow") and "15.6h scrolled off" in feed.overflow


def test_a_feed_that_reaches_back_past_the_window_did_not_overflow():
    since = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
    newest = datetime(2026, 10, 6, 15, 50, tzinfo=UTC)
    items = [newest - timedelta(minutes=10 * i) for i in range(50)]  # back to 07:40Z
    feed = RssFeed(
        "https://www.nst.com.my/feed",
        name="nst_business",
        opener=lambda req, timeout=None: FakeResponse(_rss(items)),
        sleep=lambda s: None,
    )
    feed.fetch(since)
    assert feed.overflow == ""


def test_an_overflowing_site_feed_makes_the_run_degraded(stores):
    newest = NOW - timedelta(minutes=9)
    items = [newest - timedelta(minutes=7 * i) for i in range(50)]

    def adapters(name, **kw):
        return RssFeed(
            "https://www.nst.com.my/feed",
            name=name,
            opener=lambda req, timeout=None: FakeResponse(_rss(items)),
            sleep=lambda s: None,
        )

    report = _sweep(Cfg(sources=("nst_business",)), "bursa_close", stores, adapters=adapters)
    sweeps, _ = _rows(stores, "nst_business")
    assert sweeps[0]["status"] == "degraded" and "window overflow" in sweeps[0]["detail"]
    assert report.exit_code == 3
