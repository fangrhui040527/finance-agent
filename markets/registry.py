"""Adapter registry. A new market is one entry here plus one class."""

from __future__ import annotations

from markets.contract import MarketAdapter
from markets.xkls import XKLS
from markets.xnas import XNAS
from markets.xses import XSES

_ADAPTERS: dict[str, type[MarketAdapter]] = {"XKLS": XKLS, "XNAS": XNAS, "XSES": XSES}
_CACHE: dict[str, MarketAdapter] = {}


def get(mic: str) -> MarketAdapter:
    mic = mic.upper()
    if mic not in _CACHE:
        try:
            _CACHE[mic] = _ADAPTERS[mic]()
        except KeyError as exc:
            raise KeyError(f"no adapter registered for MIC {mic!r}") from exc
    return _CACHE[mic]


def supported() -> list[str]:
    return sorted(_ADAPTERS)
