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
    # NOT ENABLED, and this URL is NOT the feed. Three sweeps on 2026-09-03
    # established what is true:
    #
    #   /rss/press-release  -> HTTP 404; the path predates BNM's site redesign
    #   /rss                -> an HTML landing page titled "RSS - Bank Negara
    #                          Malaysia", carrying no autodiscovery link tags
    #
    # So the feed exists behind a link on that page and its URL is unknown here:
    # this environment's egress refuses bnm.gov.my, and so does WebFetch, so the
    # page cannot be read to find it. Anyone who can open
    # https://www.bnm.gov.my/rss in a browser can read the link off it, put it
    # here, and add "bnm_press" back to [sources] enabled in config.toml.
    #
    # Kept registered rather than deleted: the adapter, the trust tier and the
    # name are all right, and one wrong field is not a reason to lose the other
    # three.
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
