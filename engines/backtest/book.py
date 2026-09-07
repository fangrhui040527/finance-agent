"""The plumbing the gate never had.

`engines/backtest/harness.py` calls itself "the gate. Nothing reaches a user
before it clears this" - and until now nothing ever reached it. It is a scoring
function that takes four return series, and no code in this repository built
those series except its own tests. A gate with nothing running through it is a
gate in a field.

This is the field's fence. Two ways in, because the gate answers two different
questions:

  `replay`  a candidate RULE over the cached bars. Answers "would this idea have
            worked", which is the question you have before you commit money to
            it, and the only one that can be asked today because the paper book
            has no history yet.
  `live`    the PAPER BOOK's own equity curve. Answers "is what this system
            actually did better than not bothering", which is the question the
            harness was written for and which becomes answerable as the book
            runs.

Both come out as the same `BacktestReport`, judged against the same three
benchmarks after the same costs.

Three rules this module will not bend:

  1. **A rule sees only the past.** `weights(prices, t)` is handed the index of
     the day being decided and may read prices up to and including it; the
     return it earns is day t+1's. Look-ahead is prevented by the shape of the
     call rather than by remembering not to do it.
  2. **It refuses thin history.** A deflated Sharpe on fourteen observations is
     a number with no information in it, and printing one is worse than
     printing nothing because it looks like an answer. Below `MIN_SESSIONS` the
     gate raises and says how many more sessions it needs.
  3. **It will not invent an exchange rate.** A book of Bursa and Nasdaq names
     has a return series only if you know what MYR did against USD every day of
     the window, and this system holds a few weeks of BNM rates, not fifteen
     years. So a mixed universe is split into single-currency sleeves and each
     is judged against its own market's index. A blended figure would be the
     one number in this repository that was made up.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from core.market.feed import market_proxy_for
from engines.backtest.harness import BacktestReport, Benchmark, Regime
from engines.backtest.harness import run as run_gate
from engines.backtest.trials import DEFAULT_PATH, TrialLedger
from markets.registry import market_currency, mic_of

#: A year of sessions. The floor under every verdict this module will give.
MIN_SESSIONS = 252

#: Sessions of history a rule may consume before its first decision. A rule that
#: wants more than this of a short window is measuring the window, not the rule.
MAX_LOOKBACK = 504

#: Spread paid per side, in basis points, on top of the exchange's own fee card.
#: Bursa's mid-cap names are wider than Nasdaq's mega-caps and the paper book's
#: slippage settings already say so; these are those numbers.
HALF_SPREAD_BPS = {"XKLS": Decimal("10"), "XNAS": Decimal("5"), "XNYS": Decimal("5")}

#: What one unit of turnover costs, when the fee card cannot be consulted.
FALLBACK_ROUND_TRIP_BPS = Decimal("40")


class NotEnoughHistory(RuntimeError):
    """Fewer sessions than a verdict can be built on. The refusal IS the answer."""


class MixedCurrency(RuntimeError):
    """A universe spanning two currencies, with no exchange-rate history to join them."""


# --- prices -------------------------------------------------------------------


@dataclass(frozen=True)
class Prices:
    """Closes and returns for one sleeve, on one shared calendar.

    Every name has a close on every day in `days`: the calendar is the
    INTERSECTION of the names' trading days, not their union. A union would need
    a forward-fill, and a forward-filled close is a price nobody could have
    traded at - it flatters every rule that rebalances on it.
    """

    instruments: tuple[str, ...]
    days: tuple[date, ...]
    closes: dict[str, list[float]]
    #: `returns[i]` is the return EARNED ON `days[i]`, so `returns[0]` is 0.0 by
    #: construction - there is no day before the first one to have earned it.
    returns: dict[str, list[float]]

    def __len__(self) -> int:
        return len(self.days)


def load(feed, instruments: Sequence[str], start: date | None, end: date | None) -> Prices:
    """Aligned closes for a set of names, from whatever the price cache holds."""
    per: dict[str, dict[date, float]] = {}
    for iid in instruments:
        series = feed.fetch(iid, start=start, end=end)
        per[iid] = {b.day: float(b.close) for b in series.raw() if b.close > 0}
    if not per:
        raise NotEnoughHistory("no instruments given")
    shared = set.intersection(*(set(d) for d in per.values()))
    days = tuple(sorted(shared))
    if len(days) < 2:
        raise NotEnoughHistory(
            f"{len(days)} day(s) common to all of {', '.join(instruments)}; "
            "the names do not share a calendar"
        )
    closes = {iid: [per[iid][d] for d in days] for iid in instruments}
    returns = {
        iid: [0.0] + [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))] for iid, c in closes.items()
    }
    return Prices(tuple(instruments), days, closes, returns)


def sleeves(instruments: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Names grouped by the currency they trade in. See rule 3 in the docstring."""
    out: dict[str, list[str]] = {}
    for iid in instruments:
        out.setdefault(market_currency(mic_of(iid)), []).append(iid)
    return {ccy: tuple(names) for ccy, names in sorted(out.items())}


# --- what a trade costs -------------------------------------------------------


def round_trip_bps(instrument_id: str, consideration: Decimal = Decimal(1000)) -> Decimal:
    """One full round trip as a share of consideration, from the real fee card.

    `consideration` matters: the Bursa card has minimum charges that dominate a
    small ticket, and a backtest run at a size the book will never trade would
    charge a cost the book will never pay. The paper book's opening equity is
    the honest default.
    """
    mic = mic_of(instrument_id)
    spread = HALF_SPREAD_BPS.get(mic, Decimal("10"))
    try:
        from markets.registry import get as market_get

        adapter = market_get(mic)
        fees = adapter.fee_schedule.one_side(consideration)
    except Exception:
        return FALLBACK_ROUND_TRIP_BPS
    if consideration <= 0:
        return FALLBACK_ROUND_TRIP_BPS
    one_side_bps = fees / consideration * Decimal(10_000) + spread
    return one_side_bps * 2


def sleeve_cost_bps(instruments: Sequence[str], consideration: Decimal = Decimal(1000)) -> float:
    """The average round trip across a sleeve, in basis points."""
    if not instruments:
        return float(FALLBACK_ROUND_TRIP_BPS)
    total = sum(round_trip_bps(i, consideration) for i in instruments)
    return float(total / Decimal(len(instruments)))


# --- rules --------------------------------------------------------------------


class Rule(Protocol):
    """A weighting rule. `weights` may read prices up to and including `t`."""

    name: str
    lookback: int

    def weights(self, prices: Prices, t: int) -> dict[str, float]: ...


def _normalise(raw: dict[str, float]) -> dict[str, float]:
    total = sum(abs(v) for v in raw.values())
    return {k: v / total for k, v in raw.items()} if total > 0 else {}


@dataclass(frozen=True)
class EqualWeight:
    """The null hypothesis, and the one every idea has to beat to be an idea."""

    name: str = "equal_weight"
    lookback: int = 1

    def weights(self, prices: Prices, t: int) -> dict[str, float]:
        n = len(prices.instruments)
        return {iid: 1.0 / n for iid in prices.instruments}


@dataclass(frozen=True)
class Momentum:
    """Twelve-month return skipping the last month, top half, equally weighted.

    The month is skipped because the most recent month reverses on average, and
    a momentum rule that includes it is measuring that reversal instead.
    """

    window: int = 252
    skip: int = 21
    name: str = "momentum_12_1"

    @property
    def lookback(self) -> int:
        return self.window + self.skip

    def weights(self, prices: Prices, t: int) -> dict[str, float]:
        scored: list[tuple[float, str]] = []
        for iid in prices.instruments:
            c = prices.closes[iid]
            a, b = t - self.window - self.skip, t - self.skip
            if a < 0 or c[a] <= 0:
                continue
            scored.append((c[b] / c[a] - 1.0, iid))
        if not scored:
            return {}
        scored.sort(reverse=True)
        keep = scored[: max(1, len(scored) // 2)]
        return _normalise({iid: 1.0 for _, iid in keep})


#: The smallest daily volatility this rule will believe. A name whose window
#: of returns has zero spread is not risk-free, it is untraded - a suspended
#: counter, a stub with one printed price, a series the feed padded. Dividing
#: by the true zero would weight it infinitely; the first version instead gave
#: it a weight of ZERO, which quietly excluded exactly the names an inverse-
#: volatility rule is supposed to like most. Both are wrong; a floor is right.
MIN_DAILY_VOL = 1e-6


@dataclass(frozen=True)
class InverseVolatility:
    """Weight by the reciprocal of recent volatility. Quiet names get more."""

    window: int = 63
    name: str = "inverse_volatility"

    @property
    def lookback(self) -> int:
        return self.window + 1

    def weights(self, prices: Prices, t: int) -> dict[str, float]:
        raw: dict[str, float] = {}
        for iid in prices.instruments:
            window = prices.returns[iid][max(1, t - self.window + 1) : t + 1]
            if len(window) < 2:
                continue
            mean = sum(window) / len(window)
            var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
            raw[iid] = 1.0 / max(math.sqrt(var), MIN_DAILY_VOL)
        return _normalise(raw)


RULES: dict[str, Rule] = {
    r.name: r  # type: ignore[misc]
    for r in (EqualWeight(), Momentum(), InverseVolatility())
}


# --- replay -------------------------------------------------------------------


@dataclass(frozen=True)
class Replayed:
    rule: str
    days: tuple[date, ...]
    net: list[float]
    gross: list[float]
    turnover: float
    cost_bps: float

    @property
    def sessions(self) -> int:
        return len(self.net)


def replay(rule: Rule, prices: Prices, cost_bps: float) -> Replayed:
    """Run a rule forward through the window, charging every weight it changes.

    The loop is the point-in-time guarantee: `rule.weights(prices, t)` is asked
    on day t and paid `returns[t + 1]`. There is no arrangement of this loop in
    which a rule can see the return it is about to earn.
    """
    start = min(max(rule.lookback, 1), MAX_LOOKBACK)
    if len(prices) - start < 2:
        raise NotEnoughHistory(
            f"{rule.name} needs {start} sessions of history before its first decision "
            f"and the window holds {len(prices)}"
        )
    held: dict[str, float] = {}
    gross: list[float] = []
    net: list[float] = []
    turned = 0.0
    for t in range(start, len(prices) - 1):
        want = rule.weights(prices, t)
        moved = sum(abs(want.get(k, 0.0) - held.get(k, 0.0)) for k in set(want) | set(held))
        turned += moved
        held = want
        earned = sum(w * prices.returns[iid][t + 1] for iid, w in want.items())
        gross.append(earned)
        net.append(earned - moved * cost_bps / 10_000.0)
    return Replayed(rule.name, prices.days[start + 1 :], net, gross, turned, cost_bps)


def _drifting(prices: Prices, start: int, weights: dict[str, float]) -> list[float]:
    """Buy once, hold, and let the weights drift the way a real holding does."""
    units = dict(weights)
    out: list[float] = []
    for t in range(start, len(prices) - 1):
        total = sum(units.values())
        if total <= 0:
            out.append(0.0)
            continue
        earned = sum(u / total * prices.returns[iid][t + 1] for iid, u in units.items())
        for iid in units:
            units[iid] *= 1.0 + prices.returns[iid][t + 1]
        out.append(earned)
    return out


def benchmarks(
    feed, prices: Prices, start: int, hold: dict[str, float] | None = None
) -> dict[Benchmark, list[float]]:
    """The three the gate insists on, all over exactly the strategy's own days."""
    n = len(prices.instruments)
    flat = {iid: 1.0 / n for iid in prices.instruments}
    out: dict[Benchmark, list[float]] = {
        # Rebalanced every session: the naive 1/N that is famously hard to beat.
        Benchmark.EQUAL_WEIGHT_UNIVERSE: [
            sum(prices.returns[iid][t + 1] / n for iid in prices.instruments)
            for t in range(start, len(prices) - 1)
        ],
        # Bought once and left alone - the same names with none of the effort.
        Benchmark.BUY_AND_HOLD: _drifting(prices, start, hold or flat),
    }
    proxy = market_proxy_for(prices.instruments[0])
    if proxy:
        try:
            index = load(feed, [proxy], prices.days[0], prices.days[-1])
        except Exception:
            index = None
        if index is not None:
            by_day = dict(zip(index.days, index.returns[proxy]))
            out[Benchmark.LOCAL_INDEX] = [
                by_day.get(prices.days[t + 1], 0.0) for t in range(start, len(prices) - 1)
            ]
    return out


# --- regimes ------------------------------------------------------------------


def regimes_from(index_returns: list[float], window: int = 63) -> list[Regime]:
    """Risk-on / neutral / risk-off from the index's own trailing return.

    Crude on purpose. The harness uses this only to say "the edge appears in one
    regime", and a regime label elaborate enough to be arguable would move the
    argument from the result to the labelling.
    """
    out: list[Regime] = []
    for i in range(len(index_returns)):
        past = index_returns[max(0, i - window) : i + 1]
        total = 1.0
        for r in past:
            total *= 1.0 + r
        out.append(
            Regime.RISK_ON
            if total > 1.02
            else (Regime.RISK_OFF if total < 0.98 else Regime.NEUTRAL)
        )
    return out


# --- the gate -----------------------------------------------------------------


def gate(
    feed,
    instruments: Sequence[str],
    rule: Rule | str = "equal_weight",
    start: date | None = None,
    end: date | None = None,
    consideration: Decimal = Decimal(1000),
    ledger_path: str | None = str(DEFAULT_PATH),
    min_sessions: int = MIN_SESSIONS,
) -> BacktestReport:
    """A candidate rule, judged. Raises rather than returning a thin verdict.

    `n_trials` is READ from the ledger, never passed in. The whole value of the
    deflated Sharpe is that it knows how many attempts preceded the one being
    reported, and a caller who supplies that number is grading their own
    homework - see `engines/backtest/trials.py`.
    """
    picked = RULES[rule] if isinstance(rule, str) else rule
    by_ccy = sleeves(instruments)
    if len(by_ccy) > 1:
        raise MixedCurrency(
            f"{', '.join(instruments)} spans {' and '.join(by_ccy)}; a joint return series "
            "needs a daily exchange rate over the whole window and this system holds "
            "weeks of BNM rates, not years. Run one sleeve at a time: "
            + "; ".join(f"{c} -> {', '.join(n)}" for c, n in by_ccy.items())
        )

    prices = load(feed, instruments, start, end)
    if len(prices) < min_sessions:
        raise NotEnoughHistory(
            f"{len(prices)} sessions of shared history; the gate needs {min_sessions} "
            f"({min_sessions - len(prices)} more) before a deflated Sharpe means anything"
        )

    cost_bps = sleeve_cost_bps(instruments, consideration)
    played = replay(picked, prices, cost_bps)
    if len(played.net) < min_sessions:
        raise NotEnoughHistory(
            f"{picked.name} produced {len(played.net)} scored sessions after its "
            f"{picked.lookback}-session warm-up; the gate needs {min_sessions}"
        )

    first = min(max(picked.lookback, 1), MAX_LOOKBACK)
    marks = benchmarks(feed, prices, first)
    index = marks.get(Benchmark.LOCAL_INDEX)

    universe = TrialLedger.universe_key(instruments)
    n_trials = 1
    if ledger_path:
        with TrialLedger(ledger_path) as led:
            from engines.backtest.metrics import performance

            perf = performance(played.net)
            led.record(
                picked.name,
                universe,
                prices.days[0],
                prices.days[-1],
                played.sessions,
                perf.sharpe,
                perf.cagr,
            )
            n_trials = max(1, led.distinct_rules(universe, prices.days[0], prices.days[-1]))

    report = run_gate(
        strategy_returns=played.net,
        gross_returns=played.gross,
        benchmark_returns=marks,
        regimes=regimes_from(index) if index else None,
        n_trials=n_trials,
        turnover=played.turnover,
        label_horizon=21,
    )
    report.notes.append(
        f"{picked.name} over {played.sessions} sessions "
        f"({prices.days[first]} to {prices.days[-1]}), "
        f"{cost_bps:.0f} bps charged per unit of turnover"
    )
    if start is not None and prices.days[0] > start:
        # Asking for 2016 and quietly being given 2021 is how a five-year result
        # gets described as a ten-year one. The cache holds what the collector
        # fetched, and the collector asks Yahoo for its default range.
        report.notes.append(
            f"asked for history from {start} and the price cache begins at "
            f"{prices.days[0]} - the window is {(prices.days[0] - start).days} days shorter "
            "than requested, and it is the cache that is short, not the market"
        )
    if n_trials > 1:
        report.notes.append(
            f"deflated against {n_trials} distinct rules tried on this exact window "
            "(engines/backtest/trials.py); the correction is not optional and not chosen here"
        )
    if Benchmark.LOCAL_INDEX not in marks:
        report.notes.append(
            "no local index in the price cache for this market - the gate judged two "
            "benchmarks, not three, and a pass here is weaker than a pass with all three"
        )
    return report


def live(
    store,
    feed,
    instruments: Sequence[str],
    control: str = "control",
    min_sessions: int = MIN_SESSIONS,
) -> BacktestReport:
    """The paper book's own record, put through the same gate.

    This is what the harness was written for: not "would this idea have worked"
    but "was what this system actually did worth doing". It needs the book to
    have a track record, which is why it refuses today and will not tomorrow.
    """
    decided = store.marks("decided")
    passive = store.marks(control)
    if len(decided) < min_sessions:
        raise NotEnoughHistory(
            f"the paper book has {len(decided)} marked session(s); the gate needs "
            f"{min_sessions} before a verdict is worth reading. "
            "Nothing is wrong - the book opens and then it takes a year."
        )

    def curve(marks) -> list[float]:
        eq = [float(m.equity_usd) for m in marks if m.equity_usd > 0]
        return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq))]

    strategy = curve(decided)
    prices = load(feed, instruments, decided[0].day, decided[-1].day)
    marks = benchmarks(feed, prices, 0)
    if passive:
        marks[Benchmark.BUY_AND_HOLD] = curve(passive)
    n = min(len(strategy), *(len(v) for v in marks.values()))
    report = run_gate(
        strategy_returns=strategy[-n:],
        gross_returns=strategy[-n:],
        benchmark_returns={k: v[-n:] for k, v in marks.items()},
        n_trials=1,
        label_horizon=21,
    )
    report.notes.append(
        f"the paper book's own equity over {n} sessions; costs are already inside it "
        "(fees, FX spread and slippage were charged at every mark), so gross and net "
        "are the same series here"
    )
    return report
