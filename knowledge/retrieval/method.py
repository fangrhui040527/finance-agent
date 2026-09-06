"""The five human-written knowledge stores, filled from curated notes.

`agents/registry.yaml` has named `kb_craft`, `kb_method_valuation`,
`kb_method_technical`, `kb_method_risk` and `kb_failures` since the registry
existed, and until this module every one of them was registered empty: the
teacher answered from a Python tuple with no sources, the valuation agent
never cited a method, and the red team argued from six fixed sentences. The
notes under `knowledge/method/<collection>/` are what those stores hold now.

The contract (knowledge/method/README.md is the long form):

  * one note per file, YAML front-matter between `---` fences, then a body
    whose sections are numbered `1. Title` so the chunker splits on them and a
    citation can point at one section;
  * `licence: own` on every note - this system's own writing, quotable in
    full. References carry their own licence (`open`, `attributed` or
    `link_only`); a `link_only` reference is a title and a URL, never a body;
  * `kb_craft` notes name the curriculum `concepts` they teach, so
    `Concept.sources` in the teacher can point at them; `kb_method_valuation`
    notes name the `archetypes` they apply to, so a bank method is never
    retrieved for a software name; `kb_failures` notes carry `patterns` from a
    pinned vocabulary and a `case`, because the red team retrieves on the
    structural pattern, never the company;
  * ASCII only. Windows opens text as cp1252 unless told otherwise, and a note
    that renders on Linux and raises on Windows is the failure mode the
    2026-09-05 CI run demonstrated.

A note that breaks the contract raises `NoteError` naming the file; a folder
that is missing yields an empty collection, never a crash, so a checkout
without notes still answers "no evidence".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from knowledge.chunking.parent_child import Chunk, chunk_document
from knowledge.retrieval.hybrid import Collection

METHOD_DIR = Path("knowledge/method")
COC_TABLE = METHOD_DIR / "data" / "cost_of_capital.yaml"

#: The stores the notes fill, in registry order. A directory that is not one of
#: these is ignored, so a scratch folder cannot register a collection.
COLLECTIONS = (
    "kb_craft",
    "kb_method_valuation",
    "kb_method_technical",
    "kb_method_risk",
    "kb_failures",
)
LICENCES = frozenset({"own", "open", "attributed", "link_only"})
#: docs/06 section 5.4's twelve structural patterns, plus three the 2026 case
#: set needed. Pinned here so a typo in a note is a test failure, not a tag
#: nobody ever retrieves.
PATTERN_TAGS = frozenset(
    {
        "accruals_divergence",
        "receivables_run",
        "related_party_dependence",
        "single_customer_concentration",
        "serial_acquirer",
        "covenant_cliff",
        "going_concern_language",
        "auditor_change",
        "segment_reorganisation",
        "peak_cycle_margin_extrapolated",
        "promoter_pledge",
        "capital_raise_treadmill",
        "duration_mismatch",
        "hidden_leverage",
        "fabricated_sales",
    }
)

_FRONT = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n(.*)\Z", re.S)
_SECTION = re.compile(r"^\d+\.\s+\S", re.M)


class NoteError(ValueError):
    """A note that breaks the contract. The message names the file."""


@dataclass(frozen=True)
class MethodNote:
    collection: str
    slug: str
    title: str
    as_of: datetime
    licence: str
    body: str
    meta: dict[str, Any]
    refs: tuple[dict[str, str], ...]
    path: Path

    @property
    def doc_id(self) -> str:
        return f"{self.collection}:{self.slug}"

    @property
    def concepts(self) -> tuple[str, ...]:
        return tuple(self.meta.get("concepts") or ())

    @property
    def archetypes(self) -> tuple[str, ...]:
        return tuple(self.meta.get("archetypes") or ())

    @property
    def patterns(self) -> tuple[str, ...]:
        return tuple(self.meta.get("patterns") or ())

    def metadata(self) -> dict[str, Any]:
        """What every chunk of this note carries: the filters read it."""
        out: dict[str, Any] = {
            "licence": self.licence,
            "kind": "method_note",
            "collection": self.collection,
            "slug": self.slug,
            "title": self.title,
            "as_of": self.as_of.date().isoformat(),
            "refs": [dict(r) for r in self.refs],
        }
        for key in ("concepts", "archetypes", "patterns", "case", "base_rate"):
            if key in self.meta:
                out[key] = self.meta[key]
        return out


def _non_ascii(text: str) -> tuple[int, str] | None:
    for i, ch in enumerate(text):
        if ord(ch) > 127:
            return i, ch
    return None


def _as_day(raw: Any, path: Path) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise NoteError(f"{path}: as_of must be a YYYY-MM-DD date, got {raw!r}") from None


def _str_list(meta: dict, key: str, path: Path) -> list[str]:
    raw = meta.get(key)
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) and x for x in raw):
        raise NoteError(f"{path}: {key} must be a non-empty list of strings")
    return raw


def parse_note(path: str | Path, collection: str) -> MethodNote:
    """One file -> one validated note. Every rule here is one an agent relies on."""
    path = Path(path)
    if collection not in COLLECTIONS:
        raise NoteError(f"{path}: {collection!r} is not a method collection")
    raw = path.read_text(encoding="utf-8")
    bad = _non_ascii(raw)
    if bad is not None:
        i, ch = bad
        raise NoteError(f"{path}: non-ASCII character {ch!r} (U+{ord(ch):04X}) at offset {i}")
    m = _FRONT.match(raw)
    if m is None:
        raise NoteError(f"{path}: no YAML front-matter between --- fences")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise NoteError(f"{path}: front-matter is not YAML: {e}") from None
    if not isinstance(meta, dict):
        raise NoteError(f"{path}: front-matter must be a mapping")
    body = m.group(2).strip()
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        raise NoteError(f"{path}: title is required")
    if meta.get("licence") != "own":
        raise NoteError(f"{path}: licence must be 'own' (this system's own writing)")
    as_of = _as_day(meta.get("as_of"), path)
    refs_raw = meta.get("refs")
    if not isinstance(refs_raw, list) or not refs_raw:
        raise NoteError(f"{path}: at least one reference is required")
    refs: list[dict[str, str]] = []
    for r in refs_raw:
        if not isinstance(r, dict) or not r.get("title") or not r.get("url"):
            raise NoteError(f"{path}: every reference needs a title and a url")
        lic = r.get("licence")
        if lic not in LICENCES or lic == "own":
            raise NoteError(
                f"{path}: reference {r.get('title')!r} needs a licence in open|attributed|link_only"
            )
        refs.append({"title": str(r["title"]), "url": str(r["url"]), "licence": str(lic)})
    if len(_SECTION.findall(body)) < 3:
        raise NoteError(f"{path}: the body needs at least three numbered sections ('1. Title')")
    if collection == "kb_craft":
        _str_list(meta, "concepts", path)
    if collection == "kb_method_valuation":
        _str_list(meta, "archetypes", path)
    if collection == "kb_failures":
        tags = _str_list(meta, "patterns", path)
        unknown = sorted(set(tags) - PATTERN_TAGS)
        if unknown:
            raise NoteError(
                f"{path}: unknown pattern tag(s) {unknown}; the vocabulary is PATTERN_TAGS"
            )
        case = meta.get("case")
        if not isinstance(case, dict) or not all(
            case.get(k) for k in ("name", "country", "year", "outcome")
        ):
            raise NoteError(
                f"{path}: kb_failures notes need case: {{name, country, year, outcome}}"
            )
    return MethodNote(
        collection=collection,
        slug=path.stem,
        title=title.strip(),
        as_of=as_of,
        licence="own",
        body=body,
        meta=meta,
        refs=tuple(refs),
        path=path,
    )


def iter_notes(root: str | Path = METHOD_DIR) -> list[MethodNote]:
    """Every note under the method root, in collection then file order."""
    root = Path(root)
    notes: list[MethodNote] = []
    for collection in COLLECTIONS:
        folder = root / collection
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.upper().startswith("README"):
                continue
            notes.append(parse_note(path, collection))
    return notes


def note_chunks(note: MethodNote) -> list[Chunk]:
    """Parent/child chunks of one note, children preferred, every one carrying
    the note's metadata so the archetype, concept and pattern filters work."""
    parents, children = chunk_document(
        note.doc_id, note.body, note.collection, as_of=note.as_of, metadata=note.metadata()
    )
    return children or parents


# --- the cost-of-capital table --------------------------------------------------------


def load_cost_of_capital(path: str | Path = COC_TABLE) -> dict[str, Any]:
    """The curated Damodaran table as a dict, or {} when the file is absent.

    Numbers in it are transcribed by a person from the published pages and
    dated; a null value means 'not transcribed yet' and is never read as zero.
    """
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _pct(v: Any) -> str:
    return f"{float(v) * 100:.2f}%"


def cost_of_capital_chunks(path: str | Path = COC_TABLE) -> list[Chunk]:
    """One citable chunk per table row, so a cost-of-capital finding can quote
    the exact premium it used and the verifier can check it verbatim."""
    table = load_cost_of_capital(path)
    if not table:
        return []
    as_of = _as_day(table.get("as_of"), Path(path))
    source = str(table.get("source") or "cost-of-capital table")
    stamp = as_of.date().isoformat()
    base = {"licence": "attributed", "kind": "cost_of_capital", "source": source, "as_of": stamp}
    out: list[Chunk] = []

    def add(row: str, text: str, extra: dict[str, Any] | None = None) -> None:
        meta = {**base, "row": row, "title": f"Cost of capital: {row}", **(extra or {})}
        out.append(
            Chunk(
                f"cost_of_capital#{row}",
                text,
                "kb_method_valuation",
                None,
                row,
                as_of,
                meta,
            )
        )

    if table.get("mature_market_erp") is not None:
        text = f"Mature market equity risk premium {_pct(table['mature_market_erp'])} ({source}, as of {stamp})."
        implied = table.get("implied_erp") or {}
        for k, v in sorted(implied.items()):
            if v is not None:
                text += f" Implied equity risk premium {k}: {_pct(v)}."
        add("erp", text)
    for code, row in sorted((table.get("country") or {}).items()):
        if not isinstance(row, dict) or row.get("crp") is None or row.get("erp") is None:
            continue
        rating = f", rating {row['rating']}" if row.get("rating") else ""
        add(
            f"country:{code}",
            f"{row.get('name', code)} ({code}): country risk premium {_pct(row['crp'])}, "
            f"total equity risk premium {_pct(row['erp'])}{rating} ({source}, as of {stamp}).",
            {"country": code},
        )
    for name, row in sorted((table.get("industry_betas") or {}).items()):
        if not isinstance(row, dict) or row.get("unlevered") is None:
            continue
        lev = f", levered {row['levered']:.2f}" if row.get("levered") is not None else ""
        add(
            f"industry:{name}",
            f"Industry {name}: unlevered beta {row['unlevered']:.2f}{lev} ({source}, as of {stamp}).",
            {"industry": name},
        )
    return out


# --- assembly ---------------------------------------------------------------------------


def method_collections(
    root: str | Path = METHOD_DIR, cost_of_capital: str | Path | None = COC_TABLE
) -> dict[str, Collection]:
    """The five collections, filled. Always all five: an empty one is an honest
    'no evidence', a missing one is a KeyError an agent cannot tell from a
    denial (knowledge/retrieval/index.py rule 1)."""
    cols = {name: Collection(name) for name in COLLECTIONS}
    for note in iter_notes(root):
        cols[note.collection].add_all(note_chunks(note))
    if cost_of_capital is not None:
        cols["kb_method_valuation"].add_all(cost_of_capital_chunks(cost_of_capital))
    return cols


def method_stamp(root: str | Path = METHOD_DIR) -> tuple:
    """Identity of the notes on disk, for the shared router's cache key: an
    edited note must be visible on the next call, not after the corpus changes."""
    root = Path(root)
    if not root.is_dir():
        return (str(root), 0, 0, 0)
    n, latest, size = 0, 0, 0
    for path in list(root.rglob("*.md")) + list(root.rglob("*.yaml")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        n += 1
        latest = max(latest, st.st_mtime_ns)
        size += st.st_size
    return (str(root), n, latest, size)


def notes_for_concept(key: str, root: str | Path = METHOD_DIR) -> list[MethodNote]:
    """The kb_craft notes that teach one curriculum concept."""
    return [n for n in iter_notes(root) if n.collection == "kb_craft" and key in n.concepts]


def describe_notes(root: str | Path = METHOD_DIR) -> dict[str, int]:
    """Notes per collection, for `ask.py doctor` and the tests."""
    counts: dict[str, int] = {name: 0 for name in COLLECTIONS}
    for note in iter_notes(root):
        counts[note.collection] += 1
    return counts
