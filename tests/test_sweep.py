"""The catalogue-driven sweep: slots, both kinds of source, every recorded outcome.

Offline throughout: adapters and collectors are injected through the seams
`run_sweep` exposes for exactly this purpose, and the stores are temporary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.corpus import Corpus
from knowledge.facts import FactBook, Observation, SeriesPoint
from knowledge.feeds.adapter import FeedError, FixtureFeed
from knowledge.news.features import Article
from knowledge.sources.base import Collector, KeyMissing, PlanExcluded, Pull, SourceError
from knowledge.sweep import DEGRADED, probe, run_sweep

NOW = datetime(2026, 9, 4, 9, 30, tzinfo=UTC)
INDEX = {
    "Maybank": "MYX:1155",
    "Malayan Banking Berhad": "MYX:1155",
    "Tenaga": "MYX:5347",
    "Nvidia": "XNAS:NVDA",
    "NVIDIA": "XNAS:NVDA",
}


@dataclass
class Cfg:
    sources: tuple = ("google_news",)
    holdings: tuple = ()
    watchlist: tuple = ("MYX:1155", "MYX:5347", "XNAS:NVDA")
    corpus_db: str = ":memory:"
    facts_db: str = ":memory:"
    languages: tuple = ()
    gdelt_languages: tuple = ()
    gdelt_countries: tuple = ()
    gdelt_query: str = ""


def row(
    i, title, body="", when="2026-09-04T08:00:00+00:00", domain="thestar.com.my", lang="en", **extra
):
    """`_for=` names the query the RecordingAdapters double answers this row to."""
    return {
        "id": str(i),
        "title": title,
        "body": body,
        "published_at": when,
        "domain": domain,
        "language": lang,
        **extra,
    }


class RecordingAdapters:
    """adapter_for double: remembers every construction, answers from a table."""

    def __init__(self, table: dict[str, list[dict]] | None = None, fail: set[str] = frozenset()):
        self.table = table or {}
        self.fail = set(fail)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name, **kw):
        self.calls.append((name, kw))
        key = kw.get("query") or kw.get("instrument_id") or name
        if any(f in str(key) for f in self.fail):

            class _Broken(FixtureFeed):
                def _fetch_raw(self, since, limit):
                    raise FeedError(f"{key}: 429")

            return _Broken(records=[])
        rows = self.table.get(name, [])
        picked = [r for r in rows if not r.get("_for") or r["_for"] in str(key)]
        return FixtureFeed(records=[{k: v for k, v in r.items() if k != "_for"} for r in picked])


class CannedCollector(Collector):
    name = "fred"
    key_env = None

    def __init__(self, pull=None, raise_=None, **kw):
        super().__init__(**kw)
        self._pull = pull or Pull()
        self._raise = raise_
        self.seen: list[tuple] = []

    def collect(self, since, instruments=(), slot="all"):
        self.seen.append((since, instruments, slot))
        if self._raise:
            raise self._raise
        return self._pull


def collectors_returning(**by_name):
    def factory(name, **kw):
        return by_name[name]

    return factory


@pytest.fixture
def stores(tmp_path):
    return str(tmp_path / "corpus.db"), str(tmp_path / "facts.db")


# --- slots decide what runs and for whom ------------------------------------------------


def test_bursa_close_asks_only_the_bursa_names_in_their_own_edition(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(
        {"google_news": [row(1, "Maybank posts record quarter", _for="Maybank")]}
    )
    report = run_sweep(
        Cfg(),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    queries = [kw.get("query", "") for name, kw in adapters.calls if name == "google_news"]
    assert any("Maybank" in q for q in queries) and any("Tenaga" in q for q in queries)
    assert not any("NVIDIA" in q or "Nvidia" in q for q in queries)
    assert all(kw.get("edition") == "MY" for name, kw in adapters.calls if name == "google_news")
    assert report.exit_code == 0
    (result,) = report.results
    assert result.status == "ok" and result.stored == 1
    with Corpus(corpus_db) as c:
        assert c.counts()["articles"] == 1 and c.counts()["linked"] == 1
        (sweep,) = c.sweeps()
        assert sweep["source"] == "google_news" and sweep["status"] == "ok"


def test_us_close_asks_the_nasdaq_name_in_the_us_edition(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"google_news": []})
    run_sweep(
        Cfg(),
        "us_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert [kw.get("edition") for _, kw in adapters.calls] == ["US"]
    assert "NVIDIA" in adapters.calls[0][1]["query"]


def test_a_slot_with_no_enabled_source_cannot_run(stores):
    corpus_db, facts_db = stores
    report = run_sweep(
        Cfg(sources=("fred",)),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        adapter_for=RecordingAdapters(),
        collector_for=collectors_returning(fred=CannedCollector()),
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.exit_code == 2 and "no enabled source runs in slot" in report.could_not_run


def test_an_unknown_slot_or_source_cannot_run(stores):
    corpus_db, facts_db = stores
    assert run_sweep(Cfg(), "lunch", corpus_path=corpus_db, facts_path=facts_db).exit_code == 2
    report = run_sweep(
        Cfg(sources=("bloomberg_terminal",)), "all", corpus_path=corpus_db, facts_path=facts_db
    )
    assert report.exit_code == 2 and "catalogue" in report.could_not_run


def test_naming_a_source_runs_it_whatever_the_slot(stores):
    corpus_db, facts_db = stores
    fred = CannedCollector(
        Pull(series=[SeriesPoint("fred", "DFF", date(2026, 9, 3), Decimal("4.33"), NOW.date())])
    )
    report = run_sweep(
        Cfg(sources=("google_news",)),
        "bursa_close",
        sources=("fred",),
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fred=fred),
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert [r.name for r in report.results] == ["fred"] and report.exit_code == 0
    assert fred.seen[0][2] == "bursa_close"


# --- outcomes are recorded, every kind ----------------------------------------------------


def test_a_missing_key_is_skipped_and_the_job_stays_green(stores):
    corpus_db, facts_db = stores
    fred = CannedCollector(raise_=KeyMissing("fred needs FRED_API_KEY, which is not set."))
    report = run_sweep(
        Cfg(sources=("fred",)),
        "us_preopen",
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fred=fred),
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    (r,) = report.results
    assert r.status == "skipped" and "FRED_API_KEY" in r.detail and report.exit_code == 0
    with FactBook(facts_db) as book:
        (pull,) = book.pulls()
        assert pull["status"] == "skipped" and book.last_success("fred") is None
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
        assert sweep["detail"].startswith("skipped:")


def test_a_plan_boundary_at_the_top_is_skipped_too(stores):
    corpus_db, facts_db = stores
    fred = CannedCollector(raise_=PlanExcluded("fred: HTTP 403 - the plan does not include it"))
    report = run_sweep(
        Cfg(sources=("fred",)),
        "us_preopen",
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fred=fred),
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.results[0].status == "skipped" and report.exit_code == 0


def test_a_broken_structured_source_is_failed_and_exits_3(stores):
    corpus_db, facts_db = stores
    fred = CannedCollector(raise_=SourceError("fred fetch failed: HTTP 500"))
    report = run_sweep(
        Cfg(sources=("fred",)),
        "us_preopen",
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fred=fred),
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.results[0].status == "failed" and report.exit_code == 3
    with FactBook(facts_db) as book:
        assert book.counts()["failed_pulls"] == 1
    with Corpus(corpus_db) as c:
        assert c.counts()["failed_sweeps"] == 1, "the one attempt log is the corpus's"


def test_a_broken_news_source_is_failed_and_does_not_move_its_watermark(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(fail={"thestar"})

    def broken(name, **kw):
        class _Broken(FixtureFeed):
            def _fetch_raw(self, since, limit):
                raise FeedError("HTTP Error 404: Not Found")

        return _Broken(records=[])

    report = run_sweep(
        Cfg(sources=("thestar_business",)),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        adapter_for=broken,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.exit_code == 3 and report.results[0].status == "failed"
    with Corpus(corpus_db) as c:
        assert c.last_success("thestar_business") is None
    _ = adapters


def test_most_names_unreachable_is_degraded_and_exits_3_but_still_stores(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(
        {"google_news": [row(1, "Nvidia beats", _for="NVIDIA")]}, fail={"Maybank", "Tenaga"}
    )
    report = run_sweep(
        Cfg(),
        "all",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    (r,) = report.results
    assert r.status == DEGRADED and report.exit_code == 3
    assert r.stored == 1 and "failed: Maybank, Tenaga" in r.detail
    with Corpus(corpus_db) as c:
        assert c.counts()["articles"] == 1
        assert c.sweeps()[0]["status"] == "ok", "articles were stored, so the row says ok"


def test_every_name_failing_is_a_failed_row(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(fail={"Maybank", "Tenaga", "NVIDIA"})
    report = run_sweep(
        Cfg(),
        "all",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert (
        report.results[0].status == "failed" and "no name could be read" in report.results[0].detail
    )


def test_a_per_name_source_records_which_name_fetched_each_article_without_claiming_it(stores):
    """Provenance, not attribution.

    GDELT is asked one PHRASE per company and answers from a full-text index
    the corpus never sees: 457 of the 575 GDELT articles collected to
    2026-09-06 named no book company at all. The name that DID the fetching is
    worth recording; asserting it would put a story about nothing in that
    company's evidence.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(
        {
            "google_news": [
                row(1, "Nvidia beats", _for="NVIDIA"),
                row(2, "Chip demand broadly firm", _for="NVIDIA"),  # names nobody
            ]
        }
    )
    report = run_sweep(
        Cfg(),
        "all",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.exit_code == 0
    with Corpus(corpus_db) as c:
        stored = {a.title: a for a in c.articles(limit=10)}
        assert all(a.fetched_for == "XNAS:NVDA" for a in stored.values())
        assert stored["Nvidia beats"].instruments == ["XNAS:NVDA"]
        assert stored["Chip demand broadly firm"].instruments == []  # not claimed
        assert c.coverage() == [("google_news", "XNAS:NVDA", 2, 1)]


def test_the_sweep_row_says_how_many_articles_named_the_company(stores):
    """ "96 collected" and "4 about the company" are different numbers, and only
    the first was ever recorded."""
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(
        {
            "google_news": [
                row(1, "Nvidia beats", _for="NVIDIA"),
                row(2, "Rain in Perak", _for="Maybank"),
            ]
        }
    )
    report = run_sweep(
        Cfg(),
        "all",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    (r,) = report.results
    assert "named the company: 1 of 2" in r.detail
    assert "Maybank 0/1" in r.detail


# --- structured pulls land in both stores, and their articles in the corpus -----------------


def test_a_structured_pull_is_stored_recorded_and_its_articles_reach_the_corpus(stores):
    corpus_db, facts_db = stores
    pull = Pull(
        observations=[Observation("finnhub", "XNAS:NVDA", "pe_ttm", NOW.date(), Decimal("40"))],
        articles=[
            Article(
                doc_id="finnhub:1",
                title="Nvidia raises guidance",
                body="The company now expects data-centre revenue to grow.",
                source_domain="Reuters",
                published_at=NOW - timedelta(hours=3),
                instruments=["XNAS:NVDA"],
            )
        ],
        notes=["finnhub stock/metric: HTTP 403 - the plan does not include it"],
        requests=6,
    )
    finnhub = CannedCollector(pull)
    finnhub.name = "finnhub"
    report = run_sweep(
        Cfg(sources=("finnhub",)),
        "us_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        collector_for=collectors_returning(finnhub=finnhub),
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    (r,) = report.results
    assert r.status == "ok" and r.structured_stored == 1 and r.stored == 1 and r.requests == 6
    assert "plan does not include" in r.detail
    assert finnhub.seen[0][1] == ("XNAS:NVDA",), "per-instrument: only the US name at us_close"
    with FactBook(facts_db) as book:
        assert book.latest("XNAS:NVDA", "pe_ttm").value == Decimal("40")
        assert book.last_success("finnhub") is not None
    with Corpus(corpus_db) as c:
        (art,) = c.articles()
        assert art.instruments == ["XNAS:NVDA"] and art.quality is not None and art.quality > 0.6
        assert c.sweeps()[0]["detail"].startswith("facts 1;")


def test_a_per_instrument_structured_source_with_no_name_in_the_slot_is_skipped(stores):
    corpus_db, facts_db = stores
    edgar = CannedCollector()
    edgar.name = "edgar"
    report = run_sweep(
        Cfg(sources=("edgar",)),
        "bursa_close",
        sources=("edgar",),
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(edgar=edgar),
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.results[0].status == "skipped" and not edgar.seen


# --- cleaning rules apply on the way in ----------------------------------------------------


def test_the_language_allowlist_is_applied_from_config(stores):
    corpus_db, facts_db = stores
    adapters = RecordingAdapters(
        {
            "thestar_business": [
                row(1, "Maybank naik", lang="ms"),
                row(2, "LG festival", lang="Korean", domain="zdnet.co.kr"),
                row(3, "Maybank rises", lang="en"),
            ]
        }
    )
    report = run_sweep(
        Cfg(sources=("thestar_business",), languages=("English", "Malay")),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    (r,) = report.results
    assert r.fetched == 3 and r.kept == 2 and r.filtered == 1


# --- the probe -------------------------------------------------------------------------------


def test_probe_reports_every_source_and_never_raises(monkeypatch):
    """Whatever a source does - answers, refuses, has no key, blows up - the
    probe table has a row for it. Here every network call is refused."""
    import knowledge.sweep as sweep_mod

    def refused(name, **kw):
        class _Refused(FixtureFeed):
            def _fetch_raw(self, since, limit):
                raise FeedError("CONNECT refused")

        return _Refused(records=[])

    monkeypatch.setattr("knowledge.feeds.registry.adapter_for", refused)
    for name in ("FINNHUB_API_KEY", "FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FRED_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def no_network(req, timeout=None):
        raise OSError("no route")

    import knowledge.sources.registry as reg

    real = reg.collector_for
    monkeypatch.setattr(
        reg, "collector_for", lambda name, **kw: real(name, opener=no_network, **kw)
    )

    results = probe(Cfg(), clock=lambda: NOW, log=lambda m: None)
    by = {r.name: r.status for r in results}
    assert set(by) == set(sweep_mod.catalog.CATALOG)
    assert by["fred"] == "no-key" and by["finnhub"] == "no-key"
    assert by["gdelt"] == "failed" and by["edgar"] == "failed"
    assert all(s in ("ok", "no-key", "plan", "failed", "error") for s in by.values())


# --- one slot, one collection a day -------------------------------------------------


def _sweep(corpus_db, facts_db, slot="bursa_close", *, at=NOW, **kw):
    return run_sweep(
        Cfg(),
        slot,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=RecordingAdapters(
            {"google_news": [row(1, "Maybank posts record quarter", _for="Maybank")]}
        ),
        entity_index=INDEX,
        clock=lambda: at,
        log=lambda m: None,
        **kw,
    )


def test_the_second_run_of_a_slot_on_the_same_day_collects_nothing(stores):
    """The 2026-09-07 race, in one test.

    The Routine's catch-up dispatched `us_close` at 22:38 because the 21:15 cron
    was 78 minutes absent; the cron then arrived at 23:31 and swept the same slot
    again. Two full sweeps for one slot. Whoever gets there first now wins.
    """
    corpus_db, facts_db = stores
    first = _sweep(corpus_db, facts_db)
    assert first.exit_code == 0 and not first.already_ran
    assert first.results and first.results[0].stored == 1

    second = _sweep(corpus_db, facts_db, at=NOW + timedelta(hours=1))
    assert second.already_ran, "the second arrival must be a no-op"
    assert "already collected today" in second.already_ran
    assert second.results == [], "no source is contacted at all"

    with Corpus(corpus_db) as c:
        assert c.counts()["articles"] == 1, "and it stores nothing a second time"


def test_a_skipped_slot_exits_zero_because_it_is_not_a_failure(stores):
    """Exit 2 would tell a scheduler the collection broke on the day it worked."""
    corpus_db, facts_db = stores
    _sweep(corpus_db, facts_db)
    assert _sweep(corpus_db, facts_db, at=NOW + timedelta(hours=1)).exit_code == 0


def test_the_guard_is_per_slot_not_per_day(stores):
    """A day owes three slots. One having run must not silence the other two."""
    corpus_db, facts_db = stores
    _sweep(corpus_db, facts_db, slot="bursa_close")
    other = _sweep(corpus_db, facts_db, slot="us_close", at=NOW + timedelta(hours=1))
    assert not other.already_ran


def test_tomorrow_is_a_new_day(stores):
    corpus_db, facts_db = stores
    _sweep(corpus_db, facts_db)
    assert not _sweep(corpus_db, facts_db, at=NOW + timedelta(days=1)).already_ran


def test_force_and_slot_all_and_a_named_source_all_run_anyway(stores):
    """Three deliberate acts by a person; the guard is for the schedule."""
    corpus_db, facts_db = stores
    _sweep(corpus_db, facts_db)
    later = NOW + timedelta(hours=1)
    assert not _sweep(corpus_db, facts_db, at=later, force=True).already_ran
    assert not _sweep(corpus_db, facts_db, slot="all", at=later).already_ran
    assert not _sweep(corpus_db, facts_db, at=later, sources=("google_news",)).already_ran


def test_a_store_that_never_recorded_a_slot_is_not_read_as_having_run(stores):
    """The `slot` column landed on 2026-09-07 and older rows carry "". Reading
    those as a prior run would refuse every slot on any store written before
    that build - which is every store that has collected anything at all."""
    corpus_db, facts_db = stores
    with Corpus(corpus_db) as c:
        c.record_sweep(
            "old-run", "google_news", NOW - timedelta(days=1), "ok", at=NOW - timedelta(hours=2)
        )
    assert not _sweep(corpus_db, facts_db).already_ran


# --- a throttled source asks about fewer names, and never silently ----------
#
# GDELT refuses a mean of 3.5 of the nine names per run with HTTP 429, and a
# refusal costs three attempts and up to a 90s read before it gives up. That
# is how one source came to hold 98% of all sweep time. Capping the run drops
# the requests that were already failing - but only if the cap TILES, and the
# rotation this sweep already had does not.


def test_no_cap_asks_about_every_name():
    from knowledge.sweep import _window

    names = tuple(f"N{i}" for i in range(9))
    assert _window(names, 0, NOW) == names
    assert _window(names, 9, NOW) == names
    assert _window(names, 20, NOW) == names


def test_a_cap_takes_exactly_that_many():
    from knowledge.sweep import _window

    names = tuple(f"N{i}" for i in range(9))
    assert len(_window(names, 3, NOW)) == 3


def test_a_rerun_on_the_same_day_asks_about_the_same_names():
    """The catch-up dispatches a slot again. Asking a different three would
    make a re-run a second sample rather than a repeat of the one that was
    missed."""
    from datetime import timedelta

    from knowledge.sweep import _window

    names = tuple(f"N{i}" for i in range(9))
    morning = _window(names, 3, NOW)
    evening = _window(names, 3, NOW + timedelta(hours=11))
    assert morning == evening


@pytest.mark.parametrize("total", [3, 4, 5, 6, 9, 10])
def test_every_name_comes_round_within_one_cycle(total):
    """The guarantee the cap is sold on. Without it a capped run is not a
    slower collector, it is a collector that silently never sees some names."""
    from datetime import timedelta

    from knowledge.sweep import _window

    cap = 3
    names = tuple(f"N{i}" for i in range(total))
    cycle = -(-total // cap)
    for start in range(40):
        seen: set[str] = set()
        for d in range(cycle):
            seen.update(_window(names, cap, NOW + timedelta(days=start + d)))
        assert seen == set(names), f"day {start}: only {sorted(seen)}"


def test_the_hashed_rotation_is_NOT_a_substitute_for_the_window():
    """Why `_window` exists at all, pinned so it is not 'simplified' away.

    `_rotation_offset` is hashed from the run time on purpose - right for a
    list that gets consumed whole, because consecutive runs must not line up.
    Take a WINDOW of that order and the same property starves names at random:
    simulated over 14 days with nine names and a cap of three, the worst day
    reached three of the nine.
    """
    from datetime import timedelta

    from knowledge.sweep import _rotate, _rotation_offset

    names = tuple(f"N{i}" for i in range(9))
    worst = len(names)
    for d in range(14):
        day = NOW + timedelta(days=d)
        seen: set[str] = set()
        for hour in (9, 21):  # gdelt's two slots
            t = day.replace(hour=hour, minute=20)
            seen.update(_rotate(names, _rotation_offset(t))[:3])
        worst = min(worst, len(seen))
    assert worst < len(names), (
        "the hashed rotation now tiles; if that is deliberate, _window can go - "
        "but check it holds for every list size first"
    )


def test_a_deferred_name_is_named_rather_than_looking_like_a_quiet_day():
    """An article count cannot tell 'not asked' from 'asked, no news'. They are
    opposite facts about the world."""
    from knowledge.sweep import _deferred_note

    note = _deferred_note(["Maybank", "Tenaga"], ["Maybank", "Tenaga", "Genting", "IHH"])
    assert "deferred to a later run" in note
    assert "Genting" in note and "IHH" in note
    assert "Maybank" not in note


def test_nothing_deferred_says_nothing():
    from knowledge.sweep import _deferred_note

    assert _deferred_note(["A", "B"], ["A", "B"]) == ""


def test_only_the_throttled_source_carries_a_cap():
    """A cap on a source that answers every request would cost coverage for
    nothing. google_news returns 79% on-topic at 0.1s a row; it is not the
    problem and must not inherit the fix."""
    from knowledge.sources.catalog import CATALOG

    assert CATALOG["gdelt"].names_per_run == 3
    assert CATALOG["google_news"].names_per_run == 0
    assert CATALOG["yahoo_rss"].names_per_run == 0


BOOK_MY = ("MYX:1155", "MYX:5347", "MYX:5183", "MYX:5225", "MYX:8869", "MYX:3182")


def test_a_capped_source_makes_three_requests_for_a_six_name_book(stores):
    """End to end: the cap reaches the adapter, not just the helper.

    Six Malaysian names, three requests. The three that do not go out are the
    ones GDELT was refusing anyway - 84 name-failures over 30 recorded runs,
    every one paid for with three attempts and up to a 90s read.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"gdelt": []})
    report = run_sweep(
        Cfg(sources=("gdelt",), watchlist=BOOK_MY),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    gdelt_calls = [kw for name, kw in adapters.calls if name == "gdelt"]
    assert len(gdelt_calls) == 3, [kw.get("query") for kw in gdelt_calls]
    assert report.exit_code == 0


def test_the_names_not_asked_about_are_named_in_the_sweep_row(stores):
    """Otherwise a deferred name and a name with no news are the same row."""
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"gdelt": []})
    run_sweep(
        Cfg(sources=("gdelt",), watchlist=BOOK_MY),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
    assert "deferred to a later run" in (sweep["detail"] or "")


def test_an_uncapped_source_still_asks_about_every_name(stores):
    """The cap is GDELT's, and must not leak onto a source that answers."""
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"google_news": []})
    run_sweep(
        Cfg(sources=("google_news",), watchlist=BOOK_MY),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert len([1 for name, _ in adapters.calls if name == "google_news"]) == len(BOOK_MY)
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
    assert "deferred" not in (sweep["detail"] or "")


def test_google_news_is_asked_for_every_name_the_company_is_printed_under(stores):
    """End to end: the alias reaches the adapter, not just the helper.

    Petronas Chemicals collected zero articles for a week because the sweep
    asked for the display name alone. The linker had "PCHEM" the whole time.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"google_news": []})
    run_sweep(
        Cfg(sources=("google_news",), watchlist=("MYX:5183", "MYX:5347")),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    queries = [kw.get("query", "") for name, kw in adapters.calls if name == "google_news"]
    joined = " | ".join(queries)
    assert '"PCHEM"' in joined, joined
    assert '"TNB"' in joined, joined


def test_a_name_with_no_alias_row_still_gets_asked_for():
    """The fallback matters: an instrument absent from entities.yaml must not
    silently stop being collected. `search_names()` has no row for it, so the
    query falls back to the label the sweep already resolved."""
    from knowledge.feeds.company_feeds import finance_query
    from knowledge.graph.ids import search_names

    assert search_names().get("XKLS:9999") is None
    names = search_names().get("XKLS:9999") or ("Nobody Bhd",)
    assert '"Nobody Bhd"' in finance_query(names)


def test_gdelt_is_asked_for_every_name_the_company_is_printed_under(stores):
    """End to end: the alias reaches the adapter, not only the helper.

    The per-name path sent `terms[0]` alone, so Petronas Chemicals was asked
    for as "Petronas Chemicals" and never as "PCHEM". The linker has had
    "PCHEM" the whole time - this corpus could recognise a name it never asked
    for, which is what zero articles out of 2,671 looked like from outside.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"gdelt": []})
    run_sweep(
        Cfg(sources=("gdelt",), watchlist=("MYX:5183", "MYX:1155")),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    queries = [kw.get("query", "") for name, kw in adapters.calls if name == "gdelt"]
    joined = " | ".join(queries)
    assert '"PCHEM"' in joined, joined
    assert '"Maybank"' in joined, joined
    # One request per company still: the aliases widen the query, they do not
    # multiply the requests, which is what the three-names-per-run cap counts.
    assert len(queries) == 2, queries


def test_a_gdelt_name_with_no_askable_alias_is_skipped_not_sent(stores):
    """A phrase under five characters is refused by the DOC API as plain text,
    costing three retries and up to a 90s read. The name is skipped instead."""
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({"gdelt": []})
    run_sweep(
        Cfg(sources=("gdelt",), watchlist=("MYX:9999",)),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert not [kw for name, kw in adapters.calls if name == "gdelt"]


def test_a_source_with_nothing_to_do_in_this_slot_still_records_a_sweep(stores):
    """A skip is a dispatch that had nothing to do, and the silence rule has to
    see it.

    `sweep_silence` reads `corpus.last_success` and nothing else. fmp is
    per-instrument and `us_preopen` carries no per-instrument work, so fmp was
    correctly skipped every weekday, recorded the skip in the PULLS table only,
    and opened a silence alert on the fourth day about a collector that had
    dispatched it on time all four days. The KeyMissing skip beside it has
    always written both rows; this one now does too.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({})
    run_sweep(
        Cfg(sources=("fmp",), watchlist=("MYX:1155",)),
        "us_preopen",  # a macro slot: no market's names are collected in it
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    with Corpus(corpus_db) as c:
        rows = [s for s in c.sweeps() if s["source"] == "fmp"]
        assert rows, "the skip left no sweep row, so the source reads as stopped"
        assert "skipped" in (rows[0]["detail"] or "")
        assert c.last_success("fmp") is not None, "sweep_silence would still fire"
