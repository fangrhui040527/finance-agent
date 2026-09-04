"""Live GDELT ingest, tested without a network.

The property under test throughout: a BROKEN feed must never look like a QUIET
one. Every failure path raises FeedError. A feed that swallowed its own errors
would hand the system a confident "no news" on the day the news mattered most.

Two shapes mean "quiet" and return an empty list: an explicitly empty article
list, and a bare `{}`. The second was learned the hard way - it was treated as
a failure until 2026-09-04, when four Bursa companies with no news in the window
were recorded as broken feeds. That is the same error in the other direction,
and just as costly: a quiet day filed as a fault teaches the system to distrust
a source that was working.
"""

import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from knowledge.feeds.adapter import FeedError, GdeltFeed
from tests.conftest import opener_for as _opener

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _article(url: str, title: str = "Bank posts record quarter", **kw) -> dict:
    row = {
        "url": url,
        "title": title,
        "domain": kw.get("domain", "example.com"),
        "seendate": kw.get("seendate", "20260827T113000Z"),
        "language": kw.get("language", "English"),
        "sourcecountry": kw.get("sourcecountry", "Malaysia"),
    }
    row.update({k: v for k, v in kw.items() if k not in row})
    return row


# --- failure never looks like silence ------------------------------------
def test_non_json_response_raises_because_that_is_how_gdelt_reports_errors():
    """GDELT answers a bad query with plain text, not a JSON error object."""
    feed = GdeltFeed(opener=_opener("Your query was too short or too long."))
    with pytest.raises(FeedError, match="non-JSON"):
        feed.fetch(NOW - timedelta(hours=1))


def test_missing_articles_key_raises():
    feed = GdeltFeed(opener=_opener(json.dumps({"status": "ok"})))
    with pytest.raises(FeedError, match="no articles key"):
        feed.fetch(NOW - timedelta(hours=1))


def test_articles_not_a_list_raises():
    feed = GdeltFeed(opener=_opener(json.dumps({"articles": {"url": "x"}})))
    with pytest.raises(FeedError, match="not a list"):
        feed.fetch(NOW - timedelta(hours=1))


def test_http_error_raises_rather_than_returning_nothing():
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    feed = GdeltFeed(opener=boom)
    with pytest.raises(FeedError, match="fetch failed"):
        feed.fetch(NOW - timedelta(hours=1))


def test_a_genuinely_empty_window_is_not_an_error():
    """The one case that must NOT raise: the API answered, with nothing."""
    feed = GdeltFeed(opener=_opener(json.dumps({"articles": []})))
    assert feed.fetch(NOW - timedelta(hours=1)) == []


# --- request construction -------------------------------------------------
def test_timespan_never_drops_below_what_the_api_will_accept():
    """A caller polling every minute would otherwise make the API refuse it.

    This asserted 15 minutes, which was wrong: on 2026-09-03 a sweep resuming
    from a watermark 25 minutes old was answered "Timespan is too short." in
    plain text, failing the whole request. The floor is now two hours - wider
    than the real boundary on purpose, because guessing it costs a whole sweep
    and the overlap costs nothing the corpus does not already dedupe away.
    """
    feed = GdeltFeed()
    assert feed._timespan(NOW - timedelta(seconds=30), now=NOW) == "120min"
    assert feed._timespan(NOW, now=NOW) == "120min"
    assert feed._timespan(NOW - timedelta(minutes=25), now=NOW) == "120min"
    assert feed._timespan(NOW - timedelta(hours=2), now=NOW) == "120min"


def test_timespan_rounds_up_so_the_window_is_never_short():
    """Above the floor, a partial minute still widens the window rather than
    truncating it - a window short by a second is a story missed."""
    feed = GdeltFeed()
    assert feed._timespan(NOW - timedelta(minutes=150, seconds=1), now=NOW) == "151min"


def test_maxrecords_is_capped_at_one_page():
    feed = GdeltFeed()
    assert "maxrecords=250" in feed._url(NOW - timedelta(days=1), limit=10_000)
    assert "maxrecords=1" in feed._url(NOW - timedelta(days=1), limit=0)


def test_language_and_country_filters_reach_the_query():
    feed = GdeltFeed(query="bank", languages=("malay", "chinese"), countries=("MY",))
    url = feed._url(NOW - timedelta(hours=1), limit=5)
    assert "sourcelang%3Amalay" in url and "sourcelang%3Achinese" in url
    assert "sourcecountry%3AMY" in url
    assert "bank" in url


def test_a_descriptive_user_agent_is_sent():
    """Optional at GDELT, mandatory at EDGAR next. Same courtesy either way."""
    seen: list = []
    feed = GdeltFeed(opener=_opener(json.dumps({"articles": []}), capture=seen))
    feed.fetch(NOW - timedelta(hours=1))
    assert "finplanet" in seen[0].get_header("User-agent").lower()


def test_the_user_agent_can_carry_a_real_contact_address(monkeypatch):
    """A default is fine for GDELT. EDGAR wants a human to shout at."""
    monkeypatch.setenv("GDELT_USER_AGENT", "finplanet/0.1 (me@example.com)")
    seen: list = []
    feed = GdeltFeed(opener=_opener(json.dumps({"articles": []}), capture=seen))
    feed.fetch(NOW - timedelta(hours=1))
    assert seen[0].get_header("User-agent") == "finplanet/0.1 (me@example.com)"


# --- normalisation is inherited, and still works on GDELT's shapes --------
def test_a_non_english_article_keeps_its_language_and_country():
    body = json.dumps(
        {
            "articles": [
                _article(
                    "https://a.my/1",
                    "Maybank catat keuntungan rekod",
                    language="Malay",
                    sourcecountry="Malaysia",
                    domain="a.my",
                ),
            ]
        }
    )
    feed = GdeltFeed(opener=_opener(body))
    articles, stats = feed.normalize(feed.fetch(NOW - timedelta(hours=1)))
    assert len(articles) == 1
    assert articles[0].language == "Malay"
    assert articles[0].countries == ["Malaysia"]
    assert stats.kept == 1


def test_gdelt_seendate_parses_to_an_aware_timestamp():
    """GDELT stamps basic-format ISO with a Z. A naive datetime here would
    silently compare wrong against every other timestamp in the system."""
    body = json.dumps({"articles": [_article("https://a.my/1", seendate="20260827T113000Z")]})
    feed = GdeltFeed(opener=_opener(body))
    art = feed.normalize(feed.fetch(NOW - timedelta(hours=1)))[0][0]
    assert art.published_at == datetime(2026, 8, 27, 11, 30, tzinfo=UTC)


def test_the_same_story_syndicated_to_two_domains_counts_once():
    body = json.dumps(
        {
            "articles": [
                _article("https://a.com/1", "Maybank posts record quarter", domain="a.com"),
                _article("https://b.com/9", "Maybank posts record quarter", domain="b.com"),
            ]
        }
    )
    feed = GdeltFeed(opener=_opener(body))
    articles, stats = feed.normalize(feed.fetch(NOW - timedelta(hours=1)))
    assert len(articles) == 1
    assert stats.fetched == 2 and stats.duplicates == 1


def test_records_carry_the_source_url_as_their_external_id():
    """Provenance: every downstream citation traces back through this field."""
    body = json.dumps({"articles": [_article("https://a.my/story-1")]})
    feed = GdeltFeed(opener=_opener(body))
    recs = feed.fetch(NOW - timedelta(hours=1))
    assert recs[0].external_id == "https://a.my/story-1"
    assert recs[0].source == "gdelt"


def test_the_feed_sits_on_the_general_news_trust_rung():
    """Not curated_news. A wire story may never outrank a filing."""
    assert GdeltFeed.trust == "general_news"
    assert GdeltFeed.cadence == timedelta(minutes=15)


# --- quiet is not broken, and broken is not quiet --------------------------


def test_an_empty_object_is_no_news_rather_than_a_broken_feed():
    """GDELT answers a query that matched nothing with a bare `{}`.

    Recording that as a failure is the inverse of the bug this module guards
    against, and it happened: on 2026-09-04 Genting, IHH Healthcare, Petronas
    Chemicals and Press Metal were all filed as broken when each had simply had
    no English-language news in the window. Since the sweep resumes from its
    last success the window is often a couple of hours, and a mid-cap Bursa name
    going unmentioned for two hours is the ordinary case, not the alarming one.
    """
    feed = GdeltFeed(opener=_opener(json.dumps({})))
    assert feed.fetch(NOW - timedelta(hours=1)) == []


def test_an_explicitly_empty_article_list_is_still_quiet():
    feed = GdeltFeed(opener=_opener(json.dumps({"articles": []})))
    assert feed.fetch(NOW - timedelta(hours=1)) == []


def test_any_OTHER_unexpected_object_is_still_an_error():
    """The empty-dict allowance is deliberately narrow. A dict with keys but no
    articles is a shape nobody has explained, and guessing at it is how a real
    fault gets quietly counted as a quiet day."""
    for body in ({"status": "ok"}, {"error": "rate limited"}, {"articles_": []}):
        feed = GdeltFeed(opener=_opener(json.dumps(body)))
        with pytest.raises(FeedError, match="no articles key"):
            feed.fetch(NOW - timedelta(hours=1))


def test_a_rate_limit_waits_long_enough_to_be_worth_waiting():
    """429 is a quota, not a dropped packet. The `with_retry` default backs off
    0.5s then 1s, which puts all three attempts inside the same throttle window
    - three requests spent to fail exactly as the first one did."""
    from knowledge.feeds.adapter import GdeltFeed as G

    assert G.RETRY_BASE_SECONDS >= 5.0, "GDELT asks for ~1 request per 5 seconds"

    waits: list[float] = []
    calls = {"n": 0}

    def _rate_limited(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    feed = GdeltFeed(opener=_rate_limited, sleep=waits.append)
    with pytest.raises(FeedError, match="429"):
        feed.fetch(NOW - timedelta(hours=1))

    assert calls["n"] == 3, "still three attempts, just spaced usefully"
    assert waits and waits[0] >= 5.0, f"first wait was {waits[0]}s, inside the throttle window"
