"""marketaux_news and finbert_tone: the two sources that replaced alphavantage_news.

Offline. Marketaux is answered by a fake opener in the vendor's documented
shape; FinBERT's model is never loaded - the collector is handed a scorer, and
the model wrapper's download and import paths are tested with fakes.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from knowledge.corpus import Corpus
from knowledge.news import finbert
from knowledge.news.features import Article
from knowledge.sources import catalog
from knowledge.sources.base import KeyRejected, SourceError
from knowledge.sources.finbert_tone import FinbertTone
from knowledge.sources.marketaux import MarketauxNews
from knowledge.sources.registry import collector_for
from tests.conftest import FakeResponse, http_error

NOW = datetime(2026, 11, 2, 22, 40, tzinfo=UTC)
SINCE = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CLOCK = lambda: NOW  # noqa: E731


def item(url, published, symbol="NVDA", score=0.5, other=None, title=None):
    entities = [{"symbol": symbol, "name": symbol, "match_score": 20.0, "sentiment_score": score}]
    if other:
        entities.append({"symbol": other, "sentiment_score": -0.9})
    return {
        "uuid": url,
        "title": title or f"story {url}",
        "description": f"about {url}",
        "url": f"https://news.example/{url}",
        "published_at": published,
        "source": "news.example",
        "language": "en",
        "entities": entities,
    }


def pages(answers: dict[tuple[str, int], object]):
    """An opener answering (symbol, page) with that page's data list."""
    calls: list[str] = []

    def open_(req, timeout=None):
        from urllib.parse import parse_qs, urlparse

        calls.append(req.full_url)
        q = parse_qs(urlparse(req.full_url).query)
        key = (q["symbols"][0], int(q["page"][0]))
        data = answers.get(key, [])
        if isinstance(data, BaseException):
            raise data
        assert isinstance(data, list)
        return FakeResponse(json.dumps({"meta": {"returned": len(data)}, "data": data}))

    open_.calls = calls  # type: ignore[attr-defined]
    return open_


# --- marketaux_news ----------------------------------------------------------------


def test_marketaux_aggregates_the_asked_symbols_score_per_day_and_attributes_nothing():
    opener = pages(
        {
            ("NVDA", 1): [
                item("a", "2026-11-02T15:00:00.000000Z", score=0.6, other="AAPL"),
                item("b", "2026-11-02T12:00:00.000000Z", score=0.2),
                item("c", "2026-11-02T03:00:00.000000Z", score=-0.2),
            ],
            ("NVDA", 2): [item("d", "2026-11-01T23:00:00.000000Z", score=0.9)],
        }
    )
    c = MarketauxNews(key="k", clock=CLOCK, opener=opener)
    pull = c.collect(SINCE, ("XNAS:NVDA",))
    assert c.requests == 2, "a page shorter than three articles is the last page"
    (sent,) = [o for o in pull.observations if o.concept == "mx_news_sentiment"]
    # 11-01's story is before the published_after the request asked for. A
    # vendor that sends it anyway must not open an 11-01 aggregate this pull
    # read only the last hour of.
    assert sent.period_end == date(2026, 11, 2)
    assert sent.value == Decimal("0.2000"), "the NVDA entity's score, not the AAPL one beside it"
    count = next(
        o
        for o in pull.observations
        if o.concept == "mx_news_count" and o.period_end == date(2026, 11, 2)
    )
    assert count.value == 3
    # The 03:00 story is before `since`: it counts toward the day, and is not
    # stored again (the run that read it stored it).
    assert {a.doc_id for a in pull.articles} == {
        "marketaux:https://news.example/a",
        "marketaux:https://news.example/b",
    }
    assert all(a.instruments == [] and a.fetched_for == "XNAS:NVDA" for a in pull.articles)
    assert "published_after=2026-11-02T00%3A00" in opener.calls[0]
    assert "api_token=k" in opener.calls[0] and "filter_entities=true" in opener.calls[0]


def test_marketaux_does_not_aggregate_a_day_its_page_cap_cut_short():
    full = {
        ("AAPL", p): [
            item(f"{p}{i}", f"2026-11-02T{20 - p:02d}:{i:02d}:00Z", "AAPL") for i in range(3)
        ]
        for p in range(1, MarketauxNews.MAX_PAGES + 1)
    }
    c = MarketauxNews(key="k", clock=CLOCK, opener=pages(full))
    pull = c.collect(SINCE, ("XNAS:AAPL",))
    assert c.requests == MarketauxNews.MAX_PAGES
    assert not [o for o in pull.observations if o.concept == "mx_news_sentiment"]
    assert any("not aggregated" in n for n in pull.notes)


def test_marketaux_one_failing_name_keeps_the_others_and_all_failing_raises():
    boom = http_error(429, "{}")
    ok = {("MSFT", 1): [item("m", "2026-11-02T14:00:00Z", "MSFT")], ("NVDA", 1): boom}
    c = MarketauxNews(key="k", clock=CLOCK, opener=pages(ok), sleep=lambda s: None)
    pull = c.collect(SINCE, ("XNAS:MSFT", "XNAS:NVDA"))
    assert len(pull.articles) == 1 and [n for n, _ in pull.failed] == ["XNAS:NVDA"]
    c = MarketauxNews(key="k", clock=CLOCK, opener=pages({("NVDA", 1): boom}), sleep=lambda s: None)
    with pytest.raises(SourceError):
        c.collect(SINCE, ("XNAS:NVDA",))


def test_marketaux_a_spent_allowance_refuses_the_rest_and_stops_asking():
    spent = http_error(402, '{"error":{"code":"usage_limit_reached"}}')
    opener = pages(
        {
            ("AAPL", 1): [item("a", "2026-11-02T14:00:00Z", "AAPL")],
            ("MSFT", 1): spent,
            ("NVDA", 1): [item("n", "2026-11-02T14:00:00Z")],
        }
    )
    c = MarketauxNews(key="k", clock=CLOCK, opener=opener, sleep=lambda s: None)
    pull = c.collect(SINCE, ("XNAS:AAPL", "XNAS:MSFT", "XNAS:NVDA"))
    assert len(pull.articles) == 1, "the name read before the allowance ran out is kept"
    assert [n for n, _ in pull.refused] == ["XNAS:MSFT", "XNAS:NVDA"]
    assert pull.failed == [], "a spent allowance is a refusal with its reason, not a failure"
    assert c.requests == 2, "NVDA is not asked once the day's allowance is gone"


def test_marketaux_a_refused_token_is_a_key_problem_not_a_quiet_day():
    body = {"error": {"code": "invalid_api_token", "message": "An invalid API token was supplied."}}
    c = MarketauxNews(
        key="k", clock=CLOCK, opener=lambda req, timeout=None: FakeResponse(json.dumps(body))
    )
    with pytest.raises(KeyRejected, match="MARKETAUX_API_KEY"):
        c.collect(SINCE, ("XNAS:NVDA", "XNAS:AAPL"))
    assert c.requests == 1, "a bad token is the same for every name; asked once"


def test_marketaux_needs_its_key(monkeypatch):
    from knowledge.sources.base import KeyMissing

    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    with pytest.raises(KeyMissing, match="MARKETAUX_API_KEY"):
        MarketauxNews(clock=CLOCK).collect(SINCE, ("XNAS:NVDA",))


# --- finbert_tone ------------------------------------------------------------------


def _corpus(tmp_path, rows):
    path = str(tmp_path / "corpus.db")
    with Corpus(path) as c:
        c.add_all(
            [
                Article(
                    doc_id=f"t:{i}",
                    title=title,
                    body="",
                    source_domain="x.example",
                    published_at=published,
                    language=lang,
                    instruments=names,
                )
                for i, (title, published, names, lang) in enumerate(rows)
            ],
            "google_news",
        )
    return path


def fake_scorer(calls):
    table = {"up": (0.9, 0.05, 0.05), "down": (0.1, 0.8, 0.1), "flat": (0.1, 0.1, 0.8)}

    def score(texts):
        calls.append(list(texts))
        out = []
        for t in texts:
            pos, neg, neu = table[t.split()[0]]
            out.append({"positive": pos, "negative": neg, "neutral": neu, "score": pos - neg})
        return out

    return score


def test_finbert_tone_scores_each_names_english_titles_per_day(tmp_path):
    day = datetime(2026, 11, 2, tzinfo=UTC)
    path = _corpus(
        tmp_path,
        [
            ("up Genting rebounds", day + timedelta(hours=2), ["MYX:3182"], "en"),
            ("down Genting debt", day + timedelta(hours=5), ["MYX:3182"], "English"),
            ("up Genting rebounds", day + timedelta(hours=6), ["MYX:3182"], "en"),  # same title
            ("flat Tenaga", day + timedelta(hours=3), ["MYX:5347", "MYX:3182"], "en"),
            ("down unlinked", day + timedelta(hours=3), [], "en"),
            ("up 云顶", day + timedelta(hours=3), ["MYX:3182"], "zh"),
            ("up yesterday", day - timedelta(hours=2), ["MYX:3182"], "en"),
        ],
    )
    calls: list = []
    c = FinbertTone(corpus_path=path, scorer=fake_scorer(calls), clock=CLOCK)
    pull = c.collect(day + timedelta(hours=9), ("MYX:3182", "MYX:5347"))
    sent = {o.instrument_id: o for o in pull.observations if o.concept == "finbert_sentiment"}
    assert set(sent) == {"MYX:3182", "MYX:5347"}
    g = sent["MYX:3182"]
    # up (0.85), down (-0.7), flat (0.0): the repeated title once, the Chinese
    # one and the day before not at all.
    assert g.value == Decimal("0.0500") and g.period_end == date(2026, 11, 2)
    assert g.payload["titles"] == 3
    assert (g.payload["positive"], g.payload["negative"], g.payload["neutral"]) == (1, 1, 1)
    assert g.payload["model"] == "injected scorer"
    assert sent["MYX:5347"].value == Decimal("0.0000")
    assert len(calls) == 1, "one batch for the run, not one per name"


def test_finbert_tone_scores_nothing_and_never_loads_the_model_when_no_title_is_linked(
    tmp_path, monkeypatch
):
    path = _corpus(tmp_path, [("up unrelated", NOW - timedelta(hours=1), ["XNAS:AAPL"], "en")])

    class MustNotLoad:
        def __init__(self, *a, **k):
            raise AssertionError("the model was loaded for a run with nothing to score")

    # No injected scorer: the collector's own path to the model is what is tested.
    monkeypatch.setattr(finbert, "FinBert", MustNotLoad)
    c = FinbertTone(corpus_path=path, clock=CLOCK)
    pull = c.collect(SINCE, ("MYX:3182",))
    assert pull.observations == [] and "nothing scored" in pull.notes[0]


def test_finbert_tone_scores_a_title_stored_twice_once_per_name_and_day(tmp_path):
    """The corpus refuses a repeated headline on the same day, but two sources'
    rows can still carry one title (written before that key, or a day apart in
    UTC on either side of a window). Written straight into the table here, so
    the collector's own guard is what is tested."""
    import sqlite3

    day = datetime(2026, 11, 2, 3, tzinfo=UTC)
    path = _corpus(tmp_path, [("up Genting rebounds", day, ["MYX:3182"], "en")])
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT * FROM articles WHERE doc_id = 't:0'").fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM articles LIMIT 0").description]
    copy = dict(zip(cols, row, strict=True))
    copy.update(doc_id="t:copy", source="yahoo_rss", dup_hash="other")
    conn.execute(
        f"INSERT INTO articles ({','.join(copy)}) VALUES ({','.join('?' * len(copy))})",
        tuple(copy.values()),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
    conn.close()
    calls: list = []
    c = FinbertTone(corpus_path=path, scorer=fake_scorer(calls), clock=CLOCK)
    pull = c.collect(SINCE, ("MYX:3182",))
    assert calls == [["up Genting rebounds"]]
    count = next(o for o in pull.observations if o.concept == "finbert_count")
    assert count.value == 1


def test_finbert_tone_without_its_dependencies_is_a_failure_that_names_the_fix(
    tmp_path, monkeypatch
):
    path = _corpus(tmp_path, [("up Genting", NOW - timedelta(hours=1), ["MYX:3182"], "en")])
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    c = FinbertTone(corpus_path=path, clock=CLOCK)
    with pytest.raises(SourceError, match="uv sync --group sentiment"):
        c.collect(SINCE, ("MYX:3182",))


def test_finbert_tone_with_no_corpus_fails_loudly(tmp_path):
    c = FinbertTone(corpus_path=str(tmp_path / "absent.db"), scorer=fake_scorer([]), clock=CLOCK)
    with pytest.raises(SourceError, match="no corpus"):
        c.collect(SINCE, ("MYX:3182",))


# --- the model wrapper -----------------------------------------------------------


class Stream:
    def __init__(self, body: bytes, headers=None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_model_falls_back_to_the_next_export_and_records_the_commit(tmp_path):
    asked: list[str] = []

    def opener(req, timeout=None):
        url = req.full_url
        asked.append(url)
        if url.endswith("model_quantized.onnx"):
            raise http_error(404, "", url=url)
        return Stream(b"x" * 10, {"X-Repo-Commit": "abc123def4567890"})

    root, meta = finbert.fetch_model(tmp_path, opener)
    assert meta["model_file"] == "onnx/model_int8.onnx" and meta["commit"].startswith("abc123")
    # The 404'd file's temporary copy is gone - closed before it was removed,
    # which Windows requires (WinError 32 on 2026-11-02 when it was not).
    assert (root / "onnx/model_int8.onnx").read_bytes() == b"x" * 10
    assert not list(root.rglob("*.part"))
    n = len(asked)
    finbert.fetch_model(tmp_path, opener)
    assert len(asked) == n, "a cached model is not downloaded again"


def test_a_download_cut_off_part_way_leaves_no_partial_file(tmp_path):
    class Broken(Stream):
        def read(self, n=-1):
            raise OSError("connection reset")

    with pytest.raises(finbert.FinbertUnavailable):
        finbert.fetch_model(tmp_path, lambda req, timeout=None: Broken(b""))
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and p.name != "meta.json"]


def test_fetch_model_with_no_network_is_unavailable_not_a_crash(tmp_path):
    def opener(req, timeout=None):
        raise urllib.error.URLError("no route")

    with pytest.raises(finbert.FinbertUnavailable, match="could not download"):
        finbert.fetch_model(tmp_path, opener)


def test_softmax_is_a_distribution():
    p = finbert.softmax([2.0, 1.0, -1.0])
    assert abs(sum(p) - 1) < 1e-12 and p[0] > p[1] > p[2]


# --- the switch-over ---------------------------------------------------------------


def test_alpha_vantage_is_off_and_its_replacements_are_on():
    from core.config import load
    from tests.conftest import ROOT

    enabled = load(ROOT / "config.toml").sources
    assert "alphavantage_news" not in enabled
    assert {"marketaux_news", "finbert_tone"} <= set(enabled)
    assert "alphavantage_news" in catalog.CATALOG, "still registered, for a paid key"


def test_finbert_tone_runs_after_every_news_source_in_its_slots():
    for slot in ("bursa_close", "us_close"):
        order = [s.name for s in catalog.sources_for(slot, catalog.CATALOG)]
        assert order[-1] == "finbert_tone", slot
    assert isinstance(collector_for("finbert_tone"), FinbertTone)
    assert isinstance(collector_for("marketaux_news"), MarketauxNews)
