"""A story is kept once, in any script, and gaming news about a book name is news.

Each test fails on the code before 2026-10-08:

  * the dedup hash tokenised ASCII letters only, so every article in Chinese,
    Greek or Korean hashed to sha1("") and the corpus's unique index rejected
    each one after the first (a Greek GDELT headline held that hash from
    2026-09-03), and stories differing only in their figures collided;
  * the hash was one min-hash over headline and excerpt, so the same wire story
    carried with a 0-, 253- and 434-character excerpt by three sources got
    three keys and was stored three times;
  * the junk filter dropped any article mentioning "casino", "lottery" or
    "betting odds" anywhere in its text - Genting (MYX:3182) is a casino
    operator - and with them "coupon" (a bond's), "giveaway" (a budget's) and
    "recipe for".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from knowledge.corpus import Corpus
from knowledge.feeds.adapter import FixtureFeed
from knowledge.news.clean import is_junk
from knowledge.news.features import Article, near_duplicate_hash

NOW = datetime(2026, 9, 22, 19, 30, tzinfo=UTC)
SINCE = NOW - timedelta(days=2)
INDEX = {"Genting": "MYX:3182", "Apple": "XNAS:AAPL", "Nvidia": "XNAS:NVDA"}


def _row(i: int, title: str, body: str = "", domain: str = "example.com", at: datetime = NOW):
    return {
        "id": str(i),
        "title": title,
        "body": body,
        "published_at": at.isoformat(),
        "domain": domain,
    }


def _normalize(rows):
    feed = FixtureFeed(records=rows)
    return feed.normalize(feed.fetch(SINCE), entity_index=INDEX)


def test_text_with_no_latin_letters_gets_its_own_hash_and_never_the_empty_one():
    empty = "da39a3ee5e6b4b0d"  # sha1("")[:16], the hash every such article shared
    texts = [
        "日本首相宣布辞职。",
        "中国央行宣布下调存款准备金率0.5个百分点。",
        "Στην Κύπρο η Ολγκίν",
        "한국은행 기준금리 동결",
    ]
    hashes = [near_duplicate_hash(t) for t in texts]
    assert empty not in hashes
    assert len(set(hashes)) == len(texts)
    assert near_duplicate_hash("") == "" and near_duplicate_hash("...") == ""


def test_stories_that_differ_only_in_their_figures_do_not_collide():
    assert near_duplicate_hash("Q2 profit rises 5% to RM2.6bn") != near_duplicate_hash(
        "Q3 profit rises 9% to RM2.9bn"
    )
    assert near_duplicate_hash("KLCI ends 3.2 points higher at 1,612") != near_duplicate_hash(
        "KLCI ends 7.9 points higher at 1,598"
    )


def test_a_wire_prefix_still_collapses():
    body = (
        "Malayan Banking Berhad reported a higher net interest margin for the quarter "
        "as deposit costs eased and loan growth held up across its core markets."
    )
    assert near_duplicate_hash(body) == near_duplicate_hash("(Reuters) - " + body)


def test_two_unrelated_chinese_flashes_are_both_kept_in_the_corpus(tmp_path):
    rows = [
        _row(1, "Στην Κύπρο η Ολγκίν"),
        _row(2, "日本首相宣布辞职。"),
        _row(3, "中国央行宣布下调存款准备金率0.5个百分点。"),
        _row(4, "美国9月非农就业人口增加25万人。"),
    ]
    arts, stats = _normalize(rows)
    assert stats.kept == 4 and stats.duplicates == 0
    with Corpus(tmp_path / "c.db") as c:
        assert c.add_all(arts, "fixture").stored == 4


HEADLINE = "Apple Takes Aim At Nvidia's AI Economics With New Chip Push"
EXCERPT = (
    "Apple is preparing a server chip programme that analysts say could reduce its "
    "reliance on Nvidia hardware for training and inference workloads, according to "
    "people familiar with the plans, who described a multi-year roadmap."
)


def test_one_headline_with_three_excerpt_lengths_is_one_story(tmp_path):
    copies = [
        (_row(1, f"{HEADLINE} - Benzinga", "", "news.google.com"), "google_news"),
        (_row(2, HEADLINE, EXCERPT[:253], "finance.yahoo.com"), "yahoo_rss"),
        (_row(3, HEADLINE, EXCERPT, "www.benzinga.com", NOW - timedelta(minutes=9)), "av"),
    ]
    with Corpus(tmp_path / "c.db") as c:
        stored = 0
        for row, source in copies:
            # Each source is its own adapter and its own process, as in a sweep.
            arts, _ = _normalize([row])
            stored += c.add_all(arts, source).stored
        assert stored == 1
        assert c.counts()["articles"] == 1


def test_one_batch_drops_the_second_copy_of_a_headline():
    arts, stats = _normalize(
        [_row(1, HEADLINE, ""), _row(2, HEADLINE, EXCERPT, "finance.yahoo.com")]
    )
    assert stats.kept == 1 and stats.duplicates == 1


def test_the_same_template_on_another_day_is_another_story(tmp_path):
    from knowledge.news.features import title_key

    title = "Bursa Malaysia ends lower as higher US yields weigh on sentiment"
    a = title_key(title, NOW)
    b = title_key(title, NOW + timedelta(days=1))
    assert a and b and a != b
    with Corpus(tmp_path / "c.db") as c:
        for i, (at, level) in enumerate(((NOW, "1,611.78"), (NOW + timedelta(days=1), "1,598.40"))):
            body = f"The FBM KLCI closed at {level} as foreign funds sold the banks."
            arts, _ = _normalize([_row(i, title, body, at=at)])
            assert c.add_all(arts, "fixture").stored == 1


def test_a_headline_too_short_to_identify_a_story_gets_no_key(tmp_path):
    from knowledge.news.features import title_key

    assert title_key("Market update", NOW) == ""
    with Corpus(tmp_path / "c.db") as c:
        for i in (1, 2):
            c.add(
                Article(
                    doc_id=f"raw:{i}",
                    title="Market update",
                    body=f"Different body number {i} about a different session entirely.",
                    source_domain="example.com",
                    published_at=NOW,
                    dup_hash=f"h{i}",
                ),
                "fixture",
            )
        assert c.counts()["articles"] == 2


def test_a_corpus_from_before_the_key_gains_the_column_and_keeps_its_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with Corpus(path) as c:
        arts, _ = _normalize([_row(1, HEADLINE, EXCERPT)])
        c.add_all(arts, "fixture")
    con = sqlite3.connect(path)
    con.execute("DROP INDEX articles_title_key")
    con.execute("DROP TRIGGER articles_no_update")
    con.execute("ALTER TABLE articles DROP COLUMN title_key")
    con.commit()
    con.close()
    with Corpus(path) as c:
        cols = {r[1] for r in c.conn.execute("PRAGMA table_info(articles)")}
        assert "title_key" in cols and c.counts()["articles"] == 1


def test_gaming_news_about_a_book_name_is_kept():
    for title in (
        "Genting Malaysia wins New York casino licence, shares jump 8%",
        "Genting Singapore Q3 casino revenue rises",
        "Genting Malaysia shares fall as Budget 2027 raises casino duty",
        "Maybank Q3 net profit rises; lottery operator Sports Toto falls",
        "Apple shares slip as Wall Street weighs betting odds of a Fed cut",
        "MGS 10-year auction sets the coupon at 3.52%",
        "Budget 2027 giveaways lift consumer names",
        "Nvidia's recipe for AI dominance faces a legal test",
    ):
        assert not is_junk(title), title
    arts, stats = _normalize([_row(1, "Genting Malaysia wins New York casino licence")])
    assert stats.kept == 1 and arts[0].instruments == ["MYX:3182"]


def test_what_is_never_news_is_still_dropped():
    for title in (
        "Maybank branch hosts lottery draw: 4D results",
        "Your daily horoscope for Thursday",
        "Use promo code SAVE20 at checkout",
        "Use this coupon code for 10% off",
        "Easy nasi lemak recipe",
        "Sponsored content: the best credit cards",
    ):
        assert is_junk(title), title
