"""Money and FX.

Rule from docs/06 section 4.1: never store a bare number. Every monetary field
carries (amount, currency, fx_asof). docs/08 section 8.6 applies the same rule to
the project's own cost ledger, so a year of cost history cannot silently rewrite
itself when the rate moves.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE_CURRENCY = "MYR"


class Money(BaseModel):
    """An amount that always knows its currency and when its rate was struck."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)
    fx_asof: datetime | None = None

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()

    def convert(self, to: str, rate: Decimal, asof: datetime) -> "Money":
        """Convert at an explicit rate. There is no implicit/global rate lookup."""
        to = to.upper()
        if to == self.currency:
            return self
        return Money(amount=self.amount * rate, currency=to, fx_asof=asof)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:,.2f}"
