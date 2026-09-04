"""moomoo OpenAPI — QUOTES ONLY.

The safety argument for this module lives entirely in what it imports.

`moomoo-api` ships two context classes. `OpenQuoteContext` reads market data.
The trade context carries every tool name this repository refuses to contain -
the ones tests/test_no_execution_anywhere.py greps for. This module uses the
quote context and never references the other, and tests/test_price_sources.py
asserts that by reading this file's own text, because a convention that is not
tested is a convention that erodes.

(This docstring deliberately does not spell those tool names out. The guard is a
repo-wide grep, so naming them here - even to explain their absence - would trip
it, and the right answer to that is to not need them, not to add an exemption.)

That is not caution for its own sake. The account list moomoo returns puts a
REAL margin account in the same list as the simulated ones, which is why code
that trades has to guard every call on trd_env. Here that failure mode cannot
happen, because there is no trade context to mistype an account into. The system
is a one-way door: it reads prices and forms opinions, and it has no mechanism
to act on them. docs/05 section 10.

OPERATING IT (from a session that verified this end to end on Windows):

  - moomoo OpenD must be RUNNING and LOGGED IN. v10.10 removed credentials from
    the config file, so login is manual after every reboot; there is no
    unattended start today. A source that is merely "not logged in" looks
    identical to one that is down, so this module says which.
  - Defaults confirmed from OpenD.xml: 127.0.0.1, port 11111.
  - The package is `moomoo-api`, not `futu-api`. Version 10.10.7008 matched the
    OpenD build; a mismatch is a protocol error, not a connection error.
  - Contexts must be closed. A leaked one consumes connection quota until OpenD
    restarts.

The import is lazy so this repository still imports, tests and ships with
moomoo-api absent - which is the normal case in CI and on any non-Windows box.
"""

from __future__ import annotations

from datetime import date

from core.market.prices import Bar
from core.market.sources import PriceSource, PriceSourceError

#: OpenD's confirmed defaults. Overridable; never hardcoded at the call site.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111

#: moomoo spells instruments MARKET.CODE - "HK.00700", "US.AAPL", "MY.1155".
#: The system spells them MIC:CODE. This is the whole of the mapping.
MIC_TO_MARKET = {
    "XKLS": "MY",
    "XHKG": "HK",
    "XNAS": "US",
    "XNYS": "US",
    "XSES": "SG",
}


def to_moomoo_code(instrument_id: str) -> str:
    """MYX:1155 or XKLS:1155 -> MY.1155."""
    if "." in instrument_id and ":" not in instrument_id:
        return instrument_id  # already a moomoo code
    try:
        prefix, code = instrument_id.split(":", 1)
    except ValueError:
        raise ValueError(
            f"instrument_id {instrument_id!r} is not MIC:CODE; moomoo needs a "
            "market prefix and there is no safe default"
        ) from None
    market = MIC_TO_MARKET.get(prefix.upper())
    if market is None:
        raise ValueError(f"no moomoo market for {prefix!r}. Known: {sorted(MIC_TO_MARKET)}")
    return f"{market}.{code}"


class MoomooQuotes(PriceSource):
    """Daily bars from a locally running moomoo OpenD gateway."""

    name = "moomoo"

    def __init__(
        self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, context_factory=None
    ) -> None:
        self.host = host
        self.port = port
        # Injectable so the tests never need moomoo-api or a live gateway.
        self._context_factory = context_factory

    def _open(self):
        if self._context_factory is not None:
            return self._context_factory(self.host, self.port)
        try:
            # QUOTE context only. The trade context is never imported here -
            # see the module docstring, and tests/test_price_sources.py.
            #
            # The ignore is narrow on purpose: moomoo-api is an OPTIONAL runtime
            # dependency, absent in CI and on every non-Windows box, and
            # reportMissingImports stays true repo-wide so a genuinely
            # misspelled import is still caught everywhere else.
            from moomoo import OpenQuoteContext  # pyright: ignore[reportMissingImports]
        except ImportError as e:
            raise PriceSourceError(
                "moomoo-api is not installed. `pip install moomoo-api` - note "
                "the package is moomoo-api, not futu-api, and its version must "
                "match your OpenD build."
            ) from e
        return OpenQuoteContext(host=self.host, port=self.port)

    def _fetch_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        code = to_moomoo_code(instrument_id)
        ctx = self._open()
        try:
            ret, data = ctx.request_history_kline(
                code,
                start=start.isoformat(),
                end=end.isoformat(),
            )
        except OSError as e:
            raise PriceSourceError(
                f"cannot reach moomoo OpenD at {self.host}:{self.port}. The "
                "gateway must be running - and since v10.10 it must also be "
                "logged in by hand after every reboot."
            ) from e
        finally:
            close = getattr(ctx, "close", None)
            if callable(close):
                close()  # a leaked context eats quota until restart

        if ret != 0:
            raise PriceSourceError(
                f"moomoo refused the request for {code}: {data}. A quota or "
                "subscription-tier message here is not a connection fault - "
                "check your market data entitlement for that market."
            )
        return _to_bars(data, code)


def _to_bars(frame, code: str) -> list[Bar]:
    """moomoo returns a pandas frame. Kept separate so it is testable with any
    object exposing the same columns, and so pandas is never imported here."""
    rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
    out: list[Bar] = []
    for r in rows:
        try:
            out.append(
                Bar(
                    day=date.fromisoformat(str(r["time_key"])[:10]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"]),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise PriceSourceError(
                f"moomoo returned a bar for {code} this adapter cannot read: {r!r}"
            ) from e
    return out
