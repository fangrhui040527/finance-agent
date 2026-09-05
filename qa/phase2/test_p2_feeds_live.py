"""Phase 2 - the keyless network seams: Stooq prices and GDELT headlines.

No model is involved and nothing here is billed, but these need the public
internet, so they live in the live phase and skip when a host is unreachable
rather than failing on a flaky connection. What they prove: the one price feed
and the one news feed that are wired actually answer, and answer with data that
survives the product's own validation.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from core.market.feed import NoData, PriceFeedError, StooqFeed


@pytest.mark.network
def test_stooq_returns_recent_validated_bars_for_a_us_and_a_bursa_name(stooq):
    feed = StooqFeed()
    for iid, min_bars in (("XNAS:SPY", 500), ("MYX:1155", 200)):
        series = feed.fetch(iid)
        bars = series.raw()
        assert len(bars) >= min_bars, f"{iid}: {len(bars)} bars"
        assert date.today() - bars[-1].day <= timedelta(days=10), f"{iid}: last bar {bars[-1].day}"
        assert all(b.high >= b.low > 0 for b in bars)
        assert series.adv(20) > 0 and series.atr(20) > 0
        bounded = feed.fetch(iid, end=bars[-5].day)
        assert bounded.raw()[-1].day <= bars[-5].day, "the end date is a point-in-time bound"


@pytest.mark.network
def test_stooq_answers_an_unknown_symbol_with_nodata_not_silence(stooq):
    with pytest.raises((NoData, PriceFeedError)):
        StooqFeed().fetch("XNAS:ZZZZQQQX")


@pytest.mark.network
def test_the_default_chain_returns_recent_bars_from_whichever_source_answers(prices_online):
    """stooq walled, yahoo up - or the reverse - the chain must not care."""
    from core.market.feed import default_feed

    for iid, min_bars in (("XNAS:SPY", 200), ("MYX:1155", 100)):
        feed = default_feed()
        series = feed.fetch(iid)
        bars = series.raw()
        assert feed.source_used in ("stooq", "yahoo")
        assert len(bars) >= min_bars, f"{iid}: {len(bars)} bars via {feed.source_used}"
        assert date.today() - bars[-1].day <= timedelta(days=10), f"{iid}: last bar {bars[-1].day}"
        assert all(b.high >= b.low > 0 for b in bars)
        assert series.adv(20) > 0 and series.atr(20) > 0


@pytest.mark.network
def test_the_cli_measures_returns_from_the_live_feed(prices_online, run_cli):
    out = run_cli(["ask.py", "prices", "XNAS:SPY", "--days", "5"])
    assert out.returncode == 0, out.stderr
    assert "20d ADV" in out.stdout and "return over the shown window" in out.stdout

    out = run_cli(["ask.py", "why", "XNAS:AAPL", "--move", "0", "--market", "0",
                   "--fetch", "--against", "XNAS:SPY", "--days", "5", "--currency", "USD"])
    assert out.returncode == 0, out.stderr
    assert "measured  XNAS:AAPL" in out.stdout
    assert "returns are stated" not in out.stdout, "a measured run must not carry the typed-input warning"

    out = run_cli(["ask.py", "why", "XNAS:AAPL", "--move", "0", "--market", "0", "--fetch"])
    assert out.returncode == 2 and "--against" in out.stderr


@pytest.mark.network
def test_trace_run_live_flag_records_the_feed(prices_online, run_cli, tmp_path):
    out = run_cli(["trace_run.py", "--live", "XNAS:SPY"])
    assert out.returncode == 0, out.stderr
    run = next((tmp_path / "debug").iterdir())
    events = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    feed = [e for e in events if e["kind"] == "feed"]
    assert feed and feed[0]["data"]["source"] in ("stooq", "yahoo")
    assert feed[0]["data"]["count"] > 0
    assert not [e for e in events if e["kind"] == "error"]


@pytest.mark.network
def test_gdelt_answers_with_a_list_and_never_with_silence_on_failure(gdelt):
    from knowledge.feeds.adapter import FeedError, GdeltFeed

    feed = GdeltFeed(query="Malaysia bank")
    since = datetime.now(timezone.utc) - timedelta(minutes=60)
    try:
        records = feed.fetch(since, limit=5)
    except FeedError as e:
        if "429" in str(e):
            # GDELT asks for one request every five seconds per client, and a
            # runner that has just finished the sources suite is over it. The
            # product raised, named the status and opened its breaker: that is
            # the promised behaviour. What cannot be tested from here is a
            # list, so this is an expected failure with the reason attached.
            pytest.xfail(f"GDELT throttled this runner: {e}")
        raise
    assert isinstance(records, list) and len(records) <= 5
    articles, stats = feed.normalize(records)
    assert stats.fetched == len(records) and stats.kept == len(articles)
    for a in articles:
        assert a.doc_id.startswith("gdelt:") and a.features is not None
    floor = f"{int(GdeltFeed.MIN_TIMESPAN.total_seconds() // 60)}min"
    assert feed._timespan(datetime.now(timezone.utc)) == floor, "the floor the adapter documents"
    with pytest.raises(FeedError):
        GdeltFeed(query="x", opener=lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")))\
            .fetch(since, 5)


@pytest.mark.network
def test_bnm_populates_the_fx_store_with_a_plausible_usd_rate():
    """P8 closed the oldest input gap: nothing populated `FxStore`. The claim
    worth a live check is the per-100 handling - BNM quotes some pairs per 100
    units, and a feed that missed that would store a USD rate of ~0.04 MYR,
    plausible-looking in every downstream sum. Skips when the API is
    unreachable from this network; a reachable-but-wrong answer fails."""
    from datetime import date as _date

    from core.market.fx import BnmFxFeed, FxFeedError
    from core.market.prices import FxStore

    store = FxStore()
    try:
        n = BnmFxFeed().populate(store)
    except FxFeedError as e:
        pytest.skip(f"BNM API unreachable from this machine: {e}")
    assert n > 0, "a reachable feed that stores nothing is a silent failure"
    hit = store.rate_asof("USD", "MYR", _date.today())
    assert hit is not None, "USD is the one pair the book cannot do without"
    rate, asof = hit
    assert 3 < rate < 6, f"MYR per USD came back {rate}: per-100 scaling looks broken"
    assert (_date.today() - asof).days <= 7, f"rate as-of {asof} is stale for a live feed"
