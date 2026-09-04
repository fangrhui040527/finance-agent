"""Entity linking with word boundaries, and relevance scored on names.

Every case here is a false positive or a false negative that was in the live
corpus on 2026-09-04, or the rule that stops it. The old linker was
`surface.lower() in text.lower()`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge.feeds.adapter import FixtureFeed, link_entities
from knowledge.news.features import LexiconExtractor, should_escalate
from knowledge.news.linking import EntityLinker, alias_pattern, linker_for

INDEX = {
    "Maybank": "MYX:1155",
    "Malayan Banking Berhad": "MYX:1155",
    "Maybank Islamic": "MYX:1155-I",
    "Tenaga": "MYX:5347",
    "Tenaga Nasional": "MYX:5347",
    "TNB": "MYX:5347",
    "MISC": "MYX:3816",
    "IHH": "MYX:5225",
    "Intel": "XNAS:INTC",
    "AMD": "XNAS:AMD",
    "Apple": "XNAS:AAPL",
    "Apple Inc": "XNAS:AAPL",
    "NVIDIA": "XNAS:NVDA",
    "Nvidia Corporation": "XNAS:NVDA",
    "英伟达": "XNAS:NVDA",
    "苹果公司": "XNAS:AAPL",
}


# --- the false positives that were in the corpus ------------------------------------


def test_intel_does_not_match_intelligence():
    assert link_entities("Artificial intelligence spending rose", INDEX) == []
    assert link_entities("Intel reported a loss", INDEX) == ["XNAS:INTC"]


def test_amd_does_not_match_amdocs_or_lowercase_running_text():
    assert link_entities("Amdocs signed a telco deal", INDEX) == []
    assert link_entities("the amd in the report", INDEX) == []
    assert link_entities("AMD unveiled a new GPU", INDEX) == ["XNAS:AMD"]


def test_misc_does_not_match_miscellaneous():
    assert link_entities("miscellaneous expenses and Misc. items", INDEX) == []
    assert link_entities("MISC Berhad chartered two tankers", INDEX) == ["MYX:3816"]


def test_apple_does_not_match_pineapple_or_the_fruit():
    assert link_entities("pineapple exports climbed", INDEX) == []
    assert link_entities("an apple a day", INDEX) == []
    assert link_entities("Apple's iPhone sales fell", INDEX) == ["XNAS:AAPL"]
    assert link_entities("APPLE SHARES SLIDE", INDEX) == ["XNAS:AAPL"]


def test_tenaga_the_utility_is_not_tenaga_the_malay_noun():
    """`tenaga` is Malay for energy or workforce. Lowercase is the noun."""
    assert link_entities("kekurangan tenaga kerja di sektor pembinaan", INDEX) == []
    assert link_entities("Tenaga Nasional Berhad naik 2%", INDEX) == ["MYX:5347"]
    assert link_entities("Tenaga rose after the tariff review", INDEX) == ["MYX:5347"]


# --- the matches that must survive --------------------------------------------------


def test_possessives_and_punctuation_still_count():
    assert link_entities("Maybank's results, Maybank; Maybank.", INDEX) == ["MYX:1155"]


def test_acronyms_match_as_whole_words_only():
    assert link_entities("TNB and IHH both gained", INDEX) == ["MYX:5347", "MYX:5225"]
    assert link_entities("TNBX is not TNB", INDEX) == ["MYX:5347"]


def test_all_caps_and_titlecase_forms_of_a_proper_noun_both_link():
    assert link_entities("Nvidia beat estimates", INDEX) == ["XNAS:NVDA"]
    assert link_entities("NVIDIA beat estimates", INDEX) == ["XNAS:NVDA"]


def test_a_short_inflectional_tail_on_a_proper_noun_still_links():
    """The two true mentions the strict boundary lost on the live corpus: a
    Danish genitive and a Croatian one. Two letters at most, so 'intelligence'
    stays out."""
    idx = {**INDEX, "Microsoft": "XNAS:MSFT"}
    assert link_entities("Microsofts nye regnskab", idx) == ["XNAS:MSFT"]
    assert link_entities("Programeri traže milijarde od Applea", idx) == ["XNAS:AAPL"]
    assert link_entities("Intelligence budgets rose", idx) == []
    assert link_entities("Applesauce futures", idx) == []


def test_cjk_aliases_match_inside_unspaced_text():
    assert link_entities("英伟达股价大涨，苹果公司下跌", INDEX) == ["XNAS:NVDA", "XNAS:AAPL"]


def test_longest_alias_still_wins_so_a_subsidiary_precedes_its_parent():
    assert link_entities("Maybank Islamic launched a fund", INDEX)[0] == "MYX:1155-I"


def test_multiword_aliases_are_case_insensitive():
    assert link_entities("MALAYAN BANKING BERHAD reported", INDEX) == ["MYX:1155"]


def test_the_linker_is_compiled_once_per_index():
    assert linker_for(INDEX) is linker_for(dict(INDEX))


def test_an_empty_alias_is_refused():
    import pytest

    with pytest.raises(ValueError):
        alias_pattern("  ")


# --- relevance is scored on names, and one mention clears the gate ------------------


def test_names_for_returns_every_alias_of_a_linked_instrument():
    linker = EntityLinker(INDEX)
    assert set(linker.names_for(["XNAS:AAPL"])) == {"Apple", "Apple Inc", "苹果公司"}
    assert linker.names_for(["XNAS:UNKNOWN"]) == ["XNAS:UNKNOWN"]


def test_a_single_mention_headline_about_a_watched_name_now_escalates():
    """Before: relevance was scored against 'XNAS:NVDA', which no headline
    contains, so every article scored 0.0 and 23 sweeps escalated nothing."""
    feed = FixtureFeed(
        records=[
            {
                "id": "1",
                "title": "Nvidia beats on data-centre demand",
                "body": "Chipmaker raised its outlook for the year.",
                "published_at": "2026-09-03T08:00:00+00:00",
                "domain": "example.com",
            }
        ]
    )
    arts, stats = feed.normalize(
        feed.fetch(datetime(2026, 9, 1, tzinfo=UTC)),
        entity_index=INDEX,
        holdings=set(),
        watchlist={"XNAS:NVDA"},
    )
    assert arts[0].instruments == ["XNAS:NVDA"]
    assert arts[0].features is not None and arts[0].features.relevance >= 0.5
    assert stats.escalated == 1
    assert should_escalate(arts[0].features, arts[0].instruments, set(), {"XNAS:NVDA"})


def test_relevance_grows_with_mentions_and_is_zero_without_one():
    ex = LexiconExtractor()
    assert ex.extract("Shipping rates rose", ["Maybank"]).relevance == 0.0
    one = ex.extract("Maybank posted a profit", ["Maybank"]).relevance
    two = ex.extract("Maybank said Maybank Islamic grew", ["Maybank"]).relevance
    assert 0.34 <= one < two <= 1.0
