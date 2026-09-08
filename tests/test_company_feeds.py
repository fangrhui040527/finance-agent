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


# --- a company is asked for by every name it is printed under ---------------
#
# Petronas Chemicals collected ZERO articles across 1,673 in the corpus while
# five other Bursa names collected 1 to 12. The alias table had held "PCHEM"
# all along - the linker reads every alias - but the QUERY used only the
# display name. The corpus could recognise a name it never asked for.


def test_one_name_is_still_one_quoted_phrase():
    """The old shape, unchanged: no stray parentheses around a single name."""
    q = finance_query("Maybank")
    assert q.startswith('"Maybank" (stock OR ')


def test_several_names_are_ORed_inside_their_own_group():
    """Without the inner parentheses the OR would swallow the finance clause
    and the query would match any article containing the word 'stock'."""
    q = finance_query(["Petronas Chemicals", "PCHEM"])
    assert q.startswith('("Petronas Chemicals" OR "PCHEM") (stock OR ')


def test_the_book_name_that_collected_nothing_is_now_asked_for_by_its_ticker():
    """The regression, named. PCHEM is what the Malaysian press prints."""
    from knowledge.graph.ids import instrument_id, search_names

    names = search_names()[instrument_id("MYX:5183") or "MYX:5183"]
    q = finance_query(names)
    assert '"PCHEM"' in q
    assert '"Petronas Chemicals"' in q


def test_the_gdelt_phrase_floor_is_not_applied_here():
    """MIN_PHRASE_CHARS = 5 exists because GDELT's DOC API refuses a shorter
    quoted phrase with a plain-text error. It is one API's constraint, not a
    judgement about precision - and Google News has no such limit. Applied
    here it would drop "TNB", which is what Tenaga Nasional is called."""
    from knowledge.graph.ids import instrument_id, search_names

    names = search_names()[instrument_id("MYX:5347") or "MYX:5347"]
    q = finance_query(names)
    assert '"TNB"' in q, "the three-character form the press actually uses"


def test_the_number_of_names_is_bounded():
    """Google News is asked over a GET; an unbounded query gets truncated
    somewhere nobody chose."""
    from knowledge.feeds.company_feeds import FINANCE_TERMS, MAX_NAMES

    q = finance_query([f"Name{i}" for i in range(12)])
    assert q.count(" OR ") == (MAX_NAMES - 1) + (len(FINANCE_TERMS) - 1)
    assert '"Name0"' in q and f'"Name{MAX_NAMES}"' not in q


def test_repeats_and_blanks_do_not_reach_the_query():
    q = finance_query(["Maybank", "Maybank", "", "  ", "Malayan Banking"])
    assert q.startswith('("Maybank" OR "Malayan Banking") (')


def test_no_name_at_all_is_refused_rather_than_searched_for_nothing():
    """An empty phrase would return the whole finance clause - every article
    mentioning 'stock' - attributed to one company."""
    with pytest.raises(ValueError):
        finance_query([])


def test_search_names_is_keyed_canonically_like_display_names():
    """entities.yaml writes MYX:1155 and ids resolve to XKLS:1155; keying on
    the raw form is a lookup that silently never matches."""
    from knowledge.graph.ids import search_names

    assert "XKLS:1155" in search_names()
    assert search_names()["XKLS:1155"][0] == "Maybank"


def test_search_names_carries_more_than_display_names_does():
    """The whole point: the two answer different questions."""
    from knowledge.graph.ids import display_names, search_names

    iid = "XKLS:5183"
    assert display_names()[iid] == "Petronas Chemicals"
    assert len(search_names()[iid]) > 1
    assert display_names()[iid] == search_names()[iid][0]
