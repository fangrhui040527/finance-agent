"""A refetch that knows less than the cached body does not replace it.

2026-09-29, 00:59 UTC: the us_close collector, 3h44m late, refetched NVDA,
AAPL, MSFT, SPY and ^KLSE. Yahoo answered with Monday's row present and its
close blank; the parser drops that row, so each body's last usable bar was
Friday's, and it overwrote a body pulled at 22:45 UTC that held Monday's
settled close. The offline readers - the nightly pack and the paper book -
then priced the US names at Friday's close."""

from __future__ import annotations

from core.market.cache import PriceCache, last_usable_bar_day
from core.market.feed import YahooFeed

HEAD = "date,open,high,low,close,volume\n"
FRI = "2026-09-25,224.0,226.0,223.0,225.07,100\n"
MON = "2026-09-28,226.0,230.0,225.5,228.86,100\n"
MON_BLANK = "2026-09-28,,,,,\n"  # Yahoo's answer at 00:59 UTC

SETTLED = HEAD + FRI + MON
REGRESSED = HEAD + FRI + MON_BLANK


def _cache(tmp_path, now="2026-09-29T00:59:31+00:00"):
    clock = {"now": now}
    cache = PriceCache(tmp_path / "p.db", today=lambda: clock["now"][:10], now=lambda: clock["now"])
    return cache, clock


def _held(cache, symbol="NVDA"):
    return cache.conn.execute(
        "SELECT body, fetched_at FROM price_csv WHERE feed='yahoo' AND symbol=?", (symbol,)
    ).fetchone()


def test_a_body_whose_last_usable_bar_is_older_does_not_replace_the_held_one(tmp_path):
    cache, clock = _cache(tmp_path, now="2026-09-28T22:45:22+00:00")
    assert cache.put("yahoo", "NVDA", SETTLED) == SETTLED
    clock["now"] = "2026-09-29T00:59:31+00:00"
    kept = cache.put("yahoo", "NVDA", REGRESSED)
    assert kept == SETTLED, "the caller is handed the settled body"
    body, fetched_at = _held(cache)
    assert body == SETTLED and fetched_at.startswith("2026-09-28T22:45")
    assert str(last_usable_bar_day(body)) == "2026-09-28"


def test_a_body_that_adds_a_session_still_replaces_the_held_one(tmp_path):
    cache, clock = _cache(tmp_path, now="2026-09-26T01:00:00+00:00")
    cache.put("yahoo", "NVDA", HEAD + FRI)
    clock["now"] = "2026-09-28T22:45:22+00:00"
    assert cache.put("yahoo", "NVDA", SETTLED) == SETTLED
    assert _held(cache)[0] == SETTLED


def test_an_in_progress_row_on_the_same_usable_bar_still_replaces(tmp_path):
    """The mid-session body ends on the same usable bar as the held one; storing
    it is the existing behaviour, and `get` already refuses to serve it online."""
    cache, clock = _cache(tmp_path, now="2026-09-26T01:00:00+00:00")
    cache.put("yahoo", "NVDA", HEAD + FRI)
    clock["now"] = "2026-09-28T15:00:00+00:00"
    mid = HEAD + FRI + MON_BLANK
    assert cache.put("yahoo", "NVDA", mid) == mid
    assert _held(cache)[0] == mid


def test_the_feed_prices_from_the_held_body_when_the_refetch_regresses(tmp_path, monkeypatch):
    cache, clock = _cache(tmp_path, now="2026-09-28T22:45:22+00:00")
    cache.put("yahoo", "NVDA", SETTLED)
    feed = YahooFeed()
    feed.cache = cache
    clock["now"] = "2026-09-29T00:59:31+00:00"  # a new UTC day: the cached row is a miss
    monkeypatch.setattr(feed, "_fetch_csv", lambda symbol: REGRESSED)
    series = feed.fetch("XNAS:NVDA")
    assert str(series.raw()[-1].day) == "2026-09-28"
    assert float(series.raw()[-1].close) == 228.86


# 2026-10-13, 02:34 UTC: the us_close collector refetched the Bursa names while
# Tuesday's session was trading. Yahoo answered with Monday's row blank and a
# Tuesday row carrying the session so far. The last usable bar moved FORWARD, so
# the guard above let it in, and Monday's settled close (Genting 1.45, on the
# day it fell 22.87%) left the cache. The control book was re-marked for Monday
# at Friday's close, and its 10-12 mark rose from USD 954.23 to 976.27.
KL_HEAD = "date,open,high,low,close,volume\n"
KL_FRI = "2026-10-09,1.84,1.89,1.83,1.88,2689500\n"
KL_MON = "2026-10-12,1.73,1.75,1.44,1.45,90366100\n"
KL_MON_BLANK = "2026-10-12,,,,,\n"
KL_TUE_PART = "2026-10-13,1.45,1.50,1.44,1.48,8674200\n"


def test_a_refetch_that_blanks_a_settled_day_keeps_that_day(tmp_path):
    cache, clock = _cache(tmp_path, now="2026-10-12T10:09:00+00:00")
    cache.put("yahoo", "3182.KL", KL_HEAD + KL_FRI + KL_MON)
    clock["now"] = "2026-10-13T02:34:00+00:00"
    stored = cache.put("yahoo", "3182.KL", KL_HEAD + KL_FRI + KL_MON_BLANK + KL_TUE_PART)
    bars = {b.day.isoformat(): float(b.close) for b in YahooFeed.parse(stored)}
    assert bars["2026-10-12"] == 1.45, "Monday's settled close survives the refetch"
    assert bars["2026-10-13"] == 1.48, "the fresh body's own rows are kept"
    assert _held(cache, "3182.KL")[0] == stored


def test_a_fresh_value_for_a_settled_day_wins_over_the_held_one(tmp_path):
    """Only a blank or missing row is filled from the held body; a revised close is news."""
    cache, clock = _cache(tmp_path, now="2026-10-12T10:09:00+00:00")
    cache.put("yahoo", "3182.KL", KL_HEAD + KL_FRI + KL_MON)
    clock["now"] = "2026-10-13T10:09:00+00:00"
    revised = KL_HEAD + KL_FRI + "2026-10-12,1.73,1.75,1.44,1.46,90366100\n" + KL_TUE_PART
    assert cache.put("yahoo", "3182.KL", revised) == revised
