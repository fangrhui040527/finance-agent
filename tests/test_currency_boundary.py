"""MYR is the unit of account, and the seam where that stops being true.

A Malaysian book holding a US stock has two currencies in play and exactly one
of them belongs on a report: `price`, `adv_20d`, board lots and fee minimums are
facts about the market; `investable`, every cap derived from it and every
portfolio weight are facts about the book, and the book is in MYR.

Every test here fails on the code as it stood before the boundary was named.
None of them needed new machinery to write, which is the uncomfortable part:
the arithmetic was always visible, it just never had a currency attached to
disagree with.
"""

from datetime import date
from decimal import Decimal as D

import pytest

from engines.risk.concentration import Limits, Position, check
from engines.sizing.caps import (
    BASE_CURRENCY,
    Band,
    CapSet,
    CurrencyMismatch,
    concentration_cap,
    cost_floor_value,
    liquidity_cap,
    risk_budget_cap,
    to_base,
    to_quote,
)
from engines.sizing.decision import size
from markets.registry import get, market_currency

BREAKERS = ("ROIC below 8% for two consecutive quarters", "net debt/EBITDA above 4x")
XKLS, XNAS = get("XKLS"), get("XNAS")

#: Roughly the rate through 2025-2026. The exact number does not matter; that it
#: is not 1.0 is the whole point of every test below.
MYR_PER_USD = D("4.20")


# --- the boundary itself --------------------------------------------------
def test_market_currency_is_read_off_the_adapter_never_guessed():
    assert market_currency("XKLS") == "MYR"
    assert market_currency("MYX") == "MYR"  # through the alias map
    assert market_currency("XNAS") == "USD"
    assert market_currency("XTKS") == "JPY"
    assert market_currency(None) == BASE_CURRENCY


def test_crossing_currencies_without_a_rate_raises_rather_than_guessing():
    with pytest.raises(CurrencyMismatch, match="without an FX rate"):
        to_base(D("10000"), "USD", None)
    with pytest.raises(CurrencyMismatch, match="without an"):
        to_quote(D("500000"), "USD", None)


def test_a_same_currency_conversion_is_an_identity_and_needs_no_rate():
    assert to_base(D("40000"), "MYR", None) == D("40000")
    assert to_quote(D("40000"), "myr", None) == D("40000")


def test_a_non_positive_rate_is_refused_in_both_directions():
    """A negative rate produces a negative position and a zero rate a division
    by zero or a vanished one - both of them finite, typed and plausible."""
    with pytest.raises((CurrencyMismatch, ValueError)):
        to_quote(D("500000"), "USD", D("0"))
    with pytest.raises((CurrencyMismatch, ValueError)):
        to_base(D("10000"), "USD", D("-4.2"))


def test_a_cap_set_must_name_one_currency_for_all_five():
    assert CapSet(D(1), None, D(1), D(1), D(1)).currency == BASE_CURRENCY
    assert CapSet(D(1), None, D(1), D(1), D(1), currency="usd").currency == "USD"
    with pytest.raises(CurrencyMismatch, match="not a currency code"):
        CapSet(D(1), None, D(1), D(1), D(1), currency="US")


# --- the defect this was written for --------------------------------------
def test_a_cap_in_one_currency_cannot_size_a_price_in_another():
    """`units = value / price` with an MYR value and a USD price.

    Nothing in the old signature named either currency, so the division was
    silently off by the exchange rate - in the direction that BUYS MORE. A rate
    being available is not enough: the caps must already have been built in the
    currency the price is quoted in, or the conversion happens in the wrong
    place and the cost floor is checked against the wrong magnitude too.
    """
    caps = CapSet(
        risk=D("40000"),
        kelly=None,
        concentration=D("40000"),
        liquidity=D("999999999"),
        cost_floor=D("1"),
    )  # MYR by default
    with pytest.raises(CurrencyMismatch, match="priced in USD"):
        size(
            "XNAS:NVDA",
            Band.ACCUMULATE,
            D("500000"),
            caps,
            D("180"),
            1,
            D("165"),
            BREAKERS,
            date(2028, 1, 1),
            XNAS.fee_schedule.round_trip,
            mic="XNAS",
            currency="USD",
            fx_base_per_quote=MYR_PER_USD,
        )


def test_an_eight_percent_cap_never_funds_a_thirty_percent_position():
    """The regression, stated in the units that matter to the holder.

    MYR 500,000 book, 8% single-name limit: at most MYR 40,000 of NVDA. Sizing
    the MYR cap against the USD price bought 222 shares - USD 39,960, which is
    MYR 167,832, or 33.6% of the book, reported as `bound by concentration`.
    """
    book = D("500000")
    caps = CapSet(
        risk=to_quote(risk_budget_cap(book, D("0.0075"), D("0.0833")), "USD", MYR_PER_USD),
        kelly=None,
        concentration=to_quote(concentration_cap(book, D("0.08")), "USD", MYR_PER_USD),
        liquidity=liquidity_cap(D("30000000000")),
        cost_floor=cost_floor_value(XNAS.fee_schedule.round_trip, "XNAS"),
        currency="USD",
    )
    d = size(
        "XNAS:NVDA",
        Band.ACCUMULATE,
        book,
        caps,
        D("180"),
        1,
        D("165"),
        BREAKERS,
        date(2028, 1, 1),
        XNAS.fee_schedule.round_trip,
        mic="XNAS",
        currency="USD",
        fx_base_per_quote=MYR_PER_USD,
    )

    assert d.currency == "USD"
    assert d.base_value <= book * D("0.08")
    # and the pre-fix answer is genuinely excluded, not merely close
    assert d.target_units < 222
    assert d.base_value < D("50000")


def test_the_reported_value_is_myr_and_the_traded_value_is_not():
    """Both numbers, each labelled. One of them buys shares; the other is the
    only one that may be compared with anything else the holder owns."""
    caps = CapSet(
        risk=D("9600"),
        kelly=None,
        concentration=D("9523.81"),
        liquidity=D("999999999"),
        cost_floor=D("1"),
        currency="USD",
    )
    d = size(
        "XNAS:NVDA",
        Band.ACCUMULATE,
        D("500000"),
        caps,
        D("180"),
        1,
        D("165"),
        BREAKERS,
        date(2028, 1, 1),
        XNAS.fee_schedule.round_trip,
        mic="XNAS",
        currency="USD",
        fx_base_per_quote=MYR_PER_USD,
    )
    assert d.target_value == D(d.target_units) * D("180")  # USD, buys shares
    assert d.base_value == d.target_value * MYR_PER_USD  # MYR, comparable
    assert d.base_value > d.target_value


def test_an_myr_market_needs_no_rate_and_reports_one_number():
    inv, price = D("400000"), D("6.20")
    caps = CapSet(
        risk=risk_budget_cap(inv, D("0.0075"), D("0.125")),
        kelly=None,
        concentration=concentration_cap(inv, D("0.08")),
        liquidity=liquidity_cap(D("800000")),
        cost_floor=cost_floor_value(XKLS.fee_schedule.round_trip, "XKLS"),
    )
    d = size(
        "MYX:1155",
        Band.ACCUMULATE,
        inv,
        caps,
        price,
        100,
        D("5.42"),
        BREAKERS,
        date(2028, 1, 1),
        XKLS.fee_schedule.round_trip,
        mic="XKLS",
    )
    assert d.currency == BASE_CURRENCY
    assert d.base_value == d.target_value


# --- the last line of defence was fed the same wrong number ---------------
def test_the_prospective_weight_is_myr_over_myr():
    """`final_value / port_value` was USD over MYR, so the concentration check
    that runs AFTER sizing saw a weight 4.2x too small and passed a breach."""
    existing = [
        Position("MYX:1155", 0.30, "bank", "MY", "MYR", 0.01),
        Position("MYX:5225", 0.30, "health", "MY", "MYR", 0.01),
    ]
    caps = CapSet(
        risk=D("30000"),
        kelly=None,
        concentration=D("30000"),
        liquidity=D("999999999"),
        cost_floor=D("1"),
        currency="USD",
    )
    # USD 30,000 is MYR 126,000 of a MYR 200,000 book: 63%, far past any limit.
    with pytest.raises(Exception) as e:
        size(
            "XNAS:NVDA",
            Band.ACCUMULATE,
            D("200000"),
            caps,
            D("180"),
            1,
            D("165"),
            BREAKERS,
            date(2028, 1, 1),
            XNAS.fee_schedule.round_trip,
            mic="XNAS",
            currency="USD",
            fx_base_per_quote=MYR_PER_USD,
            existing=existing,
            limits=Limits(),
            candidate_meta={"sector": "tech", "country": "US"},
        )
    assert "single_name" in str(e.value)


def test_a_candidate_defaults_to_its_market_currency_not_to_myr():
    """`meta.get("currency", "MYR")` labelled every unlabelled candidate MYR,
    so a USD position joined the book as domestic and the non-base-currency
    limit never saw it."""
    caps = CapSet(
        risk=D("2000"),
        kelly=None,
        concentration=D("2000"),
        liquidity=D("999999999"),
        cost_floor=D("1"),
        currency="USD",
    )
    d = size(
        "XNAS:NVDA",
        Band.ACCUMULATE,
        D("500000"),
        caps,
        D("180"),
        1,
        D("165"),
        BREAKERS,
        date(2028, 1, 1),
        XNAS.fee_schedule.round_trip,
        mic="XNAS",
        currency="USD",
        fx_base_per_quote=MYR_PER_USD,
        existing=[],
        limits=Limits(),
        candidate_meta={"sector": "tech", "country": "US"},
    )
    assert d.currency == "USD"


# --- the country/currency conflation --------------------------------------
def test_a_bursa_only_book_has_no_foreign_currency_exposure():
    """`currency=parts[4]` copied the COUNTRY field, so a Bursa holding carried
    "MY", which is not "MYR" - and `check` counts anything that is not the base
    currency as foreign. An all-Bursa book breached the 50% foreign limit."""
    from ask import _parse_position

    book = [_parse_position("MYX:1155:0.30:bank:MY"), _parse_position("MYX:5225:0.30:health:MY")]
    assert {p.currency for p in book} == {"MYR"}
    assert not [b for b in check(book, None, Limits()) if b.limit == "non_base_currency"]


def test_a_us_holding_still_counts_as_foreign():
    """The fix must not achieve compliance by calling everything MYR."""
    from ask import _parse_position

    book = [_parse_position("MYX:1155:0.30:bank:MY"), _parse_position("XNAS:NVDA:0.60:tech:US")]
    assert [p.currency for p in book] == ["MYR", "USD"]
    assert [b for b in check(book, None, Limits()) if b.limit == "non_base_currency"]


def test_an_explicit_currency_still_wins():
    from ask import _parse_position

    p = _parse_position("XNAS:BABA:0.10:tech:CN:0.01:HKD")
    assert p.currency == "HKD" and p.country == "CN" and p.risk_to_stop == 0.01
