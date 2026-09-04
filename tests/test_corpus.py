"""The corpus: what the system saw, kept, and the three ways that could fail.

Every test here is offline. The feed seam takes an `opener`, the corpus takes a
path, and both are handed doubles - nothing opens a socket.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.corpus import FAILED, OK, Corpus
from knowledge.feeds.adapter import FeedError, FixtureFeed
from knowledge.news.features import Article

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)

ROWS = [
    {
        "id": "1",
        "title": "Maybank posts a higher net interest margin",
        "body": "Malayan Banking Berhad said NIM rose in the quarter.",
        "published_at": "2026-09-02T08:00:00+00:00",
        "domain": "thestar.com.my",
    },
    {
        "id": "2",
        "title": "CIMB expands its digital arm",
        "body": "CIMB announced a new unit this morning.",
        "published_at": "2026-09-02T09:00:00+00:00",
        "domain": "theedge.com.my",
    },
]

INDEX = {"Maybank": "MYX:1155", "Malayan Banking Berhad": "MYX:1155", "CIMB": "MYX:1023"}


def articles(rows=None, index=None):
    feed = FixtureFeed(records=list(rows if rows is not None else ROWS))
    out, stats = feed.normalize(
        feed.fetch(SINCE), entity_index=index if index is not None else INDEX
    )
    return out, stats


@pytest.fixture
def corpus(tmp_path):
    with Corpus(tmp_path / "corpus.db") as c:
        yield c


# --- append-only ------------------------------------------------------------------


def test_a_stored_article_cannot_be_edited(corpus):
    arts, _ = articles()
    corpus.add(arts[0], "fixture")
    with pytest.raises(sqlite3.IntegrityError, match="not editable"):
        corpus.conn.execute("UPDATE articles SET title = 'something else'")


def test_a_stored_article_cannot_be_deleted(corpus):
    """The corpus is evidence about what was knowable at the time. A record you
    can prune afterwards proves nothing about what you knew when you acted."""
    arts, _ = articles()
    corpus.add(arts[0], "fixture")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        corpus.conn.execute("DELETE FROM articles")


def test_sweep_history_cannot_be_edited_or_deleted(corpus):
    corpus.record_sweep("r1", "fixture", SINCE, FAILED, detail="network refused")
    with pytest.raises(sqlite3.IntegrityError, match="not editable"):
        corpus.conn.execute("UPDATE sweeps SET status = 'ok'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        corpus.conn.execute("DELETE FROM sweeps")


# --- dedup across runs ------------------------------------------------------------


def test_the_same_story_tomorrow_is_not_stored_twice(tmp_path):
    """The bug this table exists to prevent. `FeedAdapter._seen` is per-instance,
    so it dies with the process: a wire story still on the wire tomorrow would be
    stored again every day it stays there, and a month of watching would report a
    volume of news that is mostly one story counted thirty times.
    """
    path = tmp_path / "corpus.db"
    first, _ = articles()
    with Corpus(path) as c:
        assert c.add_all(first, "fixture").stored == 2

    second, _ = articles()  # a fresh adapter, exactly as a second process would have
    with Corpus(path) as c:
        stats = c.add_all(second, "fixture")
        assert stats.stored == 0
        assert stats.duplicates == 2
        assert c.counts()["articles"] == 2


def test_articles_without_a_dup_hash_do_not_collapse_into_one(corpus):
    """'' means the adapter computed none, not that they are the same story.
    A plain UNIQUE index would keep exactly one of them."""
    for i in (1, 2):
        corpus.add(
            Article(
                doc_id=f"raw:{i}",
                title=f"Untitled {i}",
                body="",
                source_domain="example.com",
                published_at=NOW,
            ),
            "fixture",
        )
    assert corpus.counts()["articles"] == 2


def test_add_reports_whether_it_stored_anything(corpus):
    arts, _ = articles()
    assert corpus.add(arts[0], "fixture") is True
    assert corpus.add(arts[0], "fixture") is False


# --- the watermark ----------------------------------------------------------------


def test_a_failed_sweep_does_not_move_the_watermark(corpus):
    """Resuming from a failure would put the window that was never read behind
    us - the news of the outage is exactly the news you would skip."""
    corpus.record_sweep("r1", "gdelt", SINCE, OK, at=NOW)
    corpus.record_sweep("r2", "gdelt", SINCE, FAILED, at=NOW + timedelta(hours=1))
    assert corpus.last_success("gdelt") == NOW


def test_a_source_never_swept_has_no_watermark(corpus):
    assert corpus.last_success("gdelt") is None


def test_a_failed_month_and_a_quiet_month_are_different_records(corpus):
    """Both leave the articles table empty. Only one of them means the world
    was quiet, and a scheduled system that cannot tell them apart reports an
    outage as calm."""
    corpus.record_sweep("r1", "gdelt", SINCE, OK, fetched=0, kept=0)
    corpus.record_sweep("r2", "gdelt", SINCE, FAILED, detail="403 Forbidden")
    counts = corpus.counts()
    assert counts["articles"] == 0
    assert counts["sweeps"] == 2
    assert counts["failed_sweeps"] == 1
    assert "403" in corpus.sweeps(source="gdelt")[0]["detail"]


# --- reads ------------------------------------------------------------------------


def test_an_article_survives_the_round_trip_intact(corpus):
    arts, _ = articles()
    corpus.add(arts[0], "fixture", seen_at=NOW)
    back = corpus.articles()[0]
    assert back.doc_id == arts[0].doc_id
    assert back.title == arts[0].title
    assert back.instruments == arts[0].instruments
    assert back.published_at == arts[0].published_at
    assert back.source_domain == arts[0].source_domain


def test_reads_filter_by_window_source_and_instrument(corpus):
    arts, _ = articles()
    corpus.add_all(arts, "fixture", seen_at=NOW)
    assert len(corpus.articles(since=NOW - timedelta(minutes=1))) == 2
    assert corpus.articles(since=NOW + timedelta(days=1)) == []
    assert corpus.articles(source="nowhere") == []
    only = corpus.articles(instrument="MYX:1155")
    assert [a.instruments for a in only] == [["MYX:1155"]]


def test_reads_are_newest_first(corpus):
    arts, _ = articles()
    corpus.add_all(arts, "fixture")
    published = [a.published_at for a in corpus.articles()]
    assert published == sorted(published, reverse=True)


def test_an_unlinked_article_is_counted_but_not_returned_by_instrument(corpus):
    arts, stats = articles(
        rows=[{**ROWS[0], "id": "9", "title": "Rain in Ipoh", "body": "Weather."}], index={}
    )
    assert stats.unlinked == 1
    corpus.add_all(arts, "fixture")
    assert corpus.counts()["linked"] == 0
    assert corpus.articles(instrument="MYX:1155") == []


# --- the graph seam ---------------------------------------------------------------


def test_stored_articles_link_into_the_graph_as_inferred_edges(corpus, tmp_path):
    from knowledge.graph.build import build
    from knowledge.graph.entity_graph import Confidence
    from knowledge.graph.extractors.gdelt import GdeltExtractor
    from knowledge.graph.store import GraphStore

    arts, _ = articles()
    corpus.add_all(arts, "fixture")
    ex = GdeltExtractor.from_corpus(corpus)
    with GraphStore(tmp_path / "graph.db") as store:
        build(store, extractors=[ex])
        edges = store.live_edges(on=arts[0].published_at.date())

    affects = [e for e in edges if e.kind.value == "affects"]
    assert affects, "a linked article produced no edge"
    assert all(e.confidence is Confidence.INFERRED for e in affects)
    assert not any(e.citable for e in affects), "a substring match may not back a claim"


def test_the_linker_index_is_the_one_the_graph_uses():
    """Two indexes would mean a company linked in the corpus and invisible in
    the graph, or the reverse."""
    from knowledge.graph.extractors.gdelt import entity_index

    index = entity_index()
    assert index.get("Maybank") == "MYX:1155"


# --- ask.py sweep -----------------------------------------------------------------


class _Quiet(FixtureFeed):
    name = "fixture"


def test_sweep_stores_what_it_fetches_and_records_the_run(tmp_path, monkeypatch, capsys):
    import ask
    from knowledge.feeds import registry

    monkeypatch.setattr(registry, "adapter_for", lambda name, **kw: _Quiet(records=ROWS))
    db = tmp_path / "corpus.db"
    code = ask.main(
        [
            "sweep",
            "--source",
            "fixture",
            "--db",
            str(db),
            "--graph-db",
            str(tmp_path / "graph.db"),
            "--hours",
            "999999",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "stored 2" in out
    with Corpus(db) as c:
        assert c.counts()["articles"] == 2
        assert c.last_success("fixture") is not None


def test_sweep_records_a_failure_and_exits_three(tmp_path, monkeypatch, capsys):
    """Exit 3 is the interface a scheduler acts on. The row is the interface a
    person reads a month later."""
    import ask
    from knowledge.feeds import registry

    class _Broken(FixtureFeed):
        name = "fixture"

        def _fetch_raw(self, since, limit):
            raise FeedError("the gateway answered 403")

    monkeypatch.setattr(registry, "adapter_for", lambda name, **kw: _Broken())
    db = tmp_path / "corpus.db"
    code = ask.main(["sweep", "--source", "fixture", "--db", str(db)])
    err = capsys.readouterr().err
    assert code == 3
    assert "403" in err
    with Corpus(db) as c:
        assert c.counts()["failed_sweeps"] == 1
        assert c.last_success("fixture") is None


def test_sweep_refuses_a_source_with_no_adapter(tmp_path, capsys):
    import ask

    code = ask.main(["sweep", "--source", "bloomberg_terminal", "--db", str(tmp_path / "c.db")])
    assert code == 2
    assert "no adapter registered" in capsys.readouterr().err


def test_sweep_says_so_when_the_escalation_gate_cannot_fire(tmp_path, monkeypatch, capsys):
    """With an empty book nothing can ever escalate, and silence about that
    reads as 'nothing was important'.

    Builds an empty-book config rather than reading the shipped one, which used
    to happen to be empty. Asserting on a settings file that is meant to be
    filled in makes the test fail on the day the feature starts working.
    """
    import re

    import ask
    from knowledge.feeds import registry

    shipped = pathlib.Path("config.toml").read_text(encoding="utf-8")
    empty = re.sub(r"^holdings = .*$", "holdings = []", shipped, count=1, flags=re.M)
    empty = re.sub(r"^watchlist = .*$", "watchlist = []", empty, count=1, flags=re.M)
    cfg = tmp_path / "config.toml"
    cfg.write_text(empty, encoding="utf-8")
    monkeypatch.setenv("FINPLANET_CONFIG", str(cfg))

    monkeypatch.setattr(registry, "adapter_for", lambda name, **kw: _Quiet(records=ROWS))
    ask.main(
        [
            "sweep",
            "--source",
            "fixture",
            "--db",
            str(tmp_path / "c.db"),
            "--no-graph",
            "--hours",
            "999999",
        ]
    )
    assert "escalation gate" in capsys.readouterr().out


# --- config -----------------------------------------------------------------------


def test_enabled_sources_reach_the_config(tmp_path):
    from core.config import load

    p = tmp_path / "c.toml"
    p.write_text('[sources]\nenabled = ["gdelt"]\ngdelt_languages = ["eng"]\n')
    cfg = load(p)
    assert cfg.sources == ("gdelt",)
    assert cfg.gdelt_languages == ("eng",)


def test_a_source_with_no_adapter_is_refused_at_load(tmp_path):
    """Naming a source does not create it. Enabled with no adapter, it ingests
    nothing every night and the empty corpus reads as a quiet world."""
    from core.config import ConfigError, load

    p = tmp_path / "c.toml"
    p.write_text('[sources]\nenabled = ["bloomberg_terminal"]\n')
    with pytest.raises(ConfigError, match="no adapter registered"):
        load(p)


def test_a_single_string_where_a_list_belongs_is_refused(tmp_path):
    """`enabled = "gdelt"` is a list of five one-character sources to Python."""
    from core.config import ConfigError, load

    p = tmp_path / "c.toml"
    p.write_text('[sources]\nenabled = "gdelt"\n')
    with pytest.raises(ConfigError, match="must be a list"):
        load(p)


def test_a_source_listed_twice_is_refused(tmp_path):
    from core.config import ConfigError, load

    p = tmp_path / "c.toml"
    p.write_text('[sources]\nenabled = ["gdelt", "gdelt"]\n')
    with pytest.raises(ConfigError, match="more than once"):
        load(p)


# --- what the sweep asks the feed for ---------------------------------------------


def test_the_query_asks_for_the_names_in_the_book():
    """The defect the first scheduled run found. With no query the adapter falls
    back to `domainis:reuters.com`, which returned no articles at all - the
    sweep recorded a failure and no retry would have helped."""
    from knowledge.graph.extractors.gdelt import watchlist_query

    q = watchlist_query(["MYX:1155", "XNAS:NVDA"])
    assert '"Maybank"' in q and '"NVIDIA"' in q
    assert q.startswith("(") and q.endswith(")") and " OR " in q
    assert "CIMB" not in q, "a name that is not in the book must not be asked for"


def test_an_empty_book_asks_for_nothing_rather_than_an_empty_group():
    """`()` is not a narrower query, it is a broken one. Returning "" leaves the
    adapter's own default in place so the failure stays legible."""
    from knowledge.graph.extractors.gdelt import watchlist_query

    assert watchlist_query([]) == ""
    assert watchlist_query(["MYX:0000"]) == "", "an id with no surface forms asks for nothing"


def test_every_name_asked_for_can_also_be_linked():
    """Ask and link must use one table. A surface form the query matches but the
    linker does not produces an article the corpus keeps and cannot attribute."""
    from knowledge.graph.extractors.gdelt import entity_index, watchlist_query

    ids = ["MYX:1155", "MYX:5347", "XNAS:NVDA"]
    forms = {f.strip('"') for f in watchlist_query(ids).strip("()").split(" OR ")}
    index = entity_index()
    assert forms, "sanity: the ids used here must have surface forms"
    for f in forms:
        assert index.get(f) in ids


def test_the_shipped_config_asks_for_something_real():
    """The regression guard for the whole defect: shipped settings must produce
    a query, not fall through to the adapter's placeholder."""
    import core.config as C
    from knowledge.graph.extractors.gdelt import watchlist_query

    cfg = C.load()
    q = cfg.gdelt_query or watchlist_query(tuple(cfg.watchlist) + tuple(cfg.holdings))
    assert q, "the shipped book must yield a GDELT query or the sweep asks for reuters.com"
    assert "domainis:" not in q


def test_the_query_asks_for_one_form_per_company_not_all_of_them():
    """Why: all 21 surface forms of a nine-name book timed out three times at
    30s against the DOC API. The API charges for query breadth, and the extra
    aliases buy little - "Malayan Banking Berhad" and "Maybank" normally appear
    in the same article. Linking still uses every alias."""
    from knowledge.graph.extractors.gdelt import watchlist_query

    ids = ["MYX:1155", "MYX:5347", "XNAS:NVDA"]
    q = watchlist_query(ids)
    assert q.count(" OR ") + 1 == len(ids), "one phrase per company, not one per alias"
    assert '"Maybank"' in q and '"Malayan Banking Berhad"' not in q


def test_no_phrase_is_short_enough_for_gdelt_to_refuse():
    """GDELT answers "The specified phrase is too short." as plain text, not
    JSON, so one bad phrase fails the whole sweep. "IHH" and "TNB" are three
    characters; the longer alias is used instead."""
    import core.config as C
    from knowledge.graph.extractors.gdelt import MIN_PHRASE_CHARS, watchlist_query

    cfg = C.load()
    q = watchlist_query(tuple(cfg.watchlist) + tuple(cfg.holdings))
    phrases = [p for p in q.strip("()").split(" OR ")]
    assert phrases
    for p in phrases:
        assert len(p.strip('"')) >= MIN_PHRASE_CHARS, f"{p} is too short for the DOC API"


def test_a_short_name_falls_back_to_a_longer_alias_not_to_nothing():
    from knowledge.graph.extractors.gdelt import watchlist_query

    q = watchlist_query(["MYX:5225"])  # aliases: "IHH", "IHH Healthcare"
    assert q == '("IHH Healthcare")'


# --- one request per company ------------------------------------------------------


def test_terms_and_the_combined_query_cannot_disagree():
    from knowledge.graph.extractors.gdelt import watchlist_query, watchlist_terms

    ids = ["MYX:1155", "XNAS:NVDA"]
    terms = watchlist_terms(ids)
    q = watchlist_query(ids)
    assert terms == ("Maybank", "NVIDIA")
    for t in terms:
        assert f'"{t}"' in q


def test_each_company_is_asked_for_separately_with_its_own_share():
    """The defect: one OR'd query sorted newest-first is won by whichever name
    publishes most. Nine names and 250 records produced 34 attributed articles,
    every one US tech, Apple alone taking 20, and all six Bursa names nothing."""
    import ask

    asked = []

    class _Feed:
        def __init__(self, q):
            self.q = q

        def fetch(self, since, limit):
            asked.append((self.q, limit))
            return [{"id": f"{self.q}-1"}]

    records, failed, skipped, counts = ask._fetch_each(
        _Feed, ("Maybank", "Tenaga", "NVIDIA"), NOW, 90
    )
    assert [q for q, _ in asked] == ['"Maybank"', '"Tenaga"', '"NVIDIA"']
    assert {lim for _, lim in asked} == {30}, "the budget is split, not spent on the loudest"
    assert len(records) == 3 and not failed and not skipped


def test_one_name_failing_does_not_lose_the_others():
    """A sweep where Maybank failed is not a sweep where nothing was read."""
    import ask
    from knowledge.feeds.adapter import FeedError

    class _Feed:
        def __init__(self, q):
            self.q = q

        def fetch(self, since, limit):
            if "Maybank" in self.q:
                raise FeedError("timed out")
            return [{"id": self.q}]

    records, failed, skipped, counts = ask._fetch_each(_Feed, ("Maybank", "Tenaga"), NOW, 50)
    assert len(records) == 1
    assert failed == [("Maybank", "timed out")] and not skipped
    assert "failed: Maybank" in ask._sweep_note(failed, skipped)


def test_the_deadline_keeps_what_it_has_instead_of_being_killed_mid_run():
    """Nine names that each time out would run past the job's cap, and a killed
    job commits nothing at all. Stopping early keeps the names already read."""
    import ask

    class _Feed:
        def __init__(self, q):
            self.q = q

        def fetch(self, since, limit):
            return [{"id": self.q}]

    ticks = iter([NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=1)])
    records, failed, skipped, counts = ask._fetch_each(
        _Feed,
        ("Maybank", "Tenaga", "NVIDIA"),
        NOW,
        50,
        deadline=NOW + timedelta(minutes=10),
        clock=lambda: next(ticks),
    )
    assert len(records) == 1, "the first name was read before the deadline"
    assert skipped == ["Tenaga", "NVIDIA"] and not failed
    assert "not reached: Tenaga, NVIDIA" in ask._sweep_note(failed, skipped)


def test_a_clean_sweep_says_nothing_rather_than_an_empty_note():
    import ask

    assert ask._sweep_note([], []) == ""


def test_the_starved_name_leads_the_next_day():
    """`_fetch_each` stops at the deadline, so a fixed order starves the same
    names every time. Alphabetically the tail is Maybank, Petronas Chemicals,
    Press Metal and Tenaga - the whole Bursa side of a Malaysian book."""
    import ask

    terms = ("Apple", "Genting", "Maybank", "Tenaga")
    seen_first = {ask._rotate(terms, day)[0] for day in range(4)}
    assert seen_first == set(terms), "every name leads on some day"


def test_rotation_keeps_every_name_and_the_order_within_a_run():
    import ask

    terms = ("Apple", "Genting", "Maybank", "Tenaga")
    for day in range(9):
        r = ask._rotate(terms, day)
        assert sorted(r) == sorted(terms), "rotation drops nothing and invents nothing"
        assert ask._rotate(terms, day) == r, "a run is reproducible from its date"
    assert ask._rotate((), 3) == ()


def test_a_name_answered_with_nothing_is_reported_not_silent():
    """A name read and answered with zero articles looks identical to a name
    nobody watches, and they are opposite problems: one is a quiet week, the
    other is a name the source does not cover and never will. Measured
    2026-09-03: Maybank was read successfully and produced no articles, which
    could only be inferred from the absence of an MYX id."""
    import ask

    class _Feed:
        def __init__(self, q):
            self.q = q

        def fetch(self, since, limit):
            return [] if "Maybank" in self.q else [{"id": self.q}]

    records, failed, skipped, counts = ask._fetch_each(_Feed, ("Maybank", "NVIDIA"), NOW, 50)
    assert counts == [("Maybank", 0), ("NVIDIA", 1)]
    assert not failed, "answering with nothing is not a failure"
    assert "read but empty: Maybank" in ask._sweep_note(failed, skipped, counts)


def test_a_sweep_where_every_name_returned_something_says_nothing():
    import ask

    assert ask._sweep_note([], [], [("Maybank", 3), ("NVIDIA", 5)]) == ""


# --- more than one source ---------------------------------------------------------


def test_no_source_is_enabled_that_is_known_not_to_serve_a_feed():
    """bnm_press stays registered and disabled. Four sweeps on 2026-09-03/04
    established that the current year's page has RSS switched off, so there is
    no live BNM feed to point at - the registry records every URL tried.

    A source that fails every night marks the job red every night, and a red job
    that always means the same thing trains you to stop reading it.
    """
    import core.config as C
    from knowledge.feeds.registry import RSS_SOURCES

    assert "bnm_press" in RSS_SOURCES, "the registration is kept; the feed is not live"
    assert "bnm_press" not in C.load().sources


def test_every_enabled_source_has_an_adapter_that_builds():
    """Naming a source does not create it. An enabled name with no adapter
    ingests nothing every night and reads as a quiet world."""
    import core.config as C
    from knowledge.feeds.registry import adapter_for, is_news_source
    from knowledge.sources.registry import collector_for

    for name in C.load().sources:
        built = adapter_for(name) if is_news_source(name) else collector_for(name)
        assert built is not None


def test_one_source_failing_does_not_stop_the_next(tmp_path, monkeypatch, capsys):
    """Two sources means two watermarks and two sweep rows. A source that fails
    must not take the other one's articles with it - the whole reason bnm_press
    is worth enabling is the days GDELT has nothing."""
    import ask
    from knowledge.feeds import registry
    from knowledge.feeds.adapter import FeedError

    class _Broken(_Quiet):
        def fetch(self, since, limit=250):
            raise FeedError("refused")

    def _adapter(name, **kw):
        # Fails on FETCH, not on construction. core.config validates every
        # enabled source by building its adapter, so a constructor that raises
        # makes the config itself unloadable - exit 2, a different bug entirely.
        return _Broken(records=[]) if name == "gdelt" else _Quiet(records=ROWS)

    monkeypatch.setattr(registry, "adapter_for", _adapter)
    code = ask.main(
        [
            "sweep",
            "--source",
            "gdelt",
            "--source",
            "fixture",
            "--db",
            str(tmp_path / "c.db"),
            "--no-graph",
            "--hours",
            "999999",
        ]
    )
    assert code == 3, "a failed source is still reported"
    with Corpus(tmp_path / "c.db") as c:
        rows = {dict(s)["source"]: dict(s)["status"] for s in c.sweeps()}
        assert rows == {"gdelt": FAILED, "fixture": OK}
        assert c.counts()["articles"] == len(ROWS), "the working source still stored"


def test_a_page_served_instead_of_a_feed_says_so():
    """The excerpt was `text[:120]`, which on the case that actually happens -
    a URL serving a web page - was 120 literal newlines and told the reader
    nothing. Measured 2026-09-03 on bnm.gov.my/rss, where the first useful
    character was on line 123."""
    from knowledge.feeds.rss import _excerpt

    page = "\n" * 130 + "<!DOCTYPE html>\n<html><head><title>RSS - Bank Negara</title>"
    out = _excerpt(page)
    assert "an HTML page, not a feed" in out
    assert "Bank Negara" in out, "the title names the page, which names the problem"
    assert "\\n" not in out


def test_an_excerpt_of_nothing_says_nothing_rather_than_quotes():
    from knowledge.feeds.rss import _excerpt

    assert _excerpt("   \n\n \t ") == "<empty body>"


def test_a_real_feed_is_not_mislabelled_as_a_page():
    from knowledge.feeds.rss import _excerpt

    assert "HTML page" not in _excerpt('<?xml version="1.0"?><rss><channel/></rss>')


def test_a_landing_page_is_asked_where_its_feeds_are():
    """A URL serving a page instead of a feed is the ordinary failure, and the
    page almost always names the real feed in its head. Reading it turns "this
    is not a feed" into "the feed is here"."""
    from knowledge.feeds.rss import _excerpt, advertised_feeds

    page = (
        "\n" * 130 + "<!DOCTYPE html><html><head><title>RSS</title>"
        '<link rel="alternate" type="application/rss+xml" href="https://x.my/press.xml"/>'
        "<link rel='alternate' type='application/atom+xml' href='/speeches.atom'>"
        '<link rel="stylesheet" href="/style.css">'
    )
    assert advertised_feeds(page) == ["https://x.my/press.xml", "/speeches.atom"]
    assert "advertises feeds at" in _excerpt(page)
    assert "style.css" not in _excerpt(page), "a stylesheet is not a feed"


def test_a_page_advertising_nothing_says_that_too():
    from knowledge.feeds.rss import _excerpt, advertised_feeds

    page = "<!DOCTYPE html><html><head><title>Nothing here</title></head></html>"
    assert advertised_feeds(page) == []
    assert "advertises none" in _excerpt(page)


# --- a degraded sweep is a failure, not a success ------------------------------------


def test_most_names_unreachable_is_a_failure():
    """The bug this closes, from the run that exposed it: eight of nine
    companies failed, one article was stored, and the job reported SUCCESS. The
    failures were in the sweeps table, but a green check nobody has reason to
    open is not a report."""
    import ask

    failed = [
        (n, "429")
        for n in (
            "Apple",
            "Genting",
            "IHH",
            "Microsoft",
            "NVIDIA",
            "PetChem",
            "Press Metal",
            "Tenaga",
        )
    ]
    assert ask._mostly_failed(failed, [], [("Maybank", 1)]) is True


def test_one_quiet_name_is_not_a_failure():
    """Half, not any. GDELT refuses individual names routinely, and a check
    that goes red most days is a check that gets ignored - which costs more
    than the alert is worth."""
    import ask

    counts = [("Maybank", 3), ("Tenaga", 1), ("NVIDIA", 2), ("Apple", 5)]
    assert ask._mostly_failed([("Genting", "429")], [], counts) is False


def test_exactly_half_failing_is_not_yet_a_failure():
    """Strictly MORE than half, so the boundary is decided rather than
    accidental - a two-name book with one quiet name must not read as red."""
    import ask

    assert ask._mostly_failed([("A", "429")], [], [("B", 1)]) is False
    assert ask._mostly_failed([("A", "429"), ("B", "429")], [], [("C", 1)]) is True


def test_names_not_reached_before_the_deadline_count_as_unreachable():
    """A name the deadline cut off is a hole in the record exactly like one
    that was refused: no news was collected for it either way."""
    import ask

    assert ask._mostly_failed([], ["Tenaga", "NVIDIA"], [("Maybank", 1)]) is True


def test_a_sweep_that_read_nothing_at_all_is_not_a_division_by_zero():
    import ask

    assert ask._mostly_failed([], [], []) is False


def test_a_name_read_with_no_news_is_not_a_fault():
    """Counts names, not articles. A name that was read and had no news is a
    fact about the world; a name that could not be read is a hole in it."""
    import ask

    assert ask._mostly_failed([], [], [("Maybank", 0), ("Tenaga", 0)]) is False


# --- rotation is per RUN, not per day ------------------------------------------------


def test_the_days_slots_do_not_all_ask_in_the_same_order():
    """The bug this closes, measured rather than imagined - with the old
    behaviour asserted alongside so the difference is the test.

    GDELT rate-limits a run progressively: nine sequential requests, quota gone
    after about five, so the names that FAIL are the ones asked LAST. On
    2026-09-04 the 13:01 sweep read positions 1-5 and failed 6, 7, 8 and 9.

    The offset was `toordinal()`, so all four of the day's collection slots
    asked in one identical order and starved one identical tail. Tenaga and
    Petronas Chemicals failed in every run that day and looked like a problem
    with those two companies; they were simply last in the queue, four times.
    """
    from datetime import UTC, datetime

    from knowledge.sweep import _rotate, _rotation_offset

    terms = (
        "Apple",
        "Genting",
        "IHH",
        "Maybank",
        "Microsoft",
        "NVIDIA",
        "PetChem",
        "PressMetal",
        "Tenaga",
    )
    # the four real collection slots: bursa_close, us_preopen, us_close, weekly
    slots = [
        datetime(2026, 9, 4, h, m, tzinfo=UTC) for h, m in ((9, 20), (12, 30), (21, 15), (2, 0))
    ]

    was = {_rotate(terms, t.toordinal()) for t in slots}
    assert len(was) == 1, "the old offset gave every slot the same order - that was the bug"

    now = {_rotate(terms, _rotation_offset(t)) for t in slots}
    assert len(now) > 1, "slots must not all share one order"


def test_every_name_gets_an_early_position_across_a_day_of_slots():
    """The property that matters for coverage: over a day, no name is always
    last. Under the per-day offset, the tail was starved every single slot."""
    from datetime import UTC, datetime, timedelta

    from knowledge.sweep import _rotate, _rotation_offset

    terms = tuple(f"n{i}" for i in range(9))
    start = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    # the first three positions over 24 hourly slots
    early = set()
    for h in range(24):
        early.update(_rotate(terms, _rotation_offset(start + timedelta(hours=h)))[:3])
    assert early == set(terms), f"never asked early: {set(terms) - early}"


def test_the_order_is_still_reproducible_from_the_start_time():
    """A sweep you cannot replay is a sweep you cannot explain, so the offset
    stays a pure function of when the run started."""
    from datetime import UTC, datetime

    from knowledge.sweep import _rotate, _rotation_offset

    terms = ("a", "b", "c", "d")
    t = datetime(2026, 9, 4, 13, 1, tzinfo=UTC)
    assert _rotate(terms, _rotation_offset(t)) == _rotate(terms, _rotation_offset(t))
    # stable within the bucket, so a retry seconds later replays the same order
    assert _rotation_offset(t) == _rotation_offset(datetime(2026, 9, 4, 13, 1, 42, tzinfo=UTC))
