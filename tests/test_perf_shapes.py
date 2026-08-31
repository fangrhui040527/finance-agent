"""P3: caching, SQL aggregates, bounded queries, shared rate window, paging."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.llm.tiers import TaskClass, Tier, Usage
from core.market.cache import PriceCache
from core.provenance.ledger import ProvenanceLedger

# --- price cache ---------------------------------------------------------------


def test_cache_hit_within_the_same_day(tmp_path):
    c = PriceCache(tmp_path / "cache.db", today=lambda: "2026-08-31")
    assert c.get("stooq", "nvda.us") is None
    c.put("stooq", "nvda.us", "csv-body")
    assert c.get("stooq", "nvda.us") == "csv-body"


def test_cache_expires_at_the_day_boundary(tmp_path):
    day = {"d": "2026-08-31"}
    c = PriceCache(tmp_path / "cache.db", today=lambda: day["d"])
    c.put("stooq", "nvda.us", "old-session")
    day["d"] = "2026-09-01"
    assert c.get("stooq", "nvda.us") is None  # a new session may have printed


def test_feed_consults_the_cache_before_the_wire(tmp_path):
    from core.market.feed import StooqFeed
    from tests.conftest import scripted_opener

    csv = "Date,Open,High,Low,Close,Volume\n2026-08-28,10,11,9,10.5,1000\n"
    calls: list = []
    cache = PriceCache(tmp_path / "cache.db", today=lambda: "2026-08-31")
    feed = StooqFeed(opener=scripted_opener([csv], capture=calls), cache=cache)
    feed.fetch("XNAS:NVDA")
    feed.fetch("XNAS:NVDA")  # would exhaust the scripted opener if it hit the wire
    assert len(calls) == 1


# --- ledger aggregates ---------------------------------------------------------


def _call(ledger: ProvenanceLedger, myr_fx: str = "4.15", **usage_kw) -> None:
    ledger.record_call(
        agent="a10_thesis",
        task_class=TaskClass.THESIS_SYNTHESIS,
        tier=Tier.REASON,
        model_id="claude-opus-5",
        prompt="p",
        usage=Usage(input_tokens=1000, output_tokens=100, **usage_kw),
        fx_rate=Decimal(myr_fx),
    )


def test_sql_aggregate_equals_the_python_sum(tmp_path):
    led = ProvenanceLedger(tmp_path / "ledger.db")
    for _ in range(5):
        _call(led)
    exact = sum((Decimal(r["cost_myr"]) for r in led.calls()), Decimal(0))
    assert abs(led.total_cost_myr() - exact) < Decimal("0.000005") * 5  # micro rounding only


def test_legacy_rows_without_the_micro_column_still_count(tmp_path):
    led = ProvenanceLedger(tmp_path / "ledger.db")
    _call(led)
    # simulate a pre-migration row: micro NULL, cost_myr TEXT present.
    # INSERT is allowed (append-only blocks UPDATE/DELETE, not INSERT).
    led.conn.execute(
        "INSERT INTO llm_calls (at, agent, task_class, tier, model_id, prompt_hash, run_id,"
        " input_tokens, output_tokens, cached_tokens, cache_write_tokens, cost_usd,"
        " cost_myr, fx_rate, fx_asof, latency_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(UTC).isoformat(),
            "a10_thesis",
            "thesis_synthesis",
            "reason",
            "claude-opus-5",
            "h",
            "",
            10,
            10,
            0,
            0,
            "0.01",
            "0.123456",
            "4.15",
            datetime.now(UTC).isoformat(),
            0.0,
        ),
    )
    led.conn.commit()
    total = led.total_cost_myr()
    assert total > Decimal("0.123456")  # the legacy row is in the sum
    since = datetime.now(UTC) - timedelta(hours=1)
    assert led.cost_since(since) == total


def test_calls_limit(tmp_path):
    led = ProvenanceLedger(tmp_path / "ledger.db")
    for _ in range(4):
        _call(led)
    assert len(list(led.calls(limit=2))) == 2
    assert len(list(led.calls())) == 4


# --- learning store bounds ------------------------------------------------------


def test_graded_supports_limit_and_since(tmp_path):
    from datetime import date

    from agents.learning.reflection import Horizon, Outcome, Prediction
    from agents.learning.store import LearningStore

    with LearningStore(tmp_path / "l.db") as store:
        for i in range(5):
            pid = f"p{i}"
            store.record(
                Prediction(
                    prediction_id=pid,
                    instrument_id="MYX:1155",
                    agent="t",
                    made_at=datetime(2026, 1, 1, tzinfo=UTC),
                    horizon=Horizon.D21,
                    statement="s",
                    direction=1,
                    confidence=0.6,
                    grade_on=date(2026, 2, 1),
                )
            )
            store.record_outcome(Outcome(pid, date(2026, 3, 1 + i), 0.01, 0.0, True, ""))
        assert len(store.graded()) == 5
        newest_two = store.graded(limit=2)
        assert [o.prediction_id for o in newest_two] == ["p3", "p4"]
        assert len(store.graded(since=date(2026, 3, 4))) == 2


# --- shared rate window ---------------------------------------------------------


def test_rate_limit_survives_a_new_policy_instance(tmp_path):
    from core.guardrails.policy import Action, Decision, Rail, RateLimitPolicy
    from core.guardrails.ratestore import SqliteRateLimitStore

    def act():
        return Action(name="get_prices", rail=Rail.TOOL, agent="a3", payload={})

    store = SqliteRateLimitStore(tmp_path / "rate.db")
    p1 = RateLimitPolicy(max_calls=3, window_seconds=3600, store=store)
    for _ in range(3):
        assert p1.evaluate(act()) is None
    p2 = RateLimitPolicy(max_calls=3, window_seconds=3600, store=store)  # a NEW process
    denied = p2.evaluate(act())
    assert denied is not None and denied.decision is Decision.DENY


# --- gdelt time-slice paging ----------------------------------------------------


def test_gdelt_pages_by_time_slice_and_dedupes(monkeypatch):
    import json

    from knowledge.feeds.adapter import GdeltFeed
    from tests.conftest import FakeResponse

    pages: list[str] = []

    def opener(req, timeout=None):
        pages.append(req.full_url)
        articles = [
            {
                "url": f"https://x/{len(pages)}-{i}",
                "title": "t",
                "domain": "x",
                "seendate": "20260830T000000Z",
                "language": "English",
                "sourcecountry": "US",
            }
            for i in range(3)
        ] + [
            {
                "url": "https://x/dup",
                "title": "t",
                "domain": "x",
                "seendate": "20260830T000000Z",
                "language": "English",
                "sourcecountry": "US",
            }
        ]
        return FakeResponse(json.dumps({"articles": articles}))

    feed = GdeltFeed(query="test", opener=opener, sleep=lambda _s: None)
    since = datetime.now(UTC) - timedelta(days=2)
    out = feed.fetch(since, limit=600)  # > MAX_RECORDS forces slicing
    assert len(pages) == 3  # ceil(600/250)
    urls = [r.payload.get("url") for r in out]
    assert urls.count("https://x/dup") == 1  # deduped across slices
    assert "startdatetime" in pages[0] and "enddatetime" in pages[0]
