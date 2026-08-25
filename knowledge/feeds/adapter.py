"""Feed adapters — the L2 source-plugin seam.

docs/01 section 10: every source implements fetch() -> RawRecord[] and
normalize() -> Silver rows. A new feed is a new adapter and nothing downstream
changes.

The adapters here are offline by construction. A live one subclasses FeedAdapter,
implements _fetch_raw against its API, and inherits every normalisation,
deduplication and provenance rule below. That is the whole integration surface.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from knowledge.news.features import (
    Article, FeatureExtractor, LexiconExtractor, near_duplicate_hash,
)


@dataclass(frozen=True)
class RawRecord:
    """Bronze layer: the payload exactly as the source returned it."""

    source: str
    external_id: str
    fetched_at: datetime
    payload: dict

    def checksum(self) -> str:
        return near_duplicate_hash(json.dumps(self.payload, sort_keys=True))


@dataclass
class IngestStats:
    fetched: int = 0
    duplicates: int = 0
    unlinked: int = 0
    kept: int = 0
    escalated: int = 0

    def __str__(self) -> str:
        return (f"fetched {self.fetched}, kept {self.kept}, "
                f"duplicates {self.duplicates}, unlinked {self.unlinked}, "
                f"escalated {self.escalated}")


class FeedAdapter(ABC):
    """One source. Subclass and implement _fetch_raw; inherit everything else."""

    name: str
    trust: str = "general_news"     # keys into catalyst.SOURCE_TRUST
    cadence: timedelta = timedelta(minutes=15)

    def __init__(self, extractor: FeatureExtractor | None = None) -> None:
        self.extractor = extractor or LexiconExtractor()
        self._seen: set[str] = set()

    @abstractmethod
    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]: ...

    @abstractmethod
    def _to_article(self, rec: RawRecord) -> Article | None: ...

    def fetch(self, since: datetime, limit: int = 500) -> list[RawRecord]:
        return self._fetch_raw(since, limit)

    def normalize(
        self,
        records: list[RawRecord],
        entity_index: dict[str, str] | None = None,
        holdings: set[str] | None = None,
        watchlist: set[str] | None = None,
    ) -> tuple[list[Article], IngestStats]:
        """Bronze -> Silver. Dedup BEFORE indexing, link entities, extract features."""
        from knowledge.news.features import should_escalate

        stats = IngestStats(fetched=len(records))
        out: list[Article] = []
        for rec in records:
            art = self._to_article(rec)
            if art is None:
                continue

            dup = near_duplicate_hash(art.text)
            if dup in self._seen:
                stats.duplicates += 1
                continue
            self._seen.add(dup)
            art.dup_hash = dup

            if entity_index and not art.instruments:
                art.instruments = link_entities(art.text, entity_index)
            if not art.instruments:
                stats.unlinked += 1

            art.features = self.extractor.extract(art.text, art.instruments)
            if should_escalate(art.features, art.instruments,
                               holdings or set(), watchlist or set()):
                stats.escalated += 1
            out.append(art)
            stats.kept += 1
        return out, stats


def link_entities(text: str, index: dict[str, str]) -> list[str]:
    """Surface form -> instrument_id. Longest match first so 'Maybank Islamic'
    does not resolve as 'Maybank'."""
    low = text.lower()
    hits: list[str] = []
    for surface in sorted(index, key=len, reverse=True):
        if surface.lower() in low:
            iid = index[surface]
            if iid not in hits:
                hits.append(iid)
    return hits


class FixtureFeed(FeedAdapter):
    """Reads newline-delimited JSON from disk. The offline default.

    Also the CI fixture path: docs/12 section 2.5 requires the pipeline to be
    verifiable on mock data with no network and no keys.
    """

    name = "fixture"
    trust = "curated_news"

    def __init__(self, path: Path | str | None = None, records: list[dict] | None = None,
                 extractor: FeatureExtractor | None = None) -> None:
        super().__init__(extractor)
        self._path = Path(path) if path else None
        self._inline = records or []

    def _rows(self) -> list[dict]:
        if self._inline:
            return self._inline
        if self._path and self._path.exists():
            return [json.loads(l) for l in self._path.read_text().splitlines() if l.strip()]
        return []

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        out = []
        for row in self._rows()[:limit]:
            ts = datetime.fromisoformat(row["published_at"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= since:
                out.append(RawRecord(self.name, row["id"], datetime.now(timezone.utc), row))
        return out

    def _to_article(self, rec: RawRecord) -> Article | None:
        p = rec.payload
        ts = datetime.fromisoformat(p["published_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return Article(
            doc_id=f"{self.name}:{p['id']}",
            title=p.get("title", ""),
            body=p.get("body", ""),
            source_domain=p.get("domain", "fixture.local"),
            published_at=ts,
            language=p.get("language", "en"),
            countries=p.get("countries", []),
            instruments=p.get("instruments", []),
            sectors=p.get("sectors", []),
            themes=p.get("themes", []),
        )


class GdeltFeed(FeedAdapter):
    """GDELT 2.0. Free, no key, 15-minute cadence.

    _fetch_raw is the ONLY method a live implementation needs to replace; it is
    left unimplemented so the offline build cannot silently pretend to have data.
    """

    name = "gdelt"
    trust = "general_news"
    cadence = timedelta(minutes=15)
    DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        raise NotImplementedError(
            "live GDELT ingest is not wired. Implement _fetch_raw against "
            f"{self.DOC_API}, or use FixtureFeed offline. Everything downstream "
            "of this method is built and tested."
        )

    def _to_article(self, rec: RawRecord) -> Article | None:
        p = rec.payload
        ts = datetime.fromisoformat(p["seendate"]) if "seendate" in p else rec.fetched_at
        return Article(
            doc_id=f"gdelt:{p.get('url', rec.external_id)}",
            title=p.get("title", ""),
            body=p.get("body", p.get("title", "")),
            source_domain=p.get("domain", "unknown"),
            published_at=ts,
            language=p.get("language", "en"),
            countries=p.get("sourcecountry", "").split(",") if p.get("sourcecountry") else [],
            themes=p.get("themes", "").split(";") if p.get("themes") else [],
        )


REGISTRY: dict[str, type[FeedAdapter]] = {"fixture": FixtureFeed, "gdelt": GdeltFeed}
