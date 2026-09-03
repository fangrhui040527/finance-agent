"""What the system may learn from a broker, and the one shape it learns it in.

Every other input to this system is public: prices from a feed, news from
GDELT, rates from Bank Negara. This one is not. It is the operator's own
account, and it is the only place where a live credential could ever reach
this code.

So the contract is narrower than the broker's own. A broker connection can do
many things; an `AccountFeed` can do exactly one, `snapshot()`, and that
returns positions and cash. There is no method here that changes anything,
because a capability that does not exist cannot be called by mistake, cannot
be reached by a prompt injection, and cannot be added later without editing
this file and failing `tests/test_broker_readonly.py`.

The error discipline is the one `knowledge/feeds/adapter.py` already sets for
news: a BROKEN link and an EMPTY account are different answers. A transport
failure raises `BrokerError`; an account that genuinely holds nothing returns
a snapshot with no positions. Collapsing the two would let a dead connection
read as "you own nothing", which is the most dangerous empty list in the
system - it is the input to every concentration and rebalancing figure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class BrokerError(RuntimeError):
    """The account could not be read. NEVER raised for an empty account.

    Carries the source so an operator is told which link failed rather than
    that "something" did.
    """


@dataclass(frozen=True)
class Position:
    """One holding, in the repository's own instrument vocabulary."""

    instrument_id: str
    """`MYX:1155`, `XNAS:NVDA` - the spelling the rest of this system uses,
    translated from the broker's, so nothing downstream learns a second one."""

    units: Decimal
    avg_cost: Decimal
    market_value: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.units < 0:
            # A short position is a different instrument in this system's
            # vocabulary and nothing downstream models one. Refuse rather than
            # let a negative unit count reach a concentration measure that
            # assumes it is looking at ownership.
            raise ValueError(
                f"{self.instrument_id}: negative units ({self.units}). Short "
                f"positions are not modelled; this system sizes what you own."
            )
        if not self.currency or len(self.currency) != 3:
            raise ValueError(
                f"{self.instrument_id}: currency must be an ISO code, got {self.currency!r}"
            )


@dataclass(frozen=True)
class AccountSnapshot:
    """What the account held at one instant, and where that came from."""

    positions: tuple[Position, ...]
    cash: Decimal
    currency: str
    as_of: datetime
    source: str
    """Which feed produced this. Printed wherever the snapshot is, because a
    figure derived from a broker and one typed into config.toml are different
    kinds of claim and the difference must survive to the screen."""

    @property
    def is_empty(self) -> bool:
        """True for an account that genuinely holds nothing.

        Distinct from a failure, which raised before a snapshot existed.
        """
        return not self.positions


class AccountFeed(ABC):
    """Read an account. That is the entire surface.

    Deliberately not called a `BrokerClient`: a client is a thing you send
    instructions to, and naming shapes what gets added to a class later.
    """

    name: str

    @abstractmethod
    def snapshot(self) -> AccountSnapshot:
        """Positions and cash as of now, or raise BrokerError."""


#: Broker market prefix -> the MIC the rest of this system uses. `markets/`
#: owns the alias map for instrument ids; this maps only the broker's own
#: market vocabulary onto it, which is a different and much smaller thing.
MARKET_PREFIXES: dict[str, str] = {
    "MY": "MYX",  # Bursa; MYX is what ids say here, XKLS is the MIC
    "US": "XNAS",
    "HK": "XHKG",
    "SG": "XSES",
}


def to_instrument_id(broker_code: str) -> str:
    """`MY.1155` -> `MYX:1155`. Refuses a market it has no mapping for.

    Guessing would be worse than refusing: an unmapped prefix passed through
    unchanged would silently miss the alias map downstream and inherit the
    default cost floor, which is exactly the defect markets/registry.py exists
    to prevent.
    """
    if "." not in broker_code:
        raise BrokerError(
            f"unrecognised broker code {broker_code!r}; expected MARKET.CODE, e.g. MY.1155"
        )
    prefix, _, code = broker_code.partition(".")
    if prefix not in MARKET_PREFIXES:
        raise BrokerError(
            f"no instrument mapping for broker market {prefix!r} (from {broker_code!r}). "
            f"Known: {', '.join(sorted(MARKET_PREFIXES))}. Add one to MARKET_PREFIXES "
            f"rather than letting the code through unmapped."
        )
    if not code:
        raise BrokerError(f"broker code {broker_code!r} has a market but no instrument")
    return f"{MARKET_PREFIXES[prefix]}:{code}"
