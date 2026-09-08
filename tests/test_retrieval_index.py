"""The corpus, indexed: the seam between the daily sweep and the news agent.

The defect these pin: every surface built `Router({})`, so the news agent's
retrieval was refused before it read an article, and a week of sweeps filled a
database nothing read back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.base import AgentContext
from agents.evidence.agents import A4NewsNarrative
from core.guardrails.defaults import default_engine
from knowledge.corpus import Corpus
from knowledge.feeds.adapter import FixtureFeed
from knowledge.retrieval.hybrid import Collection
from knowledge.retrieval.index import (
    build_router,
    describe,
    indexable,
    news_chunks,
    news_text,
    ownership_from_registry,
    router_for,
)
from knowledge.retrieval.pipeline import CollectionScopeError, Router

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
INDEX = {"Maybank": "MYX:1155", "Malayan Banking Berhad": "MYX:1155", "Nvidia": "XNAS:NVDA"}

ROWS = [
    {
        "id": "1",
        "title": "Maybank posts a higher net interest margin",
        "body": "Malayan Banking Berhad said NIM rose in the quarter on cheaper deposits.",
        "published_at": "2026-09-03T08:00:00+00:00",
        "domain": "thestar.com.my",
    },
    {
        "id": "2",
        "title": "Maybank flags slower loan growth",
        "body": "Malayan Banking Berhad expects loan growth to ease in the second half.",
        "published_at": "2026-09-03T10:00:00+00:00",
        "domain": "theedgemalaysia.com",
    },
    {
        "id": "3",
        "title": "Nvidia beats on data-centre demand",
        "body": "Nvidia beats on data-centre demand",  # GDELT stores the headline as body
        "published_at": "2026-09-03T11:00:00+00:00",
        "domain": "reuters.com",
    },
    {
        "id": "4",
        "title": "Palm oil futures slip",
        "body": "Prices eased on weaker export data.",
        "published_at": "2026-09-03T12:00:00+00:00",
        "domain": "bernama.com",
    },
]


@pytest.fixture
def corpus(tmp_path):
    feed = FixtureFeed(records=ROWS)
    arts, _ = feed.normalize(feed.fetch(NOW - timedelta(days=7)), entity_index=INDEX)
    with Corpus(tmp_path / "corpus.db") as c:
        c.add_all(arts, "fixture", seen_at=NOW)
        yield c


def test_every_registered_store_is_registered_even_when_empty(registry, corpus):
    router = build_router(registry, corpus, now=NOW, index=INDEX)
    for name in registry.knowledge:
        assert isinstance(router.get(_owner_of(registry, name), name), Collection)


def _owner_of(reg, store):
    for aid, spec in reg.agents.items():
        if store in spec.knowledge:
            return aid
    pytest.skip(f"no agent owns {store}")


def test_ownership_is_the_registry_not_a_hand_written_dict(registry):
    own = ownership_from_registry(registry)
    assert "kb_news" in own["a4_news_narrative"]
    assert "kb_news" in own["a11_red_team"]
    assert own["a0_supervisor"] == set()


def test_an_agent_still_cannot_read_a_store_it_does_not_own(registry, corpus):
    router = build_router(registry, corpus, now=NOW, index=INDEX)
    with pytest.raises(CollectionScopeError):
        router.get("a4_news_narrative", "kb_filings")


def test_the_news_collection_holds_the_corpus(registry, corpus):
    router = build_router(registry, corpus, now=NOW, index=INDEX)
    col = router.get("a4_news_narrative", "kb_news")
    assert len(col) == 4
    assert "kb_news: 4 chunks" in describe(router)


def test_news_chunks_carry_the_metadata_the_filters_need(corpus):
    art = corpus.articles(limit=10)[-1]  # oldest: the Maybank NIM story
    (chunk,) = news_chunks(art)
    assert chunk.corpus == "kb_news"
    assert "MYX:1155" in chunk.metadata["instruments"]
    assert "XKLS:1155" in chunk.metadata["instruments"], "the canonical id too"
    assert chunk.metadata["licence"] == "summary"
    assert chunk.metadata["source_domain"] == "thestar.com.my"
    assert chunk.as_of == datetime(2026, 9, 3, 8, 0, tzinfo=UTC)


def test_a_headline_stored_as_its_own_body_is_indexed_once(corpus):
    art = next(a for a in corpus.articles(limit=10) if "Nvidia" in a.title)
    assert news_text(art) == art.title


def test_the_news_agent_now_retrieves_and_cites(registry, corpus):
    router = build_router(registry, corpus, now=NOW, index=INDEX)
    ctx = AgentContext(router=router, engine=default_engine(registry.allowlist()), now=NOW)
    findings = A4NewsNarrative(ctx).run("MYX:1155", "Maybank net interest margin loan growth")
    stories = [f for f in findings if f.kind == "news"]
    assert len(stories) == 2, [f.text for f in findings]
    assert all(f.citations and f.citations[0].source == "kb_news" for f in stories)
    assert all("thestar.com.my" in f.text or "theedgemalaysia.com" in f.text for f in stories)
    assert all("polarity" in f.numbers for f in stories)
    agg = [f for f in findings if f.kind == "news_aggregate"]
    assert agg and agg[0].numbers["n"] == 2.0


def test_the_entity_filter_keeps_another_companys_story_out(registry, corpus):
    router = build_router(registry, corpus, now=NOW, index=INDEX)
    ctx = AgentContext(router=router, engine=default_engine(registry.allowlist()), now=NOW)
    findings = A4NewsNarrative(ctx).run("XNAS:NVDA", "Maybank net interest margin")
    assert all("Maybank" not in f.text for f in findings)


def test_the_news_agent_refuses_honestly_when_the_store_is_empty(registry):
    router = build_router(registry, None, now=NOW)
    ctx = AgentContext(router=router, engine=default_engine(registry.allowlist()), now=NOW)
    (finding,) = A4NewsNarrative(ctx).run("MYX:1155", "anything at all")
    assert "no news cleared" in finding.text
    assert finding.caveats == ["no hits retrieved"]


def test_router_for_reads_the_corpus_file_and_caches_on_its_identity(registry, corpus, tmp_path):
    path = tmp_path / "corpus.db"
    first = router_for(registry, path, now=NOW)
    assert len(first.get("a4_news_narrative", "kb_news")) == 4
    assert router_for(registry, path, now=NOW) is first

    feed = FixtureFeed(
        records=[
            {
                "id": "5",
                "title": "Maybank opens a branch",
                "body": "Malayan Banking Berhad opened in Johor.",
                "published_at": "2026-09-04T09:00:00+00:00",
                "domain": "nst.com.my",
            }
        ]
    )
    arts, _ = feed.normalize(feed.fetch(NOW - timedelta(days=7)), entity_index=INDEX)
    corpus.add_all(arts, "fixture", seen_at=NOW)
    import os
    import time

    # Force a distinct mtime on filesystems with coarse timestamps.
    stamp = time.time() + 2
    os.utime(path, (stamp, stamp))
    rebuilt = router_for(registry, path, now=NOW)
    assert rebuilt is not first
    assert len(rebuilt.get("a4_news_narrative", "kb_news")) == 5


def test_a_missing_corpus_file_gives_an_empty_news_store_not_an_error(registry, tmp_path):
    router = router_for(registry, tmp_path / "absent.db", now=NOW)
    assert len(router.get("a4_news_narrative", "kb_news")) == 0


def test_the_surfaces_build_a_router_with_the_registry_ownership():
    import ask
    import mcp_server.tools as T

    for ctx in (ask.context(), T.context()):
        assert isinstance(ctx.router, Router)
        assert "kb_news" in ctx.router.ownership["a4_news_narrative"]
        # The store exists whether or not the shipped corpus has anything in it.
        ctx.router.get("a4_news_narrative", "kb_news")


# --- a headline naming nobody answers nothing -------------------------------------


def _art(title, body, instruments=()):
    from knowledge.news.features import Article

    return Article(
        doc_id=f"t:{title[:20]}",
        title=title,
        body=body,
        source_domain="example.com",
        published_at=NOW,
        language="English",
        countries=(),
        instruments=tuple(instruments),
        themes=(),
    )


def test_a_headline_naming_nobody_is_not_indexed():
    """GDELT answers in artlist mode: a headline, no article text. All 667 of
    its rows in the shipped corpus have body == title, ~73 characters. It
    matched the company deep in a page we do not hold, so the row can neither
    be retrieved for the name nor cited for a claim - it can only take one of
    the ten places in a result list, and 539 of them were."""
    assert not indexable(
        _art("Nobody likes data centers and chips", "Nobody likes data centers and chips")
    )


def test_a_headline_that_DOES_name_a_company_is_kept():
    """Google News returns no body at all and 88% of its rows are linked. A
    headline is a real, citable claim about a name; the rule is not 'thin'."""
    assert indexable(_art("Nvidia lifts its forecast", "", ("XNAS:NVDA",)))
    assert indexable(_art("Nvidia lifts its forecast", "Nvidia lifts its forecast", ("XNAS:NVDA",)))


def test_a_real_body_that_names_nobody_is_kept():
    """121 rows in the shipped corpus. Text that names no holding can still
    answer a macro or sector question; only the headline-only ones provably
    answer nothing."""
    assert indexable(_art("Fed holds rates", "The committee voted nine to two to hold."))


def test_the_collection_actually_drops_them():
    from knowledge.retrieval.index import news_collection

    arts = [
        _art("Nvidia lifts its forecast", "", ("XNAS:NVDA",)),
        _art("A Honda ZR-V family road test", "A Honda ZR-V family road test"),
    ]
    col = news_collection(arts)
    titles = {c.metadata.get("title") for c in col._order}
    assert "Nvidia lifts its forecast" in titles
    assert "A Honda ZR-V family road test" not in titles
