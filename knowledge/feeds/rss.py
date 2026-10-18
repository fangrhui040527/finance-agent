"""A generic RSS/Atom adapter: one class that covers most of the keyless 32.

docs/world-sources.html registers 33 keyless sources; most of them publish
RSS or Atom. Rather than 32 bespoke adapters - a queue nobody reads - this is
ONE class taking a feed URL, holding the same contract as GDELT: a broken
source raises, an explicitly quiet window returns few items, and nothing is
guessed. stdlib `xml.etree` only.
"""

from __future__ import annotations

import http.client
from collections.abc import Callable
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

    #: Set by the last parse when the feed could not reach back to `since`:
    #: every dated item was newer than the window's start, so whatever was
    #: published between the two had already scrolled off. A site-wide feed
    #: holds a fixed number of items (NST's is 50), and polled once a day it
    #: overflows: from 2026-09-14 every nst_business run fetched exactly 50,
    #: each batch starting 16 to 25 hours after the previous run, and the
    #: sweep recorded `ok`. Empty when the window was covered.
    overflow = ""

    #: A feed with fewer dated items than this is not judged: a short feed
    #: whose items all fall inside the window is more often a quiet one than a
    #: full one, and the parse cannot tell which.
    OVERFLOW_MIN_ITEMS = 10
    #: How far the oldest item may sit past `since` before it is a hole.
    OVERFLOW_TOLERANCE = timedelta(hours=1)

    #: The most pages one fetch may read, whatever a feed asks for. NST's site
    #: feed is 50 items, about 3.2 hours of its output, and the Bursa slots are
    #: most of a day apart, so even five pages may not cover a window - which
    #: the overflow note then says. A ceiling, not a default: see `pages`.
    MAX_PAGES = 5

    def __init__(
        self,
        url: str,
        name: str = "",
        trust: str = "general_news",
        opener=None,
        sleep=None,
        extractor: FeatureExtractor | None = None,
        pages: int = 1,
        page_param: str = "paged",
        first_page: int = 1,
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
        # Paging is OPT-IN, per feed, and one page unless a feed asks. Whether a
        # publisher's feed pages at all, and in which convention - WordPress
        # counts `paged` from 1, Drupal counts `page` from 0 - can only be read
        # off the publisher, and a site that ignores the parameter answers with
        # its first page again. So no registry entry turns this on until
        # .github/scripts/bursa_feeds_probe.py has shown a second page of OLDER
        # items from a runner; this environment cannot reach the hosts.
        self.pages = max(1, min(int(pages), self.MAX_PAGES))
        self.page_param = page_param
        self.first_page = first_page
        #: Asked before each page after the first: True once the sweep's own
        #: deadline has passed (knowledge/sweep.py sets it), so a later page is
        #: never what runs a sweep past SWEEP_DEADLINE_SECONDS. None: no limit.
        self.stop_paging: Callable[[], bool] | None = None

    # -- transport -----------------------------------------------------------

    def _fetch_raw(self, since: datetime, limit: int) -> list[RawRecord]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        out = self._parse(self._read(self.url), since, limit)
        if self.pages == 1:
            return out

        # Further pages only while the window is still open at the bottom, and
        # never past `self.pages`. The overflow is judged over every page read
        # so far, not page by page: a short last page is "too few items to
        # judge" on its own, and judging it alone would call the window covered
        # when it was not. A page that brings no item the earlier pages did not
        # is a site ignoring the parameter, or the end of its archive; either
        # way the next page would be the same answer again. Whatever is still
        # unread after the last page stays in `overflow`: degraded, not `ok`.
        #
        # A page's items must also JOIN the page before it. On the 2026-10-17
        # runner probe NST's first page ran 04:48Z-08:59Z and `?paged=2` ran
        # 12:51Z-23:01Z the day before - a cached page, by the look of it, and
        # 5.8 hours of items served by neither. Judged at the bottom alone, two
        # pages would have read "covered" over that hole. So a page whose newest
        # item is more than OVERFLOW_TOLERANCE older than the last page's oldest
        # is named in `overflow` too: its items are kept, and the hole is said.
        gap = self.overflow
        holes: list[str] = []
        known = {r.external_id for r in out}
        links = set(self._links)
        undated, dated, oldest = self.undated, self._dated, self._oldest
        read = 1
        while gap and read < self.pages and len(out) < limit:
            page = read + 1
            if self.stop_paging is not None and self.stop_paging():
                gap += f"; page {page} not asked: the sweep's deadline had passed"
                break
            try:
                more = self._parse(
                    self._read(self._page_url(read), diagnose=False), since, limit - len(out)
                )
            except FeedError as e:
                # The pages already read are kept: a 404 on page two says nothing
                # about page one, and failing the whole fetch would turn a
                # partial read into none.
                gap += f"; page {page} could not be read: {e}"
                break
            read = page
            undated += self.undated
            if not self._links - links:
                gap += (
                    f"; page {page} added nothing new, so this feed does not page "
                    f"by `{self.page_param}`"
                )
                break
            links |= self._links
            dated += self._dated
            if oldest is not None and self._newest is not None:
                apart = oldest - self._newest
                if apart > self.OVERFLOW_TOLERANCE:
                    holes.append(
                        f"page {page} starts {apart.total_seconds() / 3600:.1f}h before page "
                        f"{read - 1} ends ({self._newest.astimezone(UTC):%Y-%m-%d %H:%MZ} under "
                        f"{oldest.astimezone(UTC):%Y-%m-%d %H:%MZ}): the items between were "
                        "served by neither"
                    )
            if self._oldest is not None and (oldest is None or self._oldest < oldest):
                oldest = self._oldest
            for rec in more:
                if rec.external_id not in known:
                    known.add(rec.external_id)
                    out.append(rec)
            gap = "" if self._truncated else self._overflow_note(since, dated, oldest)
        else:
            if gap and read == self.pages:
                gap += f"; read {read} pages, the most this feed may"
        self.overflow = "; ".join(x for x in (gap, *holes) if x)
        self.undated = undated
        return out

    def _page_url(self, index: int) -> str:
        """Page `index` (0 is the feed itself) in the feed's own convention.

        Appended rather than re-encoded, so a registered URL carrying its own
        query string is sent exactly as a person registered it."""
        sep = "&" if urlparse(self.url).query else "?"
        return f"{self.url}{sep}{self.page_param}={self.first_page + index}"

    def _read(self, url: str, diagnose: bool = True) -> str:
        """One page's body as text. `diagnose` asks the site where its feeds
        are on a 404; a later page that 404s is a feed that does not page that
        way, not a feed that moved, so it is not asked there."""
        import urllib.error
        import urllib.request

        from core.net.breaker import CircuitOpen
        from core.net.retry import with_retry

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            url,
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
            moved = self._where_the_feed_moved(e) if diagnose else ""
            raise FeedError(f"{self.name} fetch failed: {e}{moved}") from e
        except http.client.HTTPException as e:  # a reply cut off mid-body is not an OSError
            self._breaker.record_failure(e)
            raise FeedError(f"{self.name} fetch failed: {type(e).__name__}: {e}") from e
        self._breaker.record_success()

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return body

    def _where_the_feed_moved(self, error) -> str:
        """On a 404, ask the SITE where its feeds are, and say so in the error.

        A registered feed URL rots. The Star, The Edge and the New Straits
        Times all answered 404 to the paths in the registry on 2026-09-04 and
        again on 2026-09-06, and the probe could report nothing but "404" -
        the autodiscovery reader below only ever saw a body, and a 404 has
        none. So the six Malaysian business feeds this book most needs stayed
        dead with no way to find where they went, from an environment that
        cannot browse.

        One extra request, only for a status that means "wrong path" (404 or
        410), only for the site root. Diagnostic: nothing follows the result
        automatically, for the reason `advertised_feeds` gives - a feed URL is
        a decision about what the system ingests and belongs in the registry
        where a person put it.
        """
        import urllib.error
        import urllib.request
        from urllib.parse import urlsplit

        if not isinstance(error, urllib.error.HTTPError) or error.code not in (404, 410):
            return ""
        parts = urlsplit(self.url)
        root = f"{parts.scheme}://{parts.netloc}/"
        if root == self.url:
            return ""
        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            root, headers={"User-Agent": "finplanet-analyst-mind/0.1 (personal research)"}
        )
        try:
            with opener(req, timeout=self.TIMEOUT) as resp:
                page = resp.read(self.MAX_BYTES)
        except Exception:  # the diagnostic must never become the failure
            return ""
        if isinstance(page, bytes):
            page = page.decode("utf-8", errors="replace")
        found = advertised_feeds(page)
        if not found:
            return f" - and {root} advertises no feed of its own"
        return f" - but {root} advertises feeds at: " + ", ".join(found[:6])

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
        self.overflow = ""
        # Every usable item on the page, in the window or not: how `_fetch_raw`
        # tells a page of older items from the first page served again.
        self._links: set[str] = set()
        usable = 0
        dated = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        truncated = False
        for item in items:
            row = self._item_to_row(item)
            if row is None:
                continue
            usable += 1
            self._links.add(row["url"])
            published = row.get("published_at")
            if published is None:
                # An item with no date is not an item published NOW. Dating it
                # `now` is the one thing this must never do - see _to_article.
                self.undated += 1
                continue
            when = datetime.fromisoformat(published)
            dated += 1
            if oldest is None or when < oldest:
                oldest = when
            if newest is None or when > newest:
                newest = when
            if when < since:
                continue
            out.append(RawRecord(self.name, row["url"], now, row))
            if len(out) >= limit:
                truncated = True  # our own limit, not the feed's: nothing to judge
                break

        # This page's tallies, which `_fetch_raw` adds up across pages.
        self._dated, self._oldest, self._newest, self._truncated = dated, oldest, newest, truncated
        if not truncated:  # our own limit, not the feed's: nothing to judge
            self.overflow = self._overflow_note(since, dated, oldest)

        if usable and self.undated == usable:
            raise FeedError(
                f"{self.name} dates none of its {usable} items, so nothing here can be "
                f"placed in time. Every item would be stamped with the moment it was "
                f"fetched, which is how an archive enters a corpus as today's news. "
                f"Refused rather than ingested: {_excerpt(text)}"
            )
        return out

    def _overflow_note(self, since: datetime, dated: int, oldest: datetime | None) -> str:
        """The part of the window the items read never reached, or "" if they
        reached back to `since` or are too few to judge."""
        if dated < self.OVERFLOW_MIN_ITEMS or oldest is None:
            return ""
        if oldest - since <= self.OVERFLOW_TOLERANCE:
            return ""
        hours = (oldest - since).total_seconds() / 3600
        # Both ends in UTC before they are printed with a "Z". `oldest` keeps
        # the offset the feed dated it in, and NST dates +0800: the 2026-10-16
        # 10:08Z sweep reported its oldest item at "14:58Z", six hours in the
        # future, when the stored article said 06:58Z. The subtraction above
        # was always right; only the label lied.
        return (
            f"window overflow: all {dated} dated items are newer than the window's start "
            f"({since.astimezone(UTC):%Y-%m-%d %H:%MZ}); the oldest is "
            f"{oldest.astimezone(UTC):%Y-%m-%d %H:%MZ}, so {hours:.1f}h scrolled off unseen"
        )

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
