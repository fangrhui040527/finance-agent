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
        """ISO 4217 is three LETTERS. The length bound alone accepted "123",
        which is not a currency and would have been carried, formatted and
        summed like one."""
        v = v.upper()
        if not v.isalpha():
            raise ValueError(
                f"{v!r} is not a currency code; ISO 4217 codes are three letters"
            )
        return v

    def convert(self, to: str, rate: Decimal, asof: datetime) -> "Money":
        """Convert at an explicit rate. There is no implicit/global rate lookup.

        The rate must be strictly positive. A negative rate turned USD 100 into
        MYR -415 and a zero rate destroyed the money outright - both silently,
        because the result is finite, plausible and correctly typed. A wrong
        number that survives every downstream check is exactly what this module
        exists to prevent.

        Converting to the same currency returns self and IGNORES the rate: it is
        an identity, and no rate can make it otherwise.
        """
        to = to.upper()
        if to == self.currency:
            return self
        if rate <= 0:
            raise ValueError(
                f"fx rate {rate} is not positive. {self.currency}->{to} at this "
                f"rate would {'destroy the amount' if rate == 0 else 'negate it'}, "
                f"and the result would look like an ordinary number."
            )
        return Money(amount=self.amount * rate, currency=to, fx_asof=asof)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:,.2f}"
