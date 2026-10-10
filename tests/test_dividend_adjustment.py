"""Returns are total return, and the paper book is credited its dividends.

What now holds:

  * The Yahoo feed asks the chart for its dividend events, keeps them in the
    price cache beside the raw bars (never inside them), and every series it
    serves carries the dividends that went ex on or before its last bar - so
    `returns(adjusted=True)` across an ex-date is total return, and a read that
    ends before the ex-date applies no adjustment at all.
  * `knowledge.pack.measure` takes its returns from the adjusted closes, so an
    ex-date drop is not an idiosyncratic leg; a leg whose feed read no
    dividends is a price return and the row says NO DIVIDEND DATA.
  * The paper book credits dividend cash on the ex-date to the units held at
    the close before it, net of withholding for a Malaysian holder (0% on
    Bursa, 30% on US names), at the mark's mid rate, as a `dividend` position
    change that moves cash only. Ex-dates before the book's boundary - the day
    after its latest mark when crediting began - are not credited, so marks
    made before the change are not restated.

Each test fails on the code before 2026-10-10:
  - market-data-4: returns were never dividend-adjusted (no feed requested or
    passed dividend events), so an ex-dividend drop read as an idiosyncratic
    move and the paper book's equity lost every dividend.
"""

from __future__ import annotations

import io
import json
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from core.market.cache import PriceCache
from core.market.feed import YahooFeed, market_proxy_for
from core.market.prices import ActionKind, Bar, CorporateAction, PriceSeries
from engines.paper.book import mark, mark_book
from engines.paper.store import DECIDED, PositionChange
from knowledge.pack import measure

# -- the feed ---------------------------------------------------------------------------

EX = date(2026, 3, 12)
DAYS = [date(2026, 3, 10), date(2026, 3, 11), EX]


def _stamp(d: date) -> int:
    # Bursa opens 01:00 UTC; the chart stamps a session at its open.
    return int(datetime(d.year, d.month, d.day, 1, 0, tzinfo=UTC).timestamp())


def _chart(dividend: bool = True) -> dict:
    """An RM10 Bursa bank that goes ex a 30 sen dividend on EX and opens 2.8% lower."""
    result = {
        "meta": {"gmtoffset": 28800, "longName": "Malayan Banking Berhad"},
        "timestamp": [_stamp(d) for d in DAYS],
        "indicators": {
            "quote": [
                {
                    "open": [10.0, 10.0, 9.70],
                    "high": [10.1, 10.1, 9.75],
                    "low": [9.9, 9.9, 9.68],
                    "close": [10.0, 10.0, 9.72],
                    "volume": [1e6, 1e6, 1e6],
                }
            ]
        },
    }
    if dividend:
        result["events"] = {"dividends": {str(_stamp(EX)): {"amount": 0.30, "date": _stamp(EX)}}}
    return {"chart": {"result": [result], "error": None}}


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload: dict, urls: list[str]):
    def opener(req, timeout=None):
        urls.append(req.full_url)
        return _Body(json.dumps(payload).encode())

    return opener


def test_an_ex_date_is_total_return_and_a_read_ending_before_it_is_not_adjusted(tmp_path):
    urls: list[str] = []
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-03-12")
    feed = YahooFeed(opener=_opener(_chart(), urls), sleep=lambda s: None, cache=cache)

    s = feed.fetch("MYX:1155")
    assert "events=div" in urls[0]
    assert s.actions_source == "yahoo"
    assert [(a.ex_date, a.amount) for a in s.dividends()] == [(EX, 0.30)]
    raw, adjusted = s.returns(adjusted=False)[-1], s.returns(adjusted=True)[-1]
    assert abs(raw - -0.028) < 1e-9, "the raw close fell 2.8%"
    assert abs(adjusted - (9.72 / (10.0 * 0.97) - 1)) < 1e-9, "the dividend is added back"
    assert [b.close for b in s.raw()] == [10.0, 10.0, 9.72], "the raw bars are untouched"

    # Point in time: a read that ends before the ex-date knows no dividend.
    before = feed.fetch("MYX:1155", end=DAYS[1])
    assert before.actions == [] and before.actions_source == "yahoo"
    assert before.closes(adjusted=True) == before.closes(adjusted=False) == [10.0, 10.0]


def test_the_dividends_live_in_the_cache_and_a_name_without_them_says_so(tmp_path, monkeypatch):
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-03-12")
    YahooFeed(opener=_opener(_chart(), []), sleep=lambda s: None, cache=cache).fetch("MYX:1155")
    YahooFeed(opener=_opener(_chart(dividend=False), []), sleep=lambda s: None, cache=cache).fetch(
        "MYX:5183"
    )
    assert cache.dividends("yahoo", "1155.KL") == [(EX, 0.30)]
    assert cache.dividends("yahoo", "5183.KL") == [], "asked, and paid nothing: an answer"
    assert cache.dividends("yahoo", "5347.KL") is None, "never asked: not an answer"

    # An offline reader - no network - sees what the collector saw.
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")

    def no_network(req, timeout=None):
        raise AssertionError("offline must not fetch")

    offline = YahooFeed(opener=no_network, sleep=lambda s: None, cache=cache)
    assert abs(offline.fetch("MYX:1155").returns()[-1] - (9.72 / 9.7 - 1)) < 1e-9

    # A body cached before dividends were asked for is served price-only, and
    # the series says so instead of passing for a name that paid nothing.
    cache.put("yahoo", "5347.KL", "date,open,high,low,close,volume\n2026-03-11,1,1,1,1,1\n")
    blind = offline.fetch("MYX:5347")
    assert blind.actions_source is None and blind.actions == []


# -- the pack ---------------------------------------------------------------------------

DAY = date(2026, 9, 4)
DROP = 0.028


def _series(iid: str, last_shock: float) -> list[Bar]:
    proxy_id = market_proxy_for(iid) or iid
    market = random.Random(sum(map(ord, proxy_id)))
    noise = random.Random(sum(map(ord, iid)) * 7)
    days = [DAY - timedelta(days=i) for i in range(260, -1, -1)]
    days = [d for d in days if d.weekday() < 5]
    proxy, own, bars = 100.0, 50.0, []
    for i, d in enumerate(days):
        m = market.gauss(0.0003, 0.009)
        proxy *= 1 + m
        own *= 1 + 0.8 * m + noise.gauss(0, 0.004) + (last_shock if i == len(days) - 1 else 0.0)
        px = proxy if proxy_id == iid else own
        bars.append(Bar(d, px, px * 1.01, px * 0.99, px, 1_000_000))
    return bars


class ExDateFeed:
    """Maybank goes ex on DAY with a dividend worth 2.8% of the prior close."""

    source_used = "cache"

    def __init__(self, read_dividends: bool) -> None:
        self.read = read_dividends

    def fetch(self, iid, start=None, end=None):
        bars = _series(iid, -DROP if iid == "MYX:1155" else 0.0)
        bars = [b for b in bars if end is None or b.day <= end]
        if not self.read:
            return PriceSeries(iid, bars)
        actions = []
        if iid == "MYX:1155" and bars[-1].day >= DAY:
            prior = next(b.close for b in reversed(bars) if b.day < DAY)
            actions = [CorporateAction(DAY, ActionKind.DIVIDEND, amount=prior * DROP)]
        return PriceSeries(iid, bars, actions, actions_source="cache")


def test_an_ex_date_drop_is_not_an_idiosyncratic_leg():
    m = measure(ExDateFeed(read_dividends=True), "MYX:1155", "Maybank", DAY)
    assert not m.error and m.last_day == DAY
    assert abs(m.components["idiosyncratic"]) < 0.012, m.components
    assert "MYX:1155 went ex-dividend" in m.adjustment and "NO DIVIDEND DATA" not in m.adjustment

    # The same bars with no dividend data: a price return, unchanged, and said.
    blind = measure(ExDateFeed(read_dividends=False), "MYX:1155", "Maybank", DAY)
    assert blind.components["idiosyncratic"] < -0.02, blind.components
    assert "NO DIVIDEND DATA for MYX:1155" in blind.adjustment


# -- the paper book ---------------------------------------------------------------------


class _DividendWorld:
    """The paper fixture's synthetic bars, with dividends read for every name."""

    def __init__(self, base, dividends: dict[str, list[tuple[date, float]]]) -> None:
        self.base, self.dividends = base, dividends
        self.name = self.source_used = "synthetic"

    def fetch(self, iid, start=None, end=None):
        s = self.base.fetch(iid, start, end)
        last = s.raw()[-1].day
        acts = [
            CorporateAction(d, ActionKind.DIVIDEND, amount=a)
            for d, a in self.dividends.get(iid, [])
            if d <= last
        ]
        return PriceSeries(iid, s.raw(), acts, actions_source="synthetic")


def _open(env, iid: str, units: int, day: date, ccy: str) -> None:
    px = Decimal(str(getattr(env, "base", env.feed).series[iid][0].close))
    env.store.record_change(
        PositionChange(
            book=DECIDED,
            target_id=None,
            day=day,
            instrument_id=iid,
            currency=ccy,
            action="open",
            units_delta=units,
            units_after=units,
            bar_open=px,
            slippage_bps=0,
            price_local=px,
            consideration_local=px * units,
            consideration_usd=px * units / (Decimal(4) if ccy == "MYR" else Decimal(1)),
            fee_local=Decimal(0),
            fee_usd=Decimal(0),
            fx_rate=Decimal(4) if ccy == "MYR" else Decimal(1),
            fx_date=day,
            fx_source="config",
            fx_spread_usd=Decimal(0),
            slippage_usd=Decimal(0),
            cash_delta_usd=Decimal(0),
            avg_cost_after=px,
        )
    )


def _paper(env, dividends):
    env.base = env.feed
    env.feed = _DividendWorld(env.feed, dividends)
    return env


def _mark(env, d: date):
    return mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d,
        slot="manual",
        now=datetime(d.year, d.month, d.day, 21, 15, tzinfo=UTC),
    )


def test_the_paper_book_is_credited_on_the_ex_date_net_of_withholding_at_the_mark_rate(paper_env):
    ex = date(2026, 3, 11)
    env = _paper(paper_env, {"MYX:5183": [(ex, 0.30)], "XNAS:AAPL": [(ex, 0.26)]})
    _open(env, "MYX:5183", 1000, date(2026, 3, 3), "MYR")
    _open(env, "XNAS:AAPL", 10, date(2026, 3, 3), "USD")
    _open(env, "MYX:8869", 100, ex, "MYR")  # bought on the ex-date: not entitled
    before = env.store.state(DECIDED, end=ex - timedelta(days=1)).cash_usd

    r = _mark(env, ex)
    divs = {c.instrument_id: c for c in r.dividends[DECIDED]}
    assert set(divs) == {"MYX:5183", "XNAS:AAPL"}, "a buy at the ex-date open is not entitled"
    bursa, us = divs["MYX:5183"], divs["XNAS:AAPL"]
    # Bursa single-tier: no withholding; RM300 at the 4.0 mid is USD 75.00.
    assert (bursa.day, bursa.units_after, bursa.units_delta) == (ex, 1000, 0)
    assert bursa.withholding_local == 0 and bursa.cash_delta_usd == Decimal("75.00")
    assert bursa.fx_spread_usd == 0 and bursa.fx_rate == env.fx.asof(ex).rate
    # US: 30% withheld from a Malaysian holder. USD 2.60 gross, 0.78 withheld.
    assert us.withholding_local == Decimal("0.78") and us.cash_delta_usd == Decimal("1.82")
    after = env.store.state(DECIDED, end=ex)
    assert after.cash_usd - before == Decimal("76.82")
    assert after.position("MYX:5183").units == 1000, "a dividend moves cash, not units"
    assert "dividend MYX:5183" in r.render()

    # Re-marking the session credits nothing twice.
    again = _mark(env, ex)
    assert again.dividends == {}
    assert sum(c.action == "dividend" for c in env.store.changes(DECIDED)) == 2


def test_marks_made_before_crediting_began_are_not_restated(paper_env):
    ex = date(2026, 3, 11)
    env = _paper(paper_env, {"MYX:5183": [(ex, 0.30), (date(2026, 3, 18), 0.10)]})
    _open(env, "MYX:5183", 1000, date(2026, 3, 3), "MYR")
    # A ledger already marked past the first ex-date by code that credited
    # nothing, as data/paper.db is: its 03-13 mark is price-only.
    at = datetime(2026, 3, 13, 21, 15, tzinfo=UTC)
    mark_book(
        env.store,
        env.cfg,
        env.base,
        env.fx,
        book=DECIDED,
        day=date(2026, 3, 13),
        slot="manual",
        now=at,
    )

    r = _mark(env, date(2026, 3, 18))
    assert env.store.dividends_from(DECIDED, at) == date(2026, 3, 14)
    assert [c.day for c in r.dividends[DECIDED]] == [date(2026, 3, 18)], (
        "the 03-11 dividend predates the boundary and is not credited"
    )


def test_a_name_with_no_dividend_data_is_unchanged_and_the_mark_says_so(paper_env):
    _open(paper_env, "MYX:5183", 1000, date(2026, 3, 3), "MYR")
    r = mark(
        paper_env.store,
        paper_env.cfg,
        paper_env.feed,
        paper_env.fx,
        day=date(2026, 3, 11),
        slot="manual",
        now=datetime(2026, 3, 11, 21, 15, tzinfo=UTC),
    )
    assert r.dividends == {}
    assert any("NO DIVIDEND DATA for MYX:5183" in n for n in r.notes), r.notes
