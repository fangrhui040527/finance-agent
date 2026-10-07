"""A per-instrument source resumes from the last run that asked THIS slot's names.

google_news, yahoo_rss and gdelt ask the Bursa names at `bursa_close` and the
US names at `us_close`. Their watermark was keyed by the source alone, so each
slot resumed from the other slot's run: the Bursa names were asked "since last
night's us_close" and the US names "since this morning's bursa_close". A
Maybank story published between `bursa_close` and that evening's `us_close`
was never requested by any run, although the feed still served it the next
morning. From 2026-09-26 the corpus held no Bursa google_news item published
14:00-22:59 UTC and no US item published 23:00-09:59 UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knowledge.corpus import Corpus
from knowledge.sources import catalog
from knowledge.sweep import run_sweep
from tests.test_sweep import (
    INDEX,
    CannedCollector,
    Cfg,
    RecordingAdapters,
    collectors_returning,
    row,
)

TUE_BURSA = datetime(2026, 9, 29, 10, 8, tzinfo=UTC)
TUE_US = datetime(2026, 9, 29, 22, 46, tzinfo=UTC)
WED_BURSA = datetime(2026, 9, 30, 10, 8, tzinfo=UTC)


@pytest.fixture
def stores(tmp_path):
    return str(tmp_path / "corpus.db"), str(tmp_path / "facts.db")


def _sweep(slot, at, corpus_db, facts_db, adapters, cfg=None, collector_for=None):
    return run_sweep(
        cfg or Cfg(),
        slot,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        collector_for=collector_for,
        entity_index=INDEX,
        clock=lambda: at,
        log=lambda m: None,
    )


def test_covering_slots_are_the_slots_that_asked_for_this_slots_names():
    assert catalog.covering_slots("bursa_close") == ("bursa_close", "weekly", "all")
    assert catalog.covering_slots("us_close") == ("us_preopen", "us_close", "weekly", "all")
    assert catalog.covering_slots("all") == ("weekly", "all")


def test_bursa_close_resumes_from_the_last_bursa_close_not_the_us_close(stores):
    corpus_db, facts_db = stores
    # Published Tuesday 14:00 UTC (22:00 MYT): after Tuesday's bursa_close read
    # the Bursa names, before Tuesday's us_close, which does not ask for them.
    maybank = row(
        1, "Maybank profit jumps on loan growth", when="2026-09-29T14:00:00+00:00", _for="Maybank"
    )
    empty = RecordingAdapters({})
    _sweep("bursa_close", TUE_BURSA, corpus_db, facts_db, empty)
    _sweep("us_close", TUE_US, corpus_db, facts_db, empty)
    _sweep(
        "bursa_close", WED_BURSA, corpus_db, facts_db, RecordingAdapters({"google_news": [maybank]})
    )

    with Corpus(corpus_db) as c:
        last = c.sweeps(limit=1, source="google_news")[0]
        assert last["slot"] == "bursa_close"
        assert datetime.fromisoformat(last["since"]) == TUE_BURSA, (
            "resumed from the us_close run, which never asked for the Bursa names"
        )
        titles = [a.title for a in c.articles(limit=10)]
    assert titles == ["Maybank profit jumps on loan growth"]


def test_us_close_resumes_from_the_last_us_close(stores):
    corpus_db, facts_db = stores
    nvda = row(
        2, "Nvidia shares slide in Asia trading", when="2026-09-30T03:00:00+00:00", _for="NVIDIA"
    )
    empty = RecordingAdapters({})
    _sweep("us_close", TUE_US, corpus_db, facts_db, empty)
    _sweep("bursa_close", WED_BURSA, corpus_db, facts_db, empty)
    wed_us = datetime(2026, 9, 30, 22, 47, tzinfo=UTC)
    _sweep("us_close", wed_us, corpus_db, facts_db, RecordingAdapters({"google_news": [nvda]}))

    with Corpus(corpus_db) as c:
        last = c.sweeps(limit=1, source="google_news")[0]
        assert datetime.fromisoformat(last["since"]) == TUE_US
        assert [a.title for a in c.articles(limit=10)] == ["Nvidia shares slide in Asia trading"]


def test_a_run_over_every_name_counts_for_each_slot(stores):
    """`ask.py sweep` with no slot asks every name, so it read the window for
    both markets: the next bursa_close resumes from it."""
    corpus_db, facts_db = stores
    empty = RecordingAdapters({})
    _sweep("bursa_close", TUE_BURSA, corpus_db, facts_db, empty)
    manual = TUE_BURSA + timedelta(hours=5)
    _sweep("all", manual, corpus_db, facts_db, empty)
    _sweep("us_close", TUE_US, corpus_db, facts_db, empty)
    _sweep("bursa_close", WED_BURSA, corpus_db, facts_db, empty)
    with Corpus(corpus_db) as c:
        assert datetime.fromisoformat(c.sweeps(limit=1, source="google_news")[0]["since"]) == manual


def test_a_slot_never_run_looks_back_its_window(stores):
    corpus_db, facts_db = stores
    empty = RecordingAdapters({})
    _sweep("us_close", TUE_US, corpus_db, facts_db, empty)
    _sweep("bursa_close", WED_BURSA, corpus_db, facts_db, empty)
    with Corpus(corpus_db) as c:
        since = datetime.fromisoformat(c.sweeps(limit=1, source="google_news")[0]["since"])
    assert since < WED_BURSA and since != TUE_US


def test_the_source_wide_watermark_is_unchanged_for_its_other_readers(stores):
    """`sweep_silence` asks whether the source was read at all, in any slot."""
    corpus_db, facts_db = stores
    empty = RecordingAdapters({})
    _sweep("bursa_close", TUE_BURSA, corpus_db, facts_db, empty)
    _sweep("us_close", TUE_US, corpus_db, facts_db, empty)
    with Corpus(corpus_db) as c:
        assert c.last_success("google_news") == TUE_US
        assert c.last_success("google_news", catalog.covering_slots("bursa_close")) == TUE_BURSA


def test_a_structured_per_instrument_source_resumes_per_slot(stores):
    """fmp asks every name at `weekly` and the US names at `us_preopen`. The
    weekly run resumes from the last run that asked for every name, not from
    Friday's us_preopen, which never asked for a Bursa name."""
    corpus_db, facts_db = stores
    fmp = CannedCollector()
    fmp.name = "fmp"
    cfg = Cfg(sources=("fmp",))
    sun = datetime(2026, 9, 27, 8, 15, tzinfo=UTC)
    fri = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
    next_sun = datetime(2026, 10, 4, 8, 15, tzinfo=UTC)
    for slot, at in (("weekly", sun), ("us_preopen", fri), ("weekly", next_sun)):
        _sweep(
            slot, at, corpus_db, facts_db, RecordingAdapters({}), cfg, collectors_returning(fmp=fmp)
        )
    assert [s[2] for s in fmp.seen] == ["weekly", "us_preopen", "weekly"]
    assert fmp.seen[2][0] == sun, "resumed from Friday's us_preopen, which asked only the US names"
    # And us_preopen resumes from the weekly run before it: weekly asked the US names too.
    assert fmp.seen[1][0] == sun
