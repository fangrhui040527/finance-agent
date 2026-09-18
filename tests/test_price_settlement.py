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

# Nasdaq trades to 16:00 New York, 21:00 UTC in winter and 20:00 in summer; the
# calendar carries tz_offset_hours=-5, so 21:00 UTC is the modelled shut. Bursa
# closes 16:45 MYT, 08:45 UTC. Both are read off the adapters, never hardcoded
# here - these comments say what the numbers below mean, they do not set them.
BAR = date(2026, 9, 16)


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
    """14:12 UTC is after Bursa's 08:45 shut and before Nasdaq's."""
    at = datetime(2026, 9, 16, 14, 12, tzinfo=UTC)
    assert price_state("MYX", BAR, at) == SETTLED
    assert price_state("XNAS", BAR, at) == PROVISIONAL


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
