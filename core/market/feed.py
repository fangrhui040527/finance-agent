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
from collections.abc import Callable
from datetime import UTC, date, datetime

from core.market.prices import Bar, PriceSeries
from core.net.breaker import CircuitBreaker

#: Ids that name an INDEX rather than a tradable line. An index is only ever
#: fetched as a price series - it is never sized, priced, charged a fee or
#: held - so it needs no market adapter entry. It does need a symbol rule of
#: its own, because no exchange suffix applies to it: Yahoo writes the FBM
#: KLCI as `^KLSE`, with a caret and no `.KL`. A feed with no literal for an id
#: listed here REFUSES rather than sending it through the suffix rule, which
#: would build a plausible symbol for something else entirely - property 2 of
#: this module, applied to the one case the suffix tables cannot express.
INDEX_IDS: frozenset[str] = frozenset({"MYX:^KLSE"})


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

    #: Index id -> this feed's literal symbol. See INDEX_IDS. Empty by default,
    #: so a feed that has never been asked about an index refuses instead of
    #: inheriting another feed's spelling.
    LITERAL: dict[str, str] = {}

    @abstractmethod
    def symbol_for(self, instrument_id: str) -> str: ...

    def _index_symbol(self, instrument_id: str) -> str:
        """The literal symbol for an index id, or a refusal naming the fix.

        Separate from the suffix rule because it is a different kind of answer:
        the suffix tables say how a market spells its shares, and an index is
        not one. A feed that cannot spell it says so - it does not fall through
        and return `^KLSE.KL`, which Yahoo would answer for with somebody
        else's prices or with nothing at all.
        """
        literal = self.LITERAL.get(instrument_id)
        if literal is None:
            raise SymbolUnmappable(
                f"{instrument_id!r} is an index and {self.name!r} has no symbol for "
                f"it. Add one to {type(self).__name__}.LITERAL and verify it with a "
                f"live fetch - market-proxy-probe.yml does exactly that."
            )
        return literal

    @abstractmethod
    def _fetch_csv(self, symbol: str) -> str: ...

    def fetched_at(self, instrument_id: str) -> datetime | None:
        """When the body behind this instrument's bars was pulled, if knowable.

        One implementation for every cached feed, because the answer is the
        cache's and not the feed's. A feed with no cache, or a symbol it cannot
        spell, answers None - and None is reported as `unknown`, never as a
        close. See `core.market.calendar.price_state`.
        """
        cache = getattr(self, "cache", None)
        if cache is None:
            return None
        try:
            symbol = self.symbol_for(instrument_id)
        except (PriceFeedError, ValueError):
            return None
        return cache.fetched_at(self.name, symbol)

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
        cache = getattr(self, "cache", None)
        body = cache.get(self.name, symbol) if cache is not None else None
        if body is None:
            body = self._fetch_csv(symbol)
            if cache is not None:
                cache.put(self.name, symbol, body)
        bars = self.parse(body, symbol)
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

        # A page, not data. stooq.com started answering every non-browser
        # client with a JavaScript check on 2026-08-31; "missing column(s)" was
        # true and told the operator nothing about what had happened.
        if text.startswith("<"):
            raise PriceFeedError(
                f"response for {symbol!r} is an HTML page, not CSV - a browser or "
                f"JavaScript check, or an error page, is standing in front of the "
                f"data: {text[:100]!r}"
            )

        # Sources answer a bad symbol with a plain-text apology, not a CSV. If
        # there is no header we are looking at prose, and prose is a failure.
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise PriceFeedError(f"response for {symbol!r} has no header row: {text[:120]!r}")

        fields = {(f or "").strip().lower() for f in reader.fieldnames}
        missing = [c for c in cls.REQUIRED if c not in fields]
        if missing:
            raise PriceFeedError(
                f"response for {symbol!r} is missing column(s) {missing}; got {sorted(fields)}"
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
                return None  # 'N/D', '', '-' all land here
            if not math.isfinite(v) or v <= 0:
                return None  # a non-positive price is not a price
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


def _guarded_read(source, symbol, breaker, opener, req, timeout, sleep) -> str:
    """Transport with retry and a per-feed circuit breaker.

    Retries only what waiting can fix (connection errors, 408/425/429/5xx,
    honouring Retry-After); a 404 becomes `NoData` - it is an answer about
    coverage, not an outage - and neither NoData nor an unmappable symbol
    ever trips the breaker. The Stooq HTML wall arrives as a 200, so it is
    handled at parse level and deliberately never retried here.
    """
    import urllib.error

    from core.net.breaker import CircuitOpen
    from core.net.retry import with_retry

    def _transport() -> bytes:
        with opener(req, timeout=timeout) as resp:
            return resp.read()

    kwargs = {"sleep": sleep} if sleep is not None else {}
    try:
        breaker.before_call()
        body = with_retry(_transport, **kwargs)
    except CircuitOpen as e:
        raise PriceFeedError(str(e)) from e
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoData(f"{source} carries nothing at this URL for {symbol!r} (404)") from e
        breaker.record_failure(e)
        raise PriceFeedError(f"{source} fetch failed for {symbol!r}: {e}") from e
    except (urllib.error.URLError, OSError) as e:
        breaker.record_failure(e)
        raise PriceFeedError(f"{source} fetch failed for {symbol!r}: {e}") from e
    breaker.record_success()
    return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body


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

    def __init__(
        self, opener: Callable | None = None, sleep: Callable | None = None, cache=None
    ) -> None:
        self._opener = opener
        self._sleep = sleep
        self._breaker = CircuitBreaker("stooq")
        self.cache = cache

    #: Index id -> this feed's literal symbol; see INDEX_IDS. Empty: no Stooq
    #: symbol for the FBM KLCI has been verified from a runner, and Stooq is
    #: behind a browser wall here anyway, so the chain falls through to Yahoo.
    LITERAL: dict[str, str] = {}

    def symbol_for(self, instrument_id: str) -> str:
        if ":" not in instrument_id:
            raise SymbolUnmappable(
                f"{instrument_id!r} has no market prefix; expected e.g. 'XNAS:NVDA'"
            )
        if instrument_id in INDEX_IDS:
            return self._index_symbol(instrument_id)
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
        return _guarded_read("Stooq", symbol, self._breaker, opener, req, self.TIMEOUT, self._sleep)


class YahooFeed(PriceFeed):
    """Yahoo Finance v8 chart endpoint. Free, no key, JSON, every market here.

    Wired the day stooq.com put a JavaScript browser check in front of its CSV
    (2026-08-31) and the only price source went dark. The JSON is rewritten into
    the CSV shape the base class already validates, so a Yahoo bar passes
    exactly the checks a Stooq bar does and nothing downstream knows which
    source answered.

    Same two rules as Stooq: a symbol is mapped from the MIC and never guessed,
    and an unusable answer raises. The endpoint has no published contract; a
    change in its shape shows up here as `PriceFeedError`, never as silence.
    """

    name = "yahoo"
    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
    TIMEOUT = 30
    #: Long enough for the 260-session estimation window plus its gap.
    RANGE = "5y"

    #: Canonical MIC -> Yahoo suffix. Every registered adapter has one, which is
    #: the coverage Stooq never had.
    SUFFIX = {
        "XNAS": "",
        "XKLS": ".KL",
        "XSES": ".SI",
        "XHKG": ".HK",
        "XLON": ".L",
        "XTKS": ".T",
        "XASX": ".AX",
        "XNSE": ".NS",
        "XTAI": ".TW",
        "XKRX": ".KS",
        "XETR": ".DE",
    }

    def __init__(self, opener=None, sleep=None, cache=None) -> None:
        self._opener = opener
        self._sleep = sleep
        self._breaker = CircuitBreaker("yahoo")
        self.cache = cache

    #: Index id -> this feed's literal symbol; see INDEX_IDS. `^KLSE` is the FBM
    #: KLCI as Yahoo writes it, confirmed from a runner by market-proxy-probe -
    #: the dev environment answers 403 for every Yahoo symbol, so it could not
    #: be confirmed here.
    LITERAL: dict[str, str] = {"MYX:^KLSE": "^KLSE"}

    def symbol_for(self, instrument_id: str) -> str:
        if ":" not in instrument_id:
            raise SymbolUnmappable(
                f"{instrument_id!r} has no market prefix; expected e.g. 'XNAS:NVDA'"
            )
        if instrument_id in INDEX_IDS:
            return self._index_symbol(instrument_id)
        from markets.registry import resolve_mic

        raw_mic, _, local = instrument_id.partition(":")
        mic, local = resolve_mic(raw_mic), local.strip().upper()
        if not local:
            raise SymbolUnmappable(f"{instrument_id!r} has an empty local code")
        suffix = self.SUFFIX.get(mic)
        if suffix is None:
            raise SymbolUnmappable(
                f"no Yahoo suffix registered for market {mic!r}. Add it to "
                f"YahooFeed.SUFFIX and verify with a live fetch - guessing a suffix "
                f"returns another company's prices."
            )
        if mic == "XHKG" and local.isdigit():
            local = local.zfill(4)  # Yahoo writes 0005.HK, never 5.HK
        return f"{local}{suffix}"

    def _url(self, symbol: str) -> str:
        from urllib.parse import quote, urlencode

        return (
            f"{self.CHART_URL}{quote(symbol)}?{urlencode({'range': self.RANGE, 'interval': '1d'})}"
        )

    def _fetch_csv(self, symbol: str) -> str:
        """JSON in, the CSV the base class validates out."""
        import json
        import urllib.error
        import urllib.request

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            self._url(symbol),
            headers={
                "User-Agent": "finplanet-analyst-mind/0.1 (personal research)",
                "Accept": "application/json",
            },
        )
        body = _guarded_read("Yahoo", symbol, self._breaker, opener, req, self.TIMEOUT, self._sleep)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise PriceFeedError(f"response for {symbol!r} is not JSON: {body[:120]!r}") from e
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise PriceFeedError(f"response for {symbol!r} has no chart object: {body[:120]!r}")
        results = chart.get("result")
        if not results:
            err = chart.get("error") or {}
            raise NoData(
                f"Yahoo carries nothing for {symbol!r}: "
                f"{err.get('description') or err.get('code') or 'empty result'}"
            )

        result = results[0] if isinstance(results[0], dict) else {}
        stamps = result.get("timestamp") or []
        quotes = (result.get("indicators") or {}).get("quote") or [{}]
        quote_block = quotes[0] if isinstance(quotes[0], dict) else {}
        # Session timestamps are the exchange's open in UTC seconds; adding the
        # exchange offset yields the exchange's own calendar day.
        offset = int((result.get("meta") or {}).get("gmtoffset") or 0)

        def cell(column: str, i: int) -> str:
            series = quote_block.get(column) or []
            value = series[i] if i < len(series) else None
            return "" if value is None else repr(float(value))

        rows = ["date,open,high,low,close,volume"]
        for i, stamp in enumerate(stamps):
            day = datetime.fromtimestamp(int(stamp) + offset, tz=UTC).date()
            cells = [cell(c, i) for c in ("open", "high", "low", "close", "volume")]
            rows.append(",".join([day.isoformat()] + cells))
        return "\n".join(rows) + "\n"


#: The instrument whose bars stand for "the market" when a move is decomposed
#: (`ask.py why --against`, the MCP tool's `market_proxy`). SPY tracks the S&P
#: 500 and is one of the most heavily traded instruments in the world; `^KLSE`
#: is the FBM KLCI itself.
#:
#: BURSA WAS `MYX:0820EA`, THE KLCI ETF, AND THAT WAS THE WRONG KIND OF THING.
#: The original reason was symbol mechanics: the feeds carry ETFs under the
#: same suffix rule as any share, and an index needs a rule of its own. That
#: reason was real and it was cheap to fix - `_index_symbol` above is the rule.
#: What it bought was four days of wrong answers:
#:
#:   median volume        2,800 shares/day, against Maybank's 11,027,500 (3,937x)
#:   sessions not traded   164 of 1,231 (13.3%)
#:   2026-09-10            no close at all, so the Bursa half of that day's page
#:                         could not be decomposed and said so
#:   2026-09-11            an UNCHANGED 1.8300 printed on 500 shares, so the
#:                         market leg read +0.00%, every Bursa name's unexplained
#:                         share was 100% by construction, and Petronas Chemicals
#:                         carried a beta of -0.99 against "the market"
#:
#: A proxy must be at least as liquid as the things it explains. The ETF is
#: not, and a stale proxy is worse than a missing one: a blank close stops the
#: arithmetic and announces itself, while an unchanged close on 500 shares
#: restarts it with a number that reads like a finding.
MARKET_PROXIES: dict[str, str] = {
    "XNAS": "XNAS:SPY",
    "XNYS": "XNAS:SPY",
    "XKLS": "MYX:^KLSE",
}


def market_proxy_for(instrument_id: str) -> str | None:
    """The proxy for an instrument's market, or None when none is registered."""
    from markets.registry import mic_of

    try:
        return MARKET_PROXIES.get(mic_of(instrument_id))
    except ValueError:
        return None


class ChainedFeed:
    """Sources in order of preference; the first that answers wins.

    Not a `PriceFeed` subclass, because it has no symbol of its own. Each
    source keeps its own guarantees: one that raises is skipped, and when none
    answers the error names every source tried, so a walled site, a missing
    suffix and a real outage are all visible in one message rather than one at
    a time. `source_used` records who answered, for the trace.
    """

    name = "chain"

    def __init__(self, feeds) -> None:
        self.feeds = list(feeds)
        self.source_used = None

    def fetched_at(self, instrument_id: str) -> datetime | None:
        """The first feed that has this symbol cached - the one `fetch` serves from."""
        for feed in self.feeds:
            at = feed.fetched_at(instrument_id)
            if at is not None:
                return at
        return None

    def fetch(self, instrument_id: str, start=None, end=None) -> PriceSeries:
        failures = []
        for feed in self.feeds:
            try:
                series = feed.fetch(instrument_id, start, end)
            except PriceFeedError as e:
                failures.append(f"{feed.name}: {e}")
                continue
            self.source_used = feed.name
            return series
        raise PriceFeedError(
            f"every price source failed for {instrument_id!r}:\n  " + "\n  ".join(failures)
        )


_DEFAULT: ChainedFeed | None = None


def default_feed() -> ChainedFeed:
    """What the surfaces use. Stooq first for its depth of history; Yahoo when
    Stooq is walled, down, or has no suffix for the market.

    One instance per process, on purpose: the circuit breakers accumulate
    evidence across calls, and both feeds share a same-trading-day cache so a
    repeated question costs zero quota (Stooq counts daily hits)."""
    global _DEFAULT
    if _DEFAULT is None:
        try:
            from core.market.cache import PriceCache

            cache = PriceCache()
        except Exception:  # a broken cache must degrade to fetching, not block prices
            cache = None
        _DEFAULT = ChainedFeed([StooqFeed(cache=cache), YahooFeed(cache=cache)])
    return _DEFAULT
