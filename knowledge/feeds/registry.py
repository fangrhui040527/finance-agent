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
