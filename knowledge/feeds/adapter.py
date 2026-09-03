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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.net.breaker import CircuitBreaker
from knowledge.news.features import (
    Article,
    FeatureExtractor,
    LexiconExtractor,
    near_duplicate_hash,
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
        return (
            f"fetched {self.fetched}, kept {self.kept}, "
            f"duplicates {self.duplicates}, unlinked {self.unlinked}, "
            f"escalated {self.escalated}"
        )


class FeedAdapter(ABC):
    """One source. Subclass and implement _fetch_raw; inherit everything else."""

    name: str
    trust: str = "general_news"  # keys into catalyst.SOURCE_TRUST
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
            if should_escalate(
                art.features, art.instruments, holdings or set(), watchlist or set()
            ):
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

    def __init__(
        self,
        path: Path | str | None = None,
        records: list[dict] | None = None,
        extractor: FeatureExtractor | None = None,
    ) -> None:
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
                ts = ts.replace(tzinfo=UTC)
            if ts >= since:
                out.append(RawRecord(self.name, row["id"], datetime.now(UTC), row))
        return out

    def _to_article(self, rec: RawRecord) -> Article | None:
        p = rec.payload
        ts = datetime.fromisoformat(p["published_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
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


class FeedError(RuntimeError):
    """A source could not be read. Deliberately NOT an empty result.

    The distinction this whole class exists to preserve: a feed that is broken
    must never be indistinguishable from a feed reporting a quiet hour. Silence
    is an answer the system is entitled to act on; failure is not.
    """


class GdeltFeed(FeedAdapter):
    """GDELT 2.0 DOC API. Free, no key, 15-minute cadence, 100+ languages.

    The widest free net in the world and the reason it is wired first: no
    signup, no quota, and coverage of markets no vendor sells cheaply.

    Two properties worth stating because they are easy to break later:

      1. A malformed response RAISES. GDELT answers errors with plain text
         rather than JSON ("your query was too short"), so a decode failure is
         a real failure and is reported as one. An explicitly empty article
         list is NOT a failure - it is a quiet window, and returns [].
      2. timespan is floored at the documented 15-minute minimum. Asking for
         less returns an error, and a caller polling on a fast loop would
         otherwise turn its own impatience into an outage.
    """

    name = "gdelt"
    trust = "general_news"
    cadence = timedelta(minutes=15)
    DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

    #: Below this the API refuses the query outright.
    #: The DOC API answers "Timespan is too short." - as plain text, not JSON -
    #: below its own minimum, and 15 minutes is under it: measured 2026-09-03,
    #: a sweep resuming from a watermark 25 minutes old was refused outright.
    #:
    #: Two hours rather than the exact minimum on purpose. The boundary is not
    #: documented and guessing it costs a whole failed sweep, while asking for a
    #: WIDER window than needed costs nothing: the corpus dedupes on dup_hash,
    #: so the overlap is discarded on arrival. A daily schedule never comes near
    #: this - it resumes from ~24h - but a manual run after one, or a retry
    #: after a failure, lands inside the hour every time.
    MIN_TIMESPAN = timedelta(hours=2)
    #: One page. Paging past this is a later problem; over-asking is refused.
    MAX_RECORDS = 250
    # 90, not 30. Measured on the 2026-09-03 runs: the DOC API answered once in
    # ~38s and then exceeded a 30s socket read twice. This is a slow service,
    # not a broken one, and a timeout under its normal response time turns every
    # sweep into a recorded failure. `with_retry` makes 3 attempts, so the worst
    # case is ~4.5min - inside the collect job's 15min cap.
    TIMEOUT = 90
    DEFAULT_USER_AGENT = "finplanet-analyst-mind/0.1 (personal research)"

    def __init__(
        self,
        query: str = "",
        languages: tuple[str, ...] = (),
        countries: tuple[str, ...] = (),
        user_agent: str = "",
        opener=None,
        sleep=None,
        extractor: FeatureExtractor | None = None,
    ) -> None:
        import os

        super().__init__(extractor)
        self.query = query
        self.languages = languages
        self.countries = countries
        # GDELT_USER_AGENT should carry a real contact address. Optional here,
        # mandatory at EDGAR next, so the habit is worth forming on the easy one.
        self.user_agent = user_agent or os.environ.get("GDELT_USER_AGENT", self.DEFAULT_USER_AGENT)
        # Measured 2026-08-31: from one network api.gdeltproject.org answers on
        # port 80 and times out on 443. The operator gets a switch, with the
        # trade-off named in .env.example: plain HTTP has no transport integrity.
        self.doc_api = os.environ.get("GDELT_DOC_API", "").strip() or self.DOC_API
        self._opener = opener
        self._sleep = sleep
        self._breaker = CircuitBreaker("gdelt")

    def _timespan(self, since: datetime, now: datetime | None = None) -> str:
        """Whole minutes back from now, never under the documented minimum."""
        now = now or datetime.now(UTC)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        span = max(now - since, self.MIN_TIMESPAN)
        return f"{max(1, -(-int(span.total_seconds()) // 60))}min"

    def _url(
        self,
        since: datetime,
        limit: int,
        window: tuple[datetime, datetime] | None = None,
    ) -> str:
        from urllib.parse import urlencode

        terms = [self.query] if self.query else []
        if self.languages:
            terms.append("(" + " OR ".join(f"sourcelang:{c}" for c in self.languages) + ")")
        if self.countries:
            terms.append("(" + " OR ".join(f"sourcecountry:{c}" for c in self.countries) + ")")

        params = {
            "query": " ".join(terms) if terms else "domainis:reuters.com",
            "mode": "artlist",
            "format": "json",
            "sort": "datedesc",
            "maxrecords": min(max(1, limit), self.MAX_RECORDS),
        }
        if window is not None:
            # The DOC API has no cursor. Paging is done by slicing TIME:
            # startdatetime/enddatetime bound one slice each.
            start, end = window
            params["startdatetime"] = start.astimezone(UTC).strftime("%Y%m%d%H%M%S")
            params["enddatetime"] = end.astimezone(UTC).strftime("%Y%m%d%H%M%S")
        else:
            params["timespan"] = self._timespan(since)
        return f"{self.doc_api}?{urlencode(params)}"

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        if limit <= self.MAX_RECORDS:
            return self._fetch_page(self._url(since, limit))
        # More than one page: slice [since, now] into equal windows and dedupe
        # by url. GDELT has no cursor - time is the only pagination there is.
        now = datetime.now(UTC)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        pages = -(-limit // self.MAX_RECORDS)
        step = (now - since) / pages
        seen: set[str] = set()
        out: list[RawRecord] = []
        for i in range(pages):
            lo, hi = since + step * i, since + step * (i + 1)
            for rec in self._fetch_page(self._url(since, self.MAX_RECORDS, window=(lo, hi))):
                key = str(rec.payload.get("url", ""))
                if key and key in seen:
                    continue
                seen.add(key)
                out.append(rec)
                if len(out) >= limit:
                    return out
        return out

    def _fetch_page(self, url: str) -> list[RawRecord]:
        import urllib.error
        import urllib.request

        from core.net.breaker import CircuitOpen
        from core.net.retry import with_retry

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})

        def _transport():
            with opener(req, timeout=self.TIMEOUT) as resp:
                return resp.read()

        kwargs = {"sleep": self._sleep} if self._sleep is not None else {}
        try:
            self._breaker.before_call()
            body = with_retry(_transport, **kwargs)
        except CircuitOpen as e:
            raise FeedError(str(e)) from e
        except urllib.error.URLError as e:  # includes HTTPError after retries
            self._breaker.record_failure(e)
            raise FeedError(f"GDELT fetch failed: {e}") from e
        except OSError as e:
            self._breaker.record_failure(e)
            raise FeedError(f"GDELT fetch failed: {e}") from e
        self._breaker.record_success()

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise FeedError(
                f"GDELT returned non-JSON, which is how it reports errors: {body[:200]!r}"
            ) from e

        if not isinstance(payload, dict) or "articles" not in payload:
            raise FeedError(f"GDELT response has no articles key: {str(payload)[:200]!r}")

        rows = payload["articles"]
        if not isinstance(rows, list):
            raise FeedError(f"GDELT articles is not a list: {type(rows).__name__}")

        fetched = datetime.now(UTC)
        return [
            RawRecord(self.name, row.get("url", ""), fetched, row)
            for row in rows
            if isinstance(row, dict)
        ]

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


def _smoke(argv: list[str] | None = None) -> int:
    """Hand-run smoke test. Never in CI - CI stays offline by design.

    python -m knowledge.feeds.adapter --gdelt --minutes 60 --limit 5
    """
    import argparse

    ap = argparse.ArgumentParser(description="Pull live headlines and print them.")
    ap.add_argument("--gdelt", action="store_true", help="use the GDELT DOC API")
    ap.add_argument("--minutes", type=int, default=60, help="how far back to look")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--query", default="", help="GDELT query terms, optional")
    ap.add_argument("--lang", default="", help="comma-separated source languages")
    args = ap.parse_args(argv)

    if not args.gdelt:
        ap.error("no feed selected; pass --gdelt")

    feed = GdeltFeed(
        query=args.query,
        languages=tuple(c for c in args.lang.split(",") if c),
    )
    since = datetime.now(UTC) - timedelta(minutes=args.minutes)
    records = feed.fetch(since, limit=args.limit)
    articles, stats = feed.normalize(records)

    for a in articles:
        print(f"[{a.language:>3}] {','.join(a.countries) or '--':<12} {a.source_domain}")
        print(f"      {a.title[:100]}")
    print(f"\n{stats}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_smoke())
