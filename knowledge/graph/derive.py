"""data/graph.db, rebuilt from the corpus rather than carried in git.

The graph's news layer is a function of the corpus: one Event per linked
article, one INFERRED `affects` edge per company it names, every edge born
closed after EVENT_VALID_DAYS. The spine (holdings, sectors, curated rivals,
markets) is a function of config.toml and the yaml beside this module. Neither
holds anything the corpus and the checked-in sources do not, so committing the
file was storing a second, larger copy of the corpus - 20.2 MB on 2026-10-22,
ahead of the corpus itself and first to reach GitHub's 50 MiB warning.

Measured on copies of both stores (2026-10-22): a rebuild takes ~11 s, two
rebuilds are byte-identical, and every derived node and edge is in the
committed graph. What the committed graph has and a rebuild does not is the
point of rebuilding it:

  - 2,542 Events for articles the corpus REFUSED as duplicates. The sweep
    linked every normalised article, stored or not (fixed in knowledge/sweep.py:
    it links only what the corpus kept), so one story told twice was two events
    and every hub was ~16% heavier than the news.
  - links an earlier linker made and today's would not. Every row is read
    through `EntityLinker.recheck` - the rule the digest already applies
    (knowledge/digest.py) - so the graph and the digest agree on who a story
    names: 41 stale links on 10-20, among them 22 *Shree TNB Polymers* rows on
    Tenaga.

Two ways in:

  sync(graph, corpus)   incremental: links corpus rows past the watermark in
                        the stamp. A full rebuild when the graph or its stamp is
                        missing, when the inputs (code, yaml, config) changed,
                        or when the watermark row is no longer the row it was.
  build_from_corpus     the full rebuild, into a file that does not exist yet.

The stamp (`<graph>.stamp.json`) is what makes a graph DERIVED. A graph file
without one is a graph something else wrote - the committed one, until git stops
carrying it - and nothing here replaces it unasked.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from knowledge.graph.store import DETERMINISTIC, GraphStore

#: Bump when the derivation changes in a way the hashed inputs cannot see.
DERIVE_VERSION = 1

#: Rows linked per transaction. Bounds memory; the result does not depend on it.
BATCH = 5000

ROOT = Path(__file__).resolve().parents[2]

#: What the graph is a function of, besides the corpus. Code is hashed as well
#: as data: a change to how an id is spelled or how long an event lasts
#: changes every derived row, and must rebuild them rather than mix two rules.
_INPUT_GLOBS = (
    "config.toml",
    "config.local.toml",
    "knowledge/graph/data/*.yaml",
    "knowledge/graph/*.py",
    "knowledge/graph/extractors/*.py",
    "knowledge/news/linking.py",
    "markets/registry.py",
)


class DeriveError(RuntimeError):
    """The graph could not be derived, or replacing it would lose something."""


@dataclass(frozen=True)
class Watermark:
    rowid: int
    doc_id: str


def stamp_path(graph: str | Path) -> Path:
    graph = Path(graph)
    return graph.with_name(graph.name + ".stamp.json")


def derived(graph: str | Path) -> bool:
    """A graph this module wrote, which it may therefore rewrite."""
    return stamp_path(graph).exists()


def wanted(graph: str | Path, corpus: str | Path) -> bool:
    """Should this graph be derived here? Yes when it already is, or when there
    is no graph and git carries the corpus as parts (so no graph is coming from
    git either). A graph file with no stamp is never taken over."""
    from knowledge import stores

    graph = Path(graph)
    if str(graph) == ":memory:":
        return False
    if derived(graph):
        return True
    return not graph.exists() and stores.governed(corpus)


def inputs_key(root: Path = ROOT) -> str:
    h = hashlib.sha256(f"derive {DERIVE_VERSION}\n".encode())
    for pattern in _INPUT_GLOBS:
        for path in sorted(root.glob(pattern)):
            h.update(path.relative_to(root).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def _read_stamp(graph: Path) -> dict | None:
    try:
        return json.loads(stamp_path(graph).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_stamp(graph: Path, key: str, mark: Watermark | None) -> None:
    body = {
        "version": DERIVE_VERSION,
        "inputs": key,
        "corpus_rowid": mark.rowid if mark else 0,
        "corpus_doc_id": mark.doc_id if mark else "",
    }
    tmp = stamp_path(graph).with_name(stamp_path(graph).name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, stamp_path(graph))


def _corpus(corpus: str | Path) -> sqlite3.Connection:
    """Read-only. Deriving the graph must never be a way to write the corpus."""
    path = Path(corpus)
    if not path.exists() or path.stat().st_size == 0:
        raise DeriveError(f"no corpus at {path} to derive the graph from")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _watermark(con: sqlite3.Connection) -> Watermark | None:
    row = con.execute("SELECT rowid, doc_id FROM articles ORDER BY rowid DESC LIMIT 1").fetchone()
    return Watermark(row[0], row[1]) if row else None


def _holds(con: sqlite3.Connection, stamp: dict) -> bool:
    """The stamped row is still the same row: the corpus grew, it was not swapped."""
    rowid = int(stamp.get("corpus_rowid") or 0)
    if rowid == 0:
        return True
    row = con.execute("SELECT doc_id FROM articles WHERE rowid = ?", (rowid,)).fetchone()
    return row is not None and row[0] == stamp.get("corpus_doc_id")


def _linker():
    from knowledge.graph.extractors.gdelt import entity_index
    from knowledge.news.linking import linker_for

    return linker_for(entity_index() or {"": ""})


def _link_rows(store: GraphStore, con: sqlite3.Connection, after: int, linker) -> int:
    """Link every linked corpus row with rowid > `after`, in rowid order."""
    from knowledge.corpus import Corpus
    from knowledge.graph.build import link_articles
    from knowledge.news.linking import relinked

    linked = 0
    while True:
        rows = con.execute(
            "SELECT rowid, * FROM articles WHERE rowid > ? "
            "AND instruments_json NOT IN ('', '[]') ORDER BY rowid LIMIT ?",
            (after, BATCH),
        ).fetchall()
        if not rows:
            return linked
        articles = [relinked(Corpus._to_article(r), linker) for r in rows]
        link_articles(store, articles)
        linked += sum(1 for a in articles if a.instruments)
        after = rows[-1]["rowid"]


def build_from_corpus(corpus: str | Path, out: str | Path, cfg=None) -> Watermark | None:
    """The whole graph, spine then events, into `out`, which must not exist.

    Deterministic: the same corpus and inputs give the same bytes. Nothing in it
    depends on today's date - event edges are born closed and nothing prunes."""
    from knowledge.graph.build import build

    out = Path(out)
    if out.exists():
        raise DeriveError(f"{out} exists; a full rebuild writes a new file")
    linker = _linker()
    con = _corpus(corpus)
    try:
        mark = _watermark(con)
        with GraphStore(out) as store:
            build(store, cfg=cfg, summarize=False)
            _link_rows(store, con, 0, linker)
            store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return mark


def _foreign_tiers(graph: Path) -> int:
    con = sqlite3.connect(f"{graph.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        return sum(
            con.execute(f"SELECT count(*) FROM {t} WHERE tier <> ?", (DETERMINISTIC,)).fetchone()[0]
            for t in ("nodes", "edges")
        )
    finally:
        con.close()


def rebuild(graph: str | Path, corpus: str | Path, cfg=None) -> str:
    """Full rebuild beside the graph, then swap it in.

    Refuses to replace a graph that holds rows of another tier: a model tier's
    edges are not derivable from the corpus and would be lost."""
    from knowledge.stores import _release

    graph = Path(graph)
    if graph.exists() and _foreign_tiers(graph):
        raise DeriveError(
            f"{graph} holds non-deterministic rows; they are not derivable, so it is not replaced"
        )
    key = inputs_key()
    tmp = graph.with_name(graph.name + f".building-{os.getpid()}")
    for side in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        if side.exists():
            side.unlink()
    try:
        mark = build_from_corpus(corpus, tmp, cfg=cfg)
        _release(graph)
        os.replace(tmp, graph)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    _write_stamp(graph, key, mark)
    with GraphStore(graph) as store:
        c = store.counts()
    return f"rebuilt from the corpus: {c['nodes']} nodes, {c['edges']} edges"


def sync(graph: str | Path, corpus: str | Path, cfg=None) -> str:
    """Bring a derived graph up to the corpus. Never touches an underived one."""
    graph = Path(graph)
    if graph.exists() and not derived(graph):
        raise DeriveError(
            f"{graph} was not derived here (no {stamp_path(graph).name}); leaving it alone"
        )
    stamp = _read_stamp(graph)
    if not graph.exists() or stamp is None or stamp.get("inputs") != inputs_key():
        return rebuild(graph, corpus, cfg=cfg)
    con = _corpus(corpus)
    try:
        if not _holds(con, stamp):
            holds = False
        else:
            holds = True
            mark = _watermark(con)
            after = int(stamp.get("corpus_rowid") or 0)
            if mark is None or mark.rowid <= after:
                return "current"
            with GraphStore(graph) as store:
                before = store.counts()
                _link_rows(store, con, after, _linker())
                now = store.counts()
    finally:
        con.close()
    if not holds:
        # The stamped row is gone or is another row: this is not the corpus the
        # graph was derived from (moved aside, replaced), so start again.
        return rebuild(graph, corpus, cfg=cfg)
    _write_stamp(graph, stamp["inputs"], mark)
    return (
        f"+{now['nodes'] - before['nodes']} nodes  +{now['edges'] - before['edges']} edges  "
        f"(INFERRED - traversable, never citable)"
    )


def ready(graph: str | Path, corpus: str | Path | None = None) -> bool:
    """For a reader: is there a graph to read? Derives it first when it should.

    A reader that finds no graph answers "not built" (None, an empty peer set);
    once git no longer carries the graph that answer would be wrong every time,
    so a derivable graph is derived rather than reported missing. A derivation
    that fails raises - an empty answer would look like a real one."""
    from knowledge.corpus import CORPUS_DB

    graph = Path(graph)
    corpus = Path(corpus or CORPUS_DB)
    if wanted(graph, corpus):
        from knowledge import stores

        stores.ensure(corpus)
        sync(graph, corpus)
    return graph.exists()
