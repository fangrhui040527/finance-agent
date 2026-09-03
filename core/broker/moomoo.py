"""Reading a moomoo account through the local OpenD gateway.

moomoo does not expose an account over the internet. Their SDK talks to a
gateway program (OpenD) that the operator runs on their OWN machine and logs
into themselves; this code connects to `127.0.0.1:11111` and speaks to that.
So no credential of the operator's ever reaches this repository: the login
happens in OpenD, not here, and there is nothing to put in `.env`.

THE READ-ONLY GUARANTEE, and why it is stronger than a promise
--------------------------------------------------------------
moomoo requires an explicit unlock, with the trading password, before their
gateway will accept ANY instruction that moves money. This module never
performs that unlock. It is not that we choose not to trade - the connection
this module opens is refused by moomoo's own server if it tries, and the
refusal is enforced on their side, not ours.

That is a materially better guarantee than "our code does not call it", and it
is the reason this integration was judged safe to build at all. It is held by
`tests/test_broker_readonly.py`, which reads this module's own source and fails
if an instruction-shaped call appears in it.

WHAT IS AND IS NOT VERIFIED
---------------------------
The method names, arguments and returned column names below were read from the
`futu-api` package source, version 10.10.7008, not from documentation and not
from memory. Malaysia is a supported market in that version (`TrdMarket.MY`,
`Currency.MYR`, real accounts only - simulated MY accounts are not supported).

What is NOT verified: nothing here has ever spoken to a running OpenD. It could
not be - this repository's environment has no network route to moomoo and no
account to read. Every test below runs against a double. Treat the first real
run as the actual test, and see docs/19-BROKER-ACCOUNT.md for how to do it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from core.broker.account import (
    AccountFeed,
    AccountSnapshot,
    BrokerError,
    Position,
    to_instrument_id,
)

#: OpenD's default listen address. It is a LOCAL gateway by design: the
#: operator runs it, logs into it, and it holds the session. Nothing here
#: authenticates, which is why this module takes no password argument and
#: must never grow one.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111

#: futu-api returns `RET_OK == 0` and puts the error string where the data
#: would be. Pinned rather than imported so this module can be read, and
#: tested, without the optional dependency installed.
RET_OK = 0


def _decimal(value: Any, field: str, code: str) -> Decimal:
    """A number from the broker, or a refusal naming which field was bad.

    Broker payloads carry sentinels - 'N/A', None, empty string - where a
    figure is unavailable. Coercing those to zero would put a silent 0 into a
    cost basis, so they raise instead.
    """
    if (
        value is None
        or value == ""
        or (isinstance(value, str) and value.strip().upper() in {"N/A", "NA", "NONE"})
    ):
        raise BrokerError(
            f"{code}: broker returned no value for {field!r}; refusing to substitute zero"
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BrokerError(
            f"{code}: broker returned {value!r} for {field!r}, which is not a number"
        ) from None


class MoomooAccountFeed(AccountFeed):
    """The operator's moomoo account, read once per `snapshot()`.

    `open_context` is injected rather than constructed so the whole class is
    testable without the optional dependency and without a gateway - the same
    seam discipline every network adapter in this repository uses. In
    production it is `_default_context`, which imports futu-api lazily.
    """

    name = "moomoo"

    def __init__(
        self,
        open_context: Callable[[], Any] | None = None,
        *,
        market: str = "MY",
        currency: str = "MYR",
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._open_context = open_context or (lambda: _default_context(market, host, port))
        self._market = market
        self._currency = currency.upper()

    def snapshot(self) -> AccountSnapshot:
        ctx = self._open_context()
        try:
            positions = self._positions(ctx)
            cash = self._cash(ctx)
        finally:
            # The gateway holds a real socket. Leaking it across snapshots
            # would eventually exhaust OpenD's connection limit and present as
            # an intermittent read failure, which is the worst shape of bug to
            # find in something that reports a portfolio.
            close = getattr(ctx, "close", None)
            if callable(close):
                close()
        return AccountSnapshot(
            positions=tuple(positions),
            cash=cash,
            currency=self._currency,
            as_of=datetime.now(UTC),
            source=f"{self.name}:{self._market}",
        )

    # -- the two reads ------------------------------------------------------------

    def _positions(self, ctx: Any) -> list[Position]:
        rows = self._rows(ctx.position_list_query(), "position_list_query")
        out: list[Position] = []
        for row in rows:
            code = str(row.get("code", ""))
            qty = _decimal(row.get("qty"), "qty", code)
            if qty == 0:
                # A closed position is still listed by the broker. It is not a
                # holding and must not reach a concentration measure as one.
                continue
            out.append(
                Position(
                    instrument_id=to_instrument_id(code),
                    units=qty,
                    avg_cost=_decimal(row.get("cost_price"), "cost_price", code),
                    market_value=_decimal(row.get("market_val"), "market_val", code),
                    currency=str(row.get("currency") or self._currency).upper(),
                )
            )
        return out

    def _cash(self, ctx: Any) -> Decimal:
        rows = self._rows(ctx.accinfo_query(), "accinfo_query")
        if not rows:
            raise BrokerError("accinfo_query returned no account row; cannot report cash")
        return _decimal(rows[0].get("cash"), "cash", "account")

    # -- the futu-api calling convention -------------------------------------------

    @staticmethod
    def _rows(result: Any, call: str) -> list[dict]:
        """futu-api returns `(ret_code, payload)`; payload is a DataFrame on
        success and an error STRING on failure.

        Unpacked here, once, so a failure becomes a BrokerError naming the call
        rather than an AttributeError on a string somewhere downstream. This is
        also the seam that keeps pandas out of this repository's dependencies:
        the frame is converted to plain dicts and never leaves this method.
        """
        try:
            ret, payload = result
        except (TypeError, ValueError):
            raise BrokerError(
                f"{call}: expected (ret_code, payload) from the gateway, got {result!r}"
            ) from None
        if ret != RET_OK:
            raise BrokerError(f"{call} failed: {payload}")
        to_dict = getattr(payload, "to_dict", None)
        if callable(to_dict):
            # A DataFrame in production, the double's stand-in in tests. Cast
            # because futu-api is an OPTIONAL dependency: pandas is not
            # importable here, so there is no real type to annotate against.
            return list(cast("list[dict]", to_dict("records")))
        if isinstance(payload, list):
            return payload
        raise BrokerError(f"{call}: cannot read rows from a {type(payload).__name__}")


def _default_context(market: str, host: str, port: int) -> Any:
    """Open a real gateway connection. Imported lazily and on purpose.

    futu-api pulls in pandas and protobuf. Making it a hard dependency would
    add both to every install of a system whose whole promise is that it runs
    offline and keyless, for a feature most installations will never enable.
    """
    try:
        from futu import OpenSecTradeContext, TrdMarket  # type: ignore[import-not-found]
    except ImportError:
        raise BrokerError(
            "the moomoo account feed needs the optional 'futu-api' package: "
            "uv add futu-api (or pip install futu-api). It also needs OpenD "
            "running and logged in on this machine - see docs/19-BROKER-ACCOUNT.md."
        ) from None
    try:
        return OpenSecTradeContext(
            filter_trdmarket=getattr(TrdMarket, market), host=host, port=port
        )
    except Exception as e:
        raise BrokerError(
            f"could not reach the moomoo gateway at {host}:{port} ({e}). "
            f"OpenD must be running and logged in before this can read anything."
        ) from None
