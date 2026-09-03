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
