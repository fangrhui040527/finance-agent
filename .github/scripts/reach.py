"""What a runner sees when it asks a URL, recorded the same way for every probe.

A probe that prints only "HTTP 404" or "OK" cannot tell a moved file from a
login wall, a bot challenge or an HTML error page served with status 200. The
answer is in five things the response already carries: the status, the content
type, the URL it ended at after redirects, how many bytes came back, and what
the first of them say. This records those, per target, into the job log and
into $GITHUB_STEP_SUMMARY as one table, so a single dispatch settles which
sources are reachable from a GitHub runner before anyone builds a collector.

Shared by bursa_feeds_probe.py and macro_alternatives_probe.py. Keyless: no
target here may carry a secret in its URL, because the URL is printed.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

#: Short on purpose: one dead host must not eat the job's 15 minutes.
TIMEOUT = 15
#: A daily PDF is a few hundred KB and a parquet a few MB. Past this the length
#: is read off Content-Length rather than downloaded - the probe asks whether a
#: file is there, not for the file.
MAX_BYTES = 5_000_000
#: How much of the body is shown: enough to tell %PDF from <html> from a JSON
#: error, short enough to keep the table readable.
HEAD = 200


@dataclass
class Reach:
    name: str
    url: str
    status: int = 0
    content_type: str = ""
    final_url: str = ""
    length: str = ""
    head: str = ""
    error: str = ""
    body: bytes = b""


def printable(data: bytes, n: int = HEAD) -> str:
    """The first n bytes, ASCII-printable, everything else escaped as \\xNN.

    `|` and the backtick are escaped too: the result sits inside a markdown
    table cell, and either one would break the row.
    """
    out: list[str] = []
    for b in data[:n]:
        if b == 0x0A:
            out.append("\\n")
        elif b == 0x0D:
            out.append("\\r")
        elif b == 0x09:
            out.append("\\t")
        elif 0x20 <= b <= 0x7E and b not in (0x5C, 0x7C, 0x60):
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


def last_trading_day(today: date) -> date:
    """The most recent weekday strictly before today.

    Bursa posts a day's files after the close, so the day before the run is the
    newest one worth asking for. Weekends only: a public holiday gives a 404 on
    that one row, and the fixed known-good date beside it says which it was.
    """
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def links_matching(html: bytes, base: str, pattern: str) -> list[str]:
    """Absolute hrefs on a page whose target matches pattern, in page order."""
    text = html.decode("utf-8", "replace")
    want = re.compile(pattern, re.IGNORECASE)
    out: list[str] = []
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", text, re.IGNORECASE):
        if want.search(href):
            out.append(urllib.parse.urljoin(base, href.strip()))
    return list(dict.fromkeys(out))


def fetch(
    name: str, url: str, ua: str, headers: dict | None = None, timeout: int = TIMEOUT
) -> Reach:
    """One request, every outcome a row. Never raises."""
    r = Reach(name, url)
    req = urllib.request.Request(url, headers={"User-Agent": ua, **(headers or {})})
    try:
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed hosts
        except urllib.error.HTTPError as e:
            resp = e  # a 4xx/5xx still has headers and a body worth reading
        with resp:
            r.status = resp.getcode() or 0
            r.final_url = resp.geturl() or url
            r.content_type = resp.headers.get("Content-Type", "") if resp.headers else ""
            declared = resp.headers.get("Content-Length", "") if resp.headers else ""
            body = resp.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            r.length = declared or f">{MAX_BYTES}"
            body = body[:MAX_BYTES]
        else:
            r.length = str(len(body))
        r.body = body
        r.head = printable(body)
    except Exception as e:  # DNS, TLS, timeout, reset: the reason is the finding
        r.error = f"{type(e).__name__}: {e}"[:160]
    return r


def log_line(r: Reach) -> str:
    if r.error:
        return f"  {r.name:<28} ERROR {r.error}\n      {r.url}"
    moved = f"\n      -> {r.final_url}" if r.final_url and r.final_url != r.url else ""
    return (
        f"  {r.name:<28} HTTP {r.status} {r.content_type or '-'} {r.length}B\n"
        f"      {r.url}{moved}\n      {r.head}"
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def table_row(r: Reach) -> str:
    status = f"ERR {r.error}" if r.error else str(r.status)
    final = "" if r.final_url in ("", r.url) else r.final_url
    head = f"`{r.head}`" if r.head else ""
    return (
        f"| {_cell(r.name)} | {_cell(r.url)} | {_cell(status)} | {_cell(r.content_type)} "
        f"| {_cell(final)} | {r.length} | {head} |"
    )


def table(rows: list[Reach]) -> str:
    head = (
        "| target | url | status | content type | final url | bytes | first bytes |\n"
        "|---|---|---|---|---|---|---|"
    )
    return "\n".join([head, *(table_row(r) for r in rows)])


def report(title: str, rows: list[Reach]) -> None:
    """Into the job log as lines, into the step summary as a table."""
    sys.stdout.write(f"\n## {title}\n" + "\n".join(log_line(r) for r in rows) + "\n")
    sys.stdout.flush()
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"\n### {title}\n\n{table(rows)}\n")
