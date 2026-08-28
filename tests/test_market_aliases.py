"""The MYX/XKLS drift, and the guard against the next one.

Instrument ids in this repo are written `MYX:1155`; the adapter MIC is `XKLS`.
Nothing mapped between them, so every lookup keyed on the id prefix missed its
table and fell back to a default. Nothing crashed - which is the point.
"""
from decimal import Decimal

import pytest

from engines.sizing.caps import COST_FLOOR_BPS_BY_MIC, cost_floor_bps, cost_floor_value
from markets.registry import (
    ALIASES,
    get,
    known_prefixes,
    mic_of,
    resolve_mic,
    supported,
)


# --- the drift itself -----------------------------------------------------
def test_the_id_prefix_this_repo_actually_writes_resolves_to_the_adapter_mic():
    assert mic_of("MYX:1155") == "XKLS"


def test_bursa_gets_its_own_cost_floor_not_the_default():
    """The bug: cost_floor_bps('MYX') returned 30, half Bursa's real 60. Every
    Bursa position was sized against a floor it could never have met."""
    assert cost_floor_bps("MYX") == Decimal("60")
    assert cost_floor_bps("MYX") == cost_floor_bps("XKLS")
    assert cost_floor_bps("MYX") != cost_floor_bps(None)


def test_the_documented_minimum_bursa_position_is_reproduced():
    """docs/05 and the README both record ~RM 4,700. If this moves, one of the
    fee schedule, the floor, or the alias map has changed underneath it."""
    floor = cost_floor_value(get("MYX").fee_schedule.round_trip, "MYX")
    assert Decimal("4500") < floor < Decimal("5000")


def test_every_alias_and_mic_agrees_on_the_floor():
    for alias, mic in ALIASES.items():
        if mic in supported():
            assert cost_floor_bps(alias) == cost_floor_bps(mic), alias


# --- resolution -----------------------------------------------------------
@pytest.mark.parametrize("spelling", ["MYX", "myx", " MYX ", "KLSE", "XKLS", "xkls"])
def test_every_spelling_of_bursa_lands_on_one_adapter(spelling):
    assert resolve_mic(spelling) == "XKLS"
    assert get(spelling).mic == "XKLS"


def test_an_unknown_mic_passes_through_so_the_error_names_what_was_asked():
    assert resolve_mic("XFRA") == "XFRA"
    with pytest.raises(KeyError, match="XFRA"):
        get("XFRA")


def test_the_error_lists_what_is_supported():
    with pytest.raises(KeyError, match="XKLS"):
        get("XFRA")


def test_an_id_without_a_prefix_is_refused_rather_than_assumed():
    with pytest.raises(ValueError, match="no market prefix"):
        mic_of("1155")


def test_known_prefixes_covers_both_spellings():
    p = known_prefixes()
    assert {"MYX", "XKLS", "XNAS", "XSES"} <= set(p)


# --- the ratchet: this is what stops the next drift ----------------------
def test_no_alias_points_at_an_unregistered_market():
    """An alias to a market with no adapter resolves to a MIC that then fails on
    get(). Better to catch the dangling entry here."""
    dangling = {a: m for a, m in ALIASES.items() if m not in supported()}
    assert not dangling, f"aliases point at unregistered markets: {dangling}"


def test_every_registered_market_has_an_explicit_cost_floor():
    """README: 'Every supported market has an explicit cost floor.' A market that
    falls back to the default has an accidental floor, not a chosen one."""
    missing = [m for m in supported() if m not in COST_FLOOR_BPS_BY_MIC]
    assert not missing, f"markets with no explicit cost floor: {missing}"


def test_every_prefix_that_resolves_also_prices():
    """The whole failure class in one assertion: anything a caller may legally
    write as an id prefix must reach a real adapter AND a real floor."""
    for prefix in known_prefixes():
        assert get(prefix) is not None
        assert cost_floor_bps(prefix) == cost_floor_bps(get(prefix).mic)
