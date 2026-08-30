"""The curated files, read as a corpus.

An extracted edge cites `curated:supply_chain#misc-pchem-marine`. Something has
to be able to hand back the text behind that id, or `path_to_citations` refuses
the path and A7's finding dies at the output gate - which is the phase-1 blocker
all over again, one layer out.

WHAT IS BEING QUOTED, AND WHY THAT IS HONEST. The curated yaml IS the source
document: a person wrote the relationship down and vouches for it, and
`created_by: human` in agents/registry.yaml is what says so. The sentence this
module renders is that row formatted for reading, derived from it by a fixed
rule with nothing added. It is not a filing, and it never claims to be - the
trust tier is METHOD_KB, four steps below FILINGS, so a curated edge can never
outrank a document from the company itself.

The round trip is real, not decorative: the citation's quoted_span is checked
back against this text by verify_claim, exactly as a filing chunk would be. A
row edited after a claim cited it fails verification, which is the point.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.contracts.answer import Citation, TrustTier
from knowledge.graph.extractors.curated import DATA as SUPPLY_CHAIN
from knowledge.graph.extractors.sectors import DATA as SECTORS
from knowledge.graph.ids import display_names, instrument_id, kind_of

SOURCE = "kb_supply_chain"

#: Human-authored method knowledge. Deliberately below FILINGS: a curated
#: relationship must never outrank what a company said about itself.
TRUST = TrustTier.METHOD_KB


def _name(raw: str, labels: dict[str, str] | None = None) -> str:
    """A readable name for either an instrument id or an already-minted node id.

    The order matters: `CM:aluminium` matches the shape of an instrument id
    (two-letter prefix, a code) and would come back as `CM:ALUMINIUM` if it went
    through the market resolver first.
    """
    if labels and raw in labels:
        return labels[raw]
    if kind_of(raw) is not None:
        return raw.split(":", 1)[1].replace("_", " ")
    canon = instrument_id(raw) or raw
    return display_names().get(canon, canon)


class CuratedCorpus:
    """Text behind every `curated:*` document id the extractors mint."""

    def __init__(self, supply_chain: Path | str = SUPPLY_CHAIN,
                 sectors: Path | str = SECTORS) -> None:
        self._chunks: dict[str, str] = {}
        self._load_supply_chain(Path(supply_chain))
        self._load_sectors(Path(sectors))

    def _load_supply_chain(self, path: Path) -> None:
        if not path.exists():
            return
        raw = yaml.safe_load(path.read_text()) or {}
        labels = {k: str(v) for k, v in (raw.get("commodities") or {}).items()}
        for row in raw.get("edges") or []:
            rid = row.get("id")
            if not rid:
                continue
            note = " ".join(str(row.get("note", "")).split())
            stated = (f"{_name(row['source'], labels)} "
                      f"{row['relation'].replace('_', ' ')} "
                      f"{_name(row['target'], labels)}.")
            self._chunks[f"curated:supply_chain#{rid}"] = (
                f"{stated} {note}".strip())

    def _load_sectors(self, path: Path) -> None:
        if not path.exists():
            return
        raw = yaml.safe_load(path.read_text()) or {}
        parent = {sub: sector
                  for sector, subs in (raw.get("sectors") or {}).items()
                  for sub in subs or []}
        for sub, sector in parent.items():
            self._chunks[f"curated:sectors#SUB:{_slug(sub)}"] = (
                f"The {sub} sub-sector is classified within {sector}.")
        for iid, spec in (raw.get("companies") or {}).items():
            cid = f"CO:{instrument_id(str(iid)) or iid}"
            self._chunks[f"curated:sectors#{cid}"] = (
                f"{_name(str(iid))} is classified within the "
                f"{spec['subsector']} sub-sector.")

    # -- the two seams A7 needs ----------------------------------------------

    def chunk(self, source: str, chunk_id: str) -> str | None:
        """(source, chunk_id) -> text. What verify_claim rechecks against."""
        return self._chunks.get(chunk_id) if source == SOURCE else None

    def citation(self, doc_id: str, as_of: datetime | None = None) -> Citation | None:
        """source_doc_id -> Citation, or None when the corpus cannot produce it.

        The span is the whole row. A curated row is one sentence long, so there
        is no shorter honest excerpt - and quoting a fragment would let the rest
        of the row change without failing verification.
        """
        text = self._chunks.get(doc_id)
        if not text:
            return None
        return Citation(source=SOURCE, chunk_id=doc_id, quoted_span=text,
                        trust=TRUST, as_of=as_of or datetime.now(timezone.utc))

    def __len__(self) -> int:
        return len(self._chunks)


def _slug(raw: str) -> str:
    from knowledge.graph.ids import slug
    return slug(raw)
