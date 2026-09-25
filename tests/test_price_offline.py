"""The price cache offline, and the market proxies the routine decomposes against."""

from __future__ import annotations

import pytest

from core.market.cache import PriceCache, looks_like_bars, offline
from core.market.feed import MARKET_PROXIES, YahooFeed, market_proxy_for

CSV = "date,open,high,low,close,volume\n2026-09-03,1,2,0.5,1.5,100\n"


def test_a_stale_row_is_not_served_by_default(tmp_path):
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('yahoo','NVDA','2026-09-03',?)",
        (CSV,),
    )
    cache.conn.commit()
    assert cache.get("yahoo", "NVDA") is None


def test_offline_serves_the_stale_row_and_says_which_day(tmp_path, monkeypatch):
    """The nightly routine runs where no price host is reachable; the collector
    filled the cache hours earlier. Offline, the cache answers and names the day
    it answered from, so nothing pretends a cached bar is today's."""
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    assert offline()
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('yahoo','NVDA','2026-09-03',?)",
        (CSV,),
    )
    cache.conn.commit()
    assert cache.get("yahoo", "NVDA") == CSV
    assert cache.last_served_from == "2026-09-03"


ERROR_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="robots" '
    'content="noindex"></head><body>403 Forbidden</body></html>'
)


def test_an_error_page_is_neither_stored_nor_served(tmp_path, monkeypatch):
    """All 27 Stooq rows in this repository's cache were the same 403 page.

    Nothing downstream was fooled - the CSV parser rejects HTML - but the cache
    reported a hit, so the next process skipped a fetch that might have worked.
    """
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.put("stooq", "1155.my", ERROR_PAGE)
    assert cache.conn.execute("SELECT count(*) FROM price_csv").fetchone()[0] == 0

    # A row already poisoned by an older build is dropped the first time it is read.
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('stooq','1155.my','2026-09-04',?)",
        (ERROR_PAGE,),
    )
    cache.conn.commit()
    assert cache.get("stooq", "1155.my") is None
    assert cache.conn.execute("SELECT count(*) FROM price_csv").fetchone()[0] == 0


def test_prune_unusable_drops_the_error_pages_and_keeps_the_bars(tmp_path):
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('stooq','1155.my','2026-09-04',?)",
        (ERROR_PAGE,),
    )
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('yahoo','NVDA','2026-09-04',?)",
        (CSV,),
    )
    cache.conn.commit()
    assert cache.prune_unusable() == 1
    assert [r[0] for r in cache.conn.execute("SELECT feed FROM price_csv")] == ["yahoo"]


def test_looks_like_bars_reads_the_shapes_the_feeds_actually_return():
    assert looks_like_bars(CSV)
    assert not looks_like_bars(ERROR_PAGE)
    assert not looks_like_bars("")
    assert not looks_like_bars("No data\n")  # stooq's plain-text refusal
    assert not looks_like_bars("date,open,high,low,close,volume\n")  # a header alone


def test_offline_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("FINPLANET_OFFLINE", raising=False)
    assert not offline()
    monkeypatch.setenv("FINPLANET_OFFLINE", "0")
    assert not offline()


def test_every_market_the_book_trades_has_a_proxy_the_feed_can_price():
    for mic, proxy in MARKET_PROXIES.items():
        assert YahooFeed().symbol_for(proxy), f"{mic}: {proxy} has no Yahoo symbol"
    assert market_proxy_for("MYX:1155") == "MYX:^KLSE"
    assert market_proxy_for("XNAS:NVDA") == "XNAS:SPY"
    assert market_proxy_for("nonsense") is None


def test_an_index_proxy_is_spelled_out_per_feed_and_never_suffixed():
    """`^KLSE` is not `KLSE.KL`, and the difference is the whole point.

    The suffix tables say how a market spells its SHARES. An index is not one,
    so it cannot go through them: `^KLSE` + `.KL` is a string Yahoo will answer
    for with nothing, or with something else, and this module's property 2 says
    a symbol is mapped and never guessed. Each feed therefore spells an index
    out by hand or refuses.
    """
    from core.market.feed import INDEX_IDS, StooqFeed, SymbolUnmappable

    assert "MYX:^KLSE" in INDEX_IDS
    assert YahooFeed().symbol_for("MYX:^KLSE") == "^KLSE"
    # The share rule is untouched by the index branch.
    assert YahooFeed().symbol_for("MYX:1155") == "1155.KL"
    assert YahooFeed().symbol_for("XNAS:NVDA") == "NVDA"

    with pytest.raises(SymbolUnmappable) as exc:
        StooqFeed().symbol_for("MYX:^KLSE")
    said = str(exc.value)
    assert "index" in said and "LITERAL" in said, "the refusal must name the fix"
    assert ".mystock" not in said, "a refusal, not a guessed symbol"


def test_no_market_proxy_is_a_name_the_book_holds():
    """A proxy that is also a holding explains a name with itself.

    Not a hypothetical: the Bursa proxy was the KLCI ETF, a tradable Bursa line,
    and the only thing that kept it out of the book was that nobody added it.
    """
    from core.config import load

    cfg = load()
    book = set(getattr(cfg, "watchlist", ()) or ()) | set(getattr(cfg, "holdings", ()) or ())
    assert book, "the config carries a book; an empty one would pass vacuously"
    assert book.isdisjoint(set(MARKET_PROXIES.values()))


def test_prices_book_refuses_nothing_but_reports_each_failure(monkeypatch, capsys):
    """Offline here means every fetch fails; the command must still visit every
    name, say so per name, and exit 3 rather than raise on the first."""
    import ask
    from core.market.feed import ChainedFeed, PriceFeedError

    class _Down:
        name = "down"

        def fetch(self, iid, start=None, end=None):
            raise PriceFeedError(f"{iid}: no route")

    monkeypatch.setattr(ask, "_feed", lambda: ChainedFeed([_Down()]))
    code = ask.main(["prices", "--book"])
    err = capsys.readouterr().err
    assert code == 3
    assert "MYX:1155" in err and "XNAS:SPY" in err and "MYX:^KLSE" in err


def test_prices_without_an_instrument_or_book_is_a_usage_error(capsys):
    import ask

    assert ask.main(["prices"]) == 2
    assert "name an instrument" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_offline_accepts_the_usual_spellings(monkeypatch, flag):
    monkeypatch.setenv("FINPLANET_OFFLINE", flag)
    assert offline()


class _Hangs:
    """An opener standing in for a host that accepts and never answers."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        raise TimeoutError("timed out")


def test_offline_a_feed_with_nothing_cached_answers_at_once_and_never_fetches(
    tmp_path, monkeypatch
):
    """2026-09-25: system-test part 7, `FINPLANET_OFFLINE=1 ask.py paper status`,
    hung 14 minutes on two runs. The cache holds Yahoo's bodies and no Stooq
    row, and the chain asks Stooq first, so each "offline" read went to
    stooq.com - which had begun to hang instead of refusing. Offline, a miss
    is NoData, raised before any socket is opened, and the chain takes the
    next feed's cached body."""
    from core.market.feed import ChainedFeed, NoData, PriceFeedError, StooqFeed

    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-25")
    cache.conn.execute(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES ('yahoo','NVDA','2026-09-24',?)",
        (CSV,),
    )
    cache.conn.commit()
    stooq_net, yahoo_net = _Hangs(), _Hangs()
    stooq = StooqFeed(opener=stooq_net, sleep=lambda s: None, cache=cache)
    yahoo = YahooFeed(opener=yahoo_net, sleep=lambda s: None, cache=cache)

    with pytest.raises(NoData, match="FINPLANET_OFFLINE=1 forbids fetching"):
        stooq.fetch("XNAS:NVDA")
    series = ChainedFeed([stooq, yahoo]).fetch("XNAS:NVDA")
    assert series.raw()[-1].close == 1.5
    assert stooq_net.calls == 0 and yahoo_net.calls == 0, "offline opened a socket"

    # Nothing cached anywhere: an answer naming both feeds, not a wait.
    with pytest.raises(PriceFeedError, match="(?s)stooq: .*forbids.*yahoo: .*forbids"):
        ChainedFeed([stooq, yahoo]).fetch("XNAS:AAPL")
    assert stooq_net.calls == 0 and yahoo_net.calls == 0


def test_online_a_cache_miss_still_fetches(tmp_path, monkeypatch):
    from core.market.feed import PriceFeedError, StooqFeed

    monkeypatch.delenv("FINPLANET_OFFLINE", raising=False)
    net = _Hangs()
    feed = StooqFeed(opener=net, sleep=lambda s: None, cache=PriceCache(tmp_path / "p.db"))
    with pytest.raises(PriceFeedError):
        feed.fetch("XNAS:NVDA")
    assert net.calls == 3, "online, the miss is fetched (and retried) as before"
