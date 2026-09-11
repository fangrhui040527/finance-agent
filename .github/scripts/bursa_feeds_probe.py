"""Which Malaysian business feed actually serves dated items, asked of the sites.

WHY THIS EXISTS. The book is six Bursa names and three Nasdaq names, and the
corpus is not: 1,968 of 2,671 articles are linked to the three US names and 62
to all six Malaysian ones. The single biggest reason is not the linker and not
the query - it is that five Malaysian outlets are REGISTERED and four are
DISABLED, because on 2026-09-04 thestar, edge and nst answered 404 and bernama
served items with no dates. Only `fmt_business` was ever enabled.

A 404 on one guessed path is not evidence a publisher has no feed. It is
evidence that one URL was wrong, and the development environment has no route
to any Malaysian host, so the difference could never be settled from here.

WHAT THIS DOES. For each outlet, in order:

  1. AUTODISCOVERY. Fetch the site's own pages and read the
     `<link rel="alternate" type="application/rss+xml">` tags out of the HTML.
     A publisher advertising its feeds is the authority on where they are;
     every other method here is a guess.
  2. CANDIDATES. Fetch a short list of conventional paths for that site's
     platform (WordPress `/feed/`, Drupal `/rss`, and the paths the registry
     already holds).
  3. VERDICT per URL: whether it parses as a feed, how many items it has, and
     - the question that matters - how many carry a DATE. Bernama's registered
     feed is the reason that is separate: it answered, with items, and dated
     none of them, which is useless to a collector that windows by time.

Read-only, keyless, stores nothing. Manual: it is a diagnostic, and the answer
changes when a publisher redesigns, not hourly.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

TIMEOUT = 30
UA = "finance-agent-probe/1.0 (+https://github.com/fangrhui040527/finance-agent)"

#: Outlet -> (pages to read autodiscovery tags from, conventional paths to try).
#: The homepage first, then the section page: a site often advertises only its
#: main feed at the root and the business feed on the business page.
OUTLETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "thestar_business": (
        ("https://www.thestar.com.my/", "https://www.thestar.com.my/business"),
        (
            "https://www.thestar.com.my/rss/business/business-news",
            "https://www.thestar.com.my/rss/Business",
            "https://www.thestar.com.my/rss/business",
            "https://www.thestar.com.my/rss/editors-pick",
            "https://www.thestar.com.my/rss",
        ),
    ),
    "edge_malaysia": (
        ("https://theedgemalaysia.com/", "https://theedgemalaysia.com/categories/malaysia"),
        (
            "https://theedgemalaysia.com/rss.html",
            "https://theedgemalaysia.com/rss",
            "https://theedgemalaysia.com/feed",
            "https://theedgemalaysia.com/rss.xml",
            "https://www.theedgemarkets.com/rss.xml",
        ),
    ),
    "nst_business": (
        ("https://www.nst.com.my/", "https://www.nst.com.my/business"),
        (
            "https://www.nst.com.my/business/rss",
            "https://www.nst.com.my/rss/business",
            "https://www.nst.com.my/feed",
            "https://www.nst.com.my/rss.xml",
        ),
    ),
    "bernama_business": (
        ("https://www.bernama.com/en/", "https://www.bernama.com/en/business/"),
        (
            "https://www.bernama.com/en/rssfeed.php",
            "https://www.bernama.com/en/rss/business.xml",
            "https://www.bernama.com/en/feed/",
        ),
    ),
    # The one that IS enabled, probed as the control: a run where every outlet
    # fails is a runner with no egress, and this row is how that is told apart
    # from four publishers that moved their feeds.
    "fmt_business": (
        ("https://www.freemalaysiatoday.com/",),
        (
            "https://www.freemalaysiatoday.com/category/business/feed/",
            "https://www.freemalaysiatoday.com/feed/",
        ),
    ),
}

#: `<link rel="alternate" type="application/rss+xml" href="...">`, attributes in
#: any order and quoted either way - a publisher's own pointer to its feed.
LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""")


def fetch(url: str) -> tuple[int, bytes, str]:
    """(status, body, error). A non-200 is data here, not an exception."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed hosts
            return r.status, r.read(), ""
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception as e:  # DNS, TLS, timeout: the reason is the finding
        return 0, b"", f"{type(e).__name__}: {e}"


def advertised(html: bytes, base: str) -> list[str]:
    """Feed URLs the page advertises, absolute, in the order it lists them."""
    text = html.decode("utf-8", "replace")
    out: list[str] = []
    for tag in LINK_TAG.findall(text):
        attrs = {k.lower(): v for k, v in ATTR.findall(tag)}
        kind = attrs.get("type", "").lower()
        if "rss" not in kind and "atom" not in kind:
            continue
        href = attrs.get("href", "").strip()
        if not href:
            continue
        out.append(urllib.parse.urljoin(base, href))
    return list(dict.fromkeys(out))


def dated_items(body: bytes) -> tuple[int, int, str]:
    """(items, of them dated, note). The second number is the one that decides.

    A feed whose items carry no date cannot be windowed - the collector asks
    for the last N hours - so it is not usable however many items it has. That
    is exactly what Bernama's registered feed did on 2026-09-04.
    """
    try:
        root = ElementTree.fromstring(body)  # noqa: S314 - read once, never stored
    except ElementTree.ParseError as e:
        return 0, 0, f"not XML ({str(e)[:60]})"
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not items:
        return 0, 0, "parses, but carries no items"
    dated = 0
    newest = ""
    for it in items:
        raw = ""
        for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "published", "updated"):
            found = it.find(tag) if tag.startswith("{") or tag == "pubDate" else None
            if found is None:
                found = it.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
            if found is not None and (found.text or "").strip():
                raw = found.text.strip()
                break
        if not raw:
            continue
        dated += 1
        try:
            when = parsedate_to_datetime(raw).isoformat()
        except (TypeError, ValueError):
            when = raw
        newest = max(newest, when)
    return len(items), dated, (f"newest {newest[:19]}" if newest else "NOTHING DATED")


def probe_url(url: str) -> str:
    status, body, err = fetch(url)
    if err:
        return f"ERROR   {err}"
    if status != 200:
        return f"HTTP {status}"
    n, dated, note = dated_items(body)
    if n and dated:
        return f"OK      {n} items, {dated} dated, {note}"
    if n:
        return f"UNUSABLE {n} items, 0 dated"
    return f"NO FEED {note}"


def main() -> int:
    only = set(sys.argv[1:])
    usable: list[tuple[str, str]] = []
    out: list[str] = ["## Malaysian business feeds", ""]
    for name, (pages, candidates) in OUTLETS.items():
        if only and name not in only:
            continue
        out.append(f"\n=== {name} " + "=" * (56 - len(name)))

        found: list[str] = []
        for page in pages:
            status, body, err = fetch(page)
            if err or status != 200:
                out.append(f"  page   {page}\n         {err or f'HTTP {status}'}")
                continue
            links = advertised(body, page)
            out.append(f"  page   {page}\n         advertises {len(links)} feed(s)")
            out += [f"           {link}" for link in links]
            found += links

        for url in dict.fromkeys(list(found) + list(candidates)):
            verdict = probe_url(url)
            mark = "advertised" if url in found else "candidate "
            out.append(f"  {mark} {url}\n             {verdict}")
            if verdict.startswith("OK"):
                usable.append((name, url))

    out.append("\n" + "=" * 64)
    if usable:
        out.append("USABLE FEEDS (items with dates) - the ones worth enabling:")
        out += [f"  {name:<20} {url}" for name, url in usable]
    else:
        out += [
            "NO USABLE FEED FOUND. If fmt_business also failed, suspect the runner's",
            "egress rather than five publishers; fmt_business is the control row.",
        ]

    text = "\n".join(out)
    sys.stdout.write(text + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
