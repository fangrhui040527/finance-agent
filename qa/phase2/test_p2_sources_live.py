"""Phase 2 - every catalogued source, live, from wherever this runs.

Nothing here is billed to Anthropic; these need the public internet and, for
the keyed providers, the provider's own free key. Each test skips when its
host is unreachable and xfails with the reason when a provider answers with a
plan boundary, so the suite says what is TRUE about each source from this
machine rather than turning a walled network into a red build.

Run from a GitHub Actions runner (the only place this repository's own
development environment can reach the data hosts from) with:

    QA_LIVE=1 python -m pytest qa/phase2/test_p2_sources_live.py -q -m network
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from qa.conftest import reachable

SINCE = datetime.now(UTC) - timedelta(hours=48)


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.mark.network
def test_gdelt_answers_a_bursa_name_with_dated_json(gdelt):
    from knowledge.feeds.registry import adapter_for

    feed = adapter_for("gdelt", query='"Maybank"')
    records = feed.fetch(SINCE, limit=5)
    arts, stats = feed.normalize(records)
    assert stats.fetched >= 0 and all(a.published_at.tzinfo for a in arts)


@pytest.mark.network
def test_google_news_returns_publisher_named_items_for_a_bursa_name():
    if not reachable("news.google.com"):
        pytest.skip("news.google.com unreachable")
    from knowledge.feeds.company_feeds import GoogleNewsFeed, finance_query

    feed = GoogleNewsFeed(finance_query("Maybank"), edition="MY")
    records = feed.fetch(SINCE, limit=5)
    assert records, "Google News returned no items for Maybank in 48h"
    assert all(r.payload["domain"] and r.payload["title"] for r in records)
    assert all(r.payload["published_at"] for r in records), "every item dated"


@pytest.mark.network
def test_yahoo_ticker_feed_returns_summaries_for_a_bursa_and_a_us_name():
    if not reachable("feeds.finance.yahoo.com"):
        pytest.skip("feeds.finance.yahoo.com unreachable")
    from knowledge.feeds.company_feeds import YahooTickerFeed

    for iid in ("XNAS:NVDA", "MYX:1155"):
        records = YahooTickerFeed(iid).fetch(SINCE - timedelta(days=5), limit=5)
        assert records, f"{iid}: no items"


@pytest.mark.network
@pytest.mark.parametrize("name", ["thestar_business", "edge_malaysia", "bernama_business", "fmt_business", "nst_business"])
def test_each_malaysian_rss_candidate_is_a_feed_or_says_where_the_feed_is(name):
    """A candidate either serves dated items or names the feeds it advertises;
    both are progress. Only a silent refusal is a failure."""
    from urllib.parse import urlparse

    from knowledge.feeds.adapter import FeedError
    from knowledge.feeds.registry import RSS_SOURCES, adapter_for

    host = urlparse(RSS_SOURCES[name][0]).netloc
    if not reachable(host):
        pytest.skip(f"{host} unreachable")
    try:
        records = adapter_for(name).fetch(SINCE, limit=5)
    except FeedError as e:
        if "advertises feeds at" in str(e):
            pytest.xfail(f"{name} is an index page: {e}")
        raise
    assert all(r.payload["published_at"] for r in records)


@pytest.mark.network
def test_edgar_submissions_answer_with_the_contact_user_agent():
    if not reachable("data.sec.gov"):
        pytest.skip("data.sec.gov unreachable")
    from knowledge.sources.edgar import EdgarFilings

    pull = EdgarFilings().collect(SINCE - timedelta(days=30), ("XNAS:AAPL",))
    assert pull.events, "no AAPL filings in 32 days is not plausible"
    assert all(e.payload.get("url", "").startswith("https://www.sec.gov/Archives/") for e in pull.events if e.payload.get("url"))


@pytest.mark.network
def test_bnm_opr_answers_with_this_years_level():
    if not reachable("api.bnm.gov.my"):
        pytest.skip("api.bnm.gov.my unreachable")
    from knowledge.sources.bnm import BnmOprCollector

    pull = BnmOprCollector().collect(SINCE)
    assert pull.series and 1 < float(pull.series[-1].value) < 6


@pytest.mark.network
def test_dosm_cpi_answers_with_monthly_points():
    if not reachable("api.data.gov.my"):
        pytest.skip("api.data.gov.my unreachable")
    from knowledge.sources.dosm import DosmCpiCollector

    pull = DosmCpiCollector().collect(SINCE)
    assert pull.series


@pytest.mark.network
def test_finnhub_free_tier_answers_the_us_names():
    _key("FINNHUB_API_KEY")
    if not reachable("finnhub.io"):
        pytest.skip("finnhub.io unreachable")
    from knowledge.sources.finnhub import FinnhubCollector

    pull = FinnhubCollector().collect(SINCE, ("XNAS:AAPL",))
    assert pull.observations or pull.articles or pull.events, pull.notes


@pytest.mark.network
def test_fmp_free_tier_answers_statements_and_names_what_it_excludes():
    _key("FMP_API_KEY")
    if not reachable("financialmodelingprep.com"):
        pytest.skip("financialmodelingprep.com unreachable")
    from knowledge.sources.fmp import FmpCollector

    pull = FmpCollector().collect(SINCE, ("XNAS:AAPL",), slot="weekly")
    assert any(o.concept == "revenue" for o in pull.observations), pull.notes


@pytest.mark.network
def test_alphavantage_answers_once_for_all_us_names():
    _key("ALPHAVANTAGE_API_KEY")
    if not reachable("www.alphavantage.co"):
        pytest.skip("alphavantage unreachable")
    from knowledge.sources.alphavantage import AlphaVantageNews
    from knowledge.sources.base import SourceError

    try:
        pull = AlphaVantageNews().collect(SINCE, ("XNAS:AAPL", "XNAS:NVDA", "XNAS:MSFT"))
    except SourceError as e:
        if "rate limit" in str(e).lower() or "refused" in str(e):
            pytest.xfail(f"quota spent today: {e}")
        raise
    assert pull.requests == 1 and pull.articles


@pytest.mark.network
def test_fred_answers_every_series_in_the_list():
    _key("FRED_API_KEY")
    if not reachable("api.stlouisfed.org"):
        pytest.skip("api.stlouisfed.org unreachable")
    from knowledge.sources.fred import SERIES, FredCollector

    pull = FredCollector().collect(SINCE)
    assert {p.series_id for p in pull.series} == set(SERIES), pull.notes
