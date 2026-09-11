"""Cleaning rules, each pinned to the artefact it removes."""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge.feeds.adapter import FixtureFeed
from knowledge.news.clean import (
    domain_of,
    is_junk,
    is_low_value,
    language_allowed,
    language_key,
    normalise_text,
    quality_score,
    split_publisher,
    strip_html,
)

# --- normal form ------------------------------------------------------------------


def test_gdelt_spacing_artefacts_are_repaired():
    assert (
        normalise_text("NYC Mayor Bans AI For Nearly 600 , 000 Students")
        == "NYC Mayor Bans AI For Nearly 600,000 Students"
    )
    assert normalise_text("Unjuran dijangka kekal 5 . 3 % tahun ini") == (
        "Unjuran dijangka kekal 5.3% tahun ini"
    )
    assert normalise_text("Google ' s ad tech tools . They live .") == (
        "Google's ad tech tools. They live."
    )
    assert normalise_text("Where Will Bitcoin Be in 2030 ? ") == "Where Will Bitcoin Be in 2030?"


def test_html_is_stripped_and_entities_decoded_even_when_double_encoded():
    raw = "<p>Maybank &amp;amp; CIMB rose<br/>on <a href='x'>hopes</a>&nbsp;of a cut</p>"
    assert normalise_text(raw) == "Maybank & CIMB rose on hopes of a cut"
    assert strip_html("") == ""


def test_zero_width_characters_and_nfkc_forms_are_normalised():
    assert normalise_text("Ｍaybank​ posts") == "Maybank posts"


def test_publisher_suffix_is_split_only_when_it_looks_like_one():
    assert split_publisher("Maybank Q2 profit up 8% - The Star") == (
        "Maybank Q2 profit up 8%",
        "The Star",
    )
    assert split_publisher("Q3 results - 12%") == ("Q3 results - 12%", None)
    assert split_publisher("No suffix here") == ("No suffix here", None)


# --- language -----------------------------------------------------------------------


def test_language_codes_and_names_meet_in_one_key():
    assert language_key("en") == language_key("English") == language_key("en-US") == "english"
    assert language_key("ms") == language_key("Malay") == "malay"
    assert language_key("zh-CN") == language_key("Chinese") == "chinese"


def test_an_empty_allowlist_keeps_everything_and_a_set_one_filters():
    assert language_allowed("Korean", [])
    assert language_allowed("English", ["English", "ms"])
    assert language_allowed("ms", ["English", "ms"])
    assert not language_allowed("Korean", ["English", "ms"])
    assert language_allowed("", ["English"]), "an RSS feed with no language tag passes"


# --- junk and value -----------------------------------------------------------------


def test_junk_is_recognised_and_low_value_is_scored_not_dropped():
    assert is_junk("Maybank branch hosts lottery draw: 4D results")
    assert not is_junk("Maybank posts record quarter")
    assert is_low_value("Should You Buy Nvidia Stock Before Earnings?")
    assert is_low_value("Where Will Bitcoin Be in 2030?")
    assert not is_low_value("Nvidia beats on data-centre demand")


def test_a_cashtag_post_is_dropped_but_the_publisher_is_not():
    """`$MAYBANK (1155.MY)$` is a forum tag, not a headline.

    Google News carries moomoo.com's social posts as if they were reporting.
    They arrive with an empty body and nothing to report - the corpus held
    `$PCHEM (5183.MY)$` as an entire article, and three variants of a giveaway
    scam. Dropping them is not the same as dropping the publisher: 11 of
    moomoo's 19 articles were real syndicated journalism, one of them the
    Bursa story this book is short of.
    """
    assert is_junk("$MAYBANK (1155.MY)$")
    assert is_junk("$MAYBANK (1155.MY)$ Keep it up")
    assert is_junk("$PCHEM (5183.MY)$ OMG!!! My mom got FREE RM188 here wowww")
    # One post can tag several names at once.
    assert is_junk("$KPJ (5878.MY)$ $SUNMED (5555.MY)$ $IHH (5225.MY)$ Today KPJ up +3.45%")

    # Same publisher, real reporting - kept.
    assert not is_junk("Foreigners Dump Banks While Locals Gobble Up Maybank")
    assert not is_junk("NVIDIA(NVDA.US) Director Sells US$235.64 Million in Common Stock")


def test_a_headline_quoting_a_dollar_amount_is_not_a_cashtag():
    """The pattern is anchored at the start of the title on purpose.

    Money in a headline is ordinary; measured across the 3,374-article corpus
    the anchored form matched the 8 cashtag posts and none of the real
    headlines carrying a figure.
    """
    for title in (
        "Tokyo Court Rejects IHH Subsidiary's $1.25 Billion Claim",
        "Nvidia to buy Hugging Face for nearly $13 billion in big bet on open AI models",
        "Got $1,000? An Investment in Micron Could Be Worth This Much by 2027",
        "Taiwan to announce at least US$20bn in US chip investments: Lutnick",
    ):
        assert not is_junk(title), title


def test_quality_rewards_a_summary_a_link_and_an_edited_source():
    bare = quality_score("Nvidia beats", "", "randomblog.example", linked=False)
    linked = quality_score("Nvidia beats estimates", "", "randomblog.example", linked=True)
    full = quality_score(
        "Nvidia beats estimates",
        "The chipmaker reported revenue well above consensus on data-centre demand.",
        "https://www.reuters.com/technology/x",
        linked=True,
    )
    assert bare < linked < full <= 1.0
    assert quality_score("Casino night: 4D results", "", "x", linked=True) == 0.0
    assert quality_score("Should You Buy Apple Stock?", "", "fool.com", linked=True) < linked


def test_domain_of_strips_scheme_and_www():
    assert domain_of("https://www.thestar.com.my/business/x") == "thestar.com.my"
    assert domain_of("Reuters.com") == "reuters.com"


# --- applied in the adapter -----------------------------------------------------------

ROWS = [
    {
        "id": "1",
        "title": "Maybank &amp; CIMB rise - The Star",
        "body": "<p>Both banks gained after Bank Negara held the OPR.</p>",
        "published_at": "2026-09-03T08:00:00+00:00",
        "domain": "thestar.com.my",
        "language": "en",
    },
    {
        "id": "2",
        "title": "LG , 7~17일 마곡서 AI 축제",
        "body": "",
        "published_at": "2026-09-03T08:00:00+00:00",
        "domain": "zdnet.co.kr",
        "language": "Korean",
    },
    {
        "id": "3",
        "title": "Maybank sponsors lottery: 4D results tonight",
        "body": "",
        "published_at": "2026-09-03T08:00:00+00:00",
        "domain": "spam.example",
        "language": "en",
    },
]
INDEX = {"Maybank": "MYX:1155", "CIMB": "MYX:1023"}


def test_the_adapter_cleans_filters_and_scores_before_it_keeps():
    feed = FixtureFeed(records=ROWS)
    arts, stats = feed.normalize(
        feed.fetch(datetime(2026, 9, 1, tzinfo=UTC)),
        entity_index=INDEX,
        languages=["English", "Malay"],
    )
    assert stats.fetched == 3 and stats.kept == 1 and stats.filtered == 2
    (a,) = arts
    assert a.title == "Maybank & CIMB rise - The Star"
    assert a.body == "Both banks gained after Bank Negara held the OPR."
    assert a.instruments == ["MYX:1155", "MYX:1023"]
    assert a.quality is not None and a.quality >= 0.8


def test_with_no_language_filter_the_korean_story_is_kept_and_only_junk_drops():
    feed = FixtureFeed(records=ROWS)
    _, stats = feed.normalize(feed.fetch(datetime(2026, 9, 1, tzinfo=UTC)), entity_index=INDEX)
    assert stats.kept == 2 and stats.filtered == 1
