"""The price-source seam, and the boundary that keeps a broker SDK read-only.

Two things are under test here and only one of them is about prices.

The first is the same rule the news feed enforces: a source that cannot be read
raises rather than returning nothing. It matters more for prices, because a
short bar series does not look broken - it looks like a quiet period, and every
downstream number (ATR, ADV, the liquidity cap, the factor fit) computes
confidently on it.

The second is that moomoo's trade context never enters this repository. That is
enforced by tests/test_no_execution_anywhere.py at the level of forbidden names;
this file enforces it one step earlier, at the import.
"""

from datetime import date
from pathlib import Path

import pytest

from core.market.prices import Bar
from core.market.sources import (
    PriceSource,
    PriceSourceError,
    StaticSource,
    validate,
)
from markets.sources import moomoo_quotes
from markets.sources.moomoo_quotes import MoomooQuotes, to_moomoo_code

ROOT = Path(__file__).resolve().parent.parent
D = date(2026, 8, 3)


def bar(day: date, o=10.0, h=11.0, lo=9.5, c=10.5, v=1000.0) -> Bar:
    return Bar(day=day, open=o, high=h, low=lo, close=c, volume=v)


def days(n: int) -> list[Bar]:
    return [bar(date(2026, 8, 3 + i)) for i in range(n)]


# --- the boundary --------------------------------------------------------
def test_the_moomoo_source_never_imports_a_trade_context():
    """The whole safety argument for this module is what it imports. A comment
    saying so is not enforcement; reading the file is."""
    text = (ROOT / "markets" / "sources" / "moomoo_quotes.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]  # drop the module docstring
    for banned in (
        "OpenSecTradeContext",
        "TrdEnv",
        "SecurityFirm",
        "unlock_trade",
        "accinfo_query",
        "acc_list",
    ):
        assert banned not in body, (
            f"{banned} appears in the moomoo price source. That is the trade "
            "path; this module is quotes only."
        )


def test_the_quote_context_is_the_only_thing_imported_from_moomoo():
    """Exactly one import from the SDK, and it is the read-only one.

    Trailing tooling comments are stripped so a `# pyright: ignore` can sit on
    that line - what is asserted is what is IMPORTED, not how the line is
    annotated.
    """
    text = (ROOT / "markets" / "sources" / "moomoo_quotes.py").read_text(encoding="utf-8")
    imports = [
        line.split("#")[0].strip()
        for line in text.splitlines()
        if line.strip().startswith(("from moomoo", "import moomoo"))
    ]
    assert imports == ["from moomoo import OpenQuoteContext"], imports


def test_the_module_imports_without_the_broker_sdk_installed():
    """CI has no moomoo-api and no Windows. Importing must still work; only
    calling it may fail."""
    assert moomoo_quotes.MoomooQuotes.name == "moomoo"


def test_a_missing_sdk_is_a_source_error_not_an_import_crash():
    src = MoomooQuotes()
    with pytest.raises(PriceSourceError, match="moomoo-api"):
        src.bars("XKLS:1155", D, date(2026, 8, 10))


# --- failure never looks like a closed market ----------------------------
class _Boom(PriceSource):
    name = "boom"

    def _fetch_bars(self, instrument_id, start, end):
        raise PriceSourceError("gateway down")


class _Liar(PriceSource):
    name = "liar"

    def _fetch_bars(self, instrument_id, start, end):
        return None


def test_a_source_that_cannot_be_read_raises():
    with pytest.raises(PriceSourceError, match="gateway down"):
        _Boom().bars("XKLS:1155", D, date(2026, 8, 10))


def test_a_source_returning_none_is_refused_rather_than_read_as_empty():
    with pytest.raises(PriceSourceError, match="never None"):
        _Liar().bars("XKLS:1155", D, date(2026, 8, 10))


def test_a_genuinely_empty_range_is_not_an_error():
    """A closed market is a real answer. Only a FAILURE must raise."""
    src = StaticSource({"XKLS:1155": days(3)})
    assert src.bars("XKLS:1155", date(2026, 1, 1), date(2026, 1, 2)) == []


def test_an_unknown_instrument_raises_rather_than_reading_as_closed():
    src = StaticSource({"XKLS:1155": days(3)})
    with pytest.raises(PriceSourceError, match="no series"):
        src.bars("XKLS:9999", D, date(2026, 8, 10))


# --- validation catches what nothing downstream would -------------------
def test_bars_out_of_order_are_refused():
    rows = [bar(date(2026, 8, 5)), bar(date(2026, 8, 3))]
    with pytest.raises(PriceSourceError, match="out of order"):
        validate(rows, "XKLS:1155", "test")


def test_a_duplicated_day_is_refused():
    rows = [bar(D), bar(D)]
    with pytest.raises(PriceSourceError, match="out of order or duplicated"):
        validate(rows, "XKLS:1155", "test")


def test_high_below_low_is_refused():
    """Nothing downstream crashes on this. It becomes a negative true range,
    which shrinks ATR, which widens the position sizing is willing to take."""
    with pytest.raises(PriceSourceError, match="above high"):
        validate([bar(D, h=9.0, lo=10.0, o=9.5, c=9.5)], "XKLS:1155", "test")


def test_a_close_outside_the_range_is_refused():
    with pytest.raises(PriceSourceError, match="outside the low-high"):
        validate([bar(D, o=10.0, h=11.0, lo=9.5, c=12.0)], "XKLS:1155", "test")


def test_negative_volume_is_refused():
    with pytest.raises(PriceSourceError, match="negative volume"):
        validate([bar(D, v=-1.0)], "XKLS:1155", "test")


def test_a_backwards_date_range_is_a_caller_error():
    with pytest.raises(ValueError, match="precedes start"):
        StaticSource().bars("XKLS:1155", date(2026, 8, 10), D)


# --- instrument codes ----------------------------------------------------
def test_mic_codes_map_to_moomoo_markets():
    assert to_moomoo_code("XKLS:1155") == "MY.1155"
    assert to_moomoo_code("XHKG:00700") == "HK.00700"
    assert to_moomoo_code("XNAS:AAPL") == "US.AAPL"


def test_an_already_moomoo_code_passes_through():
    assert to_moomoo_code("MY.1155") == "MY.1155"


def test_an_unknown_market_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="no moomoo market"):
        to_moomoo_code("XTAI:2330")


def test_a_bare_ticker_is_refused_because_there_is_no_safe_default():
    with pytest.raises(ValueError, match="not MIC:CODE"):
        to_moomoo_code("AAPL")


# --- the fetch path, with an injected gateway ----------------------------
class _FakeCtx:
    def __init__(self, ret=0, data=None, raises=None):
        self._ret, self._data, self._raises = ret, data or [], raises
        self.closed = False

    def request_history_kline(self, code, start=None, end=None):
        if self._raises:
            raise self._raises
        return self._ret, self._data

    def close(self):
        self.closed = True


def _rows(n=2):
    return [
        {
            "time_key": f"2026-08-0{3 + i} 00:00:00",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000,
        }
        for i in range(n)
    ]


def test_bars_come_back_parsed_and_validated():
    ctx = _FakeCtx(data=_rows(2))
    src = MoomooQuotes(context_factory=lambda h, p: ctx)
    out = src.bars("XKLS:1155", D, date(2026, 8, 10))
    assert [b.day for b in out] == [date(2026, 8, 3), date(2026, 8, 4)]
    assert out[0].close == 10.5


def test_the_context_is_closed_even_when_the_call_fails():
    """A leaked context eats connection quota until OpenD is restarted."""
    ctx = _FakeCtx(raises=OSError("connection refused"))
    src = MoomooQuotes(context_factory=lambda h, p: ctx)
    with pytest.raises(PriceSourceError, match="logged in by hand"):
        src.bars("XKLS:1155", D, date(2026, 8, 10))
    assert ctx.closed


def test_a_nonzero_return_code_is_a_source_error_not_empty_bars():
    ctx = _FakeCtx(ret=-1, data="quota exceeded for this market")
    src = MoomooQuotes(context_factory=lambda h, p: ctx)
    with pytest.raises(PriceSourceError, match="quota"):
        src.bars("XKLS:1155", D, date(2026, 8, 10))


def test_a_malformed_bar_is_named_rather_than_silently_dropped():
    ctx = _FakeCtx(
        data=[{"time_key": "2026-08-03", "open": "x", "high": 1, "low": 1, "close": 1, "volume": 1}]
    )
    src = MoomooQuotes(context_factory=lambda h, p: ctx)
    with pytest.raises(PriceSourceError, match="cannot read"):
        src.bars("XKLS:1155", D, date(2026, 8, 10))


def test_the_gateway_defaults_match_opend():
    assert moomoo_quotes.DEFAULT_HOST == "127.0.0.1"
    assert moomoo_quotes.DEFAULT_PORT == 11111
