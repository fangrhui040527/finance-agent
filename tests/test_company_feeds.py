"""Google News and Yahoo ticker feeds, parsed offline against the injected opener."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knowledge.feeds.company_feeds import (
    EDITION_FOR_MIC,
    GoogleNewsFeed,
    YahooTickerFeed,
    finance_query,
)
from tests.conftest import opener_for

GOOGLE_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"Maybank" - Google News</title>
<item>
  <title>Maybank Q2 net profit rises 8% on stronger fee income - The Edge Malaysia</title>
  <link>https://news.google.com/rss/articles/CBMiabc?oc=5</link>
  <guid isPermaLink="false">CBMiabc</guid>
  <pubDate>Thu, 03 Sep 2026 09:12:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/CBMiabc"&gt;Maybank Q2 net profit rises 8%&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;The Edge Malaysia&lt;/font&gt;</description>
  <source url="https://theedgemalaysia.com">The Edge Malaysia</source>
</item>
<item>
  <title>Maybank branch opens in Johor - The Star</title>
  <link>https://news.google.com/rss/articles/CBMidef?oc=5</link>
  <pubDate>Thu, 03 Sep 2026 07:00:00 GMT</pubDate>
  <description>ignored</description>
  <source url="https://www.thestar.com.my">The Star</source>
</item>
</channel></rss>"""

YAHOO_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: NVDA News</title>
<item>
  <title>Nvidia beats on data-centre demand</title>
  <link>https://finance.yahoo.com/news/nvidia-beats-123.html</link>
  <pubDate>Thu, 03 Sep 2026 21:05:00 +0000</pubDate>
  <description>&lt;p&gt;The chipmaker reported revenue well above &amp;amp; beyond consensus.&lt;/p&gt;</description>
</item>
</channel></rss>"""

SINCE = datetime(2026, 9, 1, tzinfo=UTC)


def test_google_news_builds_the_edition_url_and_names_the_publisher():
    capture: list = []
    feed = GoogleNewsFeed(
        finance_query("Maybank"), edition="MY", opener=opener_for(GOOGLE_BODY, capture)
    )
    records = feed.fetch(SINCE)
    url = capture[0].full_url
    assert "news.google.com/rss/search" in url and "hl=en-MY" in url and "ceid=MY%3Aen" in url
    assert "%22Maybank%22" in url and "when%3A2d" in url
    assert [r.payload["title"] for r in records] == [
        "Maybank Q2 net profit rises 8% on stronger fee income",
        "Maybank branch opens in Johor",
    ]
    assert records[0].payload["domain"] == "theedgemalaysia.com"
    assert records[0].payload["publisher"] == "The Edge Malaysia"
    assert records[0].payload["body"] == "", "Google's description is the headline again"


def test_google_news_articles_carry_the_publisher_domain_not_google():
    feed = GoogleNewsFeed("Maybank", edition="MY", opener=opener_for(GOOGLE_BODY))
    arts, stats = feed.normalize(feed.fetch(SINCE), entity_index={"Maybank": "MYX:1155"})
    assert stats.kept == 2 and all(a.instruments == ["MYX:1155"] for a in arts)
    assert {a.source_domain for a in arts} == {"theedgemalaysia.com", "www.thestar.com.my"}
    assert all(a.published_at.tzinfo is not None for a in arts)


def test_an_unknown_edition_is_refused():
    with pytest.raises(ValueError, match="edition"):
        GoogleNewsFeed("x", edition="MARS")


def test_every_market_the_book_can_hold_has_an_edition():
    assert EDITION_FOR_MIC["XKLS"] == "MY" and EDITION_FOR_MIC["XNAS"] == "US"


def test_yahoo_ticker_feed_maps_the_symbol_through_the_price_feed_rules():
    capture: list = []
    feed = YahooTickerFeed("MYX:1155", opener=opener_for(YAHOO_BODY, capture))
    assert feed.symbol == "1155.KL"
    feed.fetch(SINCE)
    assert "s=1155.KL" in capture[0].full_url


def test_yahoo_ticker_feed_keeps_the_summary_as_clean_text():
    feed = YahooTickerFeed("XNAS:NVDA", opener=opener_for(YAHOO_BODY))
    arts, _ = feed.normalize(feed.fetch(SINCE), entity_index={"Nvidia": "XNAS:NVDA"})
    (a,) = arts
    assert a.body == "The chipmaker reported revenue well above & beyond consensus."
    assert a.instruments == ["XNAS:NVDA"] and a.source_domain == "finance.yahoo.com"


def test_yahoo_ticker_feed_refuses_an_unmappable_instrument():
    from core.market.feed import SymbolUnmappable

    with pytest.raises(SymbolUnmappable):
        YahooTickerFeed("NOPE")
