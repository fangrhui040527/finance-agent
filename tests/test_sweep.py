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
from knowledge.feeds.adapter import FeedError, FeedThrottled, FixtureFeed
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
                # The message a throttled feed actually raises: what went
                # wrong, not what was asked. It used to interpolate the whole
                # Google News query, which is 200 characters of the question
                # rather than the answer - and while the note dropped reasons
                # entirely nothing noticed. Shared across names on purpose:
                # a throttle hits every name the same way, and that is what
                # lets `_reason_note` say it once.
                def _fetch_raw(self, since, limit):
                    raise FeedError("HTTP Error 429: Too Many Requests")

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
    assert r.stored == 1
    assert "Maybank" in r.detail and "Tenaga" in r.detail and "429" in r.detail
    with Corpus(corpus_db) as c:
        assert c.counts()["articles"] == 1
        # THIS ASSERTION USED TO READ `== "ok"`, with the rationale "articles
        # were stored, so the row says ok". The premise was right and the
        # conclusion was not: storing articles means the WINDOW WAS READ, which
        # is a fact about the watermark, not about the run's health. Writing
        # `ok` spent the status column on the watermark and left nothing to say
        # the run was degraded - so the console printed DEGRADED, the process
        # exited 3, and the only durable record said the source was fine.
        # Measured on 2026-09-15: 23 of 70 per-name sweeps, every one GDELT,
        # degraded by `_mostly_failed` and stored as ok. Defect log §20.
        assert c.sweeps()[0]["status"] == DEGRADED
        # And the property the old assertion was actually protecting, kept:
        # a degraded run still advances the watermark, because the names it DID
        # reach have been read and re-reading them every run would be the cost
        # of saying so.
        assert c.last_success("google_news") is not None


def test_a_failure_carries_its_reason_not_just_its_name():
    """The cause survives into the note, grouped by cause rather than by name.

    `_fetch_each` has always collected `(name, reason)`; the note joined the
    names and dropped the reasons. So GDELT answering HTTP 429 on two of three
    names every run for twelve days wrote `failed: NVIDIA, Apple` - which reads
    as two companies having a quiet day, not as a throttle. Defect log §20.
    """
    from knowledge.sweep import _sweep_note

    note = _sweep_note(
        [
            ("NVIDIA", "GDELT fetch failed: HTTP Error 429: Too Many Requests"),
            ("Apple", "GDELT fetch failed: HTTP Error 429: Too Many Requests"),
        ],
        [],
        [("Microsoft", 0)],
    )
    assert "429" in note, "the reason is the finding; the names are how many"
    # One cause, named once, with both companies behind it.
    assert note.count("429") == 1 and "NVIDIA, Apple" in note
    assert "read but empty: Microsoft" in note


def test_the_note_stays_bounded_when_every_name_fails_differently():
    """Grouping only shortens while the names share a cause - so cap the rest.

    A source whose every name fails its own way would otherwise write a note as
    long as its book, into a column the nightly page has to render.
    """
    from knowledge.sweep import MAX_REASONS, _sweep_note

    note = _sweep_note([(f"name{i}", f"distinct failure number {i}") for i in range(9)], [])
    assert note.count("distinct failure") == MAX_REASONS
    assert f"+{9 - MAX_REASONS} more" in note, "the tail is counted, never silently dropped"


def test_a_long_reason_cannot_crowd_out_the_rest_of_the_note():
    from knowledge.sweep import REASON_CHARS, _sweep_note

    note = _sweep_note([("Maybank", "x" * 500)], ["Tenaga"])
    assert "x" * REASON_CHARS in note and "x" * (REASON_CHARS + 1) not in note
    assert "not reached: Tenaga" in note, "the later parts of the note survive"


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


def test_only_the_throttled_source_groups_and_paces_its_requests():
    """The grouping and the pace are GDELT's and must not leak onto a source
    that answers every request: google_news returns 79% on-topic at 0.1s a
    row, one company per query, and inherits neither. And the per-run CAP is
    gone from gdelt rather than stacked under the grouping: it dropped names
    without changing the pace, which is why four days of capped runs stored
    nothing (2026-09-14 to 17)."""
    from knowledge.sources.catalog import CATALOG

    assert CATALOG["gdelt"].names_per_request == 3
    assert CATALOG["gdelt"].seconds_between_requests >= 15
    assert CATALOG["gdelt"].names_per_run == 0, "grouping replaces the cap"
    for name in ("google_news", "yahoo_rss"):
        assert CATALOG[name].names_per_request == 1 and CATALOG[name].names_per_run == 0
        assert CATALOG[name].seconds_between_requests == 0


BOOK_MY = ("MYX:1155", "MYX:5347", "MYX:5183", "MYX:5225", "MYX:8869", "MYX:3182")
BOOK_US = ("XNAS:NVDA", "XNAS:AAPL", "XNAS:MSFT")
THROTTLED = "GDELT fetch failed: HTTP Error 429: Too Many Requests"


class RefusingAdapters(RecordingAdapters):
    """RecordingAdapters whose N-th gdelt feed raises instead of answering.

    Refuses by ORDINAL, not by name: the rotation decides which names land in
    which group, and the sweep must not need to know. `error` and `message`
    are what the refused feed raises - FeedThrottled with a 429 by default, or
    a plain FeedError for the test that pins the difference between them.
    """

    def __init__(
        self, refuse_request: int, error: type[FeedError] = FeedThrottled, message: str = THROTTLED
    ):
        super().__init__({"gdelt": []})
        self.refuse_request = refuse_request
        self.error = error
        self.message = message
        self.gdelt_requests = 0

    def __call__(self, name, **kw):
        if name != "gdelt":
            return super().__call__(name, **kw)
        self.gdelt_requests += 1
        if self.gdelt_requests != self.refuse_request:
            return super().__call__(name, **kw)
        self.calls.append((name, kw))
        error, message = self.error, self.message

        class _Refused(FixtureFeed):
            def _fetch_raw(self, since, limit):
                raise error(message)

        return _Refused(records=[])


def _sweep_gdelt(stores, adapters, book, slot, **kw):
    corpus_db, facts_db = stores
    return run_sweep(
        Cfg(sources=("gdelt",), watchlist=book),
        slot,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
        sleep=kw.pop("sleep", lambda s: None),
        **kw,
    )


def test_a_nine_name_book_is_three_grouped_requests_fifteen_seconds_apart(stores):
    """End to end: the grouping and the pace reach the adapter, not just the catalogue.

    Nine names were nine requests back to back, and GDELT's quota - about one
    request every five seconds, counted per address, and GitHub's runners share
    addresses - was gone after about five. Three requests, each carrying three
    companies' phrases, starting at least fifteen seconds apart, is under it.
    The clock is frozen, so every gap owed is the full fifteen seconds.
    """
    corpus_db, _ = stores
    adapters = RecordingAdapters({"gdelt": []})
    waits: list[float] = []
    report = _sweep_gdelt(stores, adapters, BOOK_MY + BOOK_US, "all", sleep=waits.append)
    queries = [kw["query"] for name, kw in adapters.calls if name == "gdelt"]
    assert len(queries) == 3, queries
    # Every company's phrases are in exactly one request: linkage is unchanged.
    for phrase in ('"Maybank"', '"PCHEM"', '"Genting"', '"NVIDIA"', '"Apple"', '"Microsoft"'):
        assert sum(phrase in q for q in queries) == 1, (phrase, queries)
    assert all(q.startswith("(") and q.count("(") == 1 for q in queries), "one flat OR, no nesting"
    assert waits == [15.0, 15.0], waits
    assert report.exit_code == 0
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
    assert sweep["detail"].startswith("grouped 3 names per request; 3 requests"), sweep["detail"]
    assert "deferred" not in sweep["detail"]


def test_a_six_name_book_is_two_requests(stores):
    """Six Bursa names at bursa_close: two groups, one pause between them."""
    adapters = RecordingAdapters({"gdelt": []})
    waits: list[float] = []
    _sweep_gdelt(stores, adapters, BOOK_MY, "bursa_close", sleep=waits.append)
    assert len([1 for name, _ in adapters.calls if name == "gdelt"]) == 2
    assert waits == [15.0]


def test_a_paced_source_is_asked_for_one_page_per_request(stores):
    """Past its page size the adapter pages by slicing time, and every slice is
    a request the pacing loop never saw - fired back to back, on the quota the
    pace exists to respect. `--limit 1000` on a nine-name book is 333 records a
    group against a 250-record page; the ask is bounded at the page instead."""
    limits: list[int] = []

    def adapters(name, **kw):
        class _OnePage(FixtureFeed):
            MAX_RECORDS = 250

            def _fetch_raw(self, since, limit):
                limits.append(limit)
                return []

        return _OnePage(records=[])

    _sweep_gdelt(stores, adapters, BOOK_MY + BOOK_US, "all", limit=1000)
    assert limits == [250, 250, 250], limits


def test_the_first_429_ends_the_slot_and_names_the_groups_it_deferred(stores):
    """A 429 is the quota for this ADDRESS, not for this request. Asking the
    next group would spend the retry budget to learn that again, and the
    refused request would count against the next window too. So the sweep
    stops, and the row says so: which request was refused, and which names
    wait for the next slot - whose rotation starts elsewhere, so they are not
    the same names refused tomorrow."""
    corpus_db, _ = stores
    adapters = RefusingAdapters(refuse_request=2)
    report = _sweep_gdelt(stores, adapters, BOOK_MY + BOOK_US, "all")
    assert adapters.gdelt_requests == 2, "the third group was never asked"
    (r,) = report.results
    assert r.status == "ok", r.detail  # three read, three refused: not more than half
    assert r.detail.startswith(
        "grouped 3 names per request; 2 requests "
        "(HTTP 429 on request 2: the rest of the slot is deferred, not asked); deferred: "
    ), r.detail
    deferred = r.detail.split("deferred: ", 1)[1].split(";")[0]
    assert len(deferred.split(", ")) == 3, deferred
    rest = r.detail.split("deferred: ", 1)[1]
    assert "HTTP Error 429" in rest, "the names that were refused still say why"
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
        assert sweep["status"] == "ok" and "deferred: " in sweep["detail"]
        assert c.last_success("gdelt") is not None, "the group that WAS read moves the watermark"


def test_a_429_on_the_first_request_is_a_failed_row_that_still_names_the_deferred(stores):
    """Nothing read, so the watermark stays and the row is failed - and the six
    names never asked are named in it, because a failed row listing three
    companies reads as three companies with a problem."""
    corpus_db, _ = stores
    adapters = RefusingAdapters(refuse_request=1)
    report = _sweep_gdelt(stores, adapters, BOOK_MY + BOOK_US, "all")
    assert adapters.gdelt_requests == 1
    (r,) = report.results
    assert r.status == "failed" and report.exit_code == 3
    assert "no name could be read" in r.detail and "429" in r.detail, r.detail
    deferred = r.detail.split("deferred: ", 1)[1].split(";")[0]
    assert len(deferred.split(", ")) == 6, deferred
    with Corpus(corpus_db) as c:
        assert c.last_success("gdelt") is None


def test_a_plain_failure_on_one_group_does_not_defer_the_others(stores):
    """Only a 429 is the quota. A 503 on one group says nothing about the next,
    which is asked as before - the deferral is for what waiting cannot fix
    inside one slot, not for every bad answer."""
    adapters = RefusingAdapters(
        refuse_request=2,
        error=FeedError,
        message="GDELT fetch failed: HTTP Error 503: Service Unavailable",
    )
    report = _sweep_gdelt(stores, adapters, BOOK_MY + BOOK_US, "all")
    assert adapters.gdelt_requests == 3
    (r,) = report.results
    assert "deferred" not in r.detail and "503" in r.detail, r.detail
    assert r.detail.startswith("grouped 3 names per request; 3 requests;"), r.detail


def test_gdelt_is_asked_from_two_hours_before_its_watermark(stores):
    """The watermark is the time of the last READ, and GDELT indexes a story
    minutes to hours after it is published, so a story published just before
    the last read and indexed just after it fell between two windows. The
    overlap is free - the corpus dedupes on dup_hash - and google_news, which
    serves a fixed two-day window of its own, is not widened."""
    corpus_db, facts_db = stores
    seen: dict[str, list[datetime]] = {}

    def adapters(name, **kw):
        class _Since(FixtureFeed):
            def _fetch_raw(self, since, limit):
                seen.setdefault(name, []).append(since)
                return []

        return _Since(records=[])

    run_sweep(
        Cfg(sources=("gdelt", "google_news"), watchlist=("MYX:1155",)),
        "bursa_close",
        hours=24,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        adapter_for=adapters,
        entity_index=INDEX,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert seen["gdelt"] == [NOW - timedelta(hours=26)]
    assert seen["google_news"] == [NOW - timedelta(hours=24)]


def test_a_grouped_query_is_one_flat_or_of_every_askable_alias():
    """The DOC API wants an OR list inside ONE pair of parentheses and does not
    document nesting, so the per-company forms are flattened rather than
    joined. A name with no alias long enough for the API contributes nothing,
    and a group made only of such names sends no request at all."""
    from knowledge.sweep import _gdelt_group_query, _groups

    q = _gdelt_group_query(("MYX:5183", "MYX:1155", "MYX:9999"))
    for phrase in ('"Petronas Chemicals"', '"PCHEM"', '"Maybank"', '"Malayan Banking"'):
        assert phrase in q, q
    assert q.count("(") == 1 and q.startswith("(") and q.endswith(")"), q
    assert _gdelt_group_query(("MYX:9999",)) == ""
    assert _groups(tuple("ABCDEFGHI"), 3) == [tuple("ABC"), tuple("DEF"), tuple("GHI")]
    assert _groups(tuple("ABCD"), 3) == [tuple("ABC"), ("D",)]
    assert _groups(("A", "B"), 1) == [("A",), ("B",)]


def test_a_grouped_article_is_stamped_with_the_member_it_names(stores):
    """Provenance with one id per row and three names per request: an article
    naming a member of the group is stamped with that member, so provenance
    and attribution agree; one naming none is stamped with the group's first,
    so it is counted as a miss in the coverage table rather than vanishing
    from it. The total is exact; the per-name split is what one column can
    carry."""
    corpus_db, _ = stores
    adapters = RecordingAdapters(
        {"gdelt": [row(1, "Nvidia beats"), row(2, "Chip demand broadly firm")]}
    )
    _sweep_gdelt(stores, adapters, BOOK_US, "us_close")
    assert len([1 for name, _ in adapters.calls if name == "gdelt"]) == 1
    with Corpus(corpus_db) as c:
        stored = {a.title: a for a in c.articles(limit=10)}
        assert stored["Nvidia beats"].fetched_for == "XNAS:NVDA"
        assert stored["Nvidia beats"].instruments == ["XNAS:NVDA"]
        assert stored["Chip demand broadly firm"].fetched_for in set(BOOK_US)
        assert stored["Chip demand broadly firm"].instruments == []  # not claimed
        rows = c.coverage()
        assert sum(kept for _, _, kept, _ in rows) == 2
        assert sum(named for _, _, _, named in rows) == 1


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
    # Two companies fit one grouped request: the aliases widen the query, they
    # do not multiply the requests, and neither does a second company.
    assert len(queries) == 1, queries


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
    per-instrument, and when a slot hands it no names (here: a Bursa-only book
    at the US pre-open) it is correctly skipped, recorded the skip in the PULLS
    table only, and opened a silence alert on the fourth day about a collector
    that had dispatched it on time all four days. The KeyMissing skip beside
    it has always written both rows; this one now does too.
    """
    corpus_db, facts_db = stores
    adapters = RecordingAdapters({})
    run_sweep(
        Cfg(sources=("fmp",), watchlist=("MYX:1155",)),
        "us_preopen",  # the US names' slot: a Bursa-only book has nothing in it
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


def test_fmp_is_asked_for_the_us_names_before_the_open(stores):
    """SLOTS['us_preopen'] was an empty set - "macro only" - while fmp was
    catalogued per-instrument in that very slot, so `instruments_for` handed it
    no names and it was SKIPPED every weekday: twelve skipped pulls to
    2026-09-17, and the earnings dates and rating changes the slot exists for
    never collected. fmp is the ONLY per-instrument source in the slot, so
    giving it the US names starts no other per-name work."""
    from knowledge.sources import catalog

    book = ("MYX:1155", "XNAS:NVDA", "XNYS:JPM")
    assert catalog.instruments_for("us_preopen", catalog.CATALOG["fmp"], book) == (
        "XNAS:NVDA",
        "XNYS:JPM",
    )
    per_name = [
        s.name for s in catalog.sources_for("us_preopen", catalog.CATALOG) if s.per_instrument
    ]
    assert per_name == ["fmp"], per_name

    corpus_db, facts_db = stores
    fmp = CannedCollector()
    fmp.name = "fmp"
    report = run_sweep(
        Cfg(sources=("fmp",), watchlist=book),
        "us_preopen",
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fmp=fmp),
        clock=lambda: NOW,
        log=lambda m: None,
    )
    assert report.results[0].status == "ok", report.results[0].detail
    ((_, instruments, slot),) = fmp.seen
    assert instruments == ("XNAS:NVDA", "XNYS:JPM") and slot == "us_preopen"


# --- the recorded detail is what the nightly page quotes ------------------------------------


def test_a_structured_sources_detail_is_not_cut_at_400_characters(stores):
    """The nightly page quotes the per-source detail from the sweeps and pulls
    tables. At 400 characters fmp's per-endpoint notes for three names were
    cut mid-URL before they named the second company. The bound is 2000 now;
    the columns are TEXT, so nothing else changed."""
    corpus_db, facts_db = stores
    long_note = "; ".join(f"endpoint {i}: outside the plan for NVDA, AAPL, MSFT" for i in range(30))
    assert 400 < len(long_note) < 2000
    fred = CannedCollector(pull=Pull(notes=[long_note]))
    run_sweep(
        Cfg(sources=("fred",)),
        "us_preopen",
        corpus_path=corpus_db,
        facts_path=facts_db,
        collector_for=collectors_returning(fred=fred),
        clock=lambda: NOW,
        log=lambda m: None,
    )
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
    assert long_note in sweep["detail"]
    with FactBook(facts_db) as book:
        (pull_detail,) = [r[0] for r in book.conn.execute("SELECT detail FROM pulls")]
    assert pull_detail == long_note


def test_a_news_failures_detail_is_bounded_at_2000_not_400(stores):
    corpus_db, facts_db = stores
    reason = "HTTP Error 429: Too Many Requests; " * 120  # ~4,200 characters

    def broken(name, **kw):
        class _Broken(FixtureFeed):
            def _fetch_raw(self, since, limit):
                raise FeedError(reason)

        return _Broken(records=[])

    run_sweep(
        Cfg(sources=("thestar_business",)),
        "bursa_close",
        corpus_path=corpus_db,
        facts_path=facts_db,
        adapter_for=broken,
        clock=lambda: NOW,
        log=lambda m: None,
    )
    with Corpus(corpus_db) as c:
        (sweep,) = c.sweeps()
    assert len(sweep["detail"]) == 2000 and sweep["detail"] == reason[:2000]
