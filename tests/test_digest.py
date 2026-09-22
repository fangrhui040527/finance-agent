"""The digest: one day of the stores as a page, for the person, the agents, the routine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.corpus import Corpus
from knowledge.digest import build_digest, write_digest
from knowledge.facts import EventRecord, FactBook, Observation, SeriesPoint
from knowledge.feeds.adapter import FixtureFeed

NOW = datetime(2026, 9, 4, 21, 30, tzinfo=UTC)
INDEX = {"Maybank": "MYX:1155", "Nvidia": "XNAS:NVDA", "NVIDIA": "XNAS:NVDA"}


@dataclass
class Cfg:
    watchlist: tuple = ("MYX:1155", "XNAS:NVDA")
    holdings: tuple = ()
    corpus_db: str = ":memory:"
    facts_db: str = ":memory:"


def seed(tmp_path):
    corpus_db, facts_db = str(tmp_path / "c.db"), str(tmp_path / "f.db")
    rows = [
        {
            "id": "1",
            "title": "Maybank posts record quarter",
            "body": "Malayan Banking Berhad beat estimates on strong fee income.",
            "published_at": "2026-09-04T08:00:00+00:00",
            "domain": "theedgemalaysia.com",
        },
        {
            "id": "2",
            "title": "Maybank posts record quarter",
            "body": "Syndicated copy of the same story.",
            "published_at": "2026-09-04T08:30:00+00:00",
            "domain": "aol.com",
        },
        {
            "id": "3",
            "title": "Maybank warns of slower loan growth",
            "body": "The bank cut its guidance for the second half amid uncertainty.",
            "published_at": "2026-09-04T10:00:00+00:00",
            "domain": "thestar.com.my",
        },
        {
            "id": "4",
            "title": "Nvidia old story",
            "body": "From last week.",
            "published_at": "2026-08-28T10:00:00+00:00",
            "domain": "reuters.com",
        },
        {
            "id": "5",
            "title": "Should You Buy Nvidia Stock Before Earnings?",
            "body": "",
            "published_at": "2026-09-04T12:00:00+00:00",
            "domain": "fool.com",
        },
    ]
    feed = FixtureFeed(records=rows)
    arts, _ = feed.normalize(
        feed.fetch(NOW - timedelta(days=30)),
        entity_index=INDEX,
        watchlist={"MYX:1155", "XNAS:NVDA"},
    )
    with Corpus(corpus_db) as c:
        c.add_all(arts, "fixture", seen_at=NOW)
        c.record_sweep(
            "r1",
            "google_news",
            NOW - timedelta(days=1),
            "ok",
            at=NOW,
            fetched=5,
            kept=4,
            stored=4,
            detail="read but empty: NVIDIA",
        )
    with FactBook(facts_db) as f:
        f.add_observations(
            [
                Observation("finnhub", "MYX:1155", "pe_ttm", NOW.date(), Decimal("12.1")),
                Observation(
                    "fmp",
                    "MYX:1155",
                    "revenue",
                    date(2026, 8, 1),
                    Decimal("7250000000"),
                    period_end=date(2026, 6, 30),
                    currency="MYR",
                ),
            ]
        )
        f.add_events(
            [
                EventRecord(
                    "bursa_announcements",
                    "a1",
                    "MYX:1155",
                    "announcement",
                    NOW - timedelta(hours=5),
                    "Quarterly report for the period ended 30 June 2026",
                ),
                EventRecord(
                    "finnhub",
                    "e1",
                    "XNAS:NVDA",
                    "earnings_result",
                    NOW - timedelta(days=20),
                    "Q3 results after the close",
                    effective_at=NOW + timedelta(days=12),
                ),
            ]
        )
        f.add_series(
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


def test_the_digest_arranges_one_day_per_name(tmp_path):
    corpus_db, facts_db = seed(tmp_path)
    d = build_digest(Cfg(), NOW.date(), corpus_path=corpus_db, facts_path=facts_db, now=NOW)
    maybank, nvidia = d.names
    assert maybank.label == "Maybank"
    titles = [s["title"] for s in maybank.stories]
    assert titles.count("Maybank posts record quarter") == 1, "the syndicated copy is folded"
    assert "Maybank warns of slower loan growth" in titles
    assert maybank.tone["n"] == 2 and maybank.tone["sources"] == 2
    assert maybank.escalated == 2 and all(s["escalated"] for s in maybank.stories)
    assert [e["kind"] for e in maybank.events] == ["announcement"]
    assert {s["concept"] for s in maybank.snapshot} == {"pe_ttm", "revenue"}
    assert next(s for s in maybank.snapshot if s["concept"] == "revenue")["value"] == "7.25bn"
    assert nvidia.upcoming and nvidia.upcoming[0]["kind"] == "earnings_result"
    assert all("old story" not in s["title"] for s in nvidia.stories), "outside the window"
    assert all("Should You Buy" not in s["title"] for s in nvidia.stories), (
        "below the quality floor"
    )


def test_macro_shows_the_latest_point_and_its_change(tmp_path):
    corpus_db, facts_db = seed(tmp_path)
    d = build_digest(Cfg(), NOW.date(), corpus_path=corpus_db, facts_path=facts_db, now=NOW)
    (dff,) = d.macro
    assert dff["value"] == "4.08" and dff["change"] == "-0.25" and dff["title"] == "Fed funds"


def test_the_days_collection_rows_are_on_the_page(tmp_path):
    corpus_db, facts_db = seed(tmp_path)
    d = build_digest(Cfg(), NOW.date(), corpus_path=corpus_db, facts_path=facts_db, now=NOW)
    (row,) = d.collection
    assert row["source"] == "google_news" and "read but empty" in row["detail"]


def test_markdown_and_json_agree_and_are_written(tmp_path):
    corpus_db, facts_db = seed(tmp_path)
    d = build_digest(Cfg(), NOW.date(), corpus_path=corpus_db, facts_path=facts_db, now=NOW)
    md = d.to_markdown()
    assert md.startswith("# Digest 2026-09-04") and "### Maybank (MYX:1155)" in md
    assert "★" in md and "| Fed funds | 4.08 |" in md
    payload = json.loads(d.to_json())
    assert payload["names"][0]["tone"]["n"] == 2
    md_path, js_path = write_digest(d, tmp_path / "digests")
    assert (
        md_path.read_text(encoding="utf-8").startswith("# Digest")
        and (tmp_path / "digests" / "latest.md").exists()
    )
    assert json.loads(js_path.read_text(encoding="utf-8"))["day"] == "2026-09-04"


def test_a_quiet_name_says_so_instead_of_disappearing(tmp_path):
    corpus_db, facts_db = seed(tmp_path)
    d = build_digest(
        Cfg(watchlist=("MYX:5347",)),
        NOW.date(),
        corpus_path=corpus_db,
        facts_path=facts_db,
        now=NOW,
    )
    (tenaga,) = d.names
    assert tenaga.quiet and "quiet: nothing collected" in d.to_markdown()


def test_the_cli_prints_and_writes(tmp_path, capsys):
    import ask

    corpus_db, facts_db = seed(tmp_path)
    code = ask.main(
        [
            "digest",
            "--date",
            "2026-09-04",
            "--db",
            corpus_db,
            "--facts-db",
            facts_db,
            "--write",
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    assert (tmp_path / "out" / "2026-09-04.md").exists()
    assert "# Digest 2026-09-04" in capsys.readouterr().out


def test_the_digest_stars_by_the_gate_in_force_today_not_the_one_at_ingest(tmp_path):
    """The corpus is append-only, so `articles.escalated` records the rule that
    was live the day each story arrived. A reading list must apply today's rule
    or a gate change never reaches the page."""
    from datetime import UTC, datetime

    from knowledge.corpus import Corpus
    from knowledge.digest import build_digest
    from knowledge.news.features import Article, LexiconExtractor

    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    ex = LexiconExtractor()
    path = tmp_path / "corpus.db"
    with Corpus(path) as c:
        for doc_id, title in (
            ("a", "Maybank provides S$590,000 to beneficiaries through its programme"),
            ("b", "Maybank profit rose on wider margins"),
        ):
            art = Article(
                doc_id=doc_id,
                title=title,
                body="",
                source_domain="example.com",
                published_at=now,
                instruments=["MYX:1155"],
                quality=1.0,
                # stored TRUE for both, which is what the old gate did
                escalated=True,
            )
            art.features = ex.extract(art.text, ["Maybank"])
            c.add(art, source="fixture", seen_at=now)

    class _Cfg:
        watchlist = ("MYX:1155",)
        holdings = ()
        corpus_db = str(path)
        facts_db = str(tmp_path / "facts.db")

    d = build_digest(_Cfg(), now=now, corpus_path=str(path), facts_path=str(tmp_path / "facts.db"))
    name = next(n for n in d.names if n.instrument_id == "MYX:1155")
    assert len(name.stories) == 2, "both stories are still shown"
    assert name.escalated == 1, "only the one reporting something financial is starred"
    starred = [s["title"] for s in name.stories if s["escalated"]]
    assert starred == ["Maybank profit rose on wider margins"]


# --- a second arrival on the same slot must leave the directory alone -------------
#
# Two things dispatch the collector for one slot - the cron and the nightly
# Routine's catch-up - and `_already_ran` makes the second a no-op that
# collects nothing. It was not a no-op here. The digest was re-rendered with a
# later `Generated` line over identical figures, the workflow saw three changed
# files and pushed them: the 2026-09-08 23:23 commit is `2084 articles` to
# `2084 articles`, `4465 observations` to `4465 observations`.
#
# `git log data/digests` is the cheapest record of when collection actually
# moved. A timestamp advancing over unchanged data makes that record lie.


@pytest.fixture
def one_days_stores(tmp_path):
    """Seeded ONCE. `seed` appends a sweep row per call, so seeding again would
    make the second digest genuinely different and prove nothing."""
    return seed(tmp_path)


def _render(stores, minute: str, slot: str = "all"):
    corpus_db, facts_db = stores
    when = datetime.fromisoformat(f"2026-09-04T{minute}:00+00:00")
    d = build_digest(Cfg(), NOW.date(), corpus_path=corpus_db, facts_path=facts_db, now=when)
    d.slot = slot
    return d


def test_a_re_render_that_only_moves_the_clock_writes_nothing(tmp_path, one_days_stores):
    root = tmp_path / "digests"
    md = write_digest(_render(one_days_stores, "22:38"), root)[0]
    before = md.read_text(encoding="utf-8")
    stamp = md.stat().st_mtime_ns

    write_digest(_render(one_days_stores, "23:23"), root)

    assert md.read_text(encoding="utf-8") == before, "the timestamp moved over identical figures"
    assert md.stat().st_mtime_ns == stamp, "the file was rewritten with identical bytes"


def test_a_changed_figure_is_still_written(tmp_path, one_days_stores):
    """The guard must not be able to freeze a digest. Only the clock is ignored."""
    root = tmp_path / "digests"
    md = write_digest(_render(one_days_stores, "22:38"), root)[0]
    before = md.read_text(encoding="utf-8")

    d2 = _render(one_days_stores, "23:23")
    d2.counts = {**d2.counts, "articles": int(d2.counts.get("articles", 0)) + 1}
    write_digest(d2, root)

    after = md.read_text(encoding="utf-8")
    assert after != before
    assert f"{int(d2.counts['articles'])} articles in the corpus" in after


def test_a_different_slot_over_the_same_figures_is_a_fact_and_is_written(tmp_path, one_days_stores):
    """The slot shares that line with the timestamp and is NOT ignored: which
    run last wrote the day's digest is a fact about the collection, and a
    re-render at a later minute is not."""
    root = tmp_path / "digests"
    md = write_digest(_render(one_days_stores, "13:42", slot="bursa_close"), root)[0]
    before = md.read_text(encoding="utf-8")

    write_digest(_render(one_days_stores, "22:38", slot="us_close"), root)

    after = md.read_text(encoding="utf-8")
    assert after != before and "slot `us_close`" in after


def test_a_skipped_write_still_repairs_a_missing_latest(tmp_path, one_days_stores):
    """The pointer must not be left behind by a write that did not happen."""
    root = tmp_path / "digests"
    write_digest(_render(one_days_stores, "22:38"), root)
    (root / "latest.md").unlink()

    write_digest(_render(one_days_stores, "23:23"), root)

    assert (root / "latest.md").read_text(encoding="utf-8").startswith("# Digest 2026-09-04")


def test_the_publication_rail_still_runs_on_a_skipped_write(tmp_path, one_days_stores, monkeypatch):
    """A digest is checked before it is compared. Waving one through for being
    similar to a digest that already passed would be a gate with a hole in it."""
    import core.guardrails.publish as P

    root = tmp_path / "digests"
    write_digest(_render(one_days_stores, "22:38"), root)

    seen: list[str] = []
    real = P.publish
    monkeypatch.setattr(P, "publish", lambda *a, **k: (seen.append("checked"), real(*a, **k))[1])

    write_digest(_render(one_days_stores, "23:23"), root)
    assert seen, "the rail was skipped along with the write"
