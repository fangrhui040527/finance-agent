"""P5: the plumbing that finally feeds the gate.

`engines/backtest/harness.py` was written as "the gate. Nothing reaches a user
before it clears this" and for months nothing reached it: a scoring function
with no return series plumbed in. These cover the plumbing, and in particular
the two things a backtest gets wrong silently - seeing the future, and grading
its own homework on the multiple-testing correction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from core.market.prices import Bar, PriceSeries
from engines.backtest.book import (
    MAX_LOOKBACK,
    EqualWeight,
    InverseVolatility,
    MixedCurrency,
    Momentum,
    NotEnoughHistory,
    Prices,
    gate,
    live,
    load,
    regimes_from,
    replay,
    round_trip_bps,
    sleeve_cost_bps,
    sleeves,
)
from engines.backtest.harness import Benchmark
from engines.backtest.trials import TrialLedger

START = date(2020, 1, 1)


def walk(n: int, step: float, first: float = 100.0, skip: set[int] | None = None) -> list[Bar]:
    """A deterministic price path. `skip` drops days, so calendars can disagree."""
    out, price = [], first
    for i in range(n):
        if skip and i in skip:
            price *= 1.0 + step
            continue
        out.append(Bar(START + timedelta(days=i), price, price, price, price, 1_000_000))
        price *= 1.0 + step
    return out


@dataclass
class FakeFeed:
    """Bars from a dict, no network, no cache. The feed contract is one method."""

    bars: dict[str, list[Bar]]

    def fetch(self, instrument_id: str, start=None, end=None) -> PriceSeries:
        rows = self.bars[instrument_id]
        if start is not None:
            rows = [b for b in rows if b.day >= start]
        if end is not None:
            rows = [b for b in rows if b.day <= end]
        return PriceSeries(instrument_id, rows)


def prices_of(**paths: list[Bar]) -> Prices:
    return load(FakeFeed(paths), tuple(paths), None, None)


# --- loading ------------------------------------------------------------------


def test_the_calendar_is_the_intersection_not_the_union():
    """A union needs a forward fill, and a forward-filled close is a price
    nobody could have traded at - it flatters every rule that rebalances on it."""
    p = prices_of(**{"XNAS:AAPL": walk(40, 0.001), "XNAS:MSFT": walk(40, 0.002, skip={5, 6, 7})})
    assert len(p) == 37
    assert all(len(p.closes[i]) == 37 for i in p.instruments)


def test_the_first_return_is_zero_because_nothing_earned_it():
    p = prices_of(**{"XNAS:AAPL": walk(10, 0.01)})
    assert p.returns["XNAS:AAPL"][0] == 0.0
    assert p.returns["XNAS:AAPL"][1] == pytest.approx(0.01)


def test_names_with_no_shared_calendar_refuse():
    a = [Bar(date(2020, 1, i), 1, 1, 1, 1, 1) for i in range(1, 10)]
    b = [Bar(date(2021, 1, i), 1, 1, 1, 1, 1) for i in range(1, 10)]
    with pytest.raises(NotEnoughHistory, match="do not share a calendar"):
        load(FakeFeed({"XNAS:AAPL": a, "XNAS:MSFT": b}), ("XNAS:AAPL", "XNAS:MSFT"), None, None)


# --- the look-ahead guarantee -------------------------------------------------


def test_a_rule_is_paid_the_return_it_could_not_see():
    """The one property a backtest cannot be trusted without.

    This rule tries to cheat: it reads the LAST close it is given and bets on
    whichever name is highest. If `replay` handed it the day it is about to be
    paid for, a rule like this would post an impossible Sharpe. It is paid
    `returns[t + 1]` and is handed prices only to `t`, so the best it can do is
    what any honest rule can.
    """

    @dataclass
    class PeeksAtTheLastCloseItIsGiven:
        name: str = "peeker"
        lookback: int = 1
        seen: list[int] = None  # type: ignore[assignment]

        def weights(self, prices: Prices, t: int) -> dict[str, float]:
            # Record the furthest index this rule was ABLE to read.
            self.seen.append(len(prices.closes[prices.instruments[0]][: t + 1]) - 1)
            return {prices.instruments[0]: 1.0}

    rule = PeeksAtTheLastCloseItIsGiven(seen=[])
    p = prices_of(**{"XNAS:AAPL": walk(30, 0.01)})
    played = replay(rule, p, 0.0)
    # Every decision index is strictly below the index of the return it earned.
    assert rule.seen == list(range(1, len(p) - 1))
    assert len(played.net) == len(rule.seen)


def test_replay_charges_every_weight_it_moves():
    p = prices_of(**{"XNAS:AAPL": walk(60, 0.01), "XNAS:MSFT": walk(60, -0.002)})
    free = replay(EqualWeight(), p, 0.0)
    charged = replay(EqualWeight(), p, 100.0)
    assert free.turnover > 0, "opening a position from cash is turnover"
    assert sum(charged.net) < sum(free.net)
    assert charged.gross == free.gross, "costs change the net series, never the gross"


def test_a_rule_that_wants_more_history_than_the_window_holds_refuses():
    p = prices_of(**{"XNAS:AAPL": walk(30, 0.01)})
    with pytest.raises(NotEnoughHistory, match="before its first decision"):
        replay(Momentum(), p, 0.0)


def test_the_lookback_a_rule_may_ask_for_is_capped():
    @dataclass
    class Greedy:
        name: str = "greedy"
        lookback: int = 10_000

        def weights(self, prices: Prices, t: int) -> dict[str, float]:
            return {prices.instruments[0]: 1.0}

    p = prices_of(**{"XNAS:AAPL": walk(MAX_LOOKBACK + 40, 0.001)})
    played = replay(Greedy(), p, 0.0)
    assert played.sessions == len(p) - MAX_LOOKBACK - 1


# --- the rules ----------------------------------------------------------------


def test_equal_weight_holds_everything_equally():
    p = prices_of(**{"XNAS:AAPL": walk(30, 0.01), "XNAS:MSFT": walk(30, 0.02)})
    assert EqualWeight().weights(p, 5) == {"XNAS:AAPL": 0.5, "XNAS:MSFT": 0.5}


def test_momentum_keeps_the_winners_and_skips_the_last_month():
    rising = walk(400, 0.004)
    falling = walk(400, -0.002)
    p = prices_of(**{"XNAS:AAPL": rising, "XNAS:MSFT": falling})
    w = Momentum().weights(p, 300)
    assert set(w) == {"XNAS:AAPL"}
    assert Momentum().lookback == 273


def test_inverse_volatility_gives_the_quiet_name_more():
    calm = [
        Bar(START + timedelta(days=i), 100, 100, 100, 100 * (1.002 if i % 2 else 0.999), 1)
        for i in range(120)
    ]
    wild = [
        Bar(START + timedelta(days=i), 100, 100, 100, 100 * (1.3 if i % 2 else 0.8), 1)
        for i in range(120)
    ]
    p = prices_of(**{"XNAS:AAPL": calm, "XNAS:MSFT": wild})
    w = InverseVolatility().weights(p, 100)
    assert w["XNAS:AAPL"] > w["XNAS:MSFT"]
    assert sum(w.values()) == pytest.approx(1.0)


# --- costs and sleeves --------------------------------------------------------


def test_a_bursa_round_trip_costs_more_than_a_nasdaq_one():
    """Not a preference - the fee cards and the spreads say so, and a backtest
    that charged one number for both would flatter the Malaysian half."""
    assert round_trip_bps("MYX:1155") > round_trip_bps("XNAS:NVDA")


def test_the_sleeve_cost_is_the_average_of_its_names():
    both = sleeve_cost_bps(["XNAS:NVDA", "XNAS:AAPL"])
    one = float(round_trip_bps("XNAS:NVDA", Decimal(1000)))
    assert both == pytest.approx(one, rel=0.2)


def test_a_book_of_two_currencies_is_split_not_blended():
    assert sleeves(["MYX:1155", "XNAS:NVDA", "MYX:3182"]) == {
        "MYR": ("MYX:1155", "MYX:3182"),
        "USD": ("XNAS:NVDA",),
    }


def test_the_gate_refuses_a_mixed_universe_and_says_what_to_run_instead():
    feed = FakeFeed({"MYX:1155": walk(400, 0.001), "XNAS:NVDA": walk(400, 0.002)})
    with pytest.raises(MixedCurrency, match="MYR and USD"):
        gate(feed, ["MYX:1155", "XNAS:NVDA"], "equal_weight", ledger_path=None)


# --- the gate -----------------------------------------------------------------


def test_the_gate_refuses_a_window_too_short_to_mean_anything():
    """A deflated Sharpe on a handful of observations is worse than no number:
    it looks like an answer."""
    feed = FakeFeed({"XNAS:AAPL": walk(100, 0.001), "XNAS:MSFT": walk(100, 0.002)})
    with pytest.raises(NotEnoughHistory, match="the gate needs 252"):
        gate(feed, ["XNAS:AAPL", "XNAS:MSFT"], "equal_weight", ledger_path=None)


def test_equal_weight_cannot_beat_itself():
    """The null hypothesis is a benchmark AND a rule, and it must draw with
    itself: costs make the rule slightly worse, and 'beaten' is strict."""
    feed = FakeFeed({"XNAS:AAPL": walk(400, 0.001), "XNAS:MSFT": walk(400, 0.0015)})
    report = gate(feed, ["XNAS:AAPL", "XNAS:MSFT"], "equal_weight", ledger_path=None)
    same = [b for b in report.benchmarks if b.name is Benchmark.EQUAL_WEIGHT_UNIVERSE][0]
    assert not same.beaten
    assert not report.passes_gate


def test_the_gate_says_when_it_had_no_local_index():
    feed = FakeFeed({"XNAS:AAPL": walk(400, 0.001), "XNAS:MSFT": walk(400, 0.0015)})
    report = gate(feed, ["XNAS:AAPL", "XNAS:MSFT"], "equal_weight", ledger_path=None)
    assert any("no local index" in n for n in report.notes)
    assert Benchmark.LOCAL_INDEX not in {b.name for b in report.benchmarks}


def test_the_gate_names_a_window_shorter_than_the_one_asked_for():
    feed = FakeFeed({"XNAS:AAPL": walk(400, 0.001), "XNAS:MSFT": walk(400, 0.0015)})
    report = gate(
        feed, ["XNAS:AAPL", "XNAS:MSFT"], "equal_weight", start=date(2010, 1, 1), ledger_path=None
    )
    assert any("shorter than requested" in n for n in report.notes)


def test_every_rule_tried_raises_the_bar_for_the_next(tmp_path):
    """The multiple-testing correction is only honest if the count is a record.

    Try three rules on one window and the third is deflated against three, not
    against one - and not against a number the caller chose.
    """
    feed = FakeFeed({"XNAS:AAPL": walk(500, 0.001), "XNAS:MSFT": walk(500, 0.0015)})
    ledger = str(tmp_path / "trials.db")
    seen = []
    for rule in ("equal_weight", "inverse_volatility", "momentum_12_1"):
        try:
            seen.append(gate(feed, ["XNAS:AAPL", "XNAS:MSFT"], rule, ledger_path=ledger).n_trials)
        except NotEnoughHistory:
            pass
    assert seen == sorted(seen) and seen[-1] > seen[0]
    with TrialLedger(ledger) as led:
        assert led.count() == len(seen)


def test_a_trial_cannot_be_edited_or_dropped(tmp_path):
    with TrialLedger(str(tmp_path / "t.db")) as led:
        led.record("r", "u", START, START, 300, 1.0, 0.1)
        with pytest.raises(Exception, match="recorded once"):
            led.conn.execute("UPDATE trials SET net_sharpe = 9")
        with pytest.raises(Exception, match="never deleted"):
            led.conn.execute("DELETE FROM trials")


def test_repeating_one_rule_does_not_inflate_the_correction(tmp_path):
    """Reproducibility is not a second guess at the data."""
    with TrialLedger(str(tmp_path / "t.db")) as led:
        u = led.universe_key(["XNAS:AAPL"])
        for _ in range(5):
            led.record("equal_weight", u, START, START, 300, 1.0, 0.1)
        assert led.distinct_rules(u, START, START) == 1


# --- the live wiring ----------------------------------------------------------


def test_the_live_gate_refuses_a_book_with_no_track_record():
    @dataclass
    class EmptyStore:
        def marks(self, book, since=None):
            return []

    with pytest.raises(NotEnoughHistory, match="then it takes a year"):
        live(EmptyStore(), FakeFeed({}), ["XNAS:AAPL"])


# --- regimes ------------------------------------------------------------------


def test_regimes_follow_the_index_and_cover_every_day():
    up = regimes_from([0.01] * 100)
    down = regimes_from([-0.01] * 100)
    assert len(up) == 100 and up[-1].value == "risk_on"
    assert down[-1].value == "risk_off"
    assert regimes_from([]) == []


def test_a_name_that_never_moves_is_floored_not_excluded():
    """Zero spread over the window means untraded, not risk-free. Weighting it
    at zero quietly dropped exactly the names this rule is meant to prefer."""
    flat = [Bar(START + timedelta(days=i), 100, 100, 100, 100, 1) for i in range(120)]
    noisy = [
        Bar(START + timedelta(days=i), 100, 100, 100, 100 * (1.3 if i % 2 else 0.8), 1)
        for i in range(120)
    ]
    p = prices_of(**{"XNAS:AAPL": flat, "XNAS:MSFT": noisy})
    w = InverseVolatility().weights(p, 100)
    assert w["XNAS:AAPL"] > w["XNAS:MSFT"] > 0.0


def test_a_flat_index_is_neutral():
    assert all(r.value == "neutral" for r in regimes_from([0.0] * 80))


def test_performance_of_the_replayed_series_is_finite():
    p = prices_of(**{"XNAS:AAPL": walk(400, 0.001), "XNAS:MSFT": walk(400, -0.0005)})
    played = replay(EqualWeight(), p, 20.0)
    from engines.backtest.metrics import performance

    perf = performance(played.net)
    assert math.isfinite(perf.sharpe) and math.isfinite(perf.cagr)
