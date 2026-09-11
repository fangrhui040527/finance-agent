"""One canonical node id, agreed by every producer.

Three things mint ids - the extractors, the store, and whatever asks the
traversal a question - and if any two disagree the graph quietly grows a second
Maybank. Nothing crashes. The supply chain simply forks, and half the exposure
paths lead to a node no query ever names.

That is the same failure mode `markets/registry.py` was written to fix, one
layer up: `MYX:1155` and `XKLS:1155` are one company, and so are `Maybank` and
`Malayan Banking Berhad`.

Adapted from Graphify-Labs/graphify's ids.py (Apache-2.0, docs/10), whose
docstring narrates four id-drift bugs. **The ordering trap is the transferable
part**: NFKC normalisation and casefold are iterated TO CONVERGENCE BEFORE the
non-word filter, because they do not commute - casefold can expand one character
into a base plus a combining mark, which NFKC then recomposes, which can casefold
differently again. Filtering first would delete the combining mark and change
which company you are talking about.

Three guarantees, all test-enforced:

  idempotent       node_id(k, node_id(k, x)) == node_id(k, x)
  word characters  everything after the prefix matches [\\w:]+
  caseless-stable  spelling, spacing and punctuation do not change the answer
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from knowledge.graph.entity_graph import NodeKind

DATA_DIR = Path(__file__).parent / "data"
ENTITIES_FILE = DATA_DIR / "entities.yaml"

#: Short, stable prefix per kind. Short because it appears in every id, in every
#: path description, in every trace line. Stable because changing one renames
#: every node of that kind and orphans every stored edge.
PREFIX: dict[NodeKind, str] = {
    NodeKind.COMPANY: "CO",
    NodeKind.SECTOR: "SEC",
    NodeKind.SUBSECTOR: "SUB",
    NodeKind.COUNTRY: "CN",
    NodeKind.PRODUCT: "PR",
    NodeKind.TECHNOLOGY: "TE",
    NodeKind.COMMODITY: "CM",
    NodeKind.REGULATOR: "RG",
    NodeKind.EVENT: "EV",
    NodeKind.DOCUMENT: "DOC",
}

#: An instrument id as this repo writes them: a market prefix, a colon, a code.
_INSTRUMENT = re.compile(r"^([A-Za-z]{2,8}):([A-Za-z0-9.\-]{1,16})$")
_NON_WORD = re.compile(r"[^\w]+")

MAX_FOLD_PASSES = 8


class IdError(ValueError):
    """A raw string cannot be turned into an id."""


def _check_prefixes() -> None:
    missing = sorted(k.value for k in NodeKind if k not in PREFIX)
    if missing:
        raise RuntimeError(
            f"ids.PREFIX has no entry for {', '.join(missing)}. Every NodeKind needs "
            "one, or an extractor mints an id no other producer can reproduce."
        )
    seen: dict[str, NodeKind] = {}
    for kind, p in PREFIX.items():
        if p in seen:
            raise RuntimeError(
                f"ids.PREFIX gives {p!r} to both {seen[p].value} and {kind.value}; "
                "two kinds sharing a prefix makes their ids collide."
            )
        seen[p] = kind


_check_prefixes()


def fold(raw: str) -> str:
    """NFKC + casefold, iterated to convergence. See the module docstring.

    Bounded rather than `while True`: a pathological input that never settles
    should raise here, not hang a build.
    """
    prev = None
    out = raw
    for _ in range(MAX_FOLD_PASSES):
        if out == prev:
            return out
        prev = out
        out = unicodedata.normalize("NFKC", out).casefold()
    if out != prev:
        raise IdError(f"{raw!r} does not reach a stable normal form in {MAX_FOLD_PASSES} passes")
    return out


def slug(raw: str) -> str:
    """Fold first, THEN strip non-word characters. Order is load-bearing."""
    s = _NON_WORD.sub("_", fold(raw)).strip("_")
    if not s:
        raise IdError(f"{raw!r} contains no word characters and cannot become an id")
    return s


@lru_cache(maxsize=1)
def aliases() -> dict[str, str]:
    """Surface form -> instrument id, from data/entities.yaml.

    Deliberately the same shape as the `entity_index` that
    knowledge/feeds/adapter.link_entities already takes, so one checked-in file
    can serve both rather than two lists drifting apart.
    """
    if not ENTITIES_FILE.exists():
        return {}
    raw = yaml.safe_load(ENTITIES_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for iid, surfaces in (raw.get("companies") or {}).items():
        out[fold(iid)] = iid
        for surface in surfaces or []:
            out[fold(str(surface))] = iid
    return out


@lru_cache(maxsize=1)
def display_names() -> dict[str, str]:
    """Instrument id -> the name a person would read.

    The FIRST surface form in entities.yaml, by convention: the list is written
    longest-known-name-last, so the first entry is the short form a headline
    uses. Without this every company in the graph is labelled with its stock
    code, and a path reads `1155 --competes_with--> 1023`.

    Keyed by the CANONICAL instrument id, not the spelling the file happens to
    use. entities.yaml writes `MYX:1155` while ids resolve to `XKLS:1155`, and
    keying on the raw form is a lookup that silently never matches.
    """
    if not ENTITIES_FILE.exists():
        return {}
    raw = yaml.safe_load(ENTITIES_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for iid, surfaces in (raw.get("companies") or {}).items():
        if not surfaces:
            continue
        out[instrument_id(str(iid)) or str(iid)] = str(surfaces[0])
    return out


@lru_cache(maxsize=1)
def search_names() -> dict[str, tuple[str, ...]]:
    """Instrument id -> EVERY name it is written about under.

    `display_names` answers "what do I call this company"; this answers "what
    might a headline call it", and the difference is a coverage gap that ran
    for a week. A per-name source was asked for the display name alone, so
    Petronas Chemicals was searched as "Petronas Chemicals" and never as
    "PCHEM" - the form the Malaysian press actually prints. The linker has
    always known both, because `aliases()` reads the whole list: the corpus
    could RECOGNISE a name it never ASKED for.

    Keyed canonically, like `display_names`, for the same reason: entities.yaml
    writes `MYX:1155` and ids resolve to `XKLS:1155`.
    """
    if not ENTITIES_FILE.exists():
        return {}
    raw = yaml.safe_load(ENTITIES_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, tuple[str, ...]] = {}
    for iid, surfaces in (raw.get("companies") or {}).items():
        names = tuple(dict.fromkeys(str(s) for s in (surfaces or []) if str(s).strip()))
        if names:
            out[instrument_id(str(iid)) or str(iid)] = names
    return out


def instrument_id(raw: str) -> str | None:
    """The canonical `MIC:CODE` for anything naming a company, or None.

    A market prefix is resolved through markets.registry so MYX and XKLS land on
    one id - reuse, not a second alias table, because that resolver exists
    precisely because the two drifting apart sized every Bursa position against
    the wrong cost floor.
    """
    from markets.registry import resolve_mic

    text = raw.strip()
    m = _INSTRUMENT.match(text)
    if m:
        return f"{resolve_mic(m.group(1))}:{m.group(2).upper()}"
    known = aliases().get(fold(text))
    if known is None:
        return None
    m = _INSTRUMENT.match(known)
    return f"{resolve_mic(m.group(1))}:{m.group(2).upper()}" if m else known


def node_id(kind: NodeKind, raw: str) -> str:
    """`(COMPANY, 'Maybank')` -> `CO:XKLS:1155`. The only way an id is minted."""
    if not isinstance(raw, str) or not raw.strip():
        raise IdError(f"cannot mint a {kind.value} id from {raw!r}")
    prefix = PREFIX[kind]
    body = raw.strip()

    # Idempotence: an id that already carries this prefix is returned as it is,
    # never re-slugged. Without this, node_id(k, node_id(k, x)) drifts on the
    # second call and half the producers disagree with the other half.
    if body.upper().startswith(prefix + ":"):
        body = body[len(prefix) + 1 :]
        if kind is NodeKind.COMPANY:
            iid = instrument_id(body)
            return f"{prefix}:{iid}" if iid else f"{prefix}:{slug(body)}"
        return f"{prefix}:{slug(body)}"

    if kind is NodeKind.COMPANY:
        iid = instrument_id(body)
        if iid:
            return f"{prefix}:{iid}"
    return f"{prefix}:{slug(body)}"


def label_for(kind: NodeKind, raw: str) -> str:
    """The human-readable name to store beside the id. Never used for identity."""
    return " ".join(raw.strip().split())


def kind_of(node_id_: str) -> NodeKind | None:
    """Read the kind back off an id. None if the prefix is not one of ours."""
    head, _, rest = node_id_.partition(":")
    if not rest:
        return None
    for kind, prefix in PREFIX.items():
        if head == prefix:
            return kind
    return None
