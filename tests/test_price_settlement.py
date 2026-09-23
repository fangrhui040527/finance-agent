"""A price is a close only after its market has shut.

The defect this file pins was not a wrong number, it was a number that could
not be told apart from a right one. On 2026-09-16 the paper pack printed

    fundable at this equity (one lot at the last close)
    XNAS:AAPL       1 x   333.5050 USD  = USD   333.51  (33.4% of equity)

under a heading that says "close" over a price pulled at 17:13 UTC, three hours
before Nasdaq shut. The night before, the same table's figures were read 39
minutes before the us_close sweep landed and Apple's session read -0.96% where
the settled close says -0.52%. Both wrong figures sat INSIDE that session's
eventual high-low range, which is what an intraday quote looks like after the
fact: five full columns, a plausible price, nothing unfinished about it.

`cache.is_mid_session` already caught the other half - a vendor row whose close
is blank, as Yahoo returns for Bursa mid-session, which the bar parser throws
away. When the vendor fills every column with the session so far, the parser
takes it and only the clock can tell.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

from core.market.cache import PriceCache
from core.market.calendar import PROVISIONAL, SETTLED, UNKNOWN, price_state
from engines.paper.rules import Fundable, fundables
from engines.paper.settings import PaperSettings

S = PaperSettings()

# Nasdaq trades to 16:00 New York: 20:00 UTC under daylight saving, which a
# September bar is, and 21:00 UTC in winter. The calendar runs on the named zone
# now; while the adapter carried a fixed UTC-5 the modelled shut was 21:00 UTC
# all year, an hour after the real September close. Bursa's closing auction and
# trading-at-last end 17:00 MYT, 09:00 UTC. Both are read off the adapters,
# never hardcoded here - these comments say what the numbers below mean, they
# do not set them.
BAR = date(2026, 9, 16)
# Bursa did not open on BAR - it was Malaysia Day - so the Bursa cases use the
# Thursday after. A bar dated a day with no session is tests/test_market_holidays.py.
BURSA_BAR = date(2026, 9, 17)


def test_a_bar_fetched_before_its_market_shut_is_not_called_a_close():
    """The exact 2026-09-16 case, against the two real sweep times."""
    us_preopen = datetime(2026, 9, 16, 17, 13, tzinfo=UTC)
    us_close = datetime(2026, 9, 16, 23, 31, tzinfo=UTC)
    assert price_state("XNAS", BAR, us_preopen) == PROVISIONAL
    assert price_state("XNAS", BAR, us_close) == SETTLED


def test_a_bar_from_an_earlier_session_is_settled_whenever_it_was_pulled():
    """Its market had already shut when it printed, so the clock cannot undo it."""
    mid_session = datetime(2026, 9, 16, 17, 13, tzinfo=UTC)
    assert price_state("XNAS", date(2026, 9, 15), mid_session) == SETTLED
    assert price_state("MYX", date(2026, 9, 15), mid_session) == SETTLED


def test_each_market_is_judged_by_its_own_clock():
    """14:12 UTC is after Bursa's 09:00 shut and before Nasdaq's 20:00."""
    at = datetime(2026, 9, 17, 14, 12, tzinfo=UTC)
    assert price_state("MYX", BURSA_BAR, at) == SETTLED
    assert price_state("XNAS", BURSA_BAR, at) == PROVISIONAL


def test_the_modelled_shut_follows_new_york_daylight_saving():
    """A September close is 20:00 UTC. With a fixed UTC-5 the calendar put it at
    21:00, so a settled bar pulled in that hour was printed as the session so
    far - the opposite error to the one this file opens with, from the same
    cause: the clock the bar is judged by was not the market's own."""
    assert price_state("XNAS", BAR, datetime(2026, 9, 16, 19, 59, tzinfo=UTC)) == PROVISIONAL
    assert price_state("XNAS", BAR, datetime(2026, 9, 16, 20, 0, tzinfo=UTC)) == SETTLED


def test_unknown_is_a_third_answer_and_not_a_guess():
    """Two would lie: `settled` invents a fetch time, `provisional` cries wolf."""
    assert price_state("XNAS", BAR, None) == UNKNOWN  # cached before the column
    assert price_state("NOPE", BAR, datetime(2026, 9, 16, 23, 31, tzinfo=UTC)) == UNKNOWN
    assert price_state("XNAS", date(2026, 9, 13), datetime(2026, 9, 16, tzinfo=UTC)) == UNKNOWN


def test_a_naive_fetch_instant_is_read_as_utc():
    assert price_state("XNAS", BAR, datetime(2026, 9, 16, 23, 31)) == SETTLED


# -- the cache remembers the instant, not just the day -------------------------------------


def _body(day: str) -> str:
    return f"date,open,high,low,close,volume\n{day},1,2,0.5,1.5,100\n"


def test_the_cache_records_when_a_body_was_pulled(tmp_path):
    cache = PriceCache(tmp_path / "p.db", now=lambda: "2026-09-16T17:13:00+00:00")
    cache.put("yahoo", "AAPL", _body("2026-09-16"))
    assert cache.fetched_at("yahoo", "AAPL") == datetime(2026, 9, 16, 17, 13, tzinfo=UTC)
    assert cache.fetched_at("yahoo", "NEVER-FETCHED") is None


def test_a_body_is_stale_once_its_market_has_shut_since_it_was_pulled(tmp_path):
    """2026-09-22. Monday's us_close collector arrived at 00:08 UTC Tuesday and
    refetched every book name, so each Bursa row was fetched-on Tuesday with
    Monday's close as its last bar. Bursa shut at 09:00 UTC, and a cache keyed
    to the day served Monday's closes to every read for the rest of Tuesday -
    the bursa_close sweep at 09:25 first. The row was today's; the session was
    not. Each market is judged by its own clock: Nasdaq had not shut between
    00:08 and 09:25, so the US row pulled at the same instant is still fresh."""
    clock = {"now": "2026-09-22T00:08:56+00:00"}
    cache = PriceCache(tmp_path / "p.db", today=lambda: clock["now"][:10], now=lambda: clock["now"])
    monday = _body("2026-09-21")
    cache.put("yahoo", "5183.KL", monday)
    cache.put("yahoo", "NVDA", monday)
    clock["now"] = "2026-09-22T08:59:00+00:00"  # Bursa still trading
    assert cache.get("yahoo", "5183.KL", mic="XKLS") == monday
    clock["now"] = "2026-09-22T09:25:00+00:00"  # the bursa_close sweep
    assert cache.get("yahoo", "5183.KL", mic="XKLS") is None, "Bursa shut at 09:00 since the pull"
    assert cache.get("yahoo", "5183.KL", mic="MYX") is None, "the book's spelling of the market"
    assert cache.get("yahoo", "NVDA", mic="XNAS") == monday, "no Nasdaq session shut since 00:08"
    clock["now"] = "2026-09-22T20:05:00+00:00"
    assert cache.get("yahoo", "NVDA", mic="XNAS") is None
    # a caller that cannot name the market keeps the day rule: the looser answer, never a fresher one
    assert cache.get("yahoo", "5183.KL") == monday
    assert cache.get("yahoo", "5183.KL", mic="NOPE") == monday


def test_the_feed_names_the_market_so_the_cache_can_judge_the_session(tmp_path, monkeypatch):
    """What the 09:25 bursa_close sweep of 2026-09-22 has to do: fetch Tuesday's
    Bursa close, not be served Monday's from a row stamped that morning."""
    from core.market.feed import YahooFeed

    clock = {"now": "2026-09-22T00:08:56+00:00"}
    cache = PriceCache(tmp_path / "p.db", today=lambda: clock["now"][:10], now=lambda: clock["now"])
    feed = YahooFeed()
    feed.cache = cache
    pulls: list[str] = []

    def pull(symbol):
        pulls.append(symbol)
        return _body("2026-09-21")

    monkeypatch.setattr(feed, "_fetch_csv", pull)
    feed.fetch("MYX:5183")
    assert pulls == ["5183.KL"]
    clock["now"] = "2026-09-22T08:59:00+00:00"
    feed.fetch("MYX:5183")
    assert pulls == ["5183.KL"], "served from the cache while Bursa is still trading"
    clock["now"] = "2026-09-22T09:25:00+00:00"
    feed.fetch("MYX:5183")
    assert pulls == ["5183.KL", "5183.KL"], "Bursa shut at 09:00; the cached row is a session old"


def test_a_database_written_before_the_column_existed_still_opens(tmp_path):
    """Every cached row predates this change; none may be given an invented time."""
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE price_csv (feed TEXT NOT NULL, symbol TEXT NOT NULL,"
        " fetched_on TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (feed, symbol));"
    )
    con.execute(
        "INSERT INTO price_csv VALUES (?,?,?,?)",
        ("yahoo", "AAPL", "2026-09-15", _body("2026-09-15")),
    )
    con.commit()
    con.close()

    cache = PriceCache(p)
    assert "fetched_at" in {r[1] for r in cache.conn.execute("PRAGMA table_info(price_csv)")}
    assert cache.fetched_at("yahoo", "AAPL") is None
    assert price_state("XNAS", date(2026, 9, 15), cache.fetched_at("yahoo", "AAPL")) == UNKNOWN


# -- the fundable table says which it printed ----------------------------------------------


class _FeedAt:
    """`paper_env`'s synthetic feed, plus an answer to "when was this pulled"."""

    def __init__(self, inner, at):
        self._inner, self._at = inner, at
        self.name = inner.name

    def fetch(self, instrument_id, start=None, end=None):
        return self._inner.fetch(instrument_id, start, end)

    def fetched_at(self, instrument_id):
        return self._at


def _funds(paper_env, at, day=date(2026, 3, 13)):
    q = paper_env.fx.asof(day)
    feed = _FeedAt(paper_env.feed, at) if at is not None else paper_env.feed
    return {f.instrument_id: f for f in fundables(feed, paper_env.cfg, Decimal(1000), q, day, S)}


def test_the_fundable_row_marks_a_price_that_is_the_session_so_far(paper_env):
    mid = datetime(2026, 3, 13, 17, 13, tzinfo=UTC)  # Nasdaq open, Bursa shut
    by = _funds(paper_env, mid)

    assert by["XNAS:NVDA"].price_state == PROVISIONAL
    assert "the session so far, not the close" in by["XNAS:NVDA"].row()
    assert by["MYX:5183"].price_state == SETTLED
    assert "the session so far" not in by["MYX:5183"].row()


def test_a_settled_table_carries_no_marker_at_all(paper_env):
    by = _funds(paper_env, datetime(2026, 3, 13, 23, 31, tzinfo=UTC))
    assert {f.price_state for f in by.values()} == {SETTLED}
    assert not any("session so far" in f.row() for f in by.values())


def test_a_feed_that_cannot_say_when_it_pulled_reads_unknown_not_close(paper_env):
    by = _funds(paper_env, None)
    assert {f.price_state for f in by.values()} == {UNKNOWN}
    assert not any(f.provisional for f in by.values())


def test_a_price_older_than_the_markets_latest_session_says_so(paper_env):
    """2026-09-22: every Bursa price in the table was Monday's close under the
    heading "the last close", eight hours after Tuesday's session shut. The
    cache held no Tuesday bar and the page could not say so."""
    from engines.paper.rules import fundables

    q = paper_env.fx.asof(date(2026, 3, 13))
    friday = date(2026, 3, 13)
    feed = _CutFeed(paper_env.feed, last=date(2026, 3, 12))  # no Friday bar cached
    by = {
        f.instrument_id: f
        for f in fundables(
            feed,
            paper_env.cfg,
            Decimal(1000),
            q,
            friday,
            S,
            now=datetime(2026, 3, 13, 22, 30, tzinfo=UTC),
        )
    }
    maybank = by["MYX:1155"]
    assert maybank.close_day == date(2026, 3, 12) and maybank.newer_session == friday
    assert "[the 2026-03-12 close; XKLS has since closed 2026-03-13]" in maybank.row()
    # before Friday's Bursa close the Thursday bar IS the latest session: nothing to say
    early = fundables(
        feed,
        paper_env.cfg,
        Decimal(1000),
        q,
        friday,
        S,
        now=datetime(2026, 3, 13, 5, 0, tzinfo=UTC),
    )
    assert all(f.newer_session is None for f in early if f.instrument_id.startswith("MYX"))


class _CutFeed:
    """The synthetic feed with nothing cached after `last`."""

    def __init__(self, inner, last):
        self._inner, self._last = inner, last
        self.name = inner.name

    def fetch(self, instrument_id, start=None, end=None):
        end = min(end, self._last) if end else self._last
        return self._inner.fetch(instrument_id, start=start, end=end)


def test_the_heading_stops_claiming_a_close_it_cannot_vouch_for(paper_env):
    from engines.paper.report import status

    settled = Fundable(
        "XNAS:NVDA",
        "USD",
        1,
        Decimal(100),
        BAR,
        Decimal(100),
        Decimal("0.1"),
        1,
        Decimal("0.01"),
        "",
        SETTLED,
    )
    mid = Fundable(
        "XNAS:AAPL",
        "USD",
        1,
        Decimal(333),
        BAR,
        Decimal(333),
        Decimal("0.33"),
        0,
        Decimal("0.01"),
        "",
        PROVISIONAL,
    )
    report = status(
        paper_env.store, paper_env.cfg, paper_env.feed, paper_env.fx, day=date(2026, 3, 13)
    )
    report.fundable = [settled, mid]
    text = report.render()
    assert "one lot at the last close, except where marked" in text
    assert "1 price(s) above are the session so far, not a close" in text
    assert "XNAS:AAPL" in text.split("session so far, not a close")[1].splitlines()[0]

    report.fundable = [settled]
    assert "one lot at the last close)" in report.render()

    report.fundable = [mid]
    assert "no price here is a close" in report.render()


def test_the_json_carries_the_state_so_a_page_can_copy_it(paper_env):
    from engines.paper.report import status

    report = status(
        paper_env.store, paper_env.cfg, paper_env.feed, paper_env.fx, day=date(2026, 3, 13)
    )
    rows = report.as_json()["fundable"]
    assert rows and all("price_state" in r and "price_day" in r for r in rows)
