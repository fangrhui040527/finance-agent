"""The price cache offline, and the market proxies the routine decomposes against."""

from __future__ import annotations

import pytest

from core.market.cache import PriceCache, offline
from core.market.feed import MARKET_PROXIES, YahooFeed, market_proxy_for

CSV = "date,open,high,low,close,volume\n2026-09-03,1,2,0.5,1.5,100\n"


def test_a_stale_row_is_not_served_by_default(tmp_path):
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.conn.execute("INSERT INTO price_csv VALUES ('yahoo','NVDA','2026-09-03',?)", (CSV,))
    cache.conn.commit()
    assert cache.get("yahoo", "NVDA") is None


def test_offline_serves_the_stale_row_and_says_which_day(tmp_path, monkeypatch):
    """The nightly routine runs where no price host is reachable; the collector
    filled the cache hours earlier. Offline, the cache answers and names the day
    it answered from, so nothing pretends a cached bar is today's."""
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    assert offline()
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-09-04")
    cache.conn.execute("INSERT INTO price_csv VALUES ('yahoo','NVDA','2026-09-03',?)", (CSV,))
    cache.conn.commit()
    assert cache.get("yahoo", "NVDA") == CSV
    assert cache.last_served_from == "2026-09-03"


def test_offline_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("FINPLANET_OFFLINE", raising=False)
    assert not offline()
    monkeypatch.setenv("FINPLANET_OFFLINE", "0")
    assert not offline()


def test_every_market_the_book_trades_has_a_proxy_the_feed_can_price():
    for mic, proxy in MARKET_PROXIES.items():
        assert YahooFeed().symbol_for(proxy), f"{mic}: {proxy} has no Yahoo symbol"
    assert market_proxy_for("MYX:1155") == "MYX:0820EA"
    assert market_proxy_for("XNAS:NVDA") == "XNAS:SPY"
    assert market_proxy_for("nonsense") is None


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
    assert "MYX:1155" in err and "XNAS:SPY" in err and "MYX:0820EA" in err


def test_prices_without_an_instrument_or_book_is_a_usage_error(capsys):
    import ask

    assert ask.main(["prices"]) == 2
    assert "name an instrument" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_offline_accepts_the_usual_spellings(monkeypatch, flag):
    monkeypatch.setenv("FINPLANET_OFFLINE", flag)
    assert offline()
