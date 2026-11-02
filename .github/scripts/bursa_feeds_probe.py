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
  4. REACH per URL: the newest and the oldest dated item, in UTC, and the
     hours between them. A feed that holds a fixed number of items covers only
     as far back as those items reach, and from 2026-09-14 every nst_business
     sweep overflowed its window with all 50 items dated (16.8h unseen on
     2026-10-16; fmt_business 1.6h). Which URL fixes that - a second page, or
     a business-only feed whose 50 items reach further - is read off the span.
  5. BURSA'S OWN FILES. The daily PDFs under /misc/missftp/ (short-selling
     trade detail, net short positions, the daily scoreboard, key indicators)
     for the last Bursa session before the run and for one date known to
     have existed, the trading-participation page, robots.txt, and wherever the
     FBM100 constituents list lives. Selected by the name `bursa_data`.

Every URL asked is also recorded in one table - status, content type, final URL
after redirects, bytes and the first 200 of them - by reach.py, because "HTTP
200" alone cannot tell a PDF from a bot challenge served with status 200.

Read-only, keyless, stores nothing. Manual: it is a diagnostic, and the answer
changes when a publisher redesigns, not hourly.
"""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.parse
from datetime import UTC, date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach  # noqa: E402

#: Short: nine outlets' pages and paths and the Bursa files share a 15-minute job.
TIMEOUT = 15
#: Seconds of asking before every remaining URL is recorded as skipped rather
#: than fetched. About 80 URLs at 15s each would outlast the job, and a job
#: cancelled at its limit loses the reachability table; this leaves the install
#: and one last slow answer room inside the 15 minutes.
BUDGET = 12 * 60
#: When the asking started; main() resets it, fetch() measures BUDGET from it.
STARTED = time.monotonic()
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
            # The page that lists The Star's feeds; paths are case-sensitive.
            "https://www.thestar.com.my/RSS",
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
            # The registered /feed is 50 site-wide items, about 3.2 hours of
            # NST, and the Bursa slots are most of a day apart. Its second
            # page in the WordPress form (`paged`, from 1) and the Drupal one
            # (`page`, from 0); a page that repeats the first page's newest
            # item is a site ignoring the parameter.
            "https://www.nst.com.my/feed?paged=2",
            "https://www.nst.com.my/feed?page=1",
            # A business-only feed: 50 items of one section reach back further
            # than 50 of the whole site.
            "https://www.nst.com.my/business/feed",
            "https://www.nst.com.my/feed/business",
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
            # The registered feed, asked for directly rather than only when the
            # homepage advertises it, and its second page in the WordPress and
            # the plain 1-based forms: it overflows by an hour or two a sweep.
            "https://www.freemalaysiatoday.com/feeds/rss/headlines",
            "https://www.freemalaysiatoday.com/feeds/rss/headlines?paged=2",
            "https://www.freemalaysiatoday.com/feeds/rss/headlines?page=2",
            # The business category above, paged, and the narrower sections.
            "https://www.freemalaysiatoday.com/category/business/feed/?paged=2",
            "https://www.freemalaysiatoday.com/feeds/rss/business",
            "https://www.freemalaysiatoday.com/category/business/local-business/feed/",
        ),
    ),
    # Not registered at all; asked whether they serve dated business items.
    "malaymail_money": (
        ("https://www.malaymail.com/",),
        ("https://www.malaymail.com/feed/rss/money",),
    ),
    "malaysian_reserve": (
        ("https://themalaysianreserve.com/",),
        ("https://themalaysianreserve.com/feed/",),
    ),
    "businesstoday": (
        ("https://www.businesstoday.com.my/",),
        ("https://www.businesstoday.com.my/feed/",),
    ),
    "bernamabiz": (("https://bernamabiz.com/",), ()),
}

BURSA = "https://www.bursamalaysia.com"
#: Bursa's day turns over at midnight MYT, eight hours before UTC's does.
MYT = timezone(timedelta(hours=8))
MISSFTP = f"{BURSA}/misc/missftp"

#: Daily PDF -> (path with {d} for YYYYMMDD, a day the file is known to have
#: existed). The known day separates "this path is wrong" from "not posted yet".
BURSA_DAILY: dict[str, tuple[str, str]] = {
    "shortsell_trades": ("securities/securities_equities_rss_{d}.pdf", "20260710"),
    "net_short_position": ("sblnt/NetShortPosition{d}.pdf", "20260717"),
    "daily_scoreboard": ("securities/securities_equities_daily_scoreboard_{d}.pdf", "20260923"),
    "key_indicators": ("securities/securities_equities_keyindicators_{d}.pdf", "20260707"),
}

#: Pages that might link the FBM100 constituents list, which Bursa posts as
#: FBM100_Constituents_List_<Mon><YYYY>.pdf after the June and December reviews.
#: Where it lives is unverified - the hrefs are read off these pages.
FBM100_PAGES: tuple[str, ...] = (
    f"{BURSA}/trade/our_products_services/indices/ftse_bursa_malaysia_indices",
    f"{BURSA}/market_information/ftse_bursa_malaysia",
    "https://www.lseg.com/en/ftse-russell/indices/ftse-bursa-malaysia",
)
FBM100_HREF = r"FBM100_Constituents|FBM100[^\"']*\.pdf|constituents?[^\"']*\.pdf"

#: Every URL fetch() asked, in order: the reachability table.
FETCHED: list[reach.Reach] = []

#: `<link rel="alternate" type="application/rss+xml" href="...">`, attributes in
#: any order and quoted either way - a publisher's own pointer to its feed.
LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""")


def fetch(url: str, name: str = "") -> tuple[int, bytes, str]:
    """(status, body, error). A non-200 is data here, not an exception.

    Every call is kept in FETCHED, so the reachability table covers each URL
    the probe asked, not only the ones that answered.
    """
    name = name or urllib.parse.urlsplit(url).netloc
    if time.monotonic() - STARTED > BUDGET:
        r = reach.Reach(name, url, error=f"skipped: the probe's {BUDGET}s were spent")
    else:
        r = reach.fetch(name, url, UA, timeout=TIMEOUT)
    FETCHED.append(r)
    if r.error:
        return 0, b"", r.error
    return r.status, (r.body if r.status == 200 else b""), ""


def last_bursa_day(today: date) -> date:
    """The last Bursa session strictly before today.

    Read off the XKLS calendar, which knows Bursa's holidays, so the day after
    one asks for a file that can exist. Weekends only (reach's rule) when the
    calendar cannot be loaded.
    """
    try:
        from markets.registry import get

        calendar = get("XKLS").calendar
    except (ImportError, KeyError, ValueError):
        return reach.last_trading_day(today)
    d = today - timedelta(days=1)
    for _ in range(14):
        if calendar.session(d) is not None:
            return d
        d -= timedelta(days=1)
    return reach.last_trading_day(today)


def bursa_targets(today: date) -> list[tuple[str, str]]:
    """(name, url) for Bursa's own files: each daily PDF for the last session
    before today and for its known day, then the fixed pages."""
    day = last_bursa_day(today).strftime("%Y%m%d")
    out = [("bursa_robots", f"{BURSA}/robots.txt")]
    for name, (path, known) in BURSA_DAILY.items():
        for d in dict.fromkeys((day, known)):
            out.append((f"{name}_{d}", f"{MISSFTP}/{path.format(d=d)}"))
    out.append(("trading_participation", f"{BURSA}/market_information/market_statistic/securities"))
    out += [(f"fbm100_page_{i}", page) for i, page in enumerate(FBM100_PAGES)]
    return out


def probe_bursa_data(today: date) -> list[str]:
    """Ask every Bursa target, then any FBM100 list the index pages link.

    Hrefs are resolved against where the page ended up after redirects, and
    the ones naming FBM100 are followed before other indices' constituents.
    """
    last = last_bursa_day(today)
    out = [f"\n=== bursa_data (last session before the run: {last}) " + "=" * 8]
    for name, url in bursa_targets(today):
        status, body, err = fetch(url, name)
        out.append(f"  {name:<34} {err or f'HTTP {status}'}")
        if name.startswith("fbm100_page") and body:
            links = reach.links_matching(body, FETCHED[-1].final_url or url, FBM100_HREF)
            links.sort(key=lambda link: "fbm100" not in link.lower())
            for i, link in enumerate(links[:5]):
                status, _, err = fetch(link, f"{name}_link_{i}")
                out.append(f"    links {link}\n      {err or f'HTTP {status}'}")
    return out


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

    The note names the newest and the oldest item and the hours between, in
    UTC. It named only the newest until 2026-10-17, as a local time with its
    offset cut off - NST's +0800 read eight hours wrong - and the newest alone
    cannot tell a feed that covers a day from one that covers three hours.
    """
    try:
        root = ElementTree.fromstring(body)  # noqa: S314 - read once, never stored
    except ElementTree.ParseError as e:
        return 0, 0, f"not XML ({str(e)[:60]})"
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not items:
        return 0, 0, "parses, but carries no items"
    dated = 0
    times: list[datetime] = []
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
            when = parsedate_to_datetime(raw)  # RFC 822, as RSS dates
        except (TypeError, ValueError):
            try:
                when = datetime.fromisoformat(raw.replace("Z", "+00:00"))  # Atom, dc:date
            except ValueError:
                continue  # dated, in a form this cannot place in time
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        times.append(when.astimezone(UTC))
    if not dated:
        return len(items), 0, "NOTHING DATED"
    if not times:
        return len(items), dated, "dated, but in no form this can read"
    newest, oldest = max(times), min(times)
    hours = (newest - oldest).total_seconds() / 3600
    return (
        len(items),
        dated,
        f"newest {newest:%Y-%m-%d %H:%MZ}, oldest {oldest:%Y-%m-%d %H:%MZ} ({hours:.1f}h)",
    )


def probe_url(url: str, name: str = "") -> str:
    status, body, err = fetch(url, name)
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


def emit(lines: list[str]) -> None:
    """One finished block into the job log and the step summary, at once.

    Not held to the end: a run cancelled at the job's limit still shows every
    block that finished before it.
    """
    text = "\n".join(lines)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")


def main() -> int:
    global STARTED
    STARTED = time.monotonic()
    only = set(sys.argv[1:])
    usable: list[tuple[str, str]] = []
    # `bursa_data` alone asks no outlet, so it gets no outlet verdict either:
    # "no usable feed" there would be about feeds nobody asked for.
    outlets = not only or bool(only & OUTLETS.keys())
    if outlets:
        emit(["## Malaysian business feeds"])
    for name, (pages, candidates) in OUTLETS.items():
        if only and name not in only:
            continue
        out = [f"\n=== {name} " + "=" * (56 - len(name))]

        found: list[str] = []
        for page in pages:
            status, body, err = fetch(page, name)
            if err or status != 200:
                out.append(f"  page   {page}\n         {err or f'HTTP {status}'}")
                continue
            links = advertised(body, page)
            out.append(f"  page   {page}\n         advertises {len(links)} feed(s)")
            out += [f"           {link}" for link in links]
            found += links

        for url in dict.fromkeys(list(found) + list(candidates)):
            verdict = probe_url(url, name)
            mark = "advertised" if url in found else "candidate "
            out.append(f"  {mark} {url}\n             {verdict}")
            if verdict.startswith("OK"):
                usable.append((name, url))
        emit(out)

    if outlets:
        out = ["\n" + "=" * 64]
        if usable:
            out.append("USABLE FEEDS (items with dates) - the ones worth enabling:")
            out += [f"  {name:<20} {url}" for name, url in usable]
        else:
            out += [
                "NO USABLE FEED FOUND. If fmt_business also failed, suspect the runner's",
                "egress rather than every publisher at once; fmt_business is the control row.",
            ]
        emit(out)
    if not only or "bursa_data" in only:
        emit(probe_bursa_data(datetime.now(MYT).date()))

    reach.report("Reachability from this runner", FETCHED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
