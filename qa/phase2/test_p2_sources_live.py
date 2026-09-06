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

from qa.conftest import ROOT, reachable

SINCE = datetime.now(UTC) - timedelta(hours=48)


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} not set")
    return value


def _enabled() -> frozenset[str]:
    """The sources a scheduled sweep actually reads (`[sources] enabled`)."""
    from core.config import load

    return frozenset(load(ROOT / "config.toml").sources)


@pytest.mark.network
def test_gdelt_answers_a_bursa_name_with_dated_json(gdelt):
    from knowledge.feeds.adapter import FeedError
    from knowledge.feeds.registry import adapter_for

    feed = adapter_for("gdelt", query='"Maybank"')
    try:
        records = feed.fetch(SINCE, limit=5)
    except FeedError as e:
        if "429" in str(e):
            pytest.xfail(f"GDELT throttled this runner (one request per 5s per client): {e}")
        raise
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

    since = SINCE - timedelta(days=5)
    assert YahooTickerFeed("XNAS:NVDA").fetch(since, limit=5), "XNAS:NVDA: no items in 7 days"
    # The same feed answers the Bursa names with an empty, well-formed document:
    # the collector has read it empty for all six in every sweep since
    # 2026-09-04, and the system test's second runner pass (2026-09-05) saw the
    # same for Maybank. That is a fact about the source under region=US, not a
    # defect in the reader, so it is an expected failure with the reason; the
    # day it returns items this test passes, which is the signal that Yahoo's
    # ticker feed covers Bursa after all (region=MY is the untried lever).
    if not YahooTickerFeed("MYX:1155").fetch(since, limit=5):
        pytest.xfail("Yahoo's ticker RSS carries no items for Bursa names under region=US")


@pytest.mark.network
@pytest.mark.parametrize(
    "name",
    ["thestar_business", "edge_malaysia", "bernama_business", "fmt_business", "nst_business"],
)
def test_each_malaysian_rss_candidate_is_a_feed_or_says_where_the_feed_is(name):
    """An ENABLED feed must serve dated items. A registered candidate that is
    not enabled is measured, not judged: it passes the day it answers with
    dated items (the signal to enable it) and xfails with the exact reason
    otherwise - a 404 on the guessed path, an index page, undated items - so
    the run's summary says what is true about each one this week without
    turning a dead guess into a red build. Only a silent refusal by an enabled
    feed is a failure."""
    from urllib.parse import urlparse

    from knowledge.feeds.adapter import FeedError
    from knowledge.feeds.registry import RSS_SOURCES, adapter_for

    host = urlparse(RSS_SOURCES[name][0]).netloc
    if not reachable(host):
        pytest.skip(f"{host} unreachable")
    enabled = name in _enabled()
    try:
        records = adapter_for(name).fetch(SINCE, limit=5)
    except FeedError as e:
        if "advertises feeds at" in str(e):
            pytest.xfail(f"{name} is an index page: {e}")
        if not enabled:
            pytest.xfail(f"{name} is a registered candidate, not enabled: {e}")
        raise
    dated = bool(records) and all(r.payload["published_at"] for r in records)
    if not enabled and not dated:
        pytest.xfail(
            f"{name} is a registered candidate, not enabled: "
            + ("no items in 48h" if not records else "items carry no date")
        )
    assert all(r.payload["published_at"] for r in records)


@pytest.mark.network
def test_edgar_submissions_answer_with_the_contact_user_agent():
    if not reachable("data.sec.gov"):
        pytest.skip("data.sec.gov unreachable")
    from knowledge.sources.edgar import EdgarFilings

    pull = EdgarFilings().collect(SINCE - timedelta(days=30), ("XNAS:AAPL",))
    assert pull.events, "no AAPL filings in 32 days is not plausible"
    assert all(
        e.payload.get("url", "").startswith("https://www.sec.gov/Archives/")
        for e in pull.events
        if e.payload.get("url")
    )


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
    if any(o.concept == "revenue" for o in pull.observations):
        return
    # The free plan's boundary moves. On 2026-09-05 the income statement and
    # the earnings calendar answered 402 ("the plan does not include it") while
    # grades, targets and the transcript still landed. The collector's promise
    # is that an exclusion is NAMED, with the endpoint, and the rest of the pull
    # survives - that is what is asserted; a revenue figure is asserted only
    # when the plan serves one.
    named = [n for n in pull.notes if "income-statement" in n and "plan" in n.lower()]
    if named:
        assert pull.events or pull.observations or pull.articles, "the rest of the pull vanished"
        pytest.xfail(f"the FMP plan excludes the income statement today: {named[0][:200]}")
    pytest.fail(f"no revenue figure and no exclusion named for it: {pull.notes}")


@pytest.mark.network
def test_alphavantage_answers_once_per_us_name_with_articles_for_each():
    """One request PER name, three of the day's twenty-five. The vendor's
    multi-ticker filter is an AND (articles that mention every listed name at
    once): a single request for three names returned three articles on a busy
    Friday and none on a Saturday, which is how this was found."""
    _key("ALPHAVANTAGE_API_KEY")
    if not reachable("www.alphavantage.co"):
        pytest.skip("alphavantage unreachable")
    from knowledge.sources.alphavantage import AlphaVantageNews
    from knowledge.sources.base import SourceError

    names = ("XNAS:AAPL", "XNAS:NVDA", "XNAS:MSFT")
    try:
        pull = AlphaVantageNews().collect(SINCE, names)
    except SourceError as e:
        if "quota" in str(e).lower() or "rate limit" in str(e).lower() or "refused" in str(e):
            pytest.xfail(f"quota spent today: {e}")
        raise
    assert pull.requests == len(names), pull.notes
    assert pull.articles, "three of the most covered names on earth had no article in 48h"
    linked = {i for a in pull.articles for i in a.instruments}
    assert linked & set(names), "no article was linked to any of the names at relevance >= 0.2"


@pytest.mark.network
def test_fred_answers_every_series_in_the_list():
    _key("FRED_API_KEY")
    if not reachable("api.stlouisfed.org"):
        pytest.skip("api.stlouisfed.org unreachable")
    from knowledge.sources.fred import SERIES, FredCollector

    pull = FredCollector().collect(SINCE)
    assert {p.series_id for p in pull.series} == set(SERIES), pull.notes


# --- 2026-09-05: the four sites, by their free routes ----------------------------------------


@pytest.mark.network
def test_jin10_flash_answers_with_dated_chinese_items():
    if not reachable("flash-api.jin10.com"):
        pytest.skip("flash-api.jin10.com unreachable")
    from knowledge.sources.jin10 import Jin10FlashCollector

    pull = Jin10FlashCollector().collect(SINCE)
    assert pull.requests == 1
    assert pull.articles, (
        "Jin10 publishes hundreds of flashes a day; none in 48h means the endpoint moved"
    )
    assert all(a.language == "zh" and a.published_at.tzinfo for a in pull.articles)


@pytest.mark.network
def test_jin10_calendar_answers_with_todays_releases():
    """Registered, not enabled (2026-09-05): the CDN host is out of DNS and
    rili.jin10.com answers 404 on every documented path. The test stays so the
    day a path answers again is noticed; until then it xfails naming each
    host's verdict, which is the same line the probe prints."""
    if not reachable("cdn-rili.jin10.com") and not reachable("rili.jin10.com"):
        pytest.skip("jin10 calendar hosts unreachable")
    from knowledge.sources.base import SourceError
    from knowledge.sources.jin10 import Jin10CalendarCollector

    try:
        pull = Jin10CalendarCollector().collect(SINCE)
    except SourceError as e:
        if "no calendar path answered" in str(e):
            pytest.xfail(f"the calendar document has moved: {e}")
        raise
    assert pull.events, "a weekday calendar with no release at all is not plausible"
    assert all(e.instrument_id.startswith("MACRO:") for e in pull.events)


@pytest.mark.network
def test_dbnomics_answers_the_starter_list_and_names_what_it_does_not_know():
    """Each id in the starter list is a best reading of the provider's codes;
    this test is how the list is pruned. It passes when at least half answer
    and prints the notes for the rest."""
    if not reachable("api.db.nomics.world"):
        pytest.skip("api.db.nomics.world unreachable")
    from knowledge.sources.dbnomics import SERIES, DbnomicsCollector

    pull = DbnomicsCollector().collect(SINCE)
    answered = {p.series_id for p in pull.series}
    assert len(answered) * 2 >= len(SERIES), f"answered {sorted(answered)}; notes {pull.notes}"
    if pull.notes:
        pytest.xfail(f"{len(pull.notes)} id(s) to fix in the starter list: {pull.notes}")


@pytest.mark.network
def test_twse_openapi_answers_for_tsmc():
    if not reachable("openapi.twse.com.tw"):
        pytest.skip("openapi.twse.com.tw unreachable")
    from knowledge.sources.twse import TwseOpenApiCollector

    pull = TwseOpenApiCollector().collect(SINCE, ("XTAI:2330",))
    concepts = {o.concept for o in pull.observations}
    assert {"pe_ttm", "revenue_month", "close"} <= concepts, (concepts, pull.notes)
    assert pull.requests == 3


@pytest.mark.network
def test_finmind_answers_for_tsmc_keyless_or_names_its_quota():
    if not reachable("api.finmindtrade.com"):
        pytest.skip("api.finmindtrade.com unreachable")
    from knowledge.sources.finmind import FinMindCollector

    pull = FinMindCollector().collect(SINCE, ("XTAI:2330",))
    if not pull.observations and pull.notes and all("quota" in n for n in pull.notes):
        pytest.xfail(f"FinMind quota for this runner's address is spent: {pull.notes[0][:120]}")
    concepts = {o.concept for o in pull.observations}
    assert "revenue_month" in concepts, (concepts, pull.notes)


# --- 2026-09-06: statement lines for the analyst engines --------------------------------


@pytest.mark.network
def test_sec_xbrl_company_facts_carry_filed_dates_for_aapl():
    if not reachable("data.sec.gov"):
        pytest.skip("data.sec.gov unreachable")
    from knowledge.sources.sec_xbrl import SecCompanyFacts

    pull = SecCompanyFacts().collect(SINCE, ("XNAS:AAPL",))
    assert pull.requests == 1
    concepts = {o.concept for o in pull.observations}
    assert {
        "revenue",
        "revenue_fy",
        "net_income",
        "cash_from_operations",
        "total_assets",
    } <= concepts, sorted(concepts)
    assert all(o.period_end is None or o.known_at >= o.period_end for o in pull.observations)
    assert any(o.payload.get("derived") for o in pull.observations if o.concept == "revenue"), (
        "Q4 is derived from FY"
    )


@pytest.mark.network
def test_eodhd_answers_one_us_name_or_names_its_plan():
    """With a key: statements for one US name, or the plan's boundary named. Without
    one: the collector skips itself, which is the documented behaviour."""
    if not reachable("eodhd.com"):
        pytest.skip("eodhd.com unreachable")
    from knowledge.sources.base import KeyMissing
    from knowledge.sources.eodhd import EodhdFundamentals

    if not os.environ.get("EODHD_API_KEY", "").strip():
        with pytest.raises(KeyMissing):
            EodhdFundamentals().collect(SINCE, ("XNAS:AAPL",))
        pytest.xfail("EODHD_API_KEY is not set; the collector skips itself as documented")
    pull = EodhdFundamentals().collect(SINCE, ("XNAS:AAPL",))
    if not pull.observations:
        pytest.xfail(f"EODHD answered without statements: {pull.notes}")
    assert {"revenue", "total_assets"} <= {o.concept for o in pull.observations}
