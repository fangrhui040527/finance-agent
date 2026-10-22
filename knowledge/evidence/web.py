"""Web evidence for moves the stored news cannot explain.

The feeds deliver what they deliver. On 2026-10-16 Press Metal fell 9.34% - an
idiosyncratic move of 5.43 sigma - and the corpus held one projection about
it; the reasons RHB gave (falling aluminium, carbon anode costs, US inflation)
were on the open web and nowhere in the store, so the nightly page could only
say "no collected evidence reaches it". This goes and looks, for exactly the
moves where that matters, and keeps what it finds as evidence like any other.

WHICH MOVES. A book name's session qualifies when the decomposition says a
company-specific cause is warranted (`no_identified_catalyst`: at least 20% of
the move is the name's own) AND that abnormal return is significant at 5%
(|SAR| > 1.96, PARAMETRIC_Z) AND the row is a clean measurement - not
mis-dated, stale, blank, provisional or a gap. Over 2026-04-22..10-22 that
rule fired 76 times in 1,125 name-sessions, about 13 a month. A US name whose
session already has an earnings result or a rating change in the fact book
is left alone: the store holds a structured candidate for it.

WHEN. A session is searched once its close is SETTLE_HOURS old - the notes
explaining a move arrive after it (on 10-16 the broker notes landed 1h47m and
2h50m after Bursa shut) - and while it is among the last LOOKBACK_SESSIONS, so
a cron GitHub dropped is still covered by the next run. In practice a Bursa
session is searched at that day's us_close slot and a US one at the next
bursa_close, both before the 22:30Z routine writes the page.

WHAT IS KEPT. One corpus row per page the model CITED, source `web_search`,
doc_id `web_search:<url>`: the page's title, the passages it cited, its own
date (`page_age`; a page with no readable date is not stored - never dated by
when it was found), `first_seen_at` = when it was retrieved, `query` = what was
asked, `fetched_for` = the name. Each row is scanned for prompt injection
before it is stored - the corpus is append-only, so a poisoned row could never
be taken out - and then goes through the one normalisation every source does.
The model's own summary is not evidence and is not stored as an article; it
goes on the attempt's record, scanned, as a pointer to the rows.

WHAT IT COSTS. Every attempt is a row in `evidence_searches` in the corpus,
cost included - the collector commits the corpus, so that table is the
month's spend record, and the record of what was already searched (a re-run
never pays twice for one move). Before each search the worst it could cost is
reserved against two caps from config.toml [budget]: the evidence sub-cap and
the USD 50 monthly total. Either would be crossed -> no search, said so.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from engines.attribution.decompose import PARAMETRIC_Z, Verdict

#: Anthropic's default model; Claude Sonnet 5.5 (`claude-sonnet-5-5`) costs
#: about half. Measured estimate per search on this one: ~USD 0.29 (3 searches,
#: ~44k input tokens re-read across the turn, ~4k output).
MODEL = "claude-opus-5-5"
#: Opus 5.5's own default. `low` makes a search-capable model more likely to
#: answer without searching, which is the one thing this is for.
EFFORT = "medium"
MAX_USES = 3
MAX_TOKENS = 8000

TRIGGER_SAR = PARAMETRIC_Z
SETTLE_HOURS = 6
LOOKBACK_SESSIONS = 3
#: The most names searched for one market's session, by |SAR|: 3 was the most
#: that cleared 1.96 on any one day in the six months measured.
PER_MARKET_SESSION = 3
#: And per run, with a deadline, inside the collector's 25-minute job.
PER_RUN = 4
DEADLINE_SECONDS = 240

#: Reserved before each search: every input token at the cache-write rate (the
#: dearest way input is billed), the full output cap twice and every search a
#: paused turn could run. Far above a real call (~USD 0.29), on purpose.
RESERVE_INPUT_TOKENS = 150_000

#: A structured candidate the fact book already holds for the session.
STRUCTURED_CAUSES = ("earnings_result", "rating_change")

SOURCE = "web_search"
AGENT = "web_evidence"

#: Outcomes that mean the move was searched (and paid for, or declined): it is
#: not searched again. `budget` and `error` are not here: nothing was billed,
#: and the next run may try.
SEARCHED = ("stored", "nothing_found", "no_new_evidence", "refused", "truncated")

EXCHANGES = {
    "XKLS": ("Bursa Malaysia", {"country": "MY", "timezone": "Asia/Kuala_Lumpur"}),
    "XNAS": ("Nasdaq", {"country": "US", "timezone": "America/New_York"}),
    "XNYS": ("the New York Stock Exchange", {"country": "US", "timezone": "America/New_York"}),
}

SYSTEM = (
    "You research why a listed company's share price moved on one trading session. "
    "Search the web for reporting that explains that session's move: company announcements "
    "and exchange filings, results, broker notes and rating changes, deals, regulatory and "
    "court news, and market reports that name the stock. Prefer sources published before or "
    "during the session, and say plainly when a source appeared only after the close. Cite "
    "every claim with the search results it rests on. If nothing you find explains the move, "
    "say so. Do not speculate beyond the sources, and do not recommend any trade."
)


@dataclass(frozen=True)
class Trigger:
    instrument_id: str
    label: str
    mic: str
    session: date
    close_utc: datetime
    prev_close_utc: datetime | None
    r1: float
    m1: float
    sar: float
    idio: float

    @property
    def key(self) -> str:
        return f"{self.instrument_id}|{self.session.isoformat()}"


@dataclass
class Attempt:
    key: str
    outcome: str
    stored: int = 0
    cost_usd: Decimal = Decimal(0)
    detail: str = ""

    def line(self) -> str:
        cost = f"USD {self.cost_usd:.4f}" if self.cost_usd else "nothing billed"
        return f"  {self.key}: {self.outcome}, {self.stored} stored, {cost}. {self.detail}"


@dataclass
class Report:
    at: datetime
    triggers: list[Trigger] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    month_usd: Decimal = Decimal(0)
    evidence_month_usd: Decimal = Decimal(0)

    def text(self) -> str:
        out = [
            f"web evidence {self.at:%Y-%m-%d %H:%M} UTC: {len(self.triggers)} move(s) qualify; "
            f"month to date USD {self.month_usd:.2f} (evidence USD {self.evidence_month_usd:.2f})"
        ]
        out += [a.line() for a in self.attempts]
        out += [f"  - {n}" for n in self.notes]
        return "\n".join(out)


# -- which moves --------------------------------------------------------------


def _closed_sessions(mic: str, now: datetime) -> list[tuple[date, datetime, datetime | None]]:
    """The market's last LOOKBACK_SESSIONS sessions closed at least
    SETTLE_HOURS ago, newest first, each with its close and the one before's."""
    from markets.registry import get as adapter_for

    calendar = adapter_for(mic).calendar
    settled = now - timedelta(hours=SETTLE_HOURS)
    found: list[tuple[date, datetime]] = []
    day = now.date()
    for _ in range(31):
        session = calendar.session(day)
        if session is not None and session.close_utc() <= settled:
            found.append((day, session.close_utc()))
            if len(found) > LOOKBACK_SESSIONS:
                break
        day -= timedelta(days=1)
    out = []
    for i, (d, close) in enumerate(found[:LOOKBACK_SESSIONS]):
        prev = found[i + 1][1] if i + 1 < len(found) else None
        out.append((d, close, prev))
    return out


def _structured_cause(book, instrument_id: str, since: datetime | None, until: datetime) -> str:
    if book is None or since is None:
        return ""
    for e in book.events(instrument_id, since=since, until=until, limit=50):
        kind = getattr(e, "kind", "")
        if kind in STRUCTURED_CAUSES:
            return kind
    return ""


def candidates(cfg, now: datetime, feed, facts=None) -> tuple[list[Trigger], list[str]]:
    """The moves that qualify, best first, within the per-session cap, and a
    note for every one that did not and why (beyond 'nothing moved')."""
    from knowledge.graph.ids import display_names
    from knowledge.graph.ids import instrument_id as canonical
    from knowledge.pack import measure
    from markets.registry import mic_of

    names = display_names()
    base = getattr(cfg, "base_currency", "MYR")
    book = tuple(dict.fromkeys(tuple(cfg.watchlist) + tuple(cfg.holdings)))
    found: list[Trigger] = []
    notes: list[str] = []
    for iid in book:
        try:
            mic = mic_of(iid)
        except ValueError:
            continue
        label = names.get(canonical(iid) or iid) or iid
        for day, close, prev in _closed_sessions(mic, now):
            move = measure(feed, iid, label, day, base)
            if move.error or move.last_day != day:
                continue
            if move.mis_dated or move.stale or move.blank or move.provisional:
                continue
            if move.verdict != Verdict.NO_IDENTIFIED_CATALYST.value or move.sar is None:
                continue
            if abs(move.sar) <= TRIGGER_SAR:
                continue
            cause = _structured_cause(facts, iid, prev, close)
            if cause:
                notes.append(
                    f"{iid} {day} ({move.sar:+.2f} sigma) not searched: the fact book holds "
                    f"a {cause} for the session"
                )
                continue
            found.append(
                Trigger(
                    iid,
                    label,
                    mic,
                    day,
                    close,
                    prev,
                    float(move.r1 or 0.0),
                    float(move.m1 or 0.0),
                    float(move.sar),
                    float(move.components.get("idiosyncratic", 0.0)),
                )
            )
    found.sort(key=lambda t: -abs(t.sar))
    kept: list[Trigger] = []
    per: dict[tuple[str, date], int] = {}
    for t in found:
        n = per.get((t.mic, t.session), 0)
        if n >= PER_MARKET_SESSION:
            notes.append(
                f"{t.key} ({t.sar:+.2f} sigma) not searched: {PER_MARKET_SESSION} larger moves "
                f"on {t.mic} that session came first"
            )
            continue
        per[(t.mic, t.session)] = n + 1
        kept.append(t)
    return kept, notes


# -- what a search found ------------------------------------------------------

_RELATIVE = re.compile(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", re.IGNORECASE)
_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%b %d %Y")


def page_date(page_age: str, retrieved: datetime) -> datetime | None:
    """A page's own date from the search's `page_age`, or None.

    `page_age` is free text ("October 19, 2026", "3 days ago"). A date that
    cannot be read - or one after the page was found - is None, and the page is
    not stored: dating it by when it was found would put an old story on the
    day it was searched for, which is look-ahead in the other direction."""
    raw = (page_age or "").strip()
    if not raw:
        return None
    m = _RELATIVE.search(raw)
    when: datetime | None = None
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        when = retrieved - timedelta(**{f"{unit}s": n})
    elif raw.lower() == "yesterday":
        when = retrieved - timedelta(days=1)
    else:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            when = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            for fmt in _FORMATS:
                try:
                    when = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
    if when is None or when > retrieved + timedelta(hours=1):
        return None
    return when.astimezone(UTC)


def _clean_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def _articles(answer, trigger: Trigger, retrieved: datetime):
    """One Article per cited page; and how many were left out for no date."""
    from knowledge.news.features import Article

    query = " | ".join(answer.queries)[:1000]
    out, undated = [], 0
    for src in answer.sources:
        published = page_date(src.page_age, retrieved)
        if published is None:
            undated += 1
            continue
        url = _clean_url(src.url)
        body = " … ".join(src.cited)
        if src.page_age:
            body = f"{body} (page dated {src.page_age})" if body else f"(page dated {src.page_age})"
        out.append(
            Article(
                doc_id=f"{SOURCE}:{url}",
                title=(src.title or url).strip(),
                body=body,
                source_domain=urlsplit(url).netloc,
                published_at=published,
                instruments=[trigger.instrument_id],
                query=query,
            )
        )
    return out, undated


def _screen(engine, text: str) -> str | None:
    """None when the injection scan refuses the text; the reason otherwise ''."""
    from core.guardrails.policy import Action, Decision, Rail

    got = engine.evaluate(
        Action(
            name="web_evidence_line",
            rail=Rail.RETRIEVAL,
            agent=AGENT,
            payload={"corpus": SOURCE, "text": text},
        )
    )
    return got.reason if got.decision is Decision.DENY else None


# -- the run ------------------------------------------------------------------


def searched_keys(corpus) -> set[str]:
    if not corpus.has_table("evidence_searches"):
        return set()
    rows = corpus.conn.execute("SELECT key, outcome, searches FROM evidence_searches").fetchall()
    return {r[0] for r in rows if r[1] in SEARCHED or int(r[2] or 0) > 0}


def evidence_month_usd(corpus, now: datetime) -> Decimal:
    from core.llm.spend import month_start

    if not corpus.has_table("evidence_searches"):
        return Decimal(0)
    rows = corpus.conn.execute(
        "SELECT cost_usd FROM evidence_searches WHERE at >= ?", (month_start(now).isoformat(),)
    ).fetchall()
    return sum((Decimal(r[0]) for r in rows), Decimal(0))


def _record(corpus, t: Trigger, at: datetime, outcome: str, **kw) -> None:
    corpus.conn.execute(
        """INSERT INTO evidence_searches
           (key, instrument, session, at, model, sar, r1, idio, queries, searches,
            input_tokens, output_tokens, cost_usd, outcome, stored, detail)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            t.key,
            t.instrument_id,
            t.session.isoformat(),
            at.isoformat(),
            kw.get("model", ""),
            t.sar,
            t.r1,
            t.idio,
            json.dumps(kw.get("queries", [])),
            int(kw.get("searches", 0)),
            int(kw.get("input_tokens", 0)),
            int(kw.get("output_tokens", 0)),
            str(kw.get("cost_usd", Decimal(0))),
            outcome,
            int(kw.get("stored", 0)),
            str(kw.get("detail", ""))[:2000],
        ),
    )
    corpus.conn.commit()


def _prompt(t: Trigger, now: datetime) -> str:
    exchange = EXCHANGES.get(t.mic, (t.mic, {}))[0]
    ticker = t.instrument_id.partition(":")[2]
    window = (
        f"from {t.prev_close_utc:%Y-%m-%d %H:%M} UTC (the previous close) to shortly after "
        if t.prev_close_utc
        else "up to shortly after "
    )
    return (
        f"Today is {now:%Y-%m-%d}, {now:%H:%M} UTC.\n\n"
        f"{t.label} (ticker {ticker} on {exchange}) moved {t.r1:+.2%} in the session of "
        f"{t.session:%A %Y-%m-%d}, while its market moved {t.m1:+.2%}. The company-specific "
        f"part of the move was {t.idio:+.2%}, {abs(t.sar):.2f} standard deviations from normal, "
        f"which is significant. The session closed at {t.close_utc:%Y-%m-%d %H:%M} UTC.\n\n"
        f"Find what explains this move: news, filings, results, broker notes or market reports "
        f"about {t.label} published {window}the close. For each source, give its publication "
        "time when the page shows one, and say whether it came before or after the close."
    )


def run(
    cfg,
    now: datetime | None = None,
    *,
    corpus_path: str | None = None,
    facts_path: str | None = None,
    feed=None,
    searcher=None,
    engine=None,
    dry_run: bool = False,
    log=None,
    clock=None,
) -> Report:
    """Find the qualifying moves, search the ones not yet searched, store what
    is cited. Never raises for one search; a failed search is a recorded row."""
    from core.guardrails.defaults import default_engine
    from core.guardrails.policy import Action, Rail
    from core.llm.tiers import Tier, cost_usd, worst_case_usd
    from core.llm.websearch import CONTINUATIONS, WebSearch
    from knowledge.corpus import Corpus
    from knowledge.facts import FactBook
    from knowledge.graph.extractors.gdelt import entity_index
    from knowledge.sweep import PreparedFeed

    now = (now or datetime.now(UTC)).astimezone(UTC)
    emit = log or (lambda msg: None)
    report = Report(now)
    if feed is None:
        from core.market.feed import default_feed

        feed = default_feed()
    # The grant is here, in code, for this one agent and this one tool: the
    # TOOL rail denies an agent with no registered toolset, and this one is
    # not an evidence agent answering a question - it is the collector.
    engine = engine or default_engine({AGENT: {"web_search"}})
    monthly_cap = Decimal(str(getattr(cfg, "monthly_budget_usd", None) or 50))
    evidence_cap = Decimal(str(getattr(cfg, "evidence_budget_usd", None) or 10))
    reserve = worst_case_usd(
        MODEL,
        RESERVE_INPUT_TOKENS,
        MAX_TOKENS * (1 + CONTINUATIONS),
        searches=MAX_USES * (1 + CONTINUATIONS),
    )
    corpus_path = corpus_path or cfg.corpus_db
    with (
        Corpus(corpus_path) as corpus,
        FactBook(facts_path or getattr(cfg, "facts_db", "data/facts.db")) as facts,
    ):
        triggers, notes = candidates(cfg, now, feed, facts)
        report.notes += notes
        done = searched_keys(corpus)
        report.triggers = [t for t in triggers if t.key not in done]
        for t in triggers:
            if t.key in done:
                report.notes.append(f"{t.key} ({t.sar:+.2f} sigma) already searched")
        if len(report.triggers) > PER_RUN:
            for t in report.triggers[PER_RUN:]:
                report.notes.append(
                    f"{t.key} ({t.sar:+.2f} sigma) left for the next run ({PER_RUN} per run)"
                )
            report.triggers = report.triggers[:PER_RUN]
        from core.llm.spend import committed_usd_since, month_start

        report.evidence_month_usd = evidence_month_usd(corpus, now)
        report.month_usd = committed_usd_since(corpus_path, month_start(now))
        if dry_run or not report.triggers:
            return report
        import os

        if searcher is None and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            report.notes.append(
                f"no ANTHROPIC_API_KEY in this environment: {len(report.triggers)} move(s) "
                "qualify and were not searched (nothing recorded, nothing billed)"
            )
            return report

        corpus.ensure_evidence_table()
        index = entity_index()
        holdings, watchlist = set(cfg.holdings), set(cfg.watchlist)
        languages = tuple(getattr(cfg, "languages", ()) or ())
        started = time.monotonic()
        for t in report.triggers:
            if time.monotonic() - started > DEADLINE_SECONDS:
                report.notes.append(f"{t.key} left for the next run: the run's deadline passed")
                continue
            evidence_spent = evidence_month_usd(corpus, now)
            month_spent = committed_usd_since(corpus_path, month_start(now))
            if evidence_spent + reserve > evidence_cap or month_spent + reserve > monthly_cap:
                detail = (
                    f"not searched: month to date USD {month_spent:.2f} of {monthly_cap} "
                    f"(evidence USD {evidence_spent:.2f} of {evidence_cap}); one search may "
                    f"cost up to USD {reserve:.2f}, reserved before it is made"
                )
                _record(corpus, t, now, "budget", detail=detail)
                report.attempts.append(Attempt(t.key, "budget", detail=detail))
                report.notes.append("the month's budget stops the run here")
                break
            engine.enforce(
                Action(
                    name="web_search",
                    rail=Rail.TOOL,
                    agent=AGENT,
                    payload={"instrument": t.instrument_id, "session": t.session.isoformat()},
                )
            )
            location = EXCHANGES.get(t.mic, ("", None))[1]
            seam = searcher or WebSearch(
                MODEL,
                max_uses=MAX_USES,
                effort=EFFORT,
                max_tokens=MAX_TOKENS,
                user_location=location,
            )
            emit(f"searching {t.key} ({t.sar:+.2f} sigma)")
            try:
                answer = seam.search(SYSTEM, _prompt(t, now))
            except Exception as e:  # noqa: BLE001 - one search must not cost the others
                from knowledge.redact import scrub  # the key never reaches a record

                detail = f"{type(e).__name__}: {scrub(str(e))[:400]}"
                _record(corpus, t, now, "error", model=MODEL, detail=detail)
                report.attempts.append(Attempt(t.key, "error", detail=detail))
                if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                    report.notes.append("the API key was refused; no further searches this run")
                    break
                continue
            # When the answer came back, not when the run started: a page
            # found is first seen when it was found.
            retrieved = (clock or (lambda: datetime.now(UTC)))()
            cost = cost_usd(Tier.REASON, answer.usage, model_id=answer.model_id)
            arts, undated = _articles(answer, t, retrieved)
            quarantined = 0
            kept = []
            for a in arts:
                if _screen(engine, f"{a.title}\n{a.body}") is not None:
                    quarantined += 1
                else:
                    kept.append(a)
            stats = None
            if kept:
                feed_in = PreparedFeed(SOURCE, kept)
                normalised, stats = feed_in.normalize(
                    feed_in.fetch(datetime(1970, 1, 1, tzinfo=UTC), limit=len(kept)),
                    entity_index=index,
                    holdings=holdings,
                    watchlist=watchlist,
                    languages=languages,
                )
                stored = corpus.add_all(normalised, SOURCE, seen_at=retrieved)
            else:
                stored = None
            n_stored = stored.stored if stored else 0
            summary = answer.text[:1200]
            if summary and _screen(engine, summary) is not None:
                summary = "[the model's summary was withheld: the injection scan refused it]"
            if answer.refused:
                outcome = "refused"
            elif answer.stop_reason == "max_tokens":
                outcome = "truncated"
            elif n_stored:
                outcome = "stored"
            elif answer.sources:
                outcome = "no_new_evidence"
            else:
                outcome = "nothing_found"
            parts = [
                f"{len(answer.sources)} cited, {len(answer.uncited)} found and not cited",
                f"{undated} undated" if undated else "",
                f"{quarantined} quarantined" if quarantined else "",
                f"{stored.duplicates} already held" if stored and stored.duplicates else "",
                f"{(stats.fetched - stats.kept)} dropped by the cleaner"
                if stats and stats.fetched > stats.kept
                else "",
                f"search errors: {', '.join(answer.errors)}" if answer.errors else "",
                f"refusal {answer.refusal}" if answer.refused else "",
            ]
            detail = "; ".join(p for p in parts if p)
            if summary:
                detail += f". The model's summary (not evidence; cite the rows): {summary}"
            _record(
                corpus,
                t,
                retrieved,
                outcome,
                model=answer.model_id,
                queries=answer.queries,
                searches=answer.usage.web_search_requests,
                input_tokens=answer.usage.input_tokens + answer.usage.cached_input_tokens,
                output_tokens=answer.usage.output_tokens,
                cost_usd=cost,
                stored=n_stored,
                detail=detail,
            )
            report.attempts.append(Attempt(t.key, outcome, n_stored, cost, detail[:300]))
        report.evidence_month_usd = evidence_month_usd(corpus, now)
        report.month_usd = committed_usd_since(corpus_path, month_start(now))
    return report


def for_pack(corpus, instrument_id: str, since: datetime) -> tuple[list[dict], list[dict]]:
    """The attempts for a name since `since`, and the rows they stored: what
    the nightly page reads under the name's move, by the move searched for and
    not by publication time - a page dated before the digest's window is still
    the evidence for that move."""
    if not corpus.has_table("evidence_searches"):
        return [], []
    attempts = [
        dict(r)
        for r in corpus.conn.execute(
            "SELECT * FROM evidence_searches WHERE instrument = ? AND at >= ? "
            "AND outcome IN ({}) ORDER BY at".format(",".join("?" * len(SEARCHED))),
            (instrument_id, since.isoformat(), *SEARCHED),
        ).fetchall()
    ]
    rows = [
        dict(r)
        for r in corpus.conn.execute(
            "SELECT doc_id, title, published_at, first_seen_at, source_domain, query FROM articles "
            "WHERE source = ? AND fetched_for = ? AND first_seen_at >= ? ORDER BY published_at",
            (SOURCE, instrument_id, since.isoformat()),
        ).fetchall()
    ]
    return attempts, rows
