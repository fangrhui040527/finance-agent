"""config.toml [sources] names -> adapter instances. Unknown names refuse.

The scoping judgement in details/10 stands: wiring all 32 keyless sources
because they are free would produce a queue nobody reads. What this registry
adds is the SEAM - enabling a source is one line of config once an entry
exists here, and a name with no entry raises instead of silently ingesting
nothing.

Two kinds of entry:

  * FACTORIES - a class per source with its own transport (GDELT's DOC API,
    Google News search, a Yahoo ticker feed). Per-instrument ones take the
    name or id as a keyword; called bare they build a harmless probe instance,
    which is how config validation confirms the adapter exists.
  * RSS_SOURCES - one line per plain RSS/Atom endpoint. The class is written;
    a new Malaysian business feed is a URL and a trust tier.

Structured (non-news) sources live in knowledge/sources/registry.py; the
catalogue in knowledge/sources/catalog.py names both kinds.
"""

from __future__ import annotations

from collections.abc import Callable

from knowledge.feeds.adapter import FeedAdapter, GdeltFeed
from knowledge.feeds.company_feeds import GoogleNewsFeed, YahooTickerFeed
from knowledge.feeds.rss import RssFeed


class UnknownSource(ValueError):
    """A configured source with no adapter. Refused, never quietly skipped."""


#: name -> factory. Per-instrument factories accept `query=` (google_news) or
#: `instrument_id=` (yahoo_rss); bare, they build a probe instance.
FACTORIES: dict[str, Callable[..., FeedAdapter]] = {
    "gdelt": GdeltFeed,
    "google_news": GoogleNewsFeed,
    "yahoo_rss": YahooTickerFeed,
}

#: Curated keyless RSS endpoints, each one line. Enabled via config, not here.
#:
#: The Malaysian entries marked CANDIDATE were registered from published
#: feed indexes rather than a verified fetch - this environment cannot reach
#: them. `ask.py sources --probe`, run from a GitHub Actions runner, fetches
#: each once; an index page answers with the feeds it advertises (see
#: rss._excerpt), which is how the right URL gets read off it. Enable a
#: candidate only after the probe shows items with dates.
#:
#: THE URLS BELOW ARE ONE GUESS EACH, and four of the five are disabled on the
#: strength of one 404 apiece from 2026-09-04. That is the largest single
#: constraint on Bursa coverage - 62 articles against the US names' 1,968 -
#: and it is a guess, not a finding. `.github/workflows/bursa-feeds-probe.yml`
#: settles it from a runner: it reads the RSS autodiscovery tags off each
#: publisher's own pages, tries the conventional paths beside them, and reports
#: which URL serves items WITH DATES. Replace a URL here with what it names.
RSS_SOURCES: dict[str, tuple[str, str]] = {
    # name: (url, trust)
    "reuters_business": ("https://feeds.reuters.com/reuters/businessNews", "wire"),
    # DEAD, settled by the 2026-09-11 runner probe. Every candidate answered 404
    # and neither the homepage nor the business page advertises any feed at all:
    #   /rss/business/business-news  /rss/Business  /rss/business
    #   /rss/editors-pick            /rss
    # Not a wrong guess to correct - The Star serves no RSS this probe can find.
    "thestar_business": ("https://www.thestar.com.my/rss/business/business-news", "curated_news"),
    # DEAD, same probe, same shape: /rss.html /rss /feed /rss.xml and the old
    # theedgemarkets.com/rss.xml all 404, and the pages advertise nothing.
    "edge_malaysia": ("https://theedgemalaysia.com/rss.html", "curated_news"),
    # UNUSABLE, not absent. This URL answers with 10 items and dates NONE of
    # them, twice measured (2026-09-04 and 2026-09-11). The collector windows by
    # time, so an undated feed is a feed it cannot use however current it is.
    "bernama_business": ("https://www.bernama.com/en/rssfeed.php", "wire"),
    # The publisher's OWN advertised feed, read off its autodiscovery tag on
    # 2026-09-11 and fresher than the category path we used before it (02:58
    # against 02:25 on the same probe). A site that points at its feed is the
    # authority on where the feed is.
    "fmt_business": ("https://www.freemalaysiatoday.com/feeds/rss/headlines", "general_news"),
    # RECOVERED 2026-09-11. `/business/rss` was a guess and it 404s; `/feed`
    # answers with 50 items, 50 of them dated, newest 11:09 the same morning.
    # NST was never dead - the registered URL was one path wrong, which is the
    # whole reason the probe exists rather than trusting a single 404.
    "nst_business": ("https://www.nst.com.my/feed", "curated_news"),
    # REGISTERED, NOT ENABLED. Four sweeps settled this; the record, so nobody
    # repeats it:
    #
    #   /rss/press-release              404 - path predates the site redesign
    #   /rss                            an HTML landing page, no autodiscovery
    #   /press-release-2020?...getRSS   WORKS - valid RSS, but the 2020 archive,
    #                                   AND IT DATES NOTHING: 0 <pubDate> in
    #                                   23,228 bytes, measured twice. Not "0
    #                                   items in a recent window" as this line
    #                                   first said - undated items passed the
    #                                   window filter and were dated on arrival,
    #                                   so every one would have landed at the
    #                                   NEWEST end of it. knowledge/feeds/rss.py
    #                                   now refuses a feed that dates nothing.
    #   /press-release-2026?...getRSS   HTML, not a feed
    #
    # The endpoint SHAPE is right - `p_p_resource_id=getRSS` on Liferay's asset
    # publisher - and the instance id below was transcribed from the 2026 page's
    # own markup, not guessed. It still returns HTML, and the page's
    # `subscribe-action` div is empty where a feed-enabled page carries the
    # subscribe control: RSS is switched OFF for that portlet instance. The 2020
    # instance has it on, which is why only the archive answers.
    #
    # Anyone trying again: the non-year-scoped pages /pr and /press-releases-main
    # are the remaining candidates, each with its own instance id readable from
    # its HTML. Weigh it first - BNM is a central bank, so even working this is
    # macro news that will not name a Bursa company.
    "bnm_press": (
        "https://www.bnm.gov.my/press-release-2026"
        "?p_p_id=com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZkJrPGjQLX7H"
        "&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
        "&p_p_resource_id=getRSS&p_p_cacheability=cacheLevelPage"
        "&_com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZkJrPGjQLX7H_currentURL=%2Fpress-release-2026"
        "&_com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZkJrPGjQLX7H_portletAjaxable=true",
        "regulator",
    ),
}


def is_news_source(name: str) -> bool:
    return name in FACTORIES or name in RSS_SOURCES


def adapter_for(name: str, **kwargs) -> FeedAdapter:
    """Build the adapter a config name refers to. Raises `UnknownSource`."""
    if name in FACTORIES:
        return FACTORIES[name](**kwargs)
    if name in RSS_SOURCES:
        url, trust = RSS_SOURCES[name]
        return RssFeed(url, name=name, trust=trust, **kwargs)
    known = sorted(list(FACTORIES) + list(RSS_SOURCES))
    raise UnknownSource(
        f"no adapter registered for source {name!r}. Known news sources: {', '.join(known)}. "
        f"An RSS source is one line in knowledge/feeds/registry.py; anything "
        f"else is a FeedAdapter subclass. Structured sources are listed in "
        f"knowledge/sources/registry.py. Enabling a name without an adapter "
        f"would ingest nothing and read as a quiet news day."
    )
