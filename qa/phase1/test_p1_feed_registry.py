"""Phase 1 - the P8 source registry: names resolve, and unknown names refuse.

`config.toml` [sources] names become adapters through one function. The
property worth a QA pin is the refusal: a misspelled source must raise, never
silently ingest nothing while reading as configured - the exact failure class
`.env.example` warns about for keys.
"""

from __future__ import annotations

import pytest


def test_known_source_names_resolve_to_their_adapters():
    from knowledge.feeds.adapter import GdeltFeed
    from knowledge.feeds.registry import RSS_SOURCES, adapter_for
    from knowledge.feeds.rss import RssFeed

    assert isinstance(adapter_for("gdelt"), GdeltFeed)
    for name, (url, trust) in RSS_SOURCES.items():
        feed = adapter_for(name)
        assert isinstance(feed, RssFeed)
        assert feed.name == name and feed.trust == trust


def test_an_unknown_source_name_refuses_instead_of_ingesting_silence():
    from knowledge.feeds.registry import UnknownSource, adapter_for

    for bad in ("bloomberg_terminal", "", "fixture"):
        # `fixture` included on purpose: it is a test double, not a source an
        # operator may enable from config, and the registry says so by refusing.
        with pytest.raises(UnknownSource, match="no adapter registered"):
            adapter_for(bad)


def test_the_rss_adapter_refuses_a_relative_url():
    from knowledge.feeds.rss import RssFeed

    with pytest.raises(ValueError, match="not an absolute feed URL"):
        RssFeed(url="feeds/latest.xml")
