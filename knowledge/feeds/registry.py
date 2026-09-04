"""config.toml [sources] names -> adapter instances. Unknown names refuse.

The scoping judgement in details/10 stands: wiring all 32 keyless sources
because they are free would produce a queue nobody reads. What this registry
adds is the SEAM - enabling a source is one line of config once an entry
exists here, and a name with no entry raises instead of silently ingesting
nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from knowledge.feeds.adapter import FeedAdapter, GdeltFeed
from knowledge.feeds.rss import RssFeed


class UnknownSource(ValueError):
    """A configured source with no adapter. Refused, never quietly skipped."""


#: name -> factory. RSS sources are registered by naming their URL here, so a
#: new one is a single line - the class is already written.
FACTORIES: dict[str, Callable[..., FeedAdapter]] = {
    "gdelt": GdeltFeed,
}

#: Curated keyless RSS endpoints, each one line. Enabled via config, not here.
RSS_SOURCES: dict[str, tuple[str, str]] = {
    # name: (url, trust)
    "reuters_business": ("https://feeds.reuters.com/reuters/businessNews", "wire"),
    # NOT ENABLED, and this URL is NOT a feed - it is the landing page, kept as
    # the value deliberately so that enabling this line cannot quietly work.
    #
    # A GitHub runner read the page on 2026-09-04 (this environment's egress and
    # WebFetch both refuse bnm.gov.my). What it established, in four rounds:
    #
    #   bnm.gov.my is LIFERAY. Its feeds are AssetPublisher portlet URLs
    #   carrying p_p_resource_id=getRSS and an opaque portlet INSTANCE id. That
    #   id cannot be derived, only read off a page - which is why six guessed
    #   paths (/rss/press-release, /-/rss, /rss.xml, /feed, /press-release/rss,
    #   index.php?ch=en_rss) all returned 404. No amount of guessing reaches it.
    #
    #   /rss advertises three feeds and TWO OF THEM ARE BROKEN: the notices and
    #   speeches URLs return the HTML home page, three fetches out of three.
    #
    #   The one that does serve XML is the 2020 ARCHIVE:
    #     /press-release-2020?p_p_id=..._INSTANCE_ZHckDJtILsio&p_p_lifecycle=2
    #     &p_p_state=normal&p_p_mode=view&p_p_resource_id=getRSS
    #     &p_p_cacheability=cacheLevelPage
    #   text/xml, 23,228 bytes, stable across three fetches - and its newest
    #   item is from SEPTEMBER 2020. It also carries NO <pubDate>, so every item
    #   would be stamped with fetch time: six-year-old central bank releases
    #   entering the corpus dated today, looking exactly like current news.
    #   Do not enable it. That is the failure this repository is built to avoid.
    #
    #   The LIVE page is /press-releases (plural; /press-release is a 404) and
    #   its portlet id is ZkJrPGjQLX7H - but that id does not serve getRSS. It
    #   returns the HTML page, three fetches out of three, on both
    #   /press-releases and /press-release-2026.
    #
    # So the URL is no longer unknown. It is known, and there is no current BNM
    # press-release feed to point at. Closing this gap needs a different source
    # or a scraper, not a corrected line here - see details/09.
    "bnm_press": ("https://www.bnm.gov.my/rss", "regulator"),
}


def adapter_for(name: str, **kwargs) -> FeedAdapter:
    """Build the adapter a config name refers to. Raises `UnknownSource`."""
    if name in FACTORIES:
        return FACTORIES[name](**kwargs)
    if name in RSS_SOURCES:
        url, trust = RSS_SOURCES[name]
        return RssFeed(url, name=name, trust=trust, **kwargs)
    known = sorted(list(FACTORIES) + list(RSS_SOURCES))
    raise UnknownSource(
        f"no adapter registered for source {name!r}. Known: {', '.join(known)}. "
        f"An RSS source is one line in knowledge/feeds/registry.py; anything "
        f"else is a FeedAdapter subclass. Enabling a name without an adapter "
        f"would ingest nothing and read as a quiet news day."
    )
