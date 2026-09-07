"""The day's digest: per name, what was collected, what escalated, what moved.

The stores are append-only tables. Nobody reads a table; an analyst reads a
page. This turns one day of the corpus and the fact book into that page, for
three readers with different needs:

  * the **person**, who wants to know in a minute what was said about each
    name, what the tone was, and what is scheduled;
  * the **agents**, which get the same content as structured JSON so the news
    agent's aggregate and the events agent's window come from one derivation;
  * the **nightly feedback routine**, which reasons over the digest rather
    than re-deriving it from raw rows, so its "why did it move" starts from
    the same facts the person saw.

Derived, never authoritative. It is regenerated from the stores on every run,
which is why it may be overwritten while the stores may not. Articles below a
quality floor are left out; nothing here is a new fact, only an arrangement.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from knowledge.corpus import Corpus
from knowledge.facts import FactBook
from knowledge.news.features import Article, LexiconExtractor

DIGEST_DIR = Path("data/digests")

#: Articles scored below this are noise by the cleaning layer's own measure:
#: a listicle or crypto-price piece that names a holding scores 0.30, a bare
#: headline from an unknown site 0.50.
MIN_QUALITY = 0.4
#: Stories per name on the page. The rest is still in the corpus.
TOP_N = 8
#: A story is "today's" if it was published in the day, or the twelve hours
#: before it - a Kuala Lumpur evening is a New York morning.
WINDOW_BEFORE = timedelta(hours=12)
UPCOMING = timedelta(days=30)

#: Snapshot figures shown when the fact book has them, in this order.
SNAPSHOT = (
    ("pe_ttm", "P/E (ttm)"),
    ("pb", "P/B"),
    ("eps_ttm", "EPS (ttm)"),
    ("dividend_yield", "dividend yield"),
    ("beta", "beta"),
    ("high_52w", "52w high"),
    ("low_52w", "52w low"),
    ("price_target_consensus", "target (consensus)"),
    ("analyst_buy", "analysts: buy"),
    ("analyst_hold", "analysts: hold"),
    ("analyst_sell", "analysts: sell"),
    ("av_news_sentiment", "vendor sentiment"),
    ("revenue", "revenue (last quarter)"),
    ("net_income", "net income (last quarter)"),
    ("eps_surprise_pct", "last EPS surprise %"),
)


@dataclass
class NameDigest:
    instrument_id: str
    label: str
    stories: list[dict] = field(default_factory=list)
    escalated: int = 0
    tone: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    upcoming: list[dict] = field(default_factory=list)
    snapshot: list[dict] = field(default_factory=list)

    @property
    def quiet(self) -> bool:
        return not (self.stories or self.events or self.upcoming)


@dataclass
class Digest:
    day: str
    generated_at: str
    slot: str
    names: list[NameDigest] = field(default_factory=list)
    macro: list[dict] = field(default_factory=list)
    collection: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    def to_markdown(self) -> str:
        out = [f"# Digest {self.day}", ""]
        out.append(
            f"Generated {self.generated_at} · slot `{self.slot}` · "
            f"{self.counts.get('articles', 0)} articles in the corpus, "
            f"{self.counts.get('observations', 0)} observations, "
            f"{self.counts.get('events', 0)} events in the fact book."
        )
        out += ["", "## The book", ""]
        for n in self.names:
            out.append(f"### {n.label} ({n.instrument_id})")
            if n.quiet:
                out.append("- quiet: nothing collected for this name in the window")
                out.append("")
                continue
            if n.tone:
                t = n.tone
                out.append(
                    f"- tone: {t['n']} stories from {t['sources']} sources, polarity "
                    f"{t['polarity_mean']:+.2f}, peak intensity {t['intensity_max']:.2f}, "
                    f"uncertainty {t['uncertainty_mean']:.2f} · {n.escalated} escalated"
                )
            for s in n.stories:
                star = " ★" if s["escalated"] else ""
                # None: stored before the quality score existed, not "worthless".
                q = "–" if s["quality"] is None else f"{s['quality']:.2f}"
                out.append(
                    f"- {s['published_at'][:10]} {s['domain']} — {s['title']} "
                    f"[q {q}, tone {s['polarity']:+.1f}]{star}"
                )
            for e in n.events:
                out.append(f"- event {e['when'][:10]} {e['kind']} — {e['title']} ({e['source']})")
            for e in n.upcoming:
                out.append(
                    f"- upcoming {e['when'][:10]} {e['kind']} — {e['title']} ({e['source']})"
                )
            if n.snapshot:
                out.append(
                    "- snapshot: "
                    + " · ".join(
                        f"{s['label']} {s['value']} ({s['source']}, {s['known_at']})"
                        for s in n.snapshot
                    )
                )
            out.append("")
        if self.macro:
            out += [
                "## Macro",
                "",
                "| series | latest | as of | change | source |",
                "|---|---|---|---|---|",
            ]
            for m in self.macro:
                raw = m["change"]
                change = "" if raw is None else (raw if str(raw).startswith("-") else f"+{raw}")
                out.append(
                    f"| {m['title'] or m['series_id']} | {m['value']} | {m['obs_date']} | {change} | {m['source']} |"
                )
            out.append("")
        if self.collection:
            out += [
                "## Collection",
                "",
                "| at (UTC) | source | status | fetched | kept | stored | detail |",
                "|---|---|---|---|---|---|---|",
            ]
            for c in self.collection:
                out.append(
                    f"| {c['at'][11:16]} | {c['source']} | {c['status']} | {c['fetched']} | "
                    f"{c['kept']} | {c['stored']} | {c['detail'][:80]} |"
                )
            out.append("")
        return "\n".join(out)


def _label(iid: str) -> str:
    from knowledge.graph.ids import display_names, instrument_id

    names = display_names()
    return names.get(instrument_id(iid) or iid, iid)


def _norm_title(t: str) -> str:
    return " ".join(t.lower().split())[:80]


def build_digest(
    cfg,
    day: date | None = None,
    *,
    slot: str = "all",
    corpus_path: str | None = None,
    facts_path: str | None = None,
    now: datetime | None = None,
    top_n: int = TOP_N,
    min_quality: float = MIN_QUALITY,
) -> Digest:
    from knowledge.graph.extractors.gdelt import entity_index
    from knowledge.graph.ids import instrument_id as canonical
    from knowledge.news.linking import linker_for

    now = now or datetime.now(UTC)
    day = day or now.date()
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    book = tuple(dict.fromkeys(tuple(cfg.watchlist) + tuple(cfg.holdings)))
    linker = linker_for(entity_index() or {"": ""})
    extractor = LexiconExtractor()
    digest = Digest(day=day.isoformat(), generated_at=now.strftime("%Y-%m-%d %H:%M UTC"), slot=slot)

    with (
        Corpus(corpus_path or cfg.corpus_db) as corpus,
        FactBook(facts_path or getattr(cfg, "facts_db", "data/facts.db")) as facts,
    ):
        articles = [
            a
            for a in corpus.articles(
                published_since=start - WINDOW_BEFORE, limit=20_000, min_quality=min_quality
            )
            if a.published_at < end
        ]
        for iid in book:
            forms = {iid, canonical(iid) or iid}
            mine = [a for a in articles if forms & set(a.instruments)]
            digest.names.append(
                _name_digest(iid, mine, facts, now, linker, extractor, top_n, set(book))
            )
        digest.macro = _macro(facts, day)
        digest.collection = [
            {
                "at": r["at"],
                "source": r["source"],
                "status": r["status"],
                "fetched": r["fetched"],
                "kept": r["kept"],
                "stored": r["stored"],
                "detail": r["detail"],
            }
            for r in corpus.sweeps(limit=200)
            if start.isoformat() <= r["at"] < end.isoformat()
        ]
        counts = dict(corpus.counts())
        counts.update(
            {
                k: v
                for k, v in facts.counts().items()
                if k in ("observations", "events", "series", "documents")
            }
        )
        digest.counts = counts
    return digest


def _name_digest(
    iid, articles: list[Article], facts, now, linker, extractor, top_n, book: set[str]
) -> NameDigest:
    """The star and the escalated count come from RE-RUNNING the gate here, not
    from the stored `escalated` column.

    The corpus is append-only by trigger, so that column is a record of the rule
    in force the day each article arrived - correct as history, and wrong as a
    reading list once the rule changes. On 2026-09-07 the gate stopped treating
    "the company is named" as materiality; a digest still reading the column
    would have gone on starring the charity cheque and the golf tournament.
    """
    from knowledge.news.features import should_escalate

    seen: set[str] = set()
    scored = []
    for a in articles:
        key = _norm_title(a.title)
        if key in seen:
            continue
        seen.add(key)
        feats = a.features or extractor.extract(
            a.text, linker.names_for(a.instruments) or a.instruments
        )
        scored.append((a, feats, should_escalate(feats, a.instruments, book, set())))
    scored.sort(
        key=lambda af: (
            not af[2],
            -(af[0].quality or 0.0),
            -af[0].published_at.timestamp(),
        )
    )
    from knowledge.news.clean import normalise_text

    nd = NameDigest(instrument_id=iid, label=_label(iid))
    nd.escalated = sum(1 for _a, _f, e in scored if e)
    for a, f, escalated in scored[:top_n]:
        nd.stories.append(
            {
                "doc_id": a.doc_id,
                "published_at": a.published_at.isoformat(),
                "domain": a.source_domain,
                # Rows stored before the cleaning layer keep their artefacts in
                # the corpus (append-only); the page shows the normal form.
                "title": normalise_text(a.title),
                "quality": None if a.quality is None else float(a.quality),
                "polarity": f.polarity,
                "intensity": f.intensity,
                "uncertainty": f.uncertainty,
                "escalated": escalated,
            }
        )
    if scored:
        pols = [f.polarity for _a, f, _e in scored]
        nd.tone = {
            "n": len(scored),
            "sources": len({a.source_domain for a, _f, _e in scored}),
            "polarity_mean": round(sum(pols) / len(pols), 3),
            "intensity_max": round(max(f.intensity for _a, f, _e in scored), 3),
            "uncertainty_mean": round(sum(f.uncertainty for _a, f, _e in scored) / len(scored), 3),
        }
    for e in facts.events(iid, since=now - timedelta(days=2), until=now, limit=20, opinions=2):
        nd.events.append(_event(e))
    for e in facts.events(iid, since=now, until=now + UPCOMING, limit=10):
        nd.upcoming.append(_event(e))
    for concept, label in SNAPSHOT:
        o = facts.latest(iid, concept)
        if o is None or o.value is None:
            continue
        nd.snapshot.append(
            {
                "concept": concept,
                "label": label,
                "value": _fmt(o.value),
                "known_at": o.known_at.isoformat(),
                "source": o.source,
                "period_end": o.period_end.isoformat() if o.period_end else None,
            }
        )
    return nd


def _event(e) -> dict:
    when = e.effective_at or e.announced_at
    return {
        "when": when.isoformat(),
        "kind": e.kind,
        "title": e.title,
        "source": e.source,
        "url": (e.payload or {}).get("url", ""),
    }


def _fmt(value: Decimal) -> str:
    if abs(value) >= 1_000_000_000:
        return f"{value / Decimal(1_000_000_000):,.2f}bn"
    if abs(value) >= 1_000_000:
        return f"{value / Decimal(1_000_000):,.1f}m"
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value:,.2f}"


def _macro(facts, day: date) -> list[dict]:
    out = []
    for sid in facts.series_ids():
        pts = facts.series(sid, since=day - timedelta(days=400))
        if not pts:
            continue
        latest = pts[-1]
        prev = pts[-2] if len(pts) > 1 else None
        change = (
            None
            if prev is None
            else (latest.value - prev.value).quantize(Decimal("0.0001")).normalize()
        )
        out.append(
            {
                "series_id": sid,
                "title": (latest.payload or {}).get("title", ""),
                "value": _fmt(latest.value),
                "obs_date": latest.obs_date.isoformat(),
                "change": None if change is None else f"{change:f}",
                "source": latest.source,
            }
        )
    return out


def write_digest(digest: Digest, root: str | Path = DIGEST_DIR) -> tuple[Path, Path]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    md = root / f"{digest.day}.md"
    js = root / f"{digest.day}.json"
    md.write_text(digest.to_markdown() + "\n", encoding="utf-8")
    js.write_text(digest.to_json() + "\n", encoding="utf-8")
    latest = root / "latest.md"
    latest.write_text(digest.to_markdown() + "\n", encoding="utf-8")
    return md, js
