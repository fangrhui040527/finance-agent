"""False company links, and stories that name a company without being news about it.

Every text here is a row from the live corpus to 2026-10-28, or the rule's
boundary built from one:

  * An exchange-qualified ticker names the company listed THERE: "Nha Be Steel
    JSC Estimates – UPCOM:TNB" is a Vietnamese steelmaker, and it was linked to
    Tenaga (MYX:5347) on the token alone.
  * entities.yaml's `not_the_company`: "Apple Hospitality REIT (NYSE:APLE)" and
    "Apple Bank" are not Apple Inc; "TNB" beside UPCOM, HOSE or Vietnam is not
    Tenaga.
  * Another company's contract win is a MENTION of its client ("Bintai Kinden
    wins RM20.3mil TNB contract", starred on Tenaga's page 2026-10-27/28).
  * A crash involving a company's lorry is an INCIDENT_BYSTANDER ("3 family
    members killed in Dungun crash", ranked first for Tenaga on 2026-10-25).

Neither kind is starred, escalated or read as a cause.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents.evidence.agents import event_type_for
from engines.events.taxonomy import EventType
from knowledge.feeds.adapter import FixtureFeed
from knowledge.news.linking import (
    INCIDENT_BYSTANDER,
    MENTION,
    NOT_A_CAUSE,
    EntityLinker,
    NotTheCompany,
    configured_exclusions,
    linker_for,
)

INDEX = {
    "Tenaga": "MYX:5347",
    "Tenaga Nasional": "MYX:5347",
    "TNB": "MYX:5347",
    "Maybank": "MYX:1155",
    "AMD": "XNAS:AMD",
    "Apple": "XNAS:AAPL",
    "Apple Inc": "XNAS:AAPL",
    "Microsoft": "XNAS:MSFT",
}
TENAGA = "MYX:5347"


# --- an exchange-qualified ticker -----------------------------------------------------


def test_a_ticker_behind_another_markets_code_is_not_the_book_name():
    linker = EntityLinker(INDEX)
    for text in (
        "Nha Be Steel JSC Estimates – UPCOM:TNB",
        "Issuance/retirement of stock (net) of Nha Be Steel JSC – UPCOM:TNB",
        "HOSE:TNB shares hit ceiling",
        "HNX:TNB Interactive Stock Chart",
        "SGX:TNB price target",
        "HKEX:TNB annual report",
        "Should you buy NYSE:AMD now?",
    ):
        assert linker.link(text) == [], text


def test_a_ticker_behind_its_own_markets_code_still_links():
    """MYX and KLSE are XKLS, NASDAQ and Nasdaq's tiers are XNAS, per markets.registry."""
    linker = EntityLinker(INDEX)
    assert linker.link("Tenaga Nasional Bhd Estimates – MYX:TENAGA") == [TENAGA]
    assert linker.link("KLSE:TNB closes higher") == [TENAGA]
    assert linker.link("The Nvidia-AMD Fight (NASDAQ:AMD)") == ["XNAS:AMD"]
    assert linker.link("Advanced Micro Devices (NasdaqGS:AMD) gains") == ["XNAS:AMD"]


def test_a_code_that_is_not_an_exchange_is_not_read_as_one():
    linker = EntityLinker(INDEX)
    assert linker.link("Update:TNB slipped 0.9%") == [TENAGA]
    assert linker.link("Stock Today: TNB Slipped 0.9%") == [TENAGA]


def test_a_rejected_exchange_link_is_an_excluded_mention_and_recheck_drops_it():
    """Recorded as the other exclusions are: the span is spent and links nothing,
    and a stored link the guard now rejects is dropped where rows are read."""
    linker = EntityLinker(INDEX)
    assert linker.recheck("Nha Be Steel JSC Estimates – UPCOM:TNB", [TENAGA]) == []
    # The company named in its own right elsewhere in the text still links.
    text = "Nha Be Steel (UPCOM:TNB) fell while Tenaga Nasional rose"
    assert linker.link(text) == [TENAGA]
    assert linker.recheck(text, [TENAGA]) == [TENAGA]


# --- not_the_company, from entities.yaml ----------------------------------------------


def test_entities_yaml_configures_the_names_that_are_not_the_company():
    ex = configured_exclusions()
    assert {"Apple Hospitality", "Apple Bank"} <= set(ex["XNAS:AAPL"].names)
    near = dict(ex[TENAGA].near)
    assert {"UPCOM", "HOSE", "Vietnam"} <= set(near["TNB"])


def test_apple_hospitality_and_apple_bank_are_not_apple():
    linker = linker_for(INDEX)
    for text in (
        "Apple Hospitality REIT, Inc. to Issue Monthly Dividend of $0.08 (NYSE:APLE)",
        "Apple Hospitality REIT stock hits 52-week high at 17.29 USD",
        "Apple Bank for Savings names new chief executive",
    ):
        assert linker.link(text) == [], text
        assert linker.recheck(text, ["XNAS:AAPL"]) == [], text
    assert linker.link("Apple Hospitality REIT rose while Apple Inc fell") == ["XNAS:AAPL"]
    assert linker.link("Apple's iPhone sales fell") == ["XNAS:AAPL"]


def test_tnb_near_a_vietnamese_market_word_is_not_tenaga():
    linker = linker_for(INDEX)
    assert linker.link("Vietnam's TNB steel unit reports loss") == []
    assert linker.link("UPCOM: TNB estimates revised") == []
    # Only the alias the rule names: Tenaga written in full is still Tenaga.
    assert linker.link("Tenaga Nasional signs Vietnam power deal") == [TENAGA]
    # Title Case or ALL CAPS only: "hose" is a word.
    assert linker.link("TNB crews hose down the cable trench") == [TENAGA]
    assert linker.link("TNB to raise tariffs next year") == [TENAGA]


def test_exclusions_reach_only_the_instrument_they_are_written_for():
    linker = EntityLinker(
        {"TNB": "MYX:5347", "Maybank": "MYX:1155"},
        {"MYX:1155": NotTheCompany(near=(("Maybank", ("Vietnam",)),))},
    )
    assert linker.link("Vietnam TNB unit") == ["MYX:5347"]
    assert linker.link("Vietnam Maybank unit") == []


# --- another company's contract win is a MENTION of its client -------------------------


def test_another_companys_contract_win_is_a_mention_of_its_client():
    linker = EntityLinker(INDEX)
    for text in (
        "Bintai Kinden wins RM20.3mil TNB contract",
        "Bintai Kinden wins RM20m TNB substation contract in Pahang",
        "Bintai Kinden bags RM20 mil Tenaga contract in Pahang, second since PN17 exit",
        "Bintai Kinden unit accepts TNB contract for RM20.28mil",
        "Jati Tinggi bags two TNB contracts worth RM15.7 mil",
        "Jati Tinggi Unit Bags MYR13 Million Deal from Tenaga Nasional",
        "Jati Tinggi, Kerjaya Prospek consortium wins RM568m TNB contract",
        "Kerjaya Prospek Bags RM568 Million TNB Substation Contract In Terengganu",
        "ACE Market-bound SKA Capital bags 6 TNB-linked jobs worth RM89.3 mil",
        "SKA Capital unit accepts RM89.28mil award for TNB contracts",
        "SKA Capital unit secures RM89.28mil contracts for TNB projects",
    ):
        # Still linked: the name is written, and retrieval and the graph read mentions.
        assert linker.link(text) == [TENAGA], text
        assert linker.incidental(text) == {TENAGA: MENTION}, text


def test_the_company_awarding_or_acting_is_still_its_own_news():
    linker = EntityLinker(INDEX)
    for text in (
        "TNB awards RM568m substation contract to Kerjaya Prospek",
        "TNB, Petronas-backed regional power project secures Singapore's conditional approval",
        "TNB issues RM4.8bil sukuk",
        # One mention in another role and the story is about the company too.
        "Bintai Kinden wins RM20.3mil TNB contract; TNB shares rose 1%",
        # An insider's share award is the company's own filing, not a contract.
        "Amy Hood Receives 29,077 Microsoft Shares in Insider Award Transaction",
    ):
        assert linker.incidental(text) == {}, text


# --- an accident involving the company's lorry is an INCIDENT_BYSTANDER -----------------


def test_a_crash_involving_the_companys_vehicle_is_an_incident_bystander():
    linker = EntityLinker(INDEX)
    for text in (
        "3 family members killed in Dungun crash. Man, wife and younger brother die after "
        "MPV and TNB vehicle collide on Kuala Terengganu-Kuantan trunk road.",
        "TNB expresses condolences to family of three killed in Dungun crash. Tenaga "
        "Nasional Bhd (TNB) has extended its condolences to the family of three people "
        "killed in a collision involving a company vehicle",
        "Man dies after suspected electric shock while stealing cable. A man died after he "
        "was believed to have been electrocuted while stealing a Tenaga Nasional Bhd (TNB) "
        "cable along Jalan Acob, Kapar, Klang",
        "Two killed as car rams TNB lorry in Kuantan",
    ):
        assert linker.incidental(text) == {TENAGA: INCIDENT_BYSTANDER}, text
        assert linker.link(text) == [TENAGA], text


def test_an_incident_that_reaches_the_business_is_still_the_companys_news():
    linker = EntityLinker(INDEX)
    for text in (
        "Two TNB workers injured in substation fire that triggered JB power outage",
        "TNB restores power within 77 minutes after Kangkar Tebrau substation fire",
        # No vehicle, staff or premises of the company's: an ordinary story.
        "SNAPSHOT | Brokerage firms downgrade TNB to 'hold' over subsidy; child dies "
        "after allegedly thrown into river",
    ):
        assert linker.incidental(text) == {}, text


def test_both_kinds_are_never_a_cause():
    assert {MENTION, INCIDENT_BYSTANDER} == NOT_A_CAUSE


def test_a_stored_event_of_either_kind_is_never_typed_as_a_catalyst():
    title = "Bintai Kinden wins RM20.3mil TNB contract"
    assert event_type_for("announcement", title) is EventType.CONTRACT_WIN
    assert event_type_for(MENTION, title) is None
    assert event_type_for(INCIDENT_BYSTANDER, "TNB lorry crash closes road") is None


# --- the escalation gate and the digest -----------------------------------------------


def test_the_ingest_gate_does_not_open_for_a_name_the_story_is_not_about():
    feed = FixtureFeed(
        records=[
            {
                "id": str(i),
                "title": title,
                "body": "",
                "published_at": "2026-10-27T08:00:00+00:00",
                "domain": "klsescreener.com",
            }
            for i, title in enumerate(
                (
                    "Bintai Kinden wins RM20.3mil TNB contract",
                    "Two killed as car rams TNB lorry, police warn of delays",
                    "TNB profit rose on higher tariffs",
                )
            )
        ]
    )
    arts, stats = feed.normalize(
        feed.fetch(datetime(2026, 10, 26, tzinfo=UTC)),
        entity_index=INDEX,
        holdings=set(),
        watchlist={TENAGA},
    )
    assert [a.instruments for a in arts] == [[TENAGA]] * 3
    assert [a.escalated for a in arts] == [False, False, True]
    assert stats.escalated == 1


def test_the_digest_lists_each_kind_unstarred_and_below_the_companys_own_news(tmp_path):
    from knowledge.corpus import Corpus
    from knowledge.digest import build_digest
    from knowledge.news.features import Article, LexiconExtractor

    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    ex = LexiconExtractor()
    path = tmp_path / "corpus.db"
    with Corpus(path) as c:
        for doc_id, title, body, quality in (
            ("a", "Bintai Kinden wins RM20.3mil TNB contract", "", 0.95),
            (
                "b",
                "3 family members killed in Dungun crash",
                "Man, wife and younger brother die after MPV and TNB vehicle collide.",
                1.0,
            ),
            ("c", "TNB issues RM4.8bil sukuk", "", 0.8),
            ("d", "TNB profit rose on higher tariffs", "", 0.6),
        ):
            art = Article(
                doc_id=doc_id,
                title=title,
                body=body,
                source_domain="example.com",
                published_at=now,
                instruments=[TENAGA],
                quality=quality,
                escalated=True,  # what the ingest gate stored before this rule
            )
            art.features = ex.extract(art.text, ["TNB"])
            c.add(art, source="fixture", seen_at=now)

    class _Cfg:
        watchlist = (TENAGA,)
        holdings = ()
        corpus_db = str(path)
        facts_db = str(tmp_path / "facts.db")

    d = build_digest(_Cfg(), now=now, corpus_path=str(path), facts_path=str(tmp_path / "facts.db"))
    name = next(n for n in d.names if n.instrument_id == TENAGA)
    assert [(s["doc_id"], s["kind"], s["escalated"]) for s in name.stories] == [
        ("d", "", True),
        ("c", "", False),
        ("b", INCIDENT_BYSTANDER, False),
        ("a", MENTION, False),
    ]
    assert name.escalated == 1
    page = d.to_markdown()
    assert "Bintai Kinden wins RM20.3mil TNB contract [q 0.95, tone +1.0] [mention]" in page
    assert "killed in Dungun crash [q 1.00, tone +0.0] [incident_bystander]" in page
    assert page.count("★") == 1
