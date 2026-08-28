"""Live price ingest, tested without a network.

The property under test throughout: an empty series must be impossible to obtain
by accident. Downstream, [] reads as "the stock did not trade", and attribution
will explain a move that never happened.
"""
import urllib.error
from datetime import date

import pytest

from core.market.feed import (
    NoData,
    PriceFeed,
    PriceFeedError,
    StooqFeed,
    SymbolUnmappable,
)

CSV = """Date,Open,High,Low,Close,Volume
2026-01-02,10.00,10.40,9.90,10.30,1000000
2026-01-05,10.30,10.55,10.10,10.20,850000
2026-01-06,10.20,10.60,10.15,10.55,910000
"""


class _Response:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body: str, capture: list | None = None):
    def open_(req, timeout=None):
        if capture is not None:
            capture.append(req)
        return _Response(body)
    return open_


# --- symbols are mapped, never guessed ------------------------------------
def test_known_markets_map_to_their_suffix():
    f = StooqFeed()
    assert f.symbol_for("XNAS:NVDA") == "nvda.us"
    assert f.symbol_for("MYX:1155") == "1155.my"
    assert f.symbol_for("XSES:D05") == "d05.sg"
    assert f.symbol_for("XHKG:0700") == "0700.hk"


def test_an_unknown_market_raises_rather_than_guessing_a_suffix():
    """A guessed suffix returns another company's prices. Silent and plausible."""
    with pytest.raises(SymbolUnmappable, match="no Stooq suffix"):
        StooqFeed().symbol_for("XFRA:BMW")


def test_a_bare_ticker_with_no_market_is_refused():
    with pytest.raises(SymbolUnmappable, match="no market prefix"):
        StooqFeed().symbol_for("NVDA")


def test_an_empty_local_code_is_refused():
    with pytest.raises(SymbolUnmappable, match="empty local code"):
        StooqFeed().symbol_for("XNAS:")


# --- the happy path -------------------------------------------------------
def test_bars_come_back_sorted_and_typed():
    series = StooqFeed(opener=_opener(CSV)).fetch("XNAS:NVDA")
    assert len(series) == 3
    bars = series.raw()
    assert [b.day for b in bars] == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    assert bars[0].close == 10.30
    assert bars[2].volume == 910000


def test_rows_arriving_out_of_order_are_sorted():
    jumbled = "Date,Open,High,Low,Close,Volume\n" \
              "2026-01-06,10.20,10.60,10.15,10.55,1\n" \
              "2026-01-02,10.00,10.40,9.90,10.30,1\n"
    bars = StooqFeed(opener=_opener(jumbled)).fetch("XNAS:NVDA").raw()
    assert [b.day for b in bars] == [date(2026, 1, 2), date(2026, 1, 6)]


def test_uppercase_headers_still_parse():
    shouted = CSV.replace("Date,Open,High,Low,Close,Volume", "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME")
    assert len(StooqFeed(opener=_opener(shouted)).fetch("XNAS:NVDA")) == 3


def test_the_request_carries_the_mapped_symbol():
    seen: list = []
    StooqFeed(opener=_opener(CSV, seen)).fetch("MYX:1155")
    assert "s=1155.my" in seen[0].full_url


# --- point-in-time bounds -------------------------------------------------
def test_end_bound_excludes_later_bars():
    """A feed returning tomorrow's bar makes every lookahead guard downstream
    irrelevant."""
    series = StooqFeed(opener=_opener(CSV)).fetch("XNAS:NVDA", end=date(2026, 1, 5))
    assert [b.day for b in series.raw()] == [date(2026, 1, 2), date(2026, 1, 5)]


def test_start_bound_excludes_earlier_bars():
    series = StooqFeed(opener=_opener(CSV)).fetch("XNAS:NVDA", start=date(2026, 1, 5))
    assert len(series) == 2


def test_a_window_containing_nothing_raises_rather_than_returning_empty():
    with pytest.raises(NoData, match="no usable bars"):
        StooqFeed(opener=_opener(CSV)).fetch("XNAS:NVDA", start=date(2027, 1, 1))


# --- broken never looks like quiet ----------------------------------------
def test_transport_failure_raises():
    def open_(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(PriceFeedError, match="fetch failed"):
        StooqFeed(opener=open_).fetch("XNAS:NVDA")


def test_http_error_raises():
    def open_(req, timeout=None):
        raise urllib.error.HTTPError("u", 404, "not found", {}, None)

    with pytest.raises(PriceFeedError, match="fetch failed"):
        StooqFeed(opener=open_).fetch("XNAS:NVDA")


def test_an_empty_body_raises():
    with pytest.raises(NoData, match="empty response"):
        StooqFeed(opener=_opener("   ")).fetch("XNAS:NVDA")


def test_prose_instead_of_csv_raises():
    """Stooq answers an unknown symbol with a plain-text apology, not a CSV."""
    with pytest.raises(PriceFeedError, match="missing column"):
        StooqFeed(opener=_opener("No data\n")).fetch("XNAS:WRONG")


def test_a_csv_missing_a_price_column_raises():
    body = "Date,Open,High,Volume\n2026-01-02,10,11,5\n"
    with pytest.raises(PriceFeedError, match="missing column"):
        StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")


def test_a_header_only_csv_raises_rather_than_returning_empty():
    with pytest.raises(NoData, match="zero valid bars"):
        StooqFeed(opener=_opener("Date,Open,High,Low,Close,Volume\n")).fetch("XNAS:NVDA")


# --- bad bars are rejected at the seam ------------------------------------
def test_a_non_finite_close_is_dropped_not_passed_downstream():
    """docs/05 3.5: a NaN return reached a verdict as `nan% unexplained`."""
    body = CSV + "2026-01-07,10.55,NaN,10.50,10.60,1000\n"
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_an_inverted_bar_is_dropped():
    body = CSV + "2026-01-07,10.00,9.00,11.00,10.50,1000\n"     # high < low
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_a_high_below_the_close_is_dropped():
    body = CSV + "2026-01-07,10.00,10.20,9.90,10.90,1000\n"     # close above high
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_a_low_above_the_open_is_dropped():
    body = CSV + "2026-01-07,9.50,10.20,9.90,10.10,1000\n"      # open below low
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_a_zero_or_negative_price_is_dropped():
    body = CSV + "2026-01-07,0,0,0,0,1000\n"
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_placeholder_prices_are_dropped():
    body = CSV + "2026-01-07,N/D,N/D,N/D,N/D,N/D\n"
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_a_malformed_date_is_dropped():
    body = CSV + "not-a-date,10.0,10.5,9.9,10.2,100\n"
    assert len(StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA")) == 3


def test_a_negative_volume_is_zeroed_not_dropped():
    """Volume is a liquidity input, not a price. A bad one should not lose the bar."""
    body = CSV + "2026-01-07,10.55,10.70,10.50,10.60,-5\n"
    bars = StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA").raw()
    assert len(bars) == 4 and bars[-1].volume == 0.0


def test_a_missing_volume_column_is_tolerated():
    body = "Date,Open,High,Low,Close\n2026-01-02,10.00,10.40,9.90,10.30\n"
    bars = StooqFeed(opener=_opener(body)).fetch("XNAS:NVDA").raw()
    assert len(bars) == 1 and bars[0].volume == 0.0


# --- the series is usable by the engines that needed it -------------------
def test_the_result_feeds_the_engines_that_had_no_data_path():
    series = StooqFeed(opener=_opener(CSV)).fetch("XNAS:NVDA")
    assert len(series.returns()) == 2
    assert series.adv(n=3) > 0
    assert series.atr(n=3) >= 0


def test_parse_is_reusable_for_a_file_on_disk():
    """The class method is the seam for a vendor dump; no HTTP involved."""
    assert len(PriceFeed.parse(CSV)) == 3
