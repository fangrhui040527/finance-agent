"""Instrument identity.

docs/06 section 4.1 rule 1: ticker+MIC resolves to one instrument_id. Without
this the same company appears as three entities and every aggregate is wrong.

docs/01 section 7: delisted_at being present and populated is what stops
survivorship bias from deleting every catastrophic outcome from the record.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Status(str, Enum):
    LISTED = "listed"
    SUSPENDED = "suspended"
    DELISTED = "delisted"
    MERGED = "merged"
    BANKRUPT = "bankrupt"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str
    isin: str | None = None
    primary_ticker: str
    mic: str = Field(min_length=4, max_length=4)
    currency: str = Field(min_length=3, max_length=3)
    lot_size: int = Field(gt=0)
    name: str
    sector: str | None = None
    first_listed: date
    delisted_at: date | None = None
    status: Status = Status.LISTED

    @field_validator("mic", "currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()

    def alive_on(self, d: date) -> bool:
        """Survivorship-safe membership test."""
        if d < self.first_listed:
            return False
        return self.delisted_at is None or d < self.delisted_at


class IdentityResolver:
    """Collapses aliases to one instrument_id.

    `0011.KL`, `1155.KL`, `MAYBANK` and an ISIN must all resolve to the same
    entity or every portfolio aggregate downstream is silently wrong.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Instrument] = {}
        self._alias: dict[str, str] = {}

    def register(self, inst: Instrument, aliases: list[str] | None = None) -> None:
        self._by_id[inst.instrument_id] = inst
        keys = [inst.instrument_id, inst.primary_ticker, f"{inst.primary_ticker}.{inst.mic}"]
        if inst.isin:
            keys.append(inst.isin)
        keys.extend(aliases or [])
        for k in keys:
            self._alias[self._norm(k)] = inst.instrument_id

    @staticmethod
    def _norm(s: str) -> str:
        return s.strip().upper().replace(" ", "")

    def resolve(self, token: str, mic: str | None = None) -> Instrument | None:
        if mic:
            hit = self._alias.get(self._norm(f"{token}.{mic}"))
            if hit:
                return self._by_id[hit]
        hit = self._alias.get(self._norm(token))
        return self._by_id[hit] if hit else None

    def get(self, instrument_id: str) -> Instrument | None:
        return self._by_id.get(instrument_id)

    def universe_on(self, d: date, mic: str | None = None) -> list[str]:
        """Point-in-time universe. Includes names that have since died."""
        return sorted(
            i.instrument_id
            for i in self._by_id.values()
            if i.alive_on(d) and (mic is None or i.mic == mic.upper())
        )
