"""P8: the two optional seams - BNM FX into the FxStore, and generic RSS."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from core.market.fx import BnmFxFeed, FxFeedError
from core.market.prices import FxStore
from knowledge.feeds.adapter import FeedError
from knowledge.feeds.registry import RSS_SOURCES, UnknownSource, adapter_for
from knowledge.feeds.rss import RssFeed
from tests.conftest import http_error, opener_for, scripted_opener

# --- BNM ------------------------------------------------------------------------

BNM_BODY = json.dumps(
    {
        "data": [
            {
                "currency_code": "USD",
                "unit": 1,
                "rate": {"middle_rate": "4.1520", "date": "2026-08-29"},
            },
            {
                "currency_code": "JPY",
                "unit": 100,
                "rate": {"middle_rate": "2.8100", "date": "2026-08-29"},
            },
            {"currency_code": "XX", "rate": {}},  # malformed ROW: skipped, not fatal
        ]
    }
)


def test_bnm_rates_parse_with_units_honoured():
    rates = BnmFxFeed(opener=opener_for(BNM_BODY)).fetch_rates()
    by_code = {c: r for c, _, r in rates}
    assert by_code["USD"] == Decimal("4.1520")
    # JPY is quoted per 100 units; storing it raw would be wrong by 100x.
    assert by_code["JPY"] == Decimal("0.0281")
    assert "XX" not in by_code


def test_populate_fills_the_store_in_the_direction_sizing_asks():
    store = FxStore()
    n = BnmFxFeed(opener=opener_for(BNM_BODY)).populate(store)
    assert n == 2
    rate, asof = store.rate_asof("USD", "MYR", date(2026, 8, 31))
    assert rate == Decimal("4.1520") and asof == date(2026, 8, 29)
    # and the inverse falls out of the store's own inverse-pair lookup
    inv, _ = store.rate_asof("MYR", "USD", date(2026, 8, 31))
    assert inv == Decimal(1) / Decimal("4.1520")


def test_a_non_json_answer_raises():
    with pytest.raises(FxFeedError, match="non-JSON"):
        BnmFxFeed(opener=opener_for("<html>maintenance</html>")).fetch_rates()


def test_an_empty_data_list_raises_rather_than_an_empty_store():
    with pytest.raises(FxFeedError, match="no data list"):
        BnmFxFeed(opener=opener_for(json.dumps({"data": []}))).fetch_rates()


def test_bnm_retries_transient_failures():
    calls: list = []
    opener = scripted_opener([http_error(503), BNM_BODY], capture=calls)
    rates = BnmFxFeed(opener=opener, sleep=lambda _s: None).fetch_rates()
    assert len(calls) == 2 and rates


# --- RSS ------------------------------------------------------------------------

RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Biz</title>
<item><title>Bank posts record quarter</title>
  <link>https://example.com/a1</link>
  <description>NIM improved.</description>
  <pubDate>Sun, 30 Aug 2026 08:00:00 GMT</pubDate></item>
<item><title>Older story</title>
  <link>https://example.com/a0</link>
  <pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate></item>
<item><link>https://example.com/no-title</link></item>
</channel></rss>"""

ATOM_BODY = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Reg</title>
<entry><title>Policy statement</title>
  <link href="https://example.org/p1"/>
  <summary>Rates unchanged.</summary>
  <updated>2026-08-30T10:00:00Z</updated></entry>
</feed>"""


def _since():
    return datetime.now(UTC) - timedelta(days=365)


def test_rss_items_parse_and_the_old_and_titleless_are_dropped():
    feed = RssFeed("https://example.com/rss", opener=opener_for(RSS_BODY))
    records = feed.fetch(_since())
    assert [r.payload["url"] for r in records] == ["https://example.com/a1"]
    articles, stats = feed.normalize(records)
    assert stats.kept == 1
    assert articles[0].title == "Bank posts record quarter"
    assert articles[0].source_domain == "example.com"


def test_atom_entries_parse_too():
    feed = RssFeed("https://example.org/atom", opener=opener_for(ATOM_BODY))
    records = feed.fetch(_since())
    assert len(records) == 1
    articles, _ = feed.normalize(records)
    assert articles[0].title == "Policy statement"
    assert articles[0].published_at.isoformat().startswith("2026-08-30T10:00")


def test_unparseable_xml_raises_a_feed_error():
    feed = RssFeed("https://example.com/rss", opener=opener_for("not xml at all"))
    with pytest.raises(FeedError, match="unparseable XML"):
        feed.fetch(_since())


def test_a_relative_url_is_refused_at_construction():
    with pytest.raises(ValueError, match="absolute feed URL"):
        RssFeed("/just/a/path")


def test_rss_duplicate_titles_dedupe_in_normalize():
    twin = """<item><title>Bank posts record quarter</title>
  <link>https://example.com/a2</link>
  <description>NIM improved.</description>
  <pubDate>Sun, 30 Aug 2026 09:00:00 GMT</pubDate></item>"""
    body = RSS_BODY.replace("</channel>", twin + "</channel>")
    feed = RssFeed("https://example.com/rss", opener=opener_for(body))
    articles, stats = feed.normalize(feed.fetch(_since()))
    assert stats.duplicates >= 1
    assert len(articles) == 1


# --- registry -------------------------------------------------------------------


def test_the_registry_builds_gdelt_and_rss_by_name():
    g = adapter_for("gdelt", opener=opener_for("{}"))
    assert g.name == "gdelt"
    name = next(iter(RSS_SOURCES))
    r = adapter_for(name, opener=opener_for(RSS_BODY))
    assert isinstance(r, RssFeed) and r.name == name


def test_an_unknown_source_is_refused_with_the_known_list():
    with pytest.raises(UnknownSource, match="gdelt"):
        adapter_for("bloomberg_terminal")


# --- an undated item is not an item published now ---------------------------------
#
# Found probing BNM on 2026-09-04. Its 2020 archive is valid RSS carrying no
# <pubDate> on any item, and the adapter dated undated items `rec.fetched_at`.
# A nightly sweep would therefore have entered six-year-old central bank
# releases at the NEWEST end of every window, indistinguishable from real news.

UNDATED_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Archive</title>
<item><title>Monetary Developments in September 2020</title>
  <link>https://example.org/a</link><description>Old.</description></item>
<item><title>Reserves as at end-September 2020</title>
  <link>https://example.org/b</link><description>Also old.</description></item>
</channel></rss>"""

MIXED_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Mixed</title>
<item><title>Dated</title><link>https://example.org/1</link>
  <description>d</description><pubDate>Thu, 03 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>Undated</title><link>https://example.org/2</link>
  <description>u</description></item>
</channel></rss>"""


def test_a_feed_that_dates_nothing_is_refused_not_ingested():
    """The whole-feed case, and the dangerous one. Refusing is right: a source
    that places nothing in time cannot be windowed, and every item it returns
    would arrive stamped with the moment we asked."""
    from knowledge.feeds.adapter import FeedError
    from knowledge.feeds.rss import RssFeed

    feed = RssFeed("https://example.org/rss", name="archive", opener=opener_for(UNDATED_BODY))
    with pytest.raises(FeedError, match="dates none of its 2 items"):
        feed.fetch(datetime(2026, 9, 1, tzinfo=UTC))


def test_an_undated_item_is_dropped_and_counted_not_dated_on_arrival():
    """The partial case. The dated item survives; the undated one is dropped and
    the count says so, rather than arriving as the freshest thing in the feed."""
    from knowledge.feeds.rss import RssFeed

    feed = RssFeed("https://example.org/rss", name="mixed", opener=opener_for(MIXED_BODY))
    records = feed.fetch(datetime(2026, 9, 1, tzinfo=UTC))
    assert [r.payload["title"] for r in records] == ["Dated"]
    assert feed.undated == 1


def test_an_undated_record_can_never_be_dated_at_the_article_boundary():
    """The guard behind the guard: if _parse ever stops dropping them, the
    fabrication must fail loudly rather than resume silently."""
    from datetime import datetime as _dt

    from knowledge.feeds.adapter import FeedError, RawRecord
    from knowledge.feeds.rss import RssFeed

    feed = RssFeed("https://example.org/rss", name="mixed", opener=opener_for(MIXED_BODY))
    rec = RawRecord(
        "mixed",
        "https://example.org/2",
        _dt(2026, 9, 4, tzinfo=UTC),
        {
            "url": "https://example.org/2",
            "title": "Undated",
            "body": "",
            "domain": "example.org",
            "published_at": None,
        },
    )
    with pytest.raises(FeedError, match="undated record"):
        feed._to_article(rec)
