"""A generic RSS/Atom adapter: one class that covers most of the keyless 32.

docs/world-sources.html registers 33 keyless sources; most of them publish
RSS or Atom. Rather than 32 bespoke adapters - a queue nobody reads - this is
ONE class taking a feed URL, holding the same contract as GDELT: a broken
source raises, an explicitly quiet window returns few items, and nothing is
guessed. stdlib `xml.etree` only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from core.net.breaker import CircuitBreaker
from knowledge.feeds.adapter import FeedAdapter, FeedError, RawRecord
from knowledge.news.features import Article, FeatureExtractor

#: Tags searched for each field, RSS 2.0 first, then Atom. Namespaced Atom tags
#: appear with their URI in ElementTree.
ATOM = "{http://www.w3.org/2005/Atom}"


class RssFeed(FeedAdapter):
    """One RSS 2.0 or Atom feed, by URL."""

    trust = "general_news"
    cadence = timedelta(minutes=30)
    TIMEOUT = 30
    MAX_BYTES = 5_000_000  # a "feed" larger than this is not a feed

    def __init__(
        self,
        url: str,
        name: str = "",
        trust: str = "general_news",
        opener=None,
        sleep=None,
        extractor: FeatureExtractor | None = None,
    ) -> None:
        super().__init__(extractor)
        host = urlparse(url).netloc
        if not host:
            raise ValueError(f"{url!r} is not an absolute feed URL")
        self.url = url
        self.name = name or f"rss:{host}"
        self.trust = trust
        self._opener = opener
        self._sleep = sleep
        self._breaker = CircuitBreaker(self.name)

    # -- transport -----------------------------------------------------------

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        import urllib.error
        import urllib.request

        from core.net.breaker import CircuitOpen
        from core.net.retry import with_retry

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            self.url,
            headers={
                "User-Agent": "finplanet-analyst-mind/0.1 (personal research)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
            },
        )

        def _transport() -> bytes:
            with opener(req, timeout=self.TIMEOUT) as resp:
                return resp.read(self.MAX_BYTES)

        kwargs = {"sleep": self._sleep} if self._sleep is not None else {}
        try:
            self._breaker.before_call()
            body = with_retry(_transport, **kwargs)
        except CircuitOpen as e:
            raise FeedError(str(e)) from e
        except (urllib.error.URLError, OSError) as e:
            self._breaker.record_failure(e)
            raise FeedError(f"{self.name} fetch failed: {e}") from e
        self._breaker.record_success()

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return self._parse(body, since, limit)

    # -- parsing -------------------------------------------------------------

    def _parse(self, text: str, since: datetime, limit: int) -> list[RawRecord]:
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(text.strip())
        except ET.ParseError as e:
            raise FeedError(f"{self.name} returned unparseable XML ({e}): {text[:120]!r}") from e

        items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        out: list[RawRecord] = []
        now = datetime.now(UTC)
        for item in items:
            row = self._item_to_row(item)
            if row is None:
                continue
            published = row.get("published_at")
            if published is not None and datetime.fromisoformat(published) < since:
                continue
            out.append(RawRecord(self.name, row["url"], now, row))
            if len(out) >= limit:
                break
        return out

    def _item_to_row(self, item) -> dict | None:
        """One <item>/<entry> to a plain payload dict, or None if unusable."""

        def first(*tags: str) -> str:
            for tag in tags:
                node = item.find(tag)
                if node is None:
                    continue
                if tag == f"{ATOM}link":
                    href = node.get("href")
                    if href:
                        return href.strip()
                if node.text:
                    return node.text.strip()
            return ""

        title = first("title", f"{ATOM}title")
        link = first("link", f"{ATOM}link") or first("guid", f"{ATOM}id")
        if not title or not link:
            return None  # an item you can neither cite nor read is not news
        body = first("description", f"{ATOM}summary", f"{ATOM}content")

        raw_date = first("pubDate", f"{ATOM}updated", f"{ATOM}published")
        published: datetime | None = None
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)  # RFC 822 (RSS)
            except (TypeError, ValueError):
                try:
                    published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except ValueError:
                    published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=UTC)

        return {
            "url": link,
            "title": title,
            "body": body,
            "domain": urlparse(link).netloc or urlparse(self.url).netloc,
            "published_at": published.isoformat() if published else None,
        }

    def _to_article(self, rec: RawRecord) -> Article | None:
        p = rec.payload
        published = (
            datetime.fromisoformat(p["published_at"]) if p.get("published_at") else rec.fetched_at
        )
        return Article(
            doc_id=str(p["url"]),
            title=str(p["title"]),
            body=str(p.get("body") or ""),
            source_domain=str(p.get("domain") or ""),
            published_at=published,
        )
