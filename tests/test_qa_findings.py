"""Findings from the live QA pass of 2026-08-31, each pinned by a test.

`qa/` measured the product against the real API, the real price feed and the
real news feed, on the cheapest model. Nine findings came back. The ones that
are the product's to fix are fixed, and this file is what keeps them fixed:

  1. stooq.com put a JavaScript browser check in front of its CSV. The only
     wired price source went dark, and the error named a missing column.
  2. `retry-after` on a 429 was ignored; backoff was 2 ** (attempt - 1) whatever
     the server said.
  3. A call that ended in `stop_reason=max_tokens` raised before the ledger saw
     it: tokens spent, nothing recorded. Same for a refusal.
  4. The product never sent `cache_control`, so prompt caching - measured live
     at a tenth of the input rate - was unreachable. And `cost_usd` subtracted
     cache reads from `input_tokens`, which the API already excludes: the bill
     would have gone NEGATIVE the day caching was switched on.
  5. "what price will it hit next month" routed to the teacher.
  6. `bucket_surprise(0.90, 1.0)` read MISS: -10% lands one ulp above -0.10.
  7. api.gdeltproject.org answers on port 80 and times out on 443 from one
     network; the base URL was hard-coded.

Helpers are imported from the sibling suites so the fixtures stay in one place.
"""

from __future__ import annotations

import json
from datetime import UTC, date, timedelta
from decimal import Decimal

import pytest

from core.llm.backends import AnthropicBackend, BackendError, TransientError, Truncated
from core.llm.tiers import TaskClass, Tier, Usage, cost_usd
from core.market.feed import NoData, PriceFeedError, StooqFeed, SymbolUnmappable
from core.provenance.ledger import ProvenanceLedger
from tests._anthropic_double import FakeAnthropic, http_status_error
from tests._anthropic_double import reply as _reply
from tests.test_anthropic_backend import _backend as _sdk_backend
from tests.test_price_feed import CSV
from tests.test_price_feed import _opener as _feed_opener

# ---------------------------------------------------------------- 1. prices
YAHOO = json.dumps(
    {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "symbol": "NVDA", "gmtoffset": -18000},
                    "timestamp": [
                        1767364200,
                        1767623400,
                        1767709800,
                    ],  # 09:30 New York, 2026-01-02 / 05 / 06
                    "indicators": {
                        "quote": [
                            {
                                "open": [10.0, 10.3, 10.2],
                                "high": [10.4, 10.55, 10.6],
                                "low": [9.9, 10.1, 10.15],
                                "close": [10.3, 10.2, None],
                                "volume": [1000000, 850000, 910000],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }
)
HTML_WALL = (
    "<!DOCTYPE html><html><head></head><body><noscript>this site requires javascript "
    "to verify your browser.</noscript></body></html>"
)


def test_an_html_page_from_the_source_is_named_as_such_not_as_a_missing_column():
    with pytest.raises(PriceFeedError, match="HTML page"):
        StooqFeed(opener=_feed_opener(HTML_WALL)).fetch("XNAS:NVDA")


def test_yahoo_maps_every_registered_market_and_pads_hong_kong_codes():
    from core.market.feed import YahooFeed
    from markets.registry import supported

    f = YahooFeed()
    expected = {
        "XNAS:NVDA": "NVDA",
        "MYX:1155": "1155.KL",
        "XSES:D05": "D05.SI",
        "XHKG:5": "0005.HK",
        "XLON:HSBA": "HSBA.L",
        "XTKS:7203": "7203.T",
        "XASX:BHP": "BHP.AX",
        "XNSE:INFY": "INFY.NS",
        "XTAI:2330": "2330.TW",
        "XKRX:005930": "005930.KS",
        "XETR:BMW": "BMW.DE",
    }
    for iid, sym in expected.items():
        assert f.symbol_for(iid) == sym, iid
    assert set(YahooFeed.SUFFIX) == set(supported()), "every adapter has a Yahoo mapping"
    with pytest.raises(SymbolUnmappable, match="no market prefix"):
        f.symbol_for("NVDA")


def test_yahoo_json_becomes_validated_bars_and_a_null_row_is_dropped():
    from core.market.feed import YahooFeed

    seen: list = []
    series = YahooFeed(opener=_feed_opener(YAHOO, seen)).fetch("XNAS:NVDA")
    assert "/v8/finance/chart/NVDA?" in seen[0].full_url and "interval=1d" in seen[0].full_url
    bars = series.raw()
    assert [b.day for b in bars] == [date(2026, 1, 2), date(2026, 1, 5)], (
        "a null close is not a bar"
    )
    assert bars[0].close == 10.3 and bars[1].volume == 850000


def test_yahoo_answers_an_unknown_symbol_with_nodata():
    from core.market.feed import YahooFeed

    body = json.dumps(
        {
            "chart": {
                "result": None,
                "error": {
                    "code": "Not Found",
                    "description": "No data found, symbol may be delisted",
                },
            }
        }
    )
    with pytest.raises(NoData, match="delisted"):
        YahooFeed(opener=_feed_opener(body)).fetch("XNAS:ZZZZ")


def test_yahoo_prose_or_html_raises():
    from core.market.feed import YahooFeed

    with pytest.raises(PriceFeedError, match="not JSON"):
        YahooFeed(opener=_feed_opener(HTML_WALL)).fetch("XNAS:NVDA")


def test_the_chain_falls_through_when_the_first_source_is_walled_or_cannot_map():
    from core.market.feed import ChainedFeed, YahooFeed

    chain = ChainedFeed(
        [StooqFeed(opener=_feed_opener(HTML_WALL)), YahooFeed(opener=_feed_opener(YAHOO))]
    )
    assert len(chain.fetch("XNAS:NVDA")) == 2 and chain.source_used == "yahoo"
    # XASX has no Stooq suffix: SymbolUnmappable falls through too; nothing is guessed.
    assert chain.fetch("XASX:BHP").instrument_id == "XASX:BHP" and chain.source_used == "yahoo"


def test_the_chain_stops_at_the_first_source_that_answers():
    from core.market.feed import ChainedFeed, YahooFeed

    seen: list = []
    chain = ChainedFeed(
        [StooqFeed(opener=_feed_opener(CSV)), YahooFeed(opener=_feed_opener(YAHOO, seen))]
    )
    assert len(chain.fetch("XNAS:NVDA")) == 3 and chain.source_used == "stooq"
    assert seen == [], "the second source is never asked when the first answers"


def test_the_chain_names_every_failure_when_all_sources_fail():
    from core.market.feed import ChainedFeed, YahooFeed

    chain = ChainedFeed(
        [StooqFeed(opener=_feed_opener(HTML_WALL)), YahooFeed(opener=_feed_opener(HTML_WALL))]
    )
    with pytest.raises(PriceFeedError, match="stooq") as exc:
        chain.fetch("XNAS:NVDA")
    assert "yahoo" in str(exc.value), "a chain that fails must say which sources it tried"


def test_the_chain_honours_the_point_in_time_bound():
    from core.market.feed import ChainedFeed, YahooFeed

    chain = ChainedFeed([YahooFeed(opener=_feed_opener(YAHOO))])
    assert [b.day for b in chain.fetch("XNAS:NVDA", end=date(2026, 1, 2)).raw()] == [
        date(2026, 1, 2)
    ]


def test_the_default_feed_is_stooq_then_yahoo_and_the_cli_uses_it():
    import ask
    from core.market.feed import ChainedFeed, YahooFeed, default_feed

    feed = default_feed()
    assert isinstance(feed, ChainedFeed)
    assert [type(f) for f in feed.feeds] == [StooqFeed, YahooFeed]
    assert isinstance(ask._feed(), ChainedFeed)


# ---------------------------------------------------------------- 2. retry-after
def _flaky(status: int, headers: dict, then=None):
    """A FakeAnthropic that fails with `status` (+headers) until `then` answers."""
    script = [http_status_error(status, headers=headers) for _ in range(3)]
    if then is not None:
        script = [http_status_error(status, headers=headers), then]
    fake = FakeAnthropic(script)
    return fake, fake.calls


def test_retry_after_seconds_are_honoured_on_a_429():
    waits: list[float] = []
    fake, calls = _flaky(429, {"Retry-After": "3"}, then=_reply("later"))
    text, _ = AnthropicBackend(client=fake, max_attempts=3, sleep=waits.append).complete(
        "claude-opus-5", "q", None
    )
    assert text == "later" and len(calls) == 2
    assert waits == [3.0], "the server said when; the client must not guess"


def test_retry_after_is_capped_so_a_hostile_header_cannot_park_a_call():
    waits: list[float] = []
    fake, _ = _flaky(429, {"retry-after": "600"})
    with pytest.raises(TransientError):
        AnthropicBackend(client=fake, max_attempts=2, sleep=waits.append).complete(
            "claude-opus-5", "q", None
        )
    assert waits == [60.0]


def test_a_non_numeric_retry_after_falls_back_to_exponential_backoff():
    """An HTTP-date retry-after is not seconds, so the computed curve applies.

    That curve is jittered (a 529 is server-wide, and every client returning
    together re-collides), so `jitter` is injected to keep the assertion exact.
    """
    waits: list[float] = []
    fake, _ = _flaky(529, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(TransientError):
        AnthropicBackend(
            client=fake,
            max_attempts=3,
            sleep=waits.append,
            jitter=lambda lo, hi: 1.0,
        ).complete("claude-opus-5", "q", None)
    assert waits == [1.0, 2.0]


def test_a_numeric_retry_after_is_obeyed_exactly_and_never_jittered():
    """Jittering an instruction is disobeying it by a random amount."""
    for _ in range(4):
        waits: list[float] = []
        fake, _ = _flaky(429, {"retry-after": "7"})
        with pytest.raises(TransientError):
            AnthropicBackend(client=fake, max_attempts=2, sleep=waits.append).complete(
                "claude-opus-5", "q", None
            )
        assert waits == [7.0]


# ---------------------------------------------------------------- 3. spend that raised
class _Raising:
    """A backend that answered - and billed - but could not hand back text."""

    def __init__(self, exc):
        self.exc = exc

    def complete(self, model_id, prompt, system, profile=None):
        raise self.exc


def _client(backend, ledger):
    from core.guardrails.defaults import default_engine
    from core.llm.client import InferenceClient

    return InferenceClient(backend, default_engine({"a1": {"llm_complete"}}), ledger)


def test_a_truncated_answer_carries_the_usage_it_cost():
    msg = _reply(
        "half", stop_reason="max_tokens", usage={"input_tokens": 900, "output_tokens": 4096}
    )
    with pytest.raises(Truncated) as exc:
        _sdk_backend([msg]).complete("claude-opus-5", "q", None)
    assert exc.value.usage == Usage(900, 4096)


def test_a_refusal_carries_the_usage_it_cost():
    from core.llm.backends import Declined

    msg = _reply("", stop_reason="refusal", usage={"input_tokens": 30, "output_tokens": 2})
    with pytest.raises(Declined, match="declined") as exc:
        _sdk_backend([msg]).complete("claude-opus-5", "q", None)
    assert isinstance(exc.value, BackendError) and exc.value.usage == Usage(30, 2)


def test_a_truncated_call_is_ledgered_before_it_is_raised():
    """The tokens were spent. A ledger that misses them under-reports cost by
    exactly the calls that went wrong, which is the worst possible selection."""
    led = ProvenanceLedger()
    client = _client(_Raising(Truncated("cut off", usage=Usage(900, 4096))), led)
    with pytest.raises(Truncated):
        client.complete("a1", TaskClass.RED_TEAM, "write a long memo")
    rows = list(led.calls())
    assert len(rows) == 1
    assert rows[0]["output_tokens"] == 4096 and Decimal(rows[0]["cost_usd"]) > 0


def test_a_declined_call_is_ledgered_and_returned_as_content():
    """A refusal is an ANSWER now (commitment 6): ledgered, then handed back as
    a Completion with refused=True - the same semantics as every REFUSED string
    in the MCP tools - rather than raised at the caller."""
    from core.llm.backends import Declined

    led = ProvenanceLedger()
    client = _client(_Raising(Declined("declined", usage=Usage(30, 2))), led)
    done = client.complete("a1", TaskClass.RED_TEAM, "x")
    assert done.refused is True and done.text == ""
    assert "declined" in (done.refusal_reason or "")
    assert len(list(led.calls())) == 1


def test_a_transport_failure_that_cost_nothing_is_not_ledgered():
    led = ProvenanceLedger()
    client = _client(_Raising(TransientError("529")), led)
    with pytest.raises(TransientError):
        client.complete("a1", TaskClass.RED_TEAM, "x")
    assert list(led.calls()) == []


def test_a_call_that_raised_is_still_traced_with_its_error(tmp_path):
    from core.trace import start_run

    led = ProvenanceLedger()
    client = _client(_Raising(Truncated("cut off", usage=Usage(900, 4096))), led)
    with start_run("qa", root=tmp_path) as tracer:
        with pytest.raises(Truncated):
            client.complete("a1", TaskClass.RED_TEAM, "write a long memo")
    calls = [e for e in tracer.events if e.kind == "llm_call"]
    assert len(calls) == 1 and "cut off" in calls[0].data["error"]
    assert calls[0].data["output_tokens"] == 4096


# ---------------------------------------------------------------- 4. caching and its price
def test_the_system_prompt_is_sent_as_a_cacheable_block():
    fake = FakeAnthropic([_reply()])
    _sdk_backend(client=fake).complete("claude-opus-5", "q", "you are terse")
    assert fake.calls[0]["system"] == [
        {"type": "text", "text": "you are terse", "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_writes_are_parsed_from_the_usage_block():
    msg = _reply(
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 640,
        }
    )
    _, usage = _sdk_backend([msg]).complete("claude-opus-5", "q", None)
    assert usage.cache_write_tokens == 640


def test_input_tokens_are_the_uncached_remainder_so_cache_reads_add_a_tenth():
    full = cost_usd(Tier.BALANCED, Usage(1_000_000, 0))
    assert cost_usd(
        Tier.BALANCED, Usage(1_000_000, 0, cached_input_tokens=1_000_000)
    ) == full * Decimal("1.1")
    assert cost_usd(Tier.BALANCED, Usage(0, 0, cached_input_tokens=1_000_000)) == full * Decimal(
        "0.1"
    )


def test_cache_writes_bill_at_a_quarter_premium():
    full = cost_usd(Tier.BALANCED, Usage(1_000_000, 0))
    assert cost_usd(Tier.BALANCED, Usage(0, 0, cache_write_tokens=1_000_000)) == full * Decimal(
        "1.25"
    )


def test_cost_can_never_go_negative():
    assert cost_usd(Tier.BALANCED, Usage(10, 0, cached_input_tokens=5_000)) > 0


def test_cache_write_tokens_are_recorded_in_the_ledger():
    led = ProvenanceLedger()
    led.record_call(
        "a10",
        TaskClass.THESIS_SYNTHESIS,
        Tier.REASON,
        "m",
        "p",
        Usage(100, 10, cached_input_tokens=5, cache_write_tokens=250),
    )
    row = next(led.calls())
    assert row["cached_tokens"] == 5 and row["cache_write_tokens"] == 250


# ---------------------------------------------------------------- 5. point forecasts
@pytest.mark.parametrize(
    "q",
    [
        "what price will maybank hit next month",
        "how high will nvidia go by year end",
        "where will the price be in december",
    ],
)
def test_supervisor_refuses_a_point_forecast_however_it_is_phrased(q):
    from agents.supervisor import A0Supervisor
    from tests.test_agents import ctx

    p = A0Supervisor(ctx()).plan(q, instrument_ids=("MYX:1155",))
    assert not p.allowed and "false precision" in p.refusal.reason


def test_asking_why_the_price_fell_is_still_a_why_question():
    from agents.supervisor import A0Supervisor, Intent
    from tests.test_agents import ctx

    p = A0Supervisor(ctx()).plan("why did the price fall today", instrument_ids=("MYX:1155",))
    assert p.allowed and p.intent is Intent.WHY_IT_MOVED


# ---------------------------------------------------------------- 6. bucket edges
def test_surprise_bucket_edges_are_exact_in_floating_point():
    from engines.events.taxonomy import SurpriseBucket, bucket_surprise

    assert bucket_surprise(0.90, 1.00) is SurpriseBucket.BIG_MISS
    assert bucket_surprise(0.98, 1.00) is SurpriseBucket.MISS
    assert bucket_surprise(1.02, 1.00) is SurpriseBucket.BEAT
    assert bucket_surprise(1.10, 1.00) is SurpriseBucket.BIG_BEAT


# ---------------------------------------------------------------- 7. gdelt base url
def test_the_doc_api_base_url_can_be_overridden_for_a_network_that_blocks_443(monkeypatch):
    from datetime import datetime

    from knowledge.feeds.adapter import GdeltFeed

    since = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setenv("GDELT_DOC_API", "http://api.gdeltproject.org/api/v2/doc/doc")
    assert (
        GdeltFeed(query="x")
        ._url(since, 5)
        .startswith("http://api.gdeltproject.org/api/v2/doc/doc?")
    )
    monkeypatch.delenv("GDELT_DOC_API")
    assert GdeltFeed(query="x")._url(since, 5).startswith("https://")
