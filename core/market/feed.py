"""Price bars from a live source.

`PriceSeries` has existed since P1 with nothing to fill it: every entrypoint made
the caller type returns on the command line. This is the seam that fills it,
built to the same contract as `knowledge/feeds/adapter.py`.

Three properties, each of which is a silent-wrong-answer bug if dropped:

  1. **A broken feed never looks like a quiet one.** Transport failure raises
     `PriceFeedError`; a symbol the source does not carry raises `NoData`. Neither
     returns [], because an empty series reads downstream as "the stock did not
     trade", and attribution will happily explain a move that never happened.
  2. **A symbol is mapped, never guessed.** An unknown market raises. Guessing a
     suffix returns *another company's* prices - plausible, silent, and wrong,
     which is the exact failure class this repository is built around.
  3. **Bars are validated at the seam.** Non-finite values, inverted high/low and
     negative volume are rejected here. docs/05 section 3.5 records a NaN return
     reaching a verdict as `nan% unexplained`; the cheapest place to stop that is
     before it is ever a Bar.
"""

from __future__ import annotations

import csv
import io
import math
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Callable

from core.market.prices import Bar, PriceSeries


class PriceFeedError(RuntimeError):
    """The source could not be reached or answered with something unusable."""


class NoData(PriceFeedError):
    """The source answered, and carries nothing for this symbol or window.

    Separate from a transport failure on purpose: this one is a coverage fact
    about the source, and the operator's next move is a different source, not a
    retry.
    """


class SymbolUnmappable(PriceFeedError):
    """No rule exists to turn this instrument id into a source symbol."""


class PriceFeed(ABC):
    """One source of daily bars. Subclass, map the symbol, fetch the CSV."""

    name: str = "abstract"

    @abstractmethod
    def symbol_for(self, instrument_id: str) -> str: ...

    @abstractmethod
    def _fetch_csv(self, symbol: str) -> str: ...

    def fetch(
        self,
        instrument_id: str,
        start: date | None = None,
        end: date | None = None,
    ) -> PriceSeries:
        """Daily bars for one instrument, bounded to [start, end] inclusive.

        `end` is a point-in-time bound, not a convenience. Backtests and
        attribution both ask "what was knowable on day X"; a feed that returns
        tomorrow's bar makes every downstream guard irrelevant.
        """
        symbol = self.symbol_for(instrument_id)
        bars = self.parse(self._fetch_csv(symbol), symbol)
        if start is not None:
            bars = [b for b in bars if b.day >= start]
        if end is not None:
            bars = [b for b in bars if b.day <= end]
        if not bars:
            raise NoData(
                f"{self.name} returned no usable bars for {instrument_id} "
                f"(symbol {symbol!r}) in the requested window"
            )
        return PriceSeries(instrument_id, bars)

    # -- parsing -------------------------------------------------------------

    #: Column names are lowercased before lookup, so vendors that shout still work.
    REQUIRED = ("date", "open", "high", "low", "close")

    @classmethod
    def parse(cls, text: str, symbol: str = "") -> list[Bar]:
        """CSV -> validated bars. Malformed rows are dropped; a malformed FILE raises."""
        text = text.strip()
        if not text:
            raise NoData(f"empty response for {symbol!r}")

        # Sources answer a bad symbol with a plain-text apology, not a CSV. If
        # there is no header we are looking at prose, and prose is a failure.
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise PriceFeedError(f"response for {symbol!r} has no header row: {text[:120]!r}")

        fields = {(f or "").strip().lower() for f in reader.fieldnames}
        missing = [c for c in cls.REQUIRED if c not in fields]
        if missing:
            raise PriceFeedError(
                f"response for {symbol!r} is missing column(s) {missing}; "
                f"got {sorted(fields)}"
            )

        bars: list[Bar] = []
        for row in reader:
            bar = cls._row_to_bar({(k or "").strip().lower(): v for k, v in row.items()})
            if bar is not None:
                bars.append(bar)

        if not bars:
            raise NoData(f"response for {symbol!r} parsed to zero valid bars")
        return sorted(bars, key=lambda b: b.day)

    @staticmethod
    def _row_to_bar(row: dict) -> Bar | None:
        """One row, or None if it is unusable. Never a half-built bar."""
        try:
            day = datetime.strptime((row.get("date") or "").strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

        vals: dict[str, float] = {}
        for col in ("open", "high", "low", "close"):
            raw = (row.get(col) or "").strip()
            try:
                v = float(raw)
            except ValueError:
                return None                      # 'N/D', '', '-' all land here
            if not math.isfinite(v) or v <= 0:
                return None                      # a non-positive price is not a price
            vals[col] = v

        raw_vol = (row.get("volume") or "0").strip() or "0"
        try:
            volume = float(raw_vol)
        except ValueError:
            volume = 0.0
        if not math.isfinite(volume) or volume < 0:
            volume = 0.0

        # An inverted bar is corrupt, not merely odd. Downstream ATR and gap
        # logic both assume high >= max(open, close) >= min(open, close) >= low.
        if vals["high"] < vals["low"]:
            return None
        if vals["high"] < max(vals["open"], vals["close"]):
            return None
        if vals["low"] > min(vals["open"], vals["close"]):
            return None

        return Bar(day, vals["open"], vals["high"], vals["low"], vals["close"], volume)


class StooqFeed(PriceFeed):
    """Stooq daily CSV. Free, no key, no quota - which is why it is wired first.

    Coverage is per-market and not uniform. The suffix table below is the mapping
    this system claims; a market absent from it raises rather than guessing, and a
    mapped market the source does not actually carry raises `NoData` on the first
    call rather than returning silence. Check a new market with one fetch before
    trusting it.
    """

    name = "stooq"
    CSV_URL = "https://stooq.com/q/d/l/"
    TIMEOUT = 30

    #: Canonical MIC -> Stooq suffix. Keyed on the MIC only: alternate spellings
    #: (MYX for XKLS, SGX for XSES) are resolved by markets.registry, so a new
    #: alias is added in one place and every consumer picks it up. A second
    #: private alias table here is how the MYX/XKLS drift happened the first time.
    SUFFIX = {
        "XNAS": "us",
        "XNYS": "us",
        "XKLS": "my",
        "XSES": "sg",
        "XHKG": "hk",
        "XLON": "uk",
        "XTKS": "jp",
    }

    def __init__(self, opener: Callable | None = None) -> None:
        self._opener = opener

    def symbol_for(self, instrument_id: str) -> str:
        if ":" not in instrument_id:
            raise SymbolUnmappable(
                f"{instrument_id!r} has no market prefix; expected e.g. 'XNAS:NVDA'"
            )
        from markets.registry import resolve_mic

        raw_mic, _, local = instrument_id.partition(":")
        mic, local = resolve_mic(raw_mic), local.strip()
        if not local:
            raise SymbolUnmappable(f"{instrument_id!r} has an empty local code")

        suffix = self.SUFFIX.get(mic)
        if suffix is None:
            raise SymbolUnmappable(
                f"no Stooq suffix registered for market {mic!r}. Add it to "
                f"StooqFeed.SUFFIX and verify with a live fetch - guessing a "
                f"suffix returns another company's prices."
            )
        return f"{local.lower()}.{suffix}"

    def _url(self, symbol: str) -> str:
        from urllib.parse import urlencode

        return f"{self.CSV_URL}?{urlencode({'s': symbol, 'i': 'd'})}"

    def _fetch_csv(self, symbol: str) -> str:
        import urllib.error
        import urllib.request

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            self._url(symbol),
            headers={"User-Agent": "finplanet-analyst-mind/0.1 (personal research)"},
        )
        try:
            with opener(req, timeout=self.TIMEOUT) as resp:
                body = resp.read()
        except urllib.error.URLError as e:               # includes HTTPError
            raise PriceFeedError(f"Stooq fetch failed for {symbol!r}: {e}") from e
        except OSError as e:
            raise PriceFeedError(f"Stooq fetch failed for {symbol!r}: {e}") from e

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return body
