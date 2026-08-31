"""Event taxonomy and the base-rate table.

docs/02 A5 and docs/03 section 6. This table is the most valuable knowledge base
in the system and no vendor sells it: historical abnormal-return distributions by
event_type x market x cap_band x surprise_bucket x regime.

Three construction rules from docs/03 section 6:
  1. The universe must include the dead, or it has already deleted every
     catastrophic outcome.
  2. Bucket on what was KNOWABLE - consensus as it stood before the announcement,
     market cap at that time.
  3. Report n everywhere. A median from six observations is shown with the six.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    EARNINGS_RESULT = "earnings_result"
    GUIDANCE_CHANGE = "guidance_change"
    MA_TARGET = "m&a_target"
    MA_ACQUIRER = "m&a_acquirer"
    BUYBACK = "buyback"
    DIVIDEND_CHANGE = "dividend_change"
    CAPITAL_RAISE = "capital_raise"
    INSIDER_BUY = "insider_buy"
    INSIDER_SELL = "insider_sell"
    INDEX_ADD = "index_add"
    INDEX_DROP = "index_drop"
    RATING_CHANGE = "rating_change"
    CONTRACT_WIN = "contract_win"
    PRODUCT_LAUNCH = "product_launch"
    REGULATORY_ACTION = "regulatory_action"
    LITIGATION = "litigation"
    EXECUTIVE_CHANGE = "executive_change"
    GOING_CONCERN = "going_concern"
    HALT = "halt"
    DELISTING = "delisting"
    LOCKUP_EXPIRY = "lockup_expiry"
    MACRO_PRINT = "macro_print"
    PEER_EARNINGS = "peer_earnings"


class CapBand(str, Enum):
    MEGA = "mega"
    LARGE = "large"
    MID = "mid"
    SMALL = "small"
    MICRO = "micro"


class SurpriseBucket(str, Enum):
    BIG_MISS = "big_miss"
    MISS = "miss"
    INLINE = "inline"
    BEAT = "beat"
    BIG_BEAT = "big_beat"
    NA = "na"


def bucket_surprise(actual: float | None, consensus: float | None) -> SurpriseBucket:
    """Consensus as it stood BEFORE the announcement, never a later revision."""
    if actual is None or consensus is None or consensus == 0:
        return SurpriseBucket.NA
    # Rounded before comparing: (0.90 - 1.0) / 1.0 is -0.09999999999999998 in
    # binary, one ulp above the -10% edge, and read as MISS. A bucket edge is a
    # decision boundary; it cannot depend on which side of an ulp it lands.
    s = round((actual - consensus) / abs(consensus), 12)
    if s <= -0.10:
        return SurpriseBucket.BIG_MISS
    if s <= -0.02:
        return SurpriseBucket.MISS
    if s < 0.02:
        return SurpriseBucket.INLINE
    if s < 0.10:
        return SurpriseBucket.BEAT
    return SurpriseBucket.BIG_BEAT


@dataclass(frozen=True)
class Event:
    event_id: str
    instrument_id: str
    event_type: EventType
    announced_at: datetime
    effective_at: datetime | None = None
    market: str = ""
    cap_band: CapBand = CapBand.MID
    surprise: SurpriseBucket = SurpriseBucket.NA
    confirmed: bool = True
    source_doc_id: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.effective_at and self.effective_at < self.announced_at:
            raise ValueError("effective_at cannot precede announced_at")

    @property
    def citable(self) -> bool:
        """docs/02 A5: an event with no primary source cannot be cited as a cause."""
        return self.confirmed and self.source_doc_id is not None


@dataclass(frozen=True)
class BaseRate:
    event_type: EventType
    market: str
    cap_band: CapBand
    surprise: SurpriseBucket
    n: int
    median_car: float
    iqr: tuple[float, float]
    hit_rate: float  # share with CAR in the same direction as the median
    pre_drift_median: float  # CAR over [-5,-1]: information leaks
    reversal_rate: float  # share where [+2,+20] reverses [0,+1]

    @property
    def thin(self) -> bool:
        """docs/03: a median CAR from six observations is presented as such."""
        return self.n < 30

    def describe(self) -> str:
        band = "" if not self.thin else "  [THIN SAMPLE]"
        return (
            f"{self.event_type.value} / {self.market} / {self.cap_band.value} / "
            f"{self.surprise.value}: median {self.median_car:+.2%} "
            f"(n={self.n}, IQR {self.iqr[0]:+.2%} to {self.iqr[1]:+.2%}){band}"
        )


@dataclass
class Observation:
    """One historical event with its measured windows."""

    event: Event
    car_pre: float  # [-5,-1]
    car_event: float  # [0,+1]
    car_post: float  # [+2,+20]


class BaseRateTable:
    def __init__(self) -> None:
        self._obs: dict[tuple, list[Observation]] = {}

    @staticmethod
    def _key(e: Event) -> tuple:
        return (e.event_type, e.market, e.cap_band, e.surprise)

    def observe(self, obs: Observation) -> None:
        self._obs.setdefault(self._key(obs.event), []).append(obs)

    def build(self, min_n: int = 3) -> dict[tuple, BaseRate]:
        out: dict[tuple, BaseRate] = {}
        for key, obs in self._obs.items():
            if len(obs) < min_n:
                continue
            cars = sorted(o.car_event for o in obs)
            med = statistics.median(cars)
            q = statistics.quantiles(cars, n=4) if len(cars) >= 4 else [cars[0], med, cars[-1]]
            same = sum(1 for c in cars if (c >= 0) == (med >= 0)) / len(cars)
            rev = sum(1 for o in obs if (o.car_post >= 0) != (o.car_event >= 0)) / len(obs)
            et, mkt, band, surp = key
            out[key] = BaseRate(
                et,
                mkt,
                band,
                surp,
                len(obs),
                med,
                (q[0], q[-1]),
                same,
                statistics.median([o.car_pre for o in obs]),
                rev,
            )
        return out

    def lookup(
        self,
        event_type: EventType,
        market: str,
        cap_band: CapBand,
        surprise: SurpriseBucket = SurpriseBucket.NA,
        min_n: int = 3,
    ) -> BaseRate | None:
        """Falls back to a coarser bucket rather than returning nothing."""
        table = self.build(min_n)
        for key in (
            (event_type, market, cap_band, surprise),
            (event_type, market, cap_band, SurpriseBucket.NA),
        ):
            if key in table:
                return table[key]
        # Pool across cap bands for the same type and market.
        pooled = [r for k, r in table.items() if k[0] is event_type and k[1] == market]
        if pooled:
            return max(pooled, key=lambda r: r.n)
        return None
