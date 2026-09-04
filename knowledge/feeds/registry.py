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
    # Liferay's asset-publisher RSS endpoint for the CURRENT year's page.
    #
    # The 2020 page's URL worked (well-formed RSS, status ok) but served the
    # 2020 archive - fetched 0 in any recent window. Swapping the year alone
    # 403s, because INSTANCE_<id> names the portlet PLACEMENT and each year page
    # has its own. The 2026 page's id was read out of its HTML:
    #
    #   id="p_p_id_com_liferay_asset_publisher_web_portlet
    #       _AssetPublisherPortlet_INSTANCE_ZkJrPGjQLX7H_"
    #
    # so this is transcribed, not guessed. One caveat that the next sweep
    # settles: the page's `subscribe-action` div is empty, which may mean RSS is
    # switched off for this instance even though the resource id exists.
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
