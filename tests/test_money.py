"""The money contract.

Found by `ask.py graph --untested`: a type carrying an amount, a currency and an
fx_asof, in a system whose whole discipline is that numbers carry provenance,
with no test importing it. Writing them found two defects.

Both are the same shape and it is the shape this module exists to prevent - a
wrong number that stays finite, plausible and correctly typed, and therefore
survives every check downstream.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.contracts.money import BASE_CURRENCY, Money

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def usd(amount="100"):
    return Money(amount=Decimal(amount), currency="USD")


# -- the two defects ---------------------------------------------------------


def test_a_negative_rate_is_refused_rather_than_negating_the_amount():
    """USD 100 became MYR -415. Finite, correctly typed, and wrong - it would
    have flowed into a portfolio value and a position size without a murmur."""
    with pytest.raises(ValueError, match="not positive"):
        usd().convert("MYR", Decimal("-4.15"), NOW)


def test_a_zero_rate_is_refused_rather_than_destroying_the_money():
    with pytest.raises(ValueError, match="not positive"):
        usd().convert("MYR", Decimal("0"), NOW)


def test_a_currency_code_must_be_three_LETTERS_not_three_characters():
    """The length bound alone accepted "123", which is not a currency and would
    have been carried, formatted and summed like one."""
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), currency="123")
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), currency="M1R")


# -- the contract ------------------------------------------------------------


def test_every_amount_knows_its_currency():
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"))


def test_a_currency_code_is_normalised_to_upper_case():
    assert Money(amount=Decimal("1"), currency="myr").currency == "MYR"


def test_a_code_of_the_wrong_length_is_refused():
    for bad in ("MY", "MYRR", ""):
        with pytest.raises(ValidationError):
            Money(amount=Decimal("1"), currency=bad)


def test_money_is_frozen_so_an_amount_cannot_drift_from_its_stamp():
    m = usd()
    with pytest.raises(ValidationError):
        m.amount = Decimal("999")


def test_a_float_amount_does_not_bring_binary_error_with_it():
    """0.1 has no exact binary representation. Pydantic routes float -> Decimal
    through str, so the cent survives; this pins that it keeps doing so."""
    assert Money(amount=0.1, currency="MYR").amount == Decimal("0.1")


def test_negative_amounts_are_allowed_because_losses_and_shorts_are_real():
    assert Money(amount=Decimal("-50"), currency="MYR").amount < 0


# -- conversion --------------------------------------------------------------


def test_conversion_carries_the_rate_date_it_was_struck_at():
    """docs/06 4.1: never a bare number. A converted amount without an as-of is
    a number whose rate cannot be reconstructed."""
    c = usd().convert("MYR", Decimal("4.15"), NOW)
    assert (c.amount, c.currency, c.fx_asof) == (Decimal("415.00"), "MYR", NOW)


def test_converting_to_the_same_currency_is_an_identity_whatever_the_rate():
    """No rate can make MYR into different MYR. Returning self rather than
    multiplying is what stops a stray rate corrupting a no-op."""
    m = Money(amount=Decimal("100"), currency="MYR")
    assert m.convert("MYR", Decimal("999"), NOW) is m
    assert m.convert("myr", Decimal("999"), NOW) is m


def test_an_identity_conversion_does_not_invent_an_fx_stamp():
    """Stamping an untouched amount would claim a rate was struck when none was."""
    m = Money(amount=Decimal("100"), currency="MYR")
    assert m.convert("MYR", Decimal("1"), NOW).fx_asof is None


def test_the_target_currency_is_normalised_too():
    assert usd().convert("myr", Decimal("4.15"), NOW).currency == "MYR"


def test_a_round_trip_at_reciprocal_rates_returns_the_original_amount():
    there = usd("100").convert("MYR", Decimal("4.15"), NOW)
    back = there.convert("USD", Decimal(1) / Decimal("4.15"), NOW)
    assert back.amount == pytest.approx(Decimal("100"))


def test_conversion_is_exact_rather_than_rounded_to_the_cent():
    """Rounding belongs at the point of display or settlement. Rounding here
    would compound across a chain of conversions."""
    c = usd("1").convert("MYR", Decimal("4.153791"), NOW)
    assert c.amount == Decimal("4.153791")


def test_a_later_conversion_overwrites_the_stamp_with_its_own_date():
    later = NOW + timedelta(days=30)
    a = usd().convert("MYR", Decimal("4.15"), NOW)
    b = a.convert("SGD", Decimal("0.29"), later)
    assert b.fx_asof == later


# -- presentation ------------------------------------------------------------


def test_it_prints_with_its_currency_so_a_bare_number_never_reaches_a_reader():
    assert str(Money(amount=Decimal("1234.5"), currency="MYR")) == "MYR 1,234.50"
    assert str(usd("-50")) == "USD -50.00"


def test_the_base_currency_is_named_once_and_is_the_reporting_currency():
    assert BASE_CURRENCY == "MYR"
