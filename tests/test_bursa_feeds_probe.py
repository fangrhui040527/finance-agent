"""The Malaysian feeds probe, exercised without the network.

The probe can only run on a runner - this environment has no route to any
Malaysian host - so what it reports has to be right the first time it is
dispatched. Since 2026-10-13 the question it is asked is no longer "does this
URL serve dated items" but "how far back does it reach": nst_business and
fmt_business answer with 50 dated items every sweep and overflow the window
between Bursa slots all the same (16.8h of NST's went unseen on 2026-10-16).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from knowledge.feeds.registry import RSS_SOURCES

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "bursa_feeds_probe.py"
spec = importlib.util.spec_from_file_location("bursa_feeds_probe", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

MYT = timezone(timedelta(hours=8))


def _rss(items: list[datetime]) -> bytes:
    body = "".join(
        f"<item><title>Story {i}</title><link>https://www.nst.com.my/{i}</link>"
        f"<pubDate>{format_datetime(when)}</pubDate></item>"
        for i, when in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


def test_the_verdict_names_how_far_back_a_feed_reaches_in_utc():
    """The newest item alone cannot tell a feed that covers a day from one that
    covers three hours, and that is the difference between NST's site feed and
    a business-only one. The span is printed in UTC: NST dates +0800, and a
    local time with the offset dropped reads eight hours wrong."""
    oldest = datetime(2026, 10, 16, 14, 58, tzinfo=MYT)  # 06:58Z
    items = [oldest + timedelta(minutes=4 * i) for i in range(50)]  # to 10:14Z
    n, dated, note = probe.dated_items(_rss(items))
    assert (n, dated) == (50, 50)
    assert "newest 2026-10-16 10:14Z" in note, note
    assert "oldest 2026-10-16 06:58Z" in note, note
    assert "(3.3h)" in note, note


def test_an_undated_feed_is_still_called_undated():
    body = b'<?xml version="1.0"?><rss><channel><item><title>a</title></item></channel></rss>'
    assert probe.dated_items(body) == (1, 0, "NOTHING DATED")


def test_the_registered_overflowing_feeds_are_probed_with_their_paged_forms():
    """Whether a feed pages can only be read off the publisher, so the probe
    asks for the registered URL and for its second page in each convention it
    might follow. A paged URL that answers with the same newest item as the
    first page is a site ignoring the parameter, which the span shows."""
    for name in ("nst_business", "fmt_business"):
        registered = RSS_SOURCES[name][0]
        _, candidates = probe.OUTLETS[name]
        assert registered in candidates, (name, candidates)
        paged = [c for c in candidates if c.startswith(registered + "?")]
        assert len(paged) >= 2, (name, paged)
