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


# --- a place named after a company is not the company -------------------------------


def test_a_road_named_after_a_bank_is_not_the_bank():
    """In the corpus on 2026-09-07: "Maybank Highway at Main Road closed after
    early morning multi-vehicle crash" was linked to MYX:1155 and escalated to
    the review queue as Maybank news."""
    assert link_entities("Maybank Highway at Main Road closed after a crash", INDEX) == []
    assert link_entities("Apple Street repaving begins Monday", INDEX) == []


def test_the_company_still_links_when_the_road_word_is_not_next_to_it():
    assert link_entities("Maybank said the Highway toll concession was refinanced", INDEX) == [
        "MYX:1155"
    ]
    assert link_entities("Maybank branch on Main Road reopened", INDEX) == ["MYX:1155"]


def test_a_lowercase_road_word_is_ordinary_english_not_a_place_name():
    """The rule is case-sensitive on purpose: "Nvidia roadmap" and "Maybank
    street lending" are the company, "Maybank Highway" is tarmac."""
    assert link_entities("Nvidia road map for the next generation", INDEX) == ["XNAS:NVDA"]


def test_the_thoroughfare_list_excludes_what_companies_name_after_themselves():
    from knowledge.news.linking import THOROUGHFARE

    for owned in ("Park", "Tower", "Plaza", "Centre", "Stadium", "Arena", "Bridge"):
        assert owned not in THOROUGHFARE
    assert link_entities("Apple Park welcomed developers this week", INDEX) == ["XNAS:AAPL"]


def test_a_building_named_in_malay_order_is_not_the_company():
    """In the corpus on 2026-10-10, fmt_business: the only Maybank in the row
    is the venue in the body, and it was linked to MYX:1155. Malay puts the
    generic noun FIRST, so THOROUGHFARE's look at the following word never saw
    it."""
    wedding = (
        "Syed Saddiq, Bella Astillah tie the knot. The marriage was solemnised by "
        "ex-religious minister Zulkifli Mohamad al-Bakri at Menara Merdeka Maybank."
    )
    assert link_entities(wedding, INDEX) == []
    assert link_entities("Wedding held at Wisma Merdeka Tenaga Nasional", INDEX) == []


def test_the_companys_own_tower_in_malay_still_links_as_maybank_tower_does():
    """'Menara Maybank' is Maybank Tower, which THOROUGHFARE deliberately keeps:
    only a tower named something ELSE that carries the alias is excluded."""
    assert link_entities("Maybank AGM held at Menara Maybank", INDEX) == ["MYX:1155"]
    assert link_entities("Fire drill at Menara Maybank", INDEX) == ["MYX:1155"]
    assert link_entities("Menara Merdeka Maybank wedding; Maybank shares rose", INDEX) == [
        "MYX:1155"
    ]


# --- a short alias inside another company's name is not the company -----------------


def test_a_short_alias_inside_another_companys_name_is_not_the_company():
    """In the corpus from 2026-09-24: 21 rows on Shree TNB Polymers, an Indian
    BSE listing, linked to Tenaga because "TNB" sits inside its name - the last
    one stored on 10-11, after the 10-10 span rule, which only spends an alias
    inside a longer name somebody LISTED."""
    for headline in (
        "Average basic shares outstanding of Shree TNB Polymers Limited – BSE:SHREETNB",
        "Shree TNB Polymers IPO Day 1 Subscription Status 25 Sep 2026",
        "Shree TNB Polymers Ltd. IPO: Check Issue Date, Price, Subscription",
        "Shree TNB Polymers (SHREETNB) Share/Stock Price Live, BSE",
        "AMD Industries Ltd board approves results",
    ):
        assert link_entities(headline, INDEX) == [], headline


def test_an_all_caps_headline_gets_the_same_exclusions():
    """Some feeds set whole titles in capitals ("MHTC AND MAYBANK PARTNER TO
    BRING MALAYSIA HEALTHCARE...", 2026-10-15), and an alias matches ALL CAPS,
    so the words that exclude it must too."""
    assert link_entities("SHREE TNB POLYMERS LIMITED LISTS ON BSE", INDEX) == []
    assert link_entities("WEDDING AT MENARA MERDEKA MAYBANK", INDEX) == []
    # Still the company in capitals, as in Title Case.
    assert link_entities("TNB SHARES RISE ON TARIFF NEWS", INDEX) == ["MYX:5347"]
    assert link_entities("FIRE DRILL AT MENARA MAYBANK", INDEX) == ["MYX:1155"]


def test_the_same_short_alias_still_links_where_it_is_the_company():
    """Real TNB rows from the same corpus, each with a capitalised word beside
    the acronym, plus the company named separately from the other one."""
    for headline in (
        "How TNB Electron is plugging Malaysia's EV gap",
        "Stock Picks: TNB And SP Setia",
        "Policy Intervention Could Undermine TNB And IBR Framework",
        "TNB Bill Relief: Anwar announces AFA exemptions",
        "Shree TNB Polymers lists in Mumbai while TNB shares rose",
    ):
        assert link_entities(headline, INDEX) == ["MYX:5347"], headline
    assert link_entities("AIA Excluding Individual IHH Consultants From Panel", INDEX) == [
        "MYX:5225"
    ]


# --- a fund named after the bank is not the bank -----------------------------------


def test_a_funds_quote_page_is_not_the_bank_that_names_the_fund():
    """All three were stored rows linked to MYX:1155. Each is a fund's quote
    page, and the fund's own identifier closes its name: a Morningstar id
    ("0P" and eight more) or a mutual-fund code ending ".MF"."""
    for headline in (
        "Fidelity FAST Maybank ACI OppAMCDIST(G)$ (0P0001STK3) Interactive Stock Chart",
        "FAST Maybank ACI Opp A-MINCOME(G)-USD (0P0001STJZ) Stock Forum & Discussion",
        "$Maybank Retail Money Market-I Fund (MYU0100A2266.MF)$",
    ):
        assert link_entities(headline, INDEX) == [], headline


def test_news_about_the_banks_funds_still_links_to_the_bank():
    """Stored rows too: a story about the bank's fund business carries no fund
    identifier, and a quote page that also names the bank elsewhere keeps it."""
    for headline in (
        "Maybank’s Nasdaq Fund Surpasses RM100 Million AUM In 2 Months",
        "Maybank Tokenises SGD Money Market Fund on Synthesys",
        "Maybank shares rose (0P0001STK3 fund page links below). Analysts cheered",
        "FAST Maybank ACI Opp (0P0001STJZ) chart; Maybank shares rose 1%",
        # Near an identifier is not inside a fund's name: a lowercase word
        # between them ends the name (Sourcery on #92).
        "Maybank launches Asia Income Fund (0P0001ABCD) for retail investors",
        "Maybank says its new fund (MYU0100A2266.MF) drew RM100 million",
    ):
        assert link_entities(headline, INDEX) == ["MYX:1155"], headline


# --- a bank's broker arm writing about another company -----------------------------

BANKS = {**INDEX, "CIMB": "MYX:1023", "Genting": "MYX:3182", "IHH Healthcare": "MYX:5225"}


def test_a_bank_writing_a_broker_note_is_its_author_not_its_subject():
    """Six of Maybank's eight digest rows on 2026-10-15 were research notes by
    Maybank Securities / Maybank IB about other companies. Linked - the bank is
    named - but as the note's author: `authored` says so."""
    linker = EntityLinker(BANKS)
    for headline in (
        "Maybank kept IDR 3,800 target for Merdeka Copper Gold stock - ad-hoc-news.de",
        "Maybank keeps THB 75.00 target for Airports of Thailand stock",
        "Maybank holds target for Bank Rakyat Indonesia stock at IDR 3,900",
        "Grab Holdings Keeps BUY Rating as Maybank Cuts Target Price to USD4.42",
        "Sheng Siong shares fall 3% after Maybank downgrade, target price cut",
        "GEN Bhd downgraded by Maybank as casino group flags rights issuance",
        "Maybank IB upgrades SD Guthrie to ‘buy’ post sell-off",
        "Maybank Keeps BUY on ISOTeam, Cuts TP to S$0.09 After FY26 Miss",
        "Billions in foreign capital not Vietnam’s biggest gain: Maybank Securities CEO",
        "Have Singapore banks fallen enough? Maybank Securities weighs in",
        "Corporate Malaysia could face tougher second-half, Maybank IB cautions",
        "SD Guthrie selldown driven by overreaction. Maybank Investment Bank says the market",
    ):
        assert "MYX:1155" in linker.link(headline), "still linked: a mention is kept"
        assert linker.authored(headline) == {"MYX:1155"}, headline


def test_the_research_rule_generalises_to_any_linked_bank_and_keeps_the_subject():
    linker = EntityLinker(BANKS)
    text = 'CIMB keeps "Buy" call on TNB, RM15.90 target'
    assert linker.link(text) == ["MYX:1023", "MYX:5347"]
    assert linker.authored(text) == {"MYX:1023"}
    text = "Maybank keeps SELL on Genting, cuts target price after the rights issue"
    assert linker.authored(text) == {"MYX:1155"}


def test_a_figure_a_bank_expects_for_another_companys_stock_is_its_note():
    """2026-10-17, stored on the merged code and still on Maybank's page: the
    bank's research on a Thai telecom. A forecast verb counts only with an
    amount followed by "for <Name> ... stock", which names whose stock it is."""
    linker = EntityLinker(BANKS)
    for headline in (
        "Results due November 3: Maybank expects THB 13.4 billion for Advanced Info "
        "Service stock - ad-hoc-news.de",
        "Maybank sees USD 4.42 for Grab stock",
        "Maybank Securities forecasts S$1.20 for Sheng Siong shares",
        # The Malaysian press often spaces the currency from the figure.
        "Maybank expects RM 5.20 for Gamuda shares",
        "Maybank keeps RM 14.60 target for Tenaga Nasional stock",
    ):
        assert "MYX:1155" in linker.link(headline), headline
        assert linker.authored(headline) == {"MYX:1155"}, headline


def test_more_broker_templates_read_as_the_banks_note():
    """Four stored rows the rule missed until 2026-10-22, each Maybank's research
    on another company: a rating set after the stock, a 'Rates' verb, a forecast
    then the call in one clause, and a name abbreviated with full stops."""
    linker = EntityLinker(BANKS)
    for headline in (
        "Mitra Keluarga targets 10% growth. Maybank rates Mitra Keluarga stock Buy - ad-hoc-news.de",
        "Maybank Rates ‘Hold’ on GPSC as Heightened Gas Prices Pressure Earnings",
        "Maybank Forecasts Robust 4Q26 Recovery for ADVANC, Maintains ‘Buy’ With THB387 Target",
        "Maybank Starts Frasers Log. & Comm. Trust at BUY With SGD1.00 Target Price",
    ):
        assert "MYX:1155" in linker.link(headline), headline
        assert linker.authored(headline) == {"MYX:1155"}, headline


def test_rates_as_a_noun_and_the_banks_own_plans_stay_its_news():
    """'rates' is also the noun the bank's own news uses, and a forecast without
    a call on another company is the bank's view of itself."""
    linker = EntityLinker(BANKS)
    for headline in (
        "Maybank rates unchanged as BNM holds OPR",
        "Maybank Rates Rise; Stock Buy Seen by Analysts",
        "Maybank forecasts loan growth for 2027, keeps dividend payout",
        "Maybank buyback: Maybank stock buyback approved",
        "Would I Buy Maybank at 6.4% Dividend Yield Now?",
        # Ambiguous: a sell call BY Maybank, or ON it - left as Maybank news.
        "Genting Bhd Rights Issue Draws Maybank Sell Call",
    ):
        assert linker.authored(headline) == set(), headline


def test_a_bank_forecasting_its_own_business_is_not_a_broker_note():
    linker = EntityLinker(BANKS)
    for headline in (
        "Maybank expects loan growth of 5% in 2026",
        "Maybank expects RM2 billion for its Islamic arm",
        "Maybank sees RM10 billion in new green financing for SMEs",
        "Maybank expects RM1.2 billion for Islamic banking expansion",
        "Maybank expects THB 13.4 billion for Advanced Info Service stock; Maybank shares rose",
        # Its own stock: the second mention is not in an author's shape.
        "Maybank expects RM10 billion for Maybank shares",
    ):
        assert linker.authored(headline) == set(), headline


def test_news_about_the_bank_itself_is_not_a_broker_note():
    linker = EntityLinker(BANKS)
    for headline in (
        "Maybank profit rose on wider margins",
        "Maybank sets RM80bil sustainable finance target for 2030",
        "Maybank keeps AAA rating from RAM",
        "Maybank lifts golf tourism",
        "Maybank Securities fined by regulator over trade reporting lapses",
        "Maybank kept IDR 3,800 target for Merdeka Copper Gold; Maybank shares rose 1%",
        "S&P cuts Maybank rating outlook to negative",
    ):
        assert linker.authored(headline) == set(), headline


def test_a_company_a_broker_rates_is_the_subject_not_the_author():
    """Both were stored rows. The call is ON the named company here: the note
    names no other company for it to be about."""
    linker = EntityLinker(BANKS)
    for headline in (
        "IHH Healthcare maintains Buy, target price lowered to RM9.06",
        "Apple upgraded to Buy as price hikes offset memory costs",
        "Suppliers slide as Apple cuts iPhone production target for China",
    ):
        assert linker.link(headline), headline
        assert linker.authored(headline) == set(), headline


# --- stored links, read by today's rules --------------------------------------------


def test_a_stored_link_the_rules_now_exclude_is_dropped_on_recheck():
    """A link is written once, when the row is stored. All of these were stored
    rows whose link a rule added later excludes; on 2026-10-17 the digest still
    listed the Shree TNB row under Tenaga after the rule had merged."""
    linker = EntityLinker({**INDEX, "Genting": "MYX:3182"})
    for text, stored in (
        ("Half Yearly Results, Half yearly Earnings for Shree TNB Polymers", ["MYX:5347"]),
        ("Is Genting Singapore Becoming a Better Dividend Stock?", ["MYX:3182"]),
        ("Maybank Highway at Main Road closed after crash", ["MYX:1155"]),
        ("Wedding held at Menara Merdeka Maybank", ["MYX:1155"]),
        (
            "Fidelity FAST Maybank ACI OppAMCDIST(G)$ (0P0001STK3) Interactive Stock Chart",
            ["MYX:1155"],
        ),
    ):
        assert linker.recheck(text, stored) == [], text


def test_recheck_keeps_what_the_rules_still_link_in_stored_order():
    linker = EntityLinker(INDEX)
    text = "Shree TNB Polymers lists in Mumbai while TNB and Maybank shares rose"
    assert linker.recheck(text, ["MYX:1155", "MYX:5347"]) == ["MYX:1155", "MYX:5347"]
    assert linker.recheck(text, []) == []
    # Only the excluded id goes; the other stays where it was.
    text = "Shree TNB Polymers IPO; Maybank shares rose"
    assert linker.recheck(text, ["MYX:5347", "MYX:1155"]) == ["MYX:1155"]


def test_recheck_keeps_a_stored_link_the_text_never_writes():
    """Until 2026-10-10 a vendor's ticker was stored as the link ("What Has
    Changed About Owning Amazon Stock?" under MSFT). The rules read what the
    text names, so a row that never writes the alias is not theirs to judge."""
    linker = EntityLinker(INDEX)
    assert linker.recheck("What Has Changed About Owning Amazon Stock?", ["XNAS:MSFT"]) == [
        "XNAS:MSFT"
    ]
    assert linker.recheck("Malaysian utilities rally on tariff review", ["MYX:5347"]) == [
        "MYX:5347"
    ]


def test_an_excluded_alias_in_capitals_or_cjk_is_still_recognised_as_written():
    linker = EntityLinker(INDEX)
    assert linker.recheck("SHREE TNB POLYMERS LIMITED LISTS ON BSE", ["MYX:5347"]) == []
    # A CJK alias has no exclusions, so a stored CJK link always stands.
    assert linker.recheck("英伟达股价上涨", ["XNAS:NVDA"]) == ["XNAS:NVDA"]


# --- the escalation gate needs materiality, not just a mention ----------------------


def _f(text: str):
    return LexiconExtractor().extract(text, ["Maybank"])


def test_naming_a_holding_is_not_by_itself_a_reason_to_spend_a_model_call():
    """The gate used to read `relevance >= 0.34 AND a watched name is present`,
    and relevance IS the mention count - so the two conditions were the same
    condition and 65% of linked articles escalated. These four are real corpus
    headlines; none reports anything about the bank."""
    held = {"MYX:1155"}
    for headline in (
        "Maybank provides over S$590,000 to 3,000 beneficiaries through its programme",
        "Golf: Maybank lifts golf tourism",
        "Maybank Singapore simplifies auto financing with an all-in-one service",
        "$MAYBANK (1155.MY)$ OMG!!! My fam got rm188 here wowww sharing",
    ):
        f = _f(headline)
        assert f.relevance >= 0.34, "the name is there; that is the point"
        assert not should_escalate(f, ["MYX:1155"], held, set()), headline


def test_a_story_that_reports_something_financial_still_escalates():
    held = {"MYX:1155"}
    for headline in (
        "Maybank keeps BUY on ISOTeam but cuts target price after an FY26 miss",
        "Maybank profit rose on wider margins",
        "Maybank shares plunged after the guidance was withdrawn",
        "Maybank outlook: management expects loan growth next year",
    ):
        assert should_escalate(_f(headline), ["MYX:1155"], held, set()), headline


def test_the_gate_still_needs_the_name_to_be_one_you_hold_or_watch():
    f = _f("Maybank profit rose on wider margins")
    assert not should_escalate(f, ["MYX:1155"], set(), set())
    assert should_escalate(f, ["MYX:1155"], set(), {"MYX:1155"})
