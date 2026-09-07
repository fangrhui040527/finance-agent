"""One run of the collector: every enabled source for a slot, kept and recorded.

`ask.py sweep` used to be GDELT-specific. This is the general form, driven by
the catalogue in knowledge/sources/catalog.py:

  * a **slot** names the moment of day (`bursa_close`, `us_preopen`,
    `us_close`, `weekly`, or `all`) and therefore which sources run and
    which names a per-instrument source is asked for;
  * a **news** source produces Articles into the corpus, with the cleaning,
    language and junk rules applied and the escalation gate evaluated;
  * a **structured** source produces observations, events, series and
    documents into the fact book - and any articles it carries go into the
    corpus by the same path as every other article;
  * every attempt is recorded, including the ones that could not run: a
    missing key is `skipped` with the variable named, a plan boundary is a
    note, a transport failure is `failed`, and a per-instrument source where
    more than half the names could not be read is `degraded` - which exits 3
    like a failure, because eight of nine names unreachable produces almost
    no news, which is the exact shape of a quiet week.

The watermark rule is unchanged: a source resumes from its last SUCCESSFUL
read, so a failed night is re-read tomorrow rather than skipped.
"""

from __future__ import annotations

import sys
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from knowledge.corpus import FAILED, OK, Corpus
from knowledge.facts import SKIPPED, FactBook
from knowledge.feeds.adapter import FeedAdapter, FeedError, IngestStats, RawRecord
from knowledge.news.features import Article
from knowledge.sources import catalog
from knowledge.sources.catalog import MIXED, NEWS, SourceSpec

#: How long a run keeps starting new requests. Past it the sweep stops
#: fetching, keeps what it has and records which names it never reached - a
#: job killed by the runner's cap commits nothing at all.
SWEEP_DEADLINE_SECONDS = 600

DEGRADED = "degraded"


# --- the pieces that used to live in ask.py -------------------------------------------


#: Rotation bucket, in seconds. A minute: two attempts of the same run replay
#: in the same order, any two real collection slots differ.
ROTATION_PERIOD_S = 60


def _rotation_offset(now: datetime) -> int:
    """How far to rotate the name list for a run starting at `now`.

    Derived from the run's start time, NOT from the date. That distinction is
    the whole point and it was wrong until 2026-09-04: the offset was
    `toordinal()`, so every run on a given day queried in the identical order
    while the docstring below claimed each RUN started somewhere new.

    What that cost is measurable in the sweeps table. GDELT rate-limits a run
    progressively - nine sequential requests, quota gone after about five - so
    the names that fail are the ones asked LAST, not particular companies. On
    2026-09-04 the 13:01 run read positions 1-5 and failed 6, 7, 8 and 9. With
    four collection slots a day and a per-DAY offset, all four asked in the same
    order and starved the same tail: Tenaga and Petronas Chemicals failed in all
    three runs that day and looked like a problem with those two companies. They
    were simply last in the queue, every time.

    Hashed rather than used as a raw counter, because a counter ALIASES against
    the number of names. Hourly buckets and nine names put the 12:30 and 21:15
    slots nine hours apart - `% 9` collides - so two of the four slots would
    still have shared an order. A hash has no such structure, so the fix does
    not quietly depend on the schedule avoiding multiples of the list length.
    crc32, not hash(), because hash() is salted per process and the order has to
    be reproducible from the start time: a sweep you cannot replay is a sweep
    you cannot explain.
    """
    bucket = int(now.timestamp()) // ROTATION_PERIOD_S
    return zlib.crc32(str(bucket).encode())


def _rotate(terms, n: int):
    """Start each run at a different name, so a deadline never starves the same tail."""
    if not terms:
        return terms
    n = n % len(terms)
    return tuple(terms[n:]) + tuple(terms[:n])


def _mostly_failed(failed, skipped, counts) -> bool:
    """True when more than half the names could not be read at all.

    Half, rather than any, because GDELT refuses individual names routinely.
    Counts names, not articles: a name read with no news is a fact about the
    world; a name that could not be read is a hole in the record.
    """
    unreachable = len(failed) + len(skipped)
    attempted = unreachable + len(counts)
    return attempted > 0 and unreachable * 2 > attempted


def _sweep_note(failed, skipped, counts=()) -> str:
    parts = []
    empty = [t for t, n in counts if n == 0]
    if empty:
        parts.append("read but empty: " + ", ".join(empty))
    if failed:
        parts.append("failed: " + ", ".join(t for t, _ in failed))
    if skipped:
        parts.append("not reached: " + ", ".join(skipped))
    return "; ".join(parts)


def _fetch_each(make_feed, terms, since, limit, deadline=None, clock=None):
    """One request per name. Returns (records, failed, skipped, counts)."""
    tick = clock or (lambda: datetime.now(UTC))
    per = max(1, limit // max(1, len(terms)))
    records: list = []
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []
    counts: list[tuple[str, int]] = []
    for term in terms:
        if deadline is not None and tick() >= deadline:
            skipped.append(term)
            continue
        try:
            got = make_feed(f'"{term}"').fetch(since, limit=per)
        except FeedError as e:
            failed.append((term, str(e)))
            continue
        counts.append((term, len(got)))
        records.extend(got)
    return records, failed, skipped, counts


# --- articles that arrive already built ------------------------------------------------


class PreparedFeed(FeedAdapter):
    """Runs already-built Articles through the one normalisation path.

    A collector's articles (Finnhub's company news, Alpha Vantage's feed) must
    be cleaned, linked, scored and gated exactly like a feed's, or two kinds
    of article would exist in one corpus.
    """

    name = "prepared"

    def __init__(self, name: str, articles: list[Article], extractor=None) -> None:
        super().__init__(extractor)
        self.name = name
        self._articles = list(articles)

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        now = datetime.now(UTC)
        return [
            RawRecord(self.name, a.doc_id, now, {"article": a})
            for a in self._articles[:limit]
            if a.published_at >= (since if since.tzinfo else since.replace(tzinfo=UTC))
        ]

    def _to_article(self, rec: RawRecord) -> Article | None:
        return rec.payload["article"]


# --- the report ---------------------------------------------------------------------------


@dataclass
class SourceResult:
    name: str
    kind: str
    status: str
    fetched: int = 0
    kept: int = 0
    stored: int = 0
    duplicates: int = 0
    unlinked: int = 0
    filtered: int = 0
    escalated: int = 0
    structured_stored: int = 0
    requests: int = 0
    seconds: float = 0.0
    detail: str = ""

    def line(self) -> str:
        head = f"  {self.name:<20} {self.status:<9}"
        if self.status == SKIPPED:
            return f"{head} {self.detail}"
        if self.status == FAILED:
            return f"{head} {self.detail[:200]}"
        body = (
            f"fetched {self.fetched:<4} kept {self.kept:<4} stored {self.stored:<4} "
            f"dup {self.duplicates:<3} unlinked {self.unlinked:<3} filtered {self.filtered:<3} "
            f"escalated {self.escalated:<3}"
        )
        if self.structured_stored:
            body += f" facts {self.structured_stored:<4}"
        body += f" {self.seconds:5.1f}s"
        if self.detail:
            body += f"\n  {'':<30} {self.detail[:300]}"
        return f"{head} {body}"


@dataclass
class SweepReport:
    run_id: str
    slot: str
    started: datetime
    results: list[SourceResult] = field(default_factory=list)
    corpus_counts: dict = field(default_factory=dict)
    facts_counts: dict = field(default_factory=dict)
    graph: str = ""
    could_not_run: str = ""

    @property
    def exit_code(self) -> int:
        """0 every source read; 3 a source failed or degraded; 2 could not run."""
        if self.could_not_run:
            return 2
        if any(r.status in (FAILED, DEGRADED) for r in self.results):
            return 3
        return 0

    def render(self) -> str:
        lines = [f"sweep {self.run_id}  slot {self.slot}"]
        lines += [r.line() for r in self.results]
        if self.graph:
            lines.append(f"  {'graph':<20} {self.graph}")
        c = self.corpus_counts
        if c:
            lines.append(
                f"  {'corpus':<20} {c.get('articles', 0)} articles, {c.get('linked', 0)} linked, "
                f"{c.get('escalated', 0)} escalated, {c.get('sweeps', 0)} sweeps, "
                f"{c.get('failed_sweeps', 0)} of them failed"
            )
        f = self.facts_counts
        if f:
            lines.append(
                f"  {'facts':<20} {f.get('observations', 0)} observations, {f.get('events', 0)} events, "
                f"{f.get('series_points', 0)} series points over {f.get('series', 0)} series, "
                f"{f.get('documents', 0)} documents"
            )
        return "\n".join(lines)


# --- the run ------------------------------------------------------------------------------


def run_sweep(
    cfg,
    slot: str = "all",
    *,
    sources: tuple[str, ...] | None = None,
    hours: int = 24,
    limit: int = 250,
    corpus_path: str | None = None,
    facts_path: str | None = None,
    graph_db: str = "data/graph.db",
    link_graph: bool = True,
    clock=None,
    adapter_for=None,
    collector_for=None,
    entity_index=None,
    log=None,
) -> SweepReport:
    """Run every enabled source for `slot`. Never raises for a source failure."""
    from knowledge.feeds.registry import adapter_for as _adapter_for
    from knowledge.graph.extractors.gdelt import entity_index as _entity_index
    from knowledge.sources.registry import collector_for as _collector_for

    adapter_for = adapter_for or _adapter_for
    collector_for = collector_for or _collector_for
    tick = clock or (lambda: datetime.now(UTC))
    emit = log or (lambda msg: print(msg, file=sys.stderr))

    started = tick()
    run_id = started.strftime("%Y%m%dT%H%M%S")
    report = SweepReport(run_id=run_id, slot=slot, started=started)

    if slot not in catalog.SLOTS:
        report.could_not_run = f"unknown slot {slot!r}; known: {', '.join(catalog.SLOTS)}"
        return report
    enabled = tuple(sources) if sources else tuple(cfg.sources)
    specs: list[SourceSpec] = []
    if sources:
        # Named on the command line: run each whatever the slot. A name outside
        # the catalogue is allowed here IF a news adapter exists for it (a
        # fixture, a one-off RSS URL registered for a test); otherwise refuse
        # with the registry's own message, which names what is known.
        from knowledge.feeds.registry import UnknownSource

        for n in enabled:
            if n in catalog.CATALOG:
                specs.append(catalog.CATALOG[n])
                continue
            try:
                adapter_for(n)
            except UnknownSource as e:
                report.could_not_run = str(e)
                return report
            specs.append(SourceSpec(n, NEWS, "named on the command line", "general_news", ("all",)))
    else:
        unknown = [n for n in enabled if n not in catalog.CATALOG]
        if unknown:
            report.could_not_run = f"not in the source catalogue: {unknown}"
            return report
        specs = catalog.sources_for(slot, enabled)
    if not specs:
        report.could_not_run = (
            f"no enabled source runs in slot {slot!r}. Enabled: {list(enabled) or 'none'}; "
            f"the register is knowledge/sources/catalog.py."
        )
        return report

    index = entity_index if entity_index is not None else _entity_index()
    holdings, watchlist = set(cfg.holdings), set(cfg.watchlist)
    # Read-only names ride along for the structured sources whose market they
    # are on; the news slots never include their market, so they cost nothing
    # there, and the escalation gate still keys on holdings and watchlist.
    book = tuple(
        dict.fromkeys(
            tuple(cfg.watchlist) + tuple(cfg.holdings) + tuple(getattr(cfg, "read_only", ()))
        )
    )
    languages = tuple(getattr(cfg, "languages", ()) or ())
    deadline = started + timedelta(seconds=SWEEP_DEADLINE_SECONDS)
    fresh: list[Article] = []

    with (
        Corpus(corpus_path or cfg.corpus_db) as corpus,
        FactBook(facts_path or getattr(cfg, "facts_db", "data/facts.db")) as facts,
    ):
        for spec in specs:
            t0 = tick()
            if spec.kind == NEWS:
                result, articles = _run_news(
                    spec,
                    cfg,
                    corpus,
                    index,
                    holdings,
                    watchlist,
                    languages,
                    book,
                    slot,
                    hours,
                    limit,
                    deadline,
                    tick,
                    adapter_for,
                    run_id,
                    emit,
                )
            else:
                result, articles = _run_structured(
                    spec,
                    cfg,
                    corpus,
                    facts,
                    index,
                    holdings,
                    watchlist,
                    languages,
                    book,
                    slot,
                    hours,
                    limit,
                    tick,
                    collector_for,
                    run_id,
                    emit,
                )
            result.seconds = (tick() - t0).total_seconds()
            report.results.append(result)
            fresh.extend(articles)
            emit(result.line())

        if fresh and link_graph:
            try:
                report.graph = _link_graph(fresh, graph_db)
            except Exception as e:  # the graph is downstream; a bad edge must not lose the sweep
                report.graph = f"not linked: {type(e).__name__}: {e}"
        report.corpus_counts = corpus.counts()
        report.facts_counts = facts.counts()
    return report


def _since_for(store_last, name: str, started: datetime, hours: int) -> datetime:
    last = store_last(name)
    return last if last is not None else started - timedelta(hours=hours)


def _run_news(
    spec: SourceSpec,
    cfg,
    corpus,
    index,
    holdings,
    watchlist,
    languages,
    book,
    slot,
    hours,
    limit,
    deadline,
    tick,
    adapter_for,
    run_id,
    emit,
) -> tuple[SourceResult, list[Article]]:
    since = _since_for(corpus.last_success, spec.name, tick(), hours)
    result = SourceResult(spec.name, spec.kind, OK)
    try:
        if spec.per_instrument:
            instruments = catalog.instruments_for(slot, spec, book)
            if not instruments:
                result.status = SKIPPED
                result.detail = f"no name in the book trades in slot {slot!r}"
                corpus.record_sweep(run_id, spec.name, since, OK, slot=slot, detail=result.detail)
                return result, []
            records, articles, notes, degraded = _news_per_instrument(
                spec,
                cfg,
                instruments,
                since,
                limit,
                deadline,
                tick,
                adapter_for,
                index,
                holdings,
                watchlist,
                languages,
                emit,
            )
            stats = _merge_stats(articles, records)
            result.detail = notes
            if degraded:
                result.status = DEGRADED
        else:
            feed = adapter_for(spec.name)
            records = feed.fetch(since, limit=limit)
            articles, stats = feed.normalize(
                records,
                entity_index=index,
                holdings=holdings,
                watchlist=watchlist,
                languages=languages,
            )
    except FeedError as e:
        corpus.record_sweep(run_id, spec.name, since, FAILED, slot=slot, detail=str(e)[:400])
        result.status = FAILED
        result.detail = str(e)
        return result, []

    stored = corpus.add_all(articles, spec.name)
    result.fetched, result.kept = stats.fetched, stats.kept
    result.duplicates, result.unlinked = stored.duplicates, stats.unlinked
    result.filtered, result.escalated, result.stored = (
        stats.filtered,
        stats.escalated,
        stored.stored,
    )
    corpus.record_sweep(
        run_id,
        spec.name,
        since,
        OK,
        slot=slot,
        fetched=result.fetched,
        kept=result.kept,
        stored=stored.stored,
        duplicates=stored.duplicates,
        unlinked=result.unlinked,
        escalated=result.escalated,
        detail=result.detail,
    )
    return result, articles


def _news_per_instrument(
    spec,
    cfg,
    instruments,
    since,
    limit,
    deadline,
    tick,
    adapter_for,
    index,
    holdings,
    watchlist,
    languages,
    emit,
):
    """One request per name; every name's records normalised by its own feed."""
    from knowledge.feeds.company_feeds import EDITION_FOR_MIC, finance_query
    from knowledge.graph.extractors.gdelt import watchlist_terms
    from knowledge.graph.ids import display_names
    from knowledge.graph.ids import instrument_id as canonical
    from markets.registry import mic_of

    names = display_names()
    rotated = _rotate(tuple(instruments), _rotation_offset(tick()))
    per = max(1, limit // max(1, len(rotated)))
    articles: list[Article] = []
    records_total = 0
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []
    counts: list[tuple[str, int]] = []
    linked_counts: list[tuple[str, int, int]] = []

    for iid in rotated:
        label: str = str(names.get(canonical(iid) or iid) or iid)
        if tick() >= deadline:
            skipped.append(label)
            continue
        try:
            feed = _feed_for(
                spec,
                cfg,
                iid,
                label,
                adapter_for,
                EDITION_FOR_MIC,
                finance_query,
                watchlist_terms,
                mic_of,
            )
        except ValueError as e:  # no edition, no symbol: this name cannot be asked for here
            failed.append((label, str(e)))
            continue
        if feed is None:
            counts.append((label, 0))
            continue
        try:
            records = feed.fetch(since, limit=per)
        except FeedError as e:
            failed.append((label, str(e)))
            continue
        arts, _ = feed.normalize(
            records,
            entity_index=index,
            holdings=holdings,
            watchlist=watchlist,
            languages=languages,
        )
        if spec.name in ("yahoo_rss",):
            for a in arts:  # keyed by ticker: attributed even when the headline omits the name
                if iid not in a.instruments:
                    a.instruments.insert(0, iid)
        # PROVENANCE, NOT ATTRIBUTION. Every per-name source is asked for one
        # company, so record which; only a source keyed by TICKER (above) may
        # also assert it. GDELT is asked a PHRASE and answers from a full-text
        # index this corpus never sees, and 457 of the 575 GDELT articles
        # collected to 2026-09-06 named no book company at all - attributing
        # those to the name that fetched them would put a brothel sale in
        # Apple's evidence. Recorded instead, so the share is measurable
        # (`ask.py sources --coverage`) rather than guessed at.
        for a in arts:
            a.fetched_for = iid
        linked = sum(1 for a in arts if iid in a.instruments)
        counts.append((label, len(records)))
        linked_counts.append((label, linked, len(arts)))
        records_total += len(records)
        articles.extend(arts)

    if not articles and not counts and (failed or skipped):
        first = failed[0][1] if failed else "deadline reached"
        raise FeedError(
            f"no name could be read ({len(failed)} failed, {len(skipped)} not reached). First: {first}"
        )
    degraded = _mostly_failed(failed, skipped, counts)
    if degraded:
        n = len(failed) + len(skipped)
        emit(f"  {'':<20} DEGRADED: {n} of {n + len(counts)} names could not be read")
    note = _sweep_note(failed, skipped, counts)
    hit = _linked_note(linked_counts)
    return records_total, articles, "; ".join(x for x in (note, hit) if x), degraded


def _linked_note(linked_counts: list[tuple[str, int, int]]) -> str:
    """`named the company: 12 of 96` - the source's precision on this run.

    A per-name source that returns a hundred articles and mentions the company
    in four is not covering that company, and the article count alone cannot
    say so. Recorded in the sweeps table so the trend survives the run.
    """
    kept = sum(total for _, _, total in linked_counts)
    if not kept:
        return ""
    named = sum(n for _, n, _ in linked_counts)
    worst = sorted((n / t if t else 1.0, label, n, t) for label, n, t in linked_counts if t)[:3]
    detail = ", ".join(f"{label} {n}/{t}" for _, label, n, t in worst)
    return f"named the company: {named} of {kept}" + (f" (lowest: {detail})" if detail else "")


def _feed_for(
    spec, cfg, iid, label, adapter_for, edition_for_mic, finance_query, watchlist_terms, mic_of
):
    """The per-name adapter for a per-instrument news source, or None to skip."""
    if spec.name == "gdelt":
        terms = watchlist_terms((iid,))
        if not terms:
            return None  # every alias is too short for the DOC API; linked when found elsewhere
        return adapter_for(
            "gdelt",
            query=f'"{terms[0]}"',
            languages=tuple(cfg.gdelt_languages),
            countries=tuple(cfg.gdelt_countries),
        )
    if spec.name == "google_news":
        mic = mic_of(iid)
        edition = edition_for_mic.get(mic)
        if edition is None:
            raise ValueError(f"no Google News edition for market {mic}")
        return adapter_for("google_news", query=finance_query(label), edition=edition)
    if spec.name == "yahoo_rss":
        from core.market.feed import SymbolUnmappable

        try:
            return adapter_for("yahoo_rss", instrument_id=iid)
        except SymbolUnmappable as e:
            raise ValueError(str(e)) from e
    return adapter_for(spec.name)


def _merge_stats(articles: list[Article], records_total: int) -> IngestStats:
    """One IngestStats over every name's normalised articles."""
    return IngestStats(
        fetched=records_total,
        kept=len(articles),
        unlinked=sum(1 for a in articles if not a.instruments),
        filtered=0,
        escalated=sum(1 for a in articles if a.escalated),
    )


def _run_structured(
    spec: SourceSpec,
    cfg,
    corpus,
    facts,
    index,
    holdings,
    watchlist,
    languages,
    book,
    slot,
    hours,
    limit,
    tick,
    collector_for,
    run_id,
    emit,
) -> tuple[SourceResult, list[Article]]:
    from knowledge.sources.base import KeyMissing, PlanExcluded, SourceError

    since = _since_for(facts.last_success, spec.name, tick(), hours)
    result = SourceResult(spec.name, spec.kind, OK)
    instruments = catalog.instruments_for(slot, spec, book) if spec.per_instrument else ()
    if spec.per_instrument and not instruments and spec.kind != MIXED:
        result.status = SKIPPED
        result.detail = f"no name in the book trades in slot {slot!r}"
        facts.record_pull(run_id, spec.name, SKIPPED, detail=result.detail)
        return result, []
    if spec.kind == MIXED and spec.markets and not instruments:
        instruments = tuple(i for i in book if _mic_in(i, spec.markets))
        if not instruments:
            result.status = SKIPPED
            result.detail = "no name in the book on a market this source covers"
            facts.record_pull(run_id, spec.name, SKIPPED, detail=result.detail)
            return result, []

    try:
        collector = collector_for(spec.name)
        pull = collector.collect(since, instruments, slot)
    except KeyMissing as e:
        result.status = SKIPPED
        result.detail = str(e)
        facts.record_pull(run_id, spec.name, SKIPPED, detail=result.detail)
        corpus.record_sweep(
            run_id, spec.name, since, OK, slot=slot, detail=f"skipped: {result.detail[:300]}"
        )
        return result, []
    except PlanExcluded as e:
        result.status = SKIPPED
        result.detail = str(e)
        facts.record_pull(run_id, spec.name, SKIPPED, detail=result.detail)
        return result, []
    except SourceError as e:
        result.status = FAILED
        result.detail = str(e)
        facts.record_pull(run_id, spec.name, FAILED, detail=result.detail)
        corpus.record_sweep(run_id, spec.name, since, FAILED, slot=slot, detail=result.detail[:400])
        return result, []

    stored = 0
    stored += facts.add_observations(pull.observations).stored
    stored += facts.add_events(pull.events).stored
    stored += facts.add_series(pull.series).stored
    stored += facts.add_documents(pull.documents).stored
    result.structured_stored = stored
    result.requests = pull.requests

    articles: list[Article] = []
    if pull.articles:
        feed = PreparedFeed(spec.name, pull.articles)
        articles, stats = feed.normalize(
            feed.fetch(since, limit=max(limit, len(pull.articles))),
            entity_index=index,
            holdings=holdings,
            watchlist=watchlist,
            languages=languages,
        )
        kept = corpus.add_all(articles, spec.name)
        result.fetched, result.kept, result.stored = stats.fetched, stats.kept, kept.stored
        result.duplicates, result.unlinked = kept.duplicates, stats.unlinked
        result.filtered, result.escalated = stats.filtered, stats.escalated
    else:
        result.fetched = pull.fetched
    result.detail = "; ".join(pull.notes)[:400]
    facts.record_pull(
        run_id,
        spec.name,
        OK,
        fetched=pull.fetched,
        stored=stored + result.stored,
        detail=result.detail,
    )
    corpus.record_sweep(
        run_id,
        spec.name,
        since,
        OK,
        slot=slot,
        fetched=result.fetched,
        kept=result.kept,
        stored=result.stored,
        duplicates=result.duplicates,
        unlinked=result.unlinked,
        escalated=result.escalated,
        detail=(f"facts {stored}; " + result.detail)[:400],
    )
    return result, articles


def _mic_in(iid: str, markets) -> bool:
    from markets.registry import mic_of

    try:
        return mic_of(iid) in markets
    except ValueError:
        return False


def _link_graph(articles, graph_db: str) -> str:
    """Attach this sweep's articles to the companies they name. Never pruned."""
    from knowledge.graph.build import build
    from knowledge.graph.extractors.gdelt import GdeltExtractor
    from knowledge.graph.store import GraphStore

    with GraphStore(graph_db) as store:
        before = store.counts()
        build(store, extractors=[GdeltExtractor(articles)])
        after = store.counts()
    return (
        f"+{after['nodes'] - before['nodes']} nodes  "
        f"+{after['edges'] - before['edges']} edges  "
        f"(INFERRED - traversable, never citable)"
    )


# --- the probe --------------------------------------------------------------------------------


@dataclass
class ProbeResult:
    name: str
    status: str
    detail: str
    seconds: float = 0.0

    def line(self) -> str:
        return f"  {self.name:<22} {self.status:<8} {self.seconds:5.1f}s  {self.detail[:220]}"


def probe(
    cfg, names=None, hours: int = 48, limit: int = 3, clock=None, log=None
) -> list[ProbeResult]:
    """Fetch each source once, store nothing, say what came back.

    The live verification tool: this repository's development environment has
    no egress to any data host, so the first real answer from a source comes
    from a GitHub Actions runner running this. Enable a source only after its
    probe shows dated items.
    """
    from knowledge.feeds.company_feeds import finance_query
    from knowledge.feeds.registry import adapter_for
    from knowledge.sources.base import KeyMissing, PlanExcluded, SourceError
    from knowledge.sources.registry import collector_for

    tick = clock or (lambda: datetime.now(UTC))
    emit = log or (lambda msg: print(msg, file=sys.stderr))
    since = tick() - timedelta(hours=hours)
    book = tuple(
        dict.fromkeys(
            tuple(cfg.watchlist) + tuple(cfg.holdings) + tuple(getattr(cfg, "read_only", ()))
        )
    )
    us = tuple(i for i in book if _mic_in(i, ("XNAS", "XNYS")))
    my = tuple(i for i in book if _mic_in(i, ("XKLS",)))
    # A Taiwan source with no read-only name configured is still probed against
    # TSMC, so the probe says whether the endpoint answers at all.
    tw = tuple(i for i in book if _mic_in(i, ("XTAI",))) or ("XTAI:2330",)
    out: list[ProbeResult] = []
    for name in names or list(catalog.CATALOG):
        spec = catalog.CATALOG.get(name)
        t0 = tick()
        if spec is None:
            out.append(ProbeResult(name, "unknown", "not in the catalogue"))
            continue
        try:
            if spec.kind == NEWS:
                if name == "gdelt":
                    feed = adapter_for("gdelt", query='"Maybank"')
                elif name == "google_news":
                    feed = adapter_for("google_news", query=finance_query("Maybank"), edition="MY")
                elif name == "yahoo_rss":
                    feed = adapter_for("yahoo_rss", instrument_id=(my or us or ("XNAS:AAPL",))[0])
                else:
                    feed = adapter_for(name)
                records = feed.fetch(since, limit=limit)
                arts, stats = feed.normalize(records)
                sample = "; ".join(f"{a.published_at:%m-%d} {a.title[:60]}" for a in arts[:2])
                detail = f"{stats}. {sample}" if arts else f"{stats}. (no items in {hours}h)"
                out.append(ProbeResult(name, "ok", detail))
            else:
                collector = collector_for(name)
                if "XTAI" in spec.markets:
                    instruments = tw
                else:
                    instruments = (
                        us if "XNAS" in spec.markets else my if "XKLS" in spec.markets else ()
                    )
                pull = collector.collect(since, instruments[:2], "all")
                detail = f"{pull} in {pull.requests} request(s)"
                if pull.notes:
                    detail += " | notes: " + " / ".join(n[:80] for n in pull.notes[:3])
                out.append(ProbeResult(name, "ok", detail))
        except KeyMissing as e:
            out.append(ProbeResult(name, "no-key", str(e)))
        except PlanExcluded as e:
            out.append(ProbeResult(name, "plan", str(e)))
        except (FeedError, SourceError) as e:
            out.append(ProbeResult(name, "failed", str(e)))
        except Exception as e:  # a probe must report every source, whatever one of them does
            out.append(ProbeResult(name, "error", f"{type(e).__name__}: {e}"))
        out[-1].seconds = (tick() - t0).total_seconds()
        emit(out[-1].line())
    return out
