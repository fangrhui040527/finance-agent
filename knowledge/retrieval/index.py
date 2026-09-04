"""The corpus, indexed - so retrieval reads what the sweep collected.

Until this module existed every surface built `Router({})`: an empty ownership
map and no collections. `A4NewsNarrative.retrieve("kb_news", ...)` therefore
raised `CollectionScopeError` on every call, before it could look at a single
article. The daily sweep filled `data/corpus.db` for a week and nothing ever
read it back. A trace showed the news agent as "denied" and nobody asked why,
because a denial looks like a guardrail doing its job.

This is the missing half: registry -> ownership, corpus -> `kb_news`
collection, one `Router` that every entrypoint shares.

Three rules, each of which is a silent failure if dropped:

  1. **Every store the registry names is registered**, empty if nothing fills
     it yet. `Router.get` raises `KeyError` for an unregistered name, and an
     agent that KeyErrors is indistinguishable from one the allowlist denied.
     An EMPTY collection instead returns no hits, grades INSUFFICIENT, and the
     agent says "no evidence" - which is true and citable as such.
  2. **Ownership comes from the registry**, never a hand-written dict. The
     registry is what `tests/test_registry.py` and the eval ratchet guard; a
     second map here would drift exactly the way the tool allowlist once did.
  3. **Chunks carry the metadata the filters need.** `Collection.search`
     filters on `metadata["instruments"]` for the entity gate and on
     `metadata["licence"]` for the licence gate. A chunk indexed without them
     passes every filter, which is the wrong default for news.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from knowledge.chunking.parent_child import Chunk, chunk_news
from knowledge.news.features import Article, LexiconExtractor
from knowledge.retrieval.hybrid import Collection
from knowledge.retrieval.pipeline import Router

#: How far back the news collection reaches. Retrieval is for "what has been
#: said about this name lately"; a month of a daily sweep is a few thousand
#: headlines, which the in-process index handles in well under a second.
NEWS_WINDOW_DAYS = 120

#: The licence tier of what the sweep stores: a publisher's own headline and
#: summary, as carried in its feed. Quotable with attribution; not a scraped
#: body, which would be `link_only` and never emitted.
NEWS_LICENCE = "summary"


#: Where the nightly feedback routine writes its dated pages (knowledge/feedback/
#: README.md is the contract). Indexed into kb_lessons.
FEEDBACK_DIR = Path("knowledge/feedback")
#: The paper book's dated journal pages (knowledge/paper/README.md). Indexed
#: into the same kb_lessons: what the book learned is a lesson of the same rank.
PAPER_DIR = Path("knowledge/paper")


def lessons_collection(
    root: str | Path, kind: str = "feedback", col: Collection | None = None
) -> Collection:
    """`kb_lessons` from the routine's dated feedback pages.

    Each `YYYY-MM-DD.md` is chunked on its headings (parent/child), stamped
    with its date, and carries `licence: own` - it is this system's own
    writing, quotable in full. Files that are not dated pages (README,
    TEMPLATE) are skipped: a template retrieved as a lesson is a lesson
    nobody learned.
    """
    import re

    from knowledge.chunking.parent_child import chunk_document

    col = col if col is not None else Collection("kb_lessons")
    root = Path(root)
    if not root.exists():
        return col
    for path in sorted(root.glob("*.md")):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.md", path.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        parents, children = chunk_document(
            f"{kind}:{m.group(1)}",
            text,
            "kb_lessons",
            as_of=day,
            metadata={"licence": "own", "kind": kind, "day": m.group(1)},
        )
        col.add_all(children or parents)
    return col


def ownership_from_registry(reg) -> dict[str, set[str]]:
    """agent id -> the knowledge stores it may read, straight from the registry."""
    return {aid: set(spec.knowledge) for aid, spec in reg.agents.items()}


def news_text(art: Article) -> str:
    """What gets indexed. GDELT stores the headline as the body too, and
    indexing the same sentence twice doubles its term frequencies for nothing."""
    title = (art.title or "").strip()
    body = (art.body or "").strip()
    if not body or body == title:
        return title
    return f"{title}\n{body}"


def news_chunks(art: Article, extractor: LexiconExtractor | None = None, names=None) -> list[Chunk]:
    """One article -> its retrieval chunks, metadata attached."""
    from knowledge.graph.ids import instrument_id as canonical

    text = news_text(art)
    if not text:
        return []
    ids: list[str] = []
    for iid in art.instruments:
        for form in (iid, canonical(iid) or iid):
            if form and form not in ids:
                ids.append(form)
    features = art.features
    if features is None and extractor is not None:
        features = extractor.extract(art.text, list(names or art.instruments))
    meta = {
        "title": art.title,
        "source_domain": art.source_domain,
        "language": art.language,
        "instruments": ids,
        "countries": list(art.countries),
        "themes": list(art.themes),
        "licence": NEWS_LICENCE,
        "features": features.as_dict() if features is not None else {},
    }
    as_of = art.published_at if art.published_at.tzinfo else art.published_at.replace(tzinfo=UTC)
    return chunk_news(art.doc_id, text, as_of, meta)


def news_collection(articles: list[Article], index: dict[str, str] | None = None) -> Collection:
    """`kb_news`, built from stored articles."""
    from knowledge.news.linking import linker_for

    col = Collection("kb_news")
    extractor = LexiconExtractor()
    linker = linker_for(index) if index else None
    for art in articles:
        names = linker.names_for(art.instruments) if linker is not None else None
        col.add_all(news_chunks(art, extractor, names))
    return col


def build_router(
    reg,
    corpus=None,
    *,
    now: datetime | None = None,
    window: timedelta = timedelta(days=NEWS_WINDOW_DAYS),
    index: dict[str, str] | None = None,
    extra: dict[str, Collection] | None = None,
    feedback_dir: str | Path | None = FEEDBACK_DIR,
    paper_dir: str | Path | None = PAPER_DIR,
) -> Router:
    """One router: registry ownership, every store registered, news filled.

    `corpus` is an open `knowledge.corpus.Corpus` or None. `extra` lets a
    caller hand in already-built collections (tests, or a future filings
    index) under their store names. `feedback_dir` is where the nightly
    routine writes its dated feedback; those pages fill `kb_lessons`, the one
    store the registry lets an agent write, so the reflection agent can read
    back what the routine concluded on earlier days.
    """
    router = Router(ownership_from_registry(reg))
    now = now or datetime.now(UTC)

    filled: dict[str, Collection] = dict(extra or {})
    if corpus is not None and "kb_news" not in filled:
        if index is None:
            from knowledge.graph.extractors.gdelt import entity_index

            index = entity_index()
        articles = corpus.articles(since=now - window, limit=50_000)
        filled["kb_news"] = news_collection(articles, index)
    if "kb_lessons" not in filled and (feedback_dir is not None or paper_dir is not None):
        col = Collection("kb_lessons")
        if feedback_dir is not None:
            col = lessons_collection(feedback_dir, "feedback", col)
        if paper_dir is not None:
            col = lessons_collection(paper_dir, "paper", col)
        filled["kb_lessons"] = col

    for name in sorted(reg.knowledge):
        router.register(filled.get(name) or Collection(name))
    for name, col in filled.items():
        if name not in reg.knowledge:
            router.register(col)  # a caller-supplied store the registry has not named yet
    return router


# --- the shared instance ------------------------------------------------------------

_CACHE: dict[tuple, Router] = {}


def _corpus_stamp(path: str) -> tuple:
    try:
        st = os.stat(path)
    except OSError:
        return (path, 0, 0)
    return (path, st.st_mtime_ns, st.st_size)


def router_for(reg, corpus_path: str | Path | None, now: datetime | None = None) -> Router:
    """The router a surface should use, rebuilt only when the corpus changes.

    The MCP server calls `context()` on every tool call, and re-indexing a
    season of headlines per call would make each answer pay for the whole
    corpus. The cache key is the corpus file's identity, so a sweep that lands
    while the server is up is picked up on the next call, and a test that
    hands a fresh temporary corpus never sees another test's index.
    """
    from knowledge.corpus import Corpus

    if corpus_path is None:
        return build_router(reg, None, now=now)
    key = _corpus_stamp(str(corpus_path))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    if not Path(corpus_path).exists():
        router = build_router(reg, None, now=now)
    else:
        with Corpus(corpus_path) as corpus:
            router = build_router(reg, corpus, now=now)
    _CACHE.clear()  # one live index per process; a changed corpus replaces it
    _CACHE[key] = router
    return router


def describe(router: Router) -> str:
    """What is loaded, for `ask.py doctor` and the trace."""
    rows = []
    for name in sorted(router._collections):
        rows.append(f"{name}: {len(router._collections[name])} chunks")
    return ", ".join(rows) if rows else "no collections registered"
