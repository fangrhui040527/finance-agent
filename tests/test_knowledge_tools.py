"""The MCP tools and CLI commands that read what the collector holds."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.corpus import Corpus
from knowledge.facts import EventRecord, FactBook, Observation, SeriesPoint
from knowledge.feeds.adapter import FixtureFeed
from knowledge.report import fact_snapshot, macro_context

#: Anchored to the real clock, not frozen. `news_evidence` takes no as-of and
#: asks the corpus for the last N DAYS from now, so a fixture pinned to a
#: literal date passes only while that date is inside the window and then
#: starts failing on a calendar day nobody touched. This one did: written
#: against 2026-09-04 with `days=3`, it went red of its own accord on
#: 2026-09-08. A test whose result depends on when it is run is not measuring
#: what it claims to.
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def filled(tmp_path, monkeypatch):
    """A config pointing at temporary, filled stores - so the tools read them."""
    corpus_db, facts_db = tmp_path / "c.db", tmp_path / "f.db"
    shipped = open("config.toml", encoding="utf-8").read()
    # as_posix(): a Windows path's backslashes are escape sequences inside a
    # TOML basic string, and sqlite reads forward slashes on every platform.
    cfg = shipped.replace(
        'corpus_database = "data/corpus.db"', f'corpus_database = "{corpus_db.as_posix()}"'
    )
    cfg = cfg.replace(
        'facts_database = "data/facts.db"', f'facts_database = "{facts_db.as_posix()}"'
    )
    path = tmp_path / "config.toml"
    path.write_text(cfg, encoding="utf-8")
    monkeypatch.setenv("FINPLANET_CONFIG", str(path))

    feed = FixtureFeed(
        records=[
            {
                "id": "1",
                "title": "Maybank posts record quarter",
                "body": "Malayan Banking Berhad beat estimates on strong fee income.",
                "published_at": (NOW - timedelta(hours=3)).isoformat(),
                "domain": "theedgemalaysia.com",
            },
            {
                "id": "2",
                "title": "Maybank flags slower loan growth ahead",
                "body": "The bank expects loan growth to ease in the second half.",
                "published_at": (NOW - timedelta(hours=2)).isoformat(),
                "domain": "thestar.com.my",
            },
        ]
    )
    from knowledge.graph.extractors.gdelt import entity_index

    arts, _ = feed.normalize(
        feed.fetch(NOW - timedelta(days=2)), entity_index=entity_index(), watchlist={"MYX:1155"}
    )
    with Corpus(corpus_db) as c:
        c.add_all(arts, "fixture", seen_at=NOW)
    with FactBook(facts_db) as b:
        b.add_observations(
            [Observation("finnhub", "MYX:1155", "pe_ttm", NOW.date(), Decimal("12.1"))]
        )
        b.add_events(
            [
                EventRecord(
                    "finnhub",
                    "e1",
                    "MYX:1155",
                    "earnings_result",
                    NOW,
                    "Q3 results",
                    effective_at=NOW + timedelta(days=20),
                )
            ]
        )
        b.add_series(
            [
                SeriesPoint(
                    "fred",
                    "DFF",
                    date(2026, 9, 2),
                    Decimal("4.33"),
                    NOW.date(),
                    {"title": "Fed funds"},
                ),
                SeriesPoint(
                    "fred",
                    "DFF",
                    date(2026, 9, 3),
                    Decimal("4.08"),
                    NOW.date(),
                    {"title": "Fed funds"},
                ),
            ]
        )
    return corpus_db, facts_db


# --- the formatters ---------------------------------------------------------------------


def test_fact_snapshot_says_nothing_collected_and_names_the_sources(tmp_path):
    with FactBook(tmp_path / "f.db") as book:
        text = fact_snapshot(book, "XNAS:NVDA", now=NOW)
    assert "NOTHING COLLECTED" in text and "finnhub" in text and "edgar" in text


def test_macro_context_names_its_collectors_when_empty(tmp_path):
    with FactBook(tmp_path / "f.db") as book:
        assert "NO MACRO SERIES" in macro_context(book)
        assert "NO SERIES 'DGS10'" in macro_context(book, "DGS10")


def test_a_stopped_upstream_is_named_under_the_table_not_only_in_the_row(tmp_path):
    """The row label has three words for it. A reader deciding whether to use a
    fourteen-month-old palm oil price needs the rest on the same screen: which
    dataset stopped, when, and that no fetch will bring it back."""
    with FactBook(tmp_path / "f.db") as book:
        book.add_series(
            [
                SeriesPoint(
                    "dbnomics",
                    "DBN:PALM_OIL_USD",
                    date(2025, 6, 1),
                    Decimal("934"),
                    known_at=date(2026, 9, 6),
                    payload={"title": "Palm oil"},
                ),
                SeriesPoint(
                    "dbnomics",
                    "DBN:BRENT_USD",
                    date(2025, 6, 1),
                    Decimal("69"),
                    known_at=date(2026, 9, 6),
                    payload={"title": "Brent"},
                ),
                SeriesPoint(
                    "fred",
                    "DGS10",
                    date(2026, 9, 8),
                    Decimal("4.78"),
                    known_at=date(2026, 9, 8),
                    payload={"title": "10-year"},
                ),
            ]
        )
        text = macro_context(book, now=datetime(2026, 9, 10, tzinfo=UTC))
    assert "466d ENDED 2025-06" in text
    # Grouped by dataset: two ids, one upstream, one line.
    assert (
        "IMF/PCPS stopped at 2025-06 (FROZEN, probed 2026-09-06): DBN:BRENT_USD, DBN:PALM_OIL_USD"
        in text
    )
    assert "2 of these are the last thing a STOPPED upstream published" in text
    assert "DGS10" in text and "STALE" not in text


def test_a_release_listed_every_day_prints_once_as_a_table(tmp_path):
    """Thirty stored "FOMC Press Release" rows printed one per day on every
    page's watch list; the one weekly print among them was hard to find."""
    now = datetime(2026, 9, 22, tzinfo=UTC)
    rows = [
        EventRecord(
            source="fred",
            event_id=f"fred:101:{d}",
            instrument_id="MACRO:US",
            kind="macro_release",
            announced_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
            title="FOMC Press Release",
            payload={"time": "not published by FRED"},
        )
        for d in (date(2026, 9, 22) + timedelta(days=i) for i in range(7))
    ] + [
        EventRecord(
            source="fred",
            event_id="fred:180:2026-09-24",
            instrument_id="MACRO:US",
            kind="macro_release",
            announced_at=datetime(2026, 9, 24, tzinfo=UTC),
            title="Unemployment Insurance Weekly Claims Report",
            payload={"time": "not published by FRED"},
        )
    ]
    with FactBook(tmp_path / "f.db") as book:
        book.add_series(
            [
                SeriesPoint(
                    "fred", "DFF", date(2026, 9, 18), Decimal("3.88"), known_at=date(2026, 9, 19)
                )
            ]
        )
        book.add_events(rows)
        text = macro_context(book, now=now)
    assert text.count("FOMC Press Release") == 1
    assert "a daily table, not a scheduled print" in text
    assert "09-24         Unemployment Insurance Weekly Claims Report  (fred)" in text


def test_a_book_of_live_series_prints_no_stopped_upstream_block(tmp_path):
    with FactBook(tmp_path / "f.db") as book:
        book.add_series(
            [
                SeriesPoint(
                    "fred", "DGS10", date(2026, 9, 8), Decimal("4.78"), known_at=date(2026, 9, 8)
                )
            ]
        )
        text = macro_context(book, now=datetime(2026, 9, 10, tzinfo=UTC))
    assert "STOPPED" not in text and "ENDED" not in text


# --- the MCP tools ------------------------------------------------------------------------


def test_the_four_collector_tools_are_registered():
    from mcp_server.server import S

    names = {
        t["name"]
        for t in S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    }
    assert {"daily_digest", "fact_snapshot", "macro_context", "news_evidence"} <= names


def test_daily_digest_and_fact_snapshot_read_the_configured_stores(filled):
    import mcp_server.tools as T

    page = T.daily_digest(NOW.date().isoformat())
    assert "### Maybank (MYX:1155)" in page and "record quarter" in page
    snap = T.fact_snapshot("MYX:1155")
    assert "pe_ttm" in snap and "12.1" in snap and "earnings_result" in snap
    macro = T.macro_context()
    assert "DFF" in macro and "4.08" in macro
    one = T.macro_context("DFF", 2)
    assert one.count("vintage") == 2


def test_news_evidence_goes_through_the_news_agent_and_cites(filled):
    import mcp_server.tools as T
    from knowledge.retrieval import index as idx

    idx._CACHE.clear()  # the router caches on the corpus file; this test filled a fresh one
    text = T.news_evidence("MYX:1155", "Maybank loan growth fee income", days=3)
    assert "news evidence" in text and "cites kb_news:" in text
    assert "polarity" in text and "Not financial advice" in text


def test_news_evidence_refuses_honestly_when_nothing_clears_the_gate(filled):
    import mcp_server.tools as T
    from knowledge.retrieval import index as idx

    idx._CACHE.clear()
    text = T.news_evidence("XNAS:NVDA", "anything", days=3)
    assert "no news cleared" in text


def test_the_tools_refuse_an_instrument_with_no_market_prefix(filled):
    import mcp_server.tools as T
    from mcp_server.protocol import ToolError

    with pytest.raises(ToolError):
        T.fact_snapshot("NVDA")
    with pytest.raises(ToolError):
        T.news_evidence("NVDA")


# --- the CLI ------------------------------------------------------------------------------


def test_cli_facts_and_macro_print_from_the_fact_book(filled, capsys):
    import ask

    assert ask.main(["facts", "MYX:1155"]) == 0
    out = capsys.readouterr().out
    assert "pe_ttm" in out and "scheduled, next 60 days" in out
    assert ask.main(["macro"]) == 0
    assert "DFF" in capsys.readouterr().out
    assert ask.main(["macro", "DFF", "--points", "1"]) == 0
    assert "4.08" in capsys.readouterr().out
    assert ask.main(["facts", "NVDA"]) == 2
    assert "market prefix" in capsys.readouterr().err


def test_digest_json_written_by_the_cli_is_what_the_tool_renders(filled, tmp_path, capsys):
    import ask

    assert (
        ask.main(
            ["digest", "--date", NOW.date().isoformat(), "--write", "--out", str(tmp_path / "d")]
        )
        == 0
    )
    payload = json.loads(
        (tmp_path / "d" / f"{NOW.date().isoformat()}.json").read_text(encoding="utf-8")
    )
    maybank = next(n for n in payload["names"] if n["instrument_id"] == "MYX:1155")
    assert maybank["tone"]["n"] == 2 and maybank["escalated"] == 2
