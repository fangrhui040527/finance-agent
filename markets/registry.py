"""Adapter registry. A new market is one entry here plus one class.

It also owns the alias map, because instrument ids and MICs are not the same
string and pretending otherwise is a silent-wrong-answer bug.

Bursa is written `MYX:1155` throughout this repository - README, verify.py,
predict.py, the tests - while its adapter MIC is `XKLS`. Nothing mapped between
them, so `cost_floor_bps("MYX")` missed the table and quietly returned the 30 bps
DEFAULT instead of Bursa's 60. Every Bursa position was sized against a floor
half the real one, and nothing crashed: exactly the failure mode docs/05 records
for the six drifted agent ids. One canonical resolver, used everywhere an id is
turned into a market, is the fix.
"""

from __future__ import annotations

from markets.contract import MarketAdapter
from markets.holidays import closures
from markets.xasx import XASX
from markets.xetr import XETR
from markets.xhkg import XHKG
from markets.xkls import XKLS
from markets.xkrx import XKRX
from markets.xlon import XLON
from markets.xnas import XNAS
from markets.xnse import XNSE
from markets.xses import XSES
from markets.xtai import XTAI
from markets.xtks import XTKS

_ADAPTERS: dict[str, type[MarketAdapter]] = {
    "XKLS": XKLS,
    "XNAS": XNAS,
    "XSES": XSES,
    "XHKG": XHKG,
    "XTKS": XTKS,
    "XLON": XLON,
    "XASX": XASX,
    "XNSE": XNSE,
    "XTAI": XTAI,
    "XKRX": XKRX,
    "XETR": XETR,
}
_CACHE: dict[str, MarketAdapter] = {}

#: Prefix as written in instrument ids -> the ISO 10383 MIC the adapter uses.
#: Only for genuine aliases of a REGISTERED market. Adding a market goes in
#: _ADAPTERS; adding a second spelling for one goes here.
ALIASES: dict[str, str] = {
    "MYX": "XKLS",  # Bursa Malaysia: ids say MYX, the MIC is XKLS
    "KLSE": "XKLS",  # the pre-2004 name, still in older sources
    "SGX": "XSES",
    "NASDAQ": "XNAS",
    "HKEX": "XHKG",
    "SEHK": "XHKG",
    # XJPX is the Japan Exchange Group operator MIC; XTKS is the Tokyo Stock
    # Exchange segment where shares actually trade. docs/06 wrote XJPX while
    # core/market/feed.py already wrote XTKS - the same doc-versus-code drift
    # MYX/XKLS caused, mapped here before it can cost anything.
    "XJPX": "XTKS",
    "TSE": "XTKS",  # Tokyo; also the initialism for several other exchanges,
    "TYO": "XTKS",  # which is exactly why the MIC is the canonical form
    "LSE": "XLON",
    "ASX": "XASX",
    "NSE": "XNSE",
    "TWSE": "XTAI",
    "KRX": "XKRX",
    "KOSPI": "XKRX",
    "XETRA": "XETR",
}


def resolve_mic(mic: str) -> str:
    """Canonical MIC for any spelling. Unknown strings pass through unchanged,
    so the caller still gets a useful KeyError naming what it actually asked for."""
    up = mic.strip().upper()
    return ALIASES.get(up, up)


def mic_of(instrument_id: str) -> str:
    """`MYX:1155` -> `XKLS`. The one place an id becomes a market."""
    prefix, sep, _ = instrument_id.partition(":")
    if not sep:
        raise ValueError(
            f"{instrument_id!r} has no market prefix; expected e.g. 'MYX:1155' or 'XNAS:NVDA'"
        )
    return resolve_mic(prefix)


def get(mic: str) -> MarketAdapter:
    mic = resolve_mic(mic)
    if mic not in _CACHE:
        try:
            cls = _ADAPTERS[mic]
        except KeyError as exc:
            raise KeyError(
                f"no adapter registered for MIC {mic!r}; supported: {supported()}"
            ) from exc
        # The one place a market meets its published closures. The classes
        # default to none so a test can build one bare, and for a year every
        # caller got exactly that: `_ADAPTERS[mic]()` made each calendar a
        # weekday filter, and Bursa traded Malaysia Day on paper.
        table = closures(mic)
        _CACHE[mic] = cls(holidays=table.holidays, early_closes=table.early_closes)
    return _CACHE[mic]


def market_currency(mic: str | None) -> str:
    """What a price on this market is denominated in.

    Read off the adapter, never guessed, because this is the value that decides
    whether an MYR portfolio cap may be compared with a local turnover figure at
    all. Eleven adapters, eight currencies: getting it from the one place that
    already declares it means a market added later cannot disagree with it.

    Two cases fall back to MYR, and both are the book's own currency rather than
    a guess:

      * `mic is None` - no market was named, so nothing foreign is in play.
      * a MIC with no adapter - the caller must then supply the price, the daily
        value and the fee model themselves, and those are unit-consistent in
        whatever currency they chose. There is no adapter fact to contradict, so
        the computation is treated as being in the book's currency; it is the
        caller's job to keep its three inputs in one currency, which they had to
        do anyway.
    """
    from core.contracts.money import BASE_CURRENCY

    if mic is None:
        return BASE_CURRENCY
    try:
        return get(mic).currency.upper()
    except KeyError:
        return BASE_CURRENCY


def supported() -> list[str]:
    return sorted(_ADAPTERS)


def known_prefixes() -> list[str]:
    """Every spelling that resolves to a registered market."""
    return sorted(set(_ADAPTERS) | {a for a, m in ALIASES.items() if m in _ADAPTERS})
