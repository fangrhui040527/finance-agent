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
from markets.xasx import XASX
from markets.xhkg import XHKG
from markets.xkls import XKLS
from markets.xlon import XLON
from markets.xnas import XNAS
from markets.xses import XSES
from markets.xtks import XTKS

_ADAPTERS: dict[str, type[MarketAdapter]] = {
    "XKLS": XKLS, "XNAS": XNAS, "XSES": XSES, "XHKG": XHKG,
    "XTKS": XTKS, "XLON": XLON, "XASX": XASX,
}
_CACHE: dict[str, MarketAdapter] = {}

#: Prefix as written in instrument ids -> the ISO 10383 MIC the adapter uses.
#: Only for genuine aliases of a REGISTERED market. Adding a market goes in
#: _ADAPTERS; adding a second spelling for one goes here.
ALIASES: dict[str, str] = {
    "MYX": "XKLS",     # Bursa Malaysia: ids say MYX, the MIC is XKLS
    "KLSE": "XKLS",    # the pre-2004 name, still in older sources
    "SGX": "XSES",
    "NASDAQ": "XNAS",
    "HKEX": "XHKG",
    "SEHK": "XHKG",
    # XJPX is the Japan Exchange Group operator MIC; XTKS is the Tokyo Stock
    # Exchange segment where shares actually trade. docs/06 wrote XJPX while
    # core/market/feed.py already wrote XTKS - the same doc-versus-code drift
    # MYX/XKLS caused, mapped here before it can cost anything.
    "XJPX": "XTKS",
    "TSE": "XTKS",     # Tokyo; also the initialism for several other exchanges,
    "TYO": "XTKS",     # which is exactly why the MIC is the canonical form
    "LSE": "XLON",
    "ASX": "XASX",
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
            _CACHE[mic] = _ADAPTERS[mic]()
        except KeyError as exc:
            raise KeyError(
                f"no adapter registered for MIC {mic!r}; supported: {supported()}"
            ) from exc
    return _CACHE[mic]


def supported() -> list[str]:
    return sorted(_ADAPTERS)


def known_prefixes() -> list[str]:
    """Every spelling that resolves to a registered market."""
    return sorted(set(_ADAPTERS) | {a for a, m in ALIASES.items() if m in _ADAPTERS})
