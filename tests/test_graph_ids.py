"""Canonical ids. The file that stops the graph growing a second Maybank.

Nothing here crashes when it breaks - that is the whole problem. Two producers
disagree about an id, the supply chain forks, and half the exposure paths lead
to a node no query ever names.
"""
import pytest

from knowledge.graph.entity_graph import NodeKind
from knowledge.graph.ids import (
    IdError, MAX_FOLD_PASSES, PREFIX, aliases, display_names, fold, instrument_id,
    kind_of, node_id, slug,
)

CO = NodeKind.COMPANY


# -- the four spellings of one company ---------------------------------------

@pytest.mark.parametrize("spelling", [
    "MYX:1155", "XKLS:1155", "KLSE:1155", "myx:1155",
    "Maybank", "maybank", "MAYBANK", "  Maybank  ",
    "Malayan Banking", "Malayan Banking Berhad",
])
def test_every_spelling_of_maybank_is_one_id(spelling):
    assert node_id(CO, spelling) == "CO:XKLS:1155"


def test_the_market_prefix_resolves_through_the_registry_not_a_second_table():
    """MYX -> XKLS is markets.registry's job. A private copy here would drift
    from it, which is exactly the bug that sized every Bursa position against a
    cost floor half the real one."""
    assert instrument_id("MYX:1155") == "XKLS:1155"
    assert instrument_id("SGX:D05") == "XSES:D05"
    assert instrument_id("HKEX:0700") == "XHKG:0700"


def test_an_unknown_name_gets_a_slug_rather_than_a_wrong_company():
    assert node_id(CO, "Some Unlisted Sdn Bhd") == "CO:some_unlisted_sdn_bhd"


# -- the three guarantees ----------------------------------------------------

@pytest.mark.parametrize("kind", list(NodeKind))
def test_minting_an_id_twice_gives_the_same_id(kind):
    once = node_id(kind, "Shipping and Marine Transport")
    assert node_id(kind, once) == once
    assert node_id(kind, node_id(kind, once)) == once


def test_idempotence_holds_for_companies_whose_id_carries_a_market():
    once = node_id(CO, "Maybank")
    assert node_id(CO, once) == once == "CO:XKLS:1155"


def test_everything_after_the_prefix_is_word_characters():
    got = node_id(NodeKind.SECTOR, "Oil, Gas & Consumable Fuels (ex-refining)")
    body = got.split(":", 1)[1]
    assert got.startswith("SEC:")
    assert body.replace("_", "").isalnum()


@pytest.mark.parametrize("a,b", [
    ("Consumer Cyclical", "consumer   cyclical"),
    ("E-Commerce", "e commerce"),
    ("Semiconductors!", "semiconductors"),
])
def test_spelling_spacing_and_punctuation_do_not_change_the_answer(a, b):
    assert node_id(NodeKind.SUBSECTOR, a) == node_id(NodeKind.SUBSECTOR, b)


# -- the ordering trap graphify documents ------------------------------------

def test_folding_happens_before_the_non_word_filter_not_after():
    """casefold can expand a character into a base plus a combining mark, which
    NFKC then recomposes. Filtering first deletes the mark and changes which
    company you are naming, so the order is load-bearing rather than stylistic."""
    # U+FB01 'ﬁ' is a compatibility ligature: NFKC expands it to 'fi'. Filter
    # first and it is a non-word character that would simply vanish.
    assert slug("ﬁnance") == "finance"
    assert node_id(NodeKind.SECTOR, "ﬁnancials") == node_id(NodeKind.SECTOR, "Financials")


def test_folding_is_iterated_to_convergence():
    # U+1E9E capital sharp s casefolds to 'ss'; the Kelvin sign normalises to
    # 'K' and then casefolds to 'k'. Both need a second pass to settle.
    assert fold("ẞ") == "ss"
    assert fold("K") == "k"
    assert fold(fold("K")) == fold("K")


def test_a_string_that_never_settles_raises_rather_than_hanging_the_build(monkeypatch):
    """The loop is bounded rather than `while True`. A pathological input should
    fail the build that fed it, not spin forever inside it."""
    import knowledge.graph.ids as ids
    calls = {"n": 0}

    def churn(form, s):
        calls["n"] += 1
        return s + "a"                      # never reaches a fixed point
    monkeypatch.setattr(ids.unicodedata, "normalize", churn)
    with pytest.raises(IdError, match="stable normal form"):
        ids.fold("x")
    assert calls["n"] == MAX_FOLD_PASSES


# -- refusals ----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_something_that_cannot_be_an_id_is_refused(bad):
    with pytest.raises(IdError):
        node_id(CO, bad)


def test_a_name_of_pure_punctuation_is_refused_rather_than_becoming_an_empty_id():
    with pytest.raises(IdError, match="no word characters"):
        node_id(NodeKind.SECTOR, "--- ??? ---")


# -- the tables --------------------------------------------------------------

def test_every_node_kind_has_a_prefix_and_no_two_share_one():
    assert set(PREFIX) == set(NodeKind)
    assert len(set(PREFIX.values())) == len(PREFIX)


def test_the_kind_can_be_read_back_off_an_id():
    assert kind_of(node_id(CO, "Maybank")) is CO
    assert kind_of(node_id(NodeKind.COMMODITY, "aluminium")) is NodeKind.COMMODITY
    assert kind_of("MYX:1155") is None          # a raw instrument id is not a node id
    assert kind_of("nonsense") is None


def test_the_alias_table_and_the_display_names_agree_on_the_same_companies():
    """display_names is keyed by the CANONICAL id. Keying it by the spelling
    entities.yaml happens to use is a lookup that silently never matches."""
    for iid in display_names():
        assert instrument_id(iid) == iid, f"{iid} is not in canonical form"
    assert display_names()["XKLS:1155"] == "Maybank"
    assert aliases()[fold("Malayan Banking Berhad")] == "MYX:1155"
