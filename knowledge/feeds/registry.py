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
    # Liferay's asset-publisher RSS endpoint, which is what /rss links to and
    # what three guesses on 2026-09-03 failed to find (/rss/press-release is a
    # 404; /rss is a landing page carrying no autodiscovery tags). Supplied by
    # the operator from the page itself.
    #
    # ANSWERED by the 2026-09-04 01:47 sweep: status ok, fetched 0. The endpoint
    # is real and returns well-formed RSS - no parse error, no HTTP error - but
    # it is the 2020 ARCHIVE, so nothing in it falls inside a 24-hour window.
    #
    # This is the failure mode the repo warns about most: a source that succeeds
    # every night and ingests nothing is indistinguishable from a world where
    # nothing happened. It is enabled and it is currently worth nothing.
    #
    # What is needed is the same URL from the CURRENT year's page. Swapping 2020
    # for 2026 in the path is not enough and is not worth a guess: in Liferay the
    # INSTANCE id identifies the portlet placement, and it is the portlet - not
    # the path - that decides which items come back. The 2026 page has its own
    # instance id. Open https://www.bnm.gov.my/press-release-2026, take the RSS
    # link off it, and replace this whole value.
    "bnm_press": (
        "https://www.bnm.gov.my/press-release-2020"
        "?p_p_id=com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZHckDJtILsio"
        "&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
        "&p_p_resource_id=getRSS&p_p_cacheability=cacheLevelPage"
        "&_com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZHckDJtILsio_currentURL=%2Fpress-release-2020"
        "&_com_liferay_asset_publisher_web_portlet_AssetPublisherPortlet"
        "_INSTANCE_ZHckDJtILsio_portletAjaxable=true",
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
