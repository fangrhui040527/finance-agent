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


def _excerpt(text: str, limit: int = 200) -> str:
    """A useful first look at a body that would not parse.

    `text[:120]` was the whole diagnostic and it is worth nothing on the case
    that actually happens: a URL that serves a web page rather than a feed
    begins with a long run of newlines and indentation, so the excerpt was 120
    literal "\n" and the reader learned only that parsing failed.

    Collapsing whitespace first shows the doctype and title, which names the
    problem outright. Measured 2026-09-03 on bnm.gov.my/rss, where the useful
    signal started at line 123.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return "<empty body>"
    head = collapsed[:limit]
    looks_html = collapsed[:400].lower().lstrip().startswith(("<!doctype html", "<html"))
    if not looks_html:
        return repr(head)
    found = advertised_feeds(text)
    if found:
        # The page says where its feeds are; repeating the guess back at the
        # reader would waste the one thing it offered.
        return "an HTML page which advertises feeds at: " + ", ".join(found[:6])
    return f"an HTML page, not a feed (and it advertises none): {head!r}"


def advertised_feeds(html: str) -> list[str]:
    """Feed URLs a page declares, via the RSS autodiscovery <link> tag.

    A URL that serves a landing page instead of a feed is the ordinary way this
    goes wrong - bnm.gov.my/rss on 2026-09-03 was exactly that - and the page
    almost always names the real feed in its head. Reading it turns "this is
    not a feed" into "the feed is here", which is the difference between an
    error you act on and one you guess at.

    Diagnostic only: nothing follows these automatically. A feed URL is a
    decision about what the system ingests, and it belongs in the registry
    where a person put it, not in a redirect chased at runtime.
    """
    import re

    out: list[str] = []
    for tag in re.findall(r"<link\b[^>]*>", html, flags=re.I):
        if not re.search(r'type\s*=\s*["\']application/(rss|atom)\+xml["\']', tag, flags=re.I):
            continue
        href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, flags=re.I)
        if href and href.group(1) not in out:
            out.append(href.group(1))
    return out


class RssFeed(FeedAdapter):
    """One RSS 2.0 or Atom feed, by URL."""

    trust = "general_news"
    cadence = timedelta(minutes=30)
    TIMEOUT = 30
    MAX_BYTES = 5_000_000  # a "feed" larger than this is not a feed

    #: Items the last parse dropped for carrying no date. A feed that dates
    #: SOME of its items is usable and lossy; one that dates none is refused.
    undated = 0

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
            raise FeedError(f"{self.name} returned unparseable XML ({e}): {_excerpt(text)}") from e

        items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        out: list[RawRecord] = []
        now = datetime.now(UTC)
        self.undated = 0
        usable = 0
        for item in items:
            row = self._item_to_row(item)
            if row is None:
                continue
            usable += 1
            published = row.get("published_at")
            if published is None:
                # An item with no date is not an item published NOW. Dating it
                # `now` is the one thing this must never do - see _to_article.
                self.undated += 1
                continue
            if datetime.fromisoformat(published) < since:
                continue
            out.append(RawRecord(self.name, row["url"], now, row))
            if len(out) >= limit:
                break

        if usable and self.undated == usable:
            raise FeedError(
                f"{self.name} dates none of its {usable} items, so nothing here can be "
                f"placed in time. Every item would be stamped with the moment it was "
                f"fetched, which is how an archive enters a corpus as today's news. "
                f"Refused rather than ingested: {_excerpt(text)}"
            )
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
        """An undated record never reaches here - `_parse` drops it.

        It used to fall back to `rec.fetched_at`, which reads as a reasonable
        default and is a fabrication: it asserts the item was published at the
        moment we happened to ask. The BNM 2020 archive is the case that shows
        the cost - valid RSS, no <pubDate> on any item, so a nightly sweep would
        have entered six-year-old central bank releases dated today, on the
        newest end of every window, indistinguishable from real news.
        """
        p = rec.payload
        if not p.get("published_at"):
            raise FeedError(
                f"{self.name} produced an undated record for {p.get('url')!r}; "
                f"_parse must drop these rather than let them be dated on arrival"
            )
        published = datetime.fromisoformat(p["published_at"])
        return Article(
            doc_id=str(p["url"]),
            title=str(p["title"]),
            body=str(p.get("body") or ""),
            source_domain=str(p.get("domain") or ""),
            published_at=published,
        )
