"""Attribution is what the text names, and a name inside a longer name is not a mention.

What now holds:

  * A source keyed by ticker - Finnhub company news, Alpha Vantage's ticker
    feed, Yahoo's per-ticker RSS - records the ticker it was asked for as
    `fetched_for`, provenance only. An article is attached to an instrument
    only where the entity linker finds the company in its title or body, so a
    story about NVIDIA fetched for AAPL is NVIDIA's and is not escalated as
    Apple news. Rows stored before 2026-10-10 keep the vendor ids they were
    stored with; the corpus is append-only and is not rewritten.
  * The linker resolves alias matches longest first over spans: a shorter
    alias that falls inside a longer alias's match is not linked. "Genting
    Malaysia" is MYX:4715 alone, and "Genting Singapore", "Genting Highlands"
    and "Genting Plantations" link to nothing unless the index names them.

Each test fails on the code before 2026-10-10:

  * corpus-retrieval-3: a vendor's per-ticker query result was stored as
    attribution, so 2,889 of 3,811 Finnhub "NVDA" articles never named NVIDIA
    and the digest starred "ETFs to Buy as NVIDIA Marches Toward $6 Trillion
    Market Cap" under AAPL.
  * corpus-retrieval-4: "Genting" fired inside "Genting Malaysia", "Genting
    Singapore", "Genting Highlands" and "Genting Plantations" and attached each
    story to Genting Berhad (MYX:3182).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from knowledge.corpus import Corpus
from knowledge.feeds.adapter import FixtureFeed, link_entities
from knowledge.news.features import Article
from knowledge.news.linking import EntityLinker
from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.finnhub import FinnhubCollector
from knowledge.sweep import PreparedFeed, run_sweep
from tests.conftest import FakeResponse, http_error

NOW = datetime(2026, 10, 7, 21, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)
US_INDEX = {
    "NVIDIA": "XNAS:NVDA",
    "Nvidia": "XNAS:NVDA",
    "Apple": "XNAS:AAPL",
    "Microsoft": "XNAS:MSFT",
    "Amazon": "XNAS:AMZN",
}
GENTING_INDEX = {
    "Genting": "MYX:3182",
    "Genting Berhad": "MYX:3182",
    "Genting Malaysia": "MYX:4715",
    "Maybank": "MYX:1155",
    "Maybank Islamic": "MYX:1155-I",
}


def _answer(body):
    return lambda req, timeout=None: FakeResponse(json.dumps(body))


def _normalise(articles, name="finnhub", watchlist=("XNAS:AAPL", "XNAS:MSFT")):
    feed = PreparedFeed(name, articles)
    arts, _ = feed.normalize(
        feed.fetch(SINCE), entity_index=US_INDEX, holdings=set(), watchlist=set(watchlist)
    )
    return arts


# --- corpus-retrieval-3: the vendor's ticker is provenance, the text is attribution -----------


def test_finnhub_company_news_records_the_ticker_it_was_asked_for_and_claims_nothing():
    routes = {
        "company-news": [
            {
                "id": 7,
                "headline": "Marvell Stock Had A Huge Year",
                "summary": "Shares of the chip designer doubled.",
                "source": "Yahoo",
                "datetime": int((NOW - timedelta(hours=2)).timestamp()),
                "url": "https://example.com/mrvl",
            }
        ]
    }

    def opener(req, timeout=None):
        for needle, body in routes.items():
            if needle in req.full_url:
                return FakeResponse(json.dumps(body))
        raise http_error(403)  # every other endpoint: outside the plan, a note

    pull = FinnhubCollector(key="k", clock=lambda: NOW, opener=opener).collect(
        SINCE, ("XNAS:NVDA",), slot="us_close"
    )
    (raw,) = pull.articles
    assert raw.instruments == [] and raw.fetched_for == "XNAS:NVDA"
    (art,) = _normalise(pull.articles)
    assert art.instruments == [], "the text never names NVIDIA"
    assert art.fetched_for == "XNAS:NVDA"


def test_a_story_fetched_for_one_name_and_about_another_belongs_to_the_other():
    """The committed 2026-10-07 digest starred the first under AAPL. The second
    names the name it was fetched for, so it is still that name's."""
    other = Article(
        doc_id="finnhub:1",
        title="ETFs to Buy as NVIDIA Marches Toward $6 Trillion Market Cap",
        body="NVIDIA guided revenue above consensus.",
        source_domain="zacks.com",
        published_at=NOW - timedelta(hours=1),
        instruments=["XNAS:AAPL"],  # what a ticker-keyed vendor used to hand in
    )
    named = Article(
        doc_id="finnhub:2",
        title="What Has Changed About Microsoft and Amazon Stock?",
        body="",
        source_domain="fool.com",
        published_at=NOW - timedelta(hours=1),
        instruments=["XNAS:MSFT"],
    )
    art, ok = _normalise([other, named], watchlist=("XNAS:AAPL",))
    assert art.instruments == ["XNAS:NVDA"]
    assert art.fetched_for == "XNAS:AAPL"
    assert not art.escalated, "nothing about Apple was reported"
    assert ok.instruments == ["XNAS:MSFT", "XNAS:AMZN"] and ok.fetched_for == "XNAS:MSFT"


def test_alphavantage_keeps_vendor_relevance_for_tone_but_not_for_attribution():
    body = {
        "feed": [
            {
                "title": "Only Four Trillion-Dollar Stocks Are Beating the S&P 500",
                "url": "https://example.com/av2",
                "time_published": (NOW - timedelta(hours=3)).strftime("%Y%m%dT%H%M%S"),
                "summary": "NVIDIA leads the group.",
                "source_domain": "fool.com",
                "ticker_sentiment": [
                    {"ticker": "AAPL", "relevance_score": "0.6", "ticker_sentiment_score": "0.2"}
                ],
            }
        ]
    }
    pull = AlphaVantageNews(key="k", clock=lambda: NOW, opener=_answer(body)).collect(
        SINCE, ("XNAS:AAPL",)
    )
    (raw,) = pull.articles
    assert raw.instruments == [] and raw.fetched_for == "XNAS:AAPL"
    assert {o.instrument_id for o in pull.observations} == {"XNAS:AAPL"}, "tone is the vendor's"
    (art,) = _normalise(pull.articles, name="alphavantage")
    assert art.instruments == ["XNAS:NVDA"] and art.fetched_for == "XNAS:AAPL"


@dataclass
class _Cfg:
    sources: tuple = ("yahoo_rss",)
    holdings: tuple = ()
    watchlist: tuple = ("XNAS:NVDA",)
    corpus_db: str = ":memory:"
    facts_db: str = ":memory:"
    languages: tuple = ()
    gdelt_languages: tuple = ()
    gdelt_countries: tuple = ()
    gdelt_query: str = ""


def test_a_yahoo_ticker_feed_row_that_never_names_the_company_is_not_attributed(tmp_path):
    rows = [
        {
            "id": "1",
            "title": "ZIM stock hits 52-week high",
            "body": "",
            "published_at": (NOW - timedelta(hours=2)).isoformat(),
            "domain": "finance.yahoo.com",
        },
        {
            "id": "2",
            "title": "Nvidia beats on data-centre demand",
            "body": "",
            "published_at": (NOW - timedelta(hours=2)).isoformat(),
            "domain": "finance.yahoo.com",
        },
    ]
    corpus_db = str(tmp_path / "corpus.db")
    run_sweep(
        _Cfg(),
        "us_close",
        corpus_path=corpus_db,
        facts_path=str(tmp_path / "facts.db"),
        link_graph=False,
        adapter_for=lambda name, **kw: FixtureFeed(records=rows),
        entity_index=US_INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    with Corpus(corpus_db) as c:
        stored = {a.title: a for a in c.articles(limit=10)}
    assert stored["ZIM stock hits 52-week high"].instruments == []
    assert stored["ZIM stock hits 52-week high"].fetched_for == "XNAS:NVDA"
    assert stored["Nvidia beats on data-centre demand"].instruments == ["XNAS:NVDA"]


# --- corpus-retrieval-4: a shorter alias does not fire inside a longer one ----------------------


def test_a_sibling_company_is_not_its_parent_unless_the_text_names_the_parent_too():
    assert link_entities("Genting Malaysia posts higher quarterly revenue", GENTING_INDEX) == [
        "MYX:4715"
    ]
    text = "Genting Malaysia results lift Genting Berhad shares"
    assert link_entities(text, GENTING_INDEX) == ["MYX:4715", "MYX:3182"]
    assert link_entities("Genting Singapore slips; Genting rises", GENTING_INDEX) == ["MYX:3182"]


def test_group_names_and_the_resort_outside_the_index_link_to_nothing():
    for text in (
        "Genting Singapore quarterly profit falls 20%",
        "Landslide closes road to Genting Highlands",
        "Genting Plantations raises CPO output",
        "Resorts World Genting visitor numbers rise",
        "GENTING SINGAPORE BEATS SECOND-QUARTER EXPECTATIONS",
    ):
        assert link_entities(text, GENTING_INDEX) == [], text


def test_a_group_name_in_the_index_links_to_its_own_instrument():
    idx = {**GENTING_INDEX, "Genting Plantations": "MYX:2291"}
    assert link_entities("Genting Plantations raises CPO output", idx) == ["MYX:2291"]


def test_a_subsidiary_is_the_subsidiary_alone_the_full_list():
    assert link_entities("Maybank Islamic launched a fund", GENTING_INDEX) == ["MYX:1155-I"]
    assert link_entities("Maybank Islamic and Maybank both grew", GENTING_INDEX) == [
        "MYX:1155-I",
        "MYX:1155",
    ]


def test_mentions_count_the_same_spans_link_does():
    linker = EntityLinker(GENTING_INDEX)
    assert linker.mentions("Genting Malaysia posts higher revenue", ["MYX:3182"]) == 0
    assert linker.mentions("Genting Berhad said Genting would", ["MYX:3182"]) == 2
    assert "Genting Singapore" not in linker.names_for(["MYX:3182"])
