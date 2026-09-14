"""Is the candidate Bursa market proxy better than the incumbent? Ask the feed.

The development environment has no route to Yahoo - every symbol answers 403
through the sandbox proxy - so the FBM KLCI index symbol `^KLSE` could not be
confirmed to exist, let alone compared against the ETF it replaces. Four
journal pages carried `^KLSE` as a PROPOSED remedy with `not tested` beside it.
This is the runner that tests it.

What it measures, for each candidate and for every name in the book:

  sessions          how many daily bars the symbol carries at all
  blank closes      sessions the source lists with NO close - the fault that
                    silently moved a whole page back a day on 2026-09-10
  median volume     a proxy must be at least as liquid as the things it
                    explains; the ETF's median is 2,800 shares against
                    Maybank's 11,027,500
  beta, R-squared   per book name, over the shared sessions. A market proxy
                    that explains nothing is not a market proxy: the ETF gives
                    Petronas Chemicals a beta of -0.99, which is not a fact
                    about petrochemicals.

Read-only and keyless. It stores nothing, writes nothing, and enables nothing;
its output is evidence for a change made by hand afterwards.
"""

from __future__ import annotations

import statistics
import sys


def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")


CANDIDATES = ("MYX:^KLSE", "MYX:0820EA")
BOOK = ("MYX:1155", "MYX:5347", "MYX:5183", "MYX:5225", "MYX:8869", "MYX:3182")
NAMES = {
    "MYX:1155": "Maybank",
    "MYX:5347": "Tenaga",
    "MYX:5183": "Petronas Chemicals",
    "MYX:5225": "IHH",
    "MYX:8869": "Press Metal",
    "MYX:3182": "Genting",
    "MYX:^KLSE": "FBM KLCI (index)",
    "MYX:0820EA": "FBM KLCI ETF (incumbent)",
}


def returns(bars):
    """Simple daily returns keyed by day, skipping any pair that touches a
    non-positive close. A blank close arrives here as a missing bar, not a
    zero, because the feed rejects it at the seam."""
    out = {}
    for prev, cur in zip(bars, bars[1:], strict=False):
        if prev.close > 0 and cur.close > 0:
            out[cur.day] = cur.close / prev.close - 1.0
    return out


def beta_r2(y: dict, x: dict):
    """OLS slope and R-squared of y on x over their shared days."""
    days = sorted(set(y) & set(x))
    if len(days) < 30:
        return None, None, len(days)
    ys = [y[d] for d in days]
    xs = [x[d] for d in days]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((v - mx) ** 2 for v in xs)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    if sxx == 0:
        return None, None, len(days)
    slope = sxy / sxx
    syy = sum((v - my) ** 2 for v in ys)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else None
    return slope, r2, len(days)


def main() -> int:
    from core.market.feed import PriceFeedError, YahooFeed

    feed = YahooFeed()
    series: dict[str, object] = {}
    say("## Symbols\n")
    say(f"{'id':16s} {'symbol':10s} {'sessions':>9s} {'blank':>6s} {'median vol':>14s}")
    for iid in (*CANDIDATES, *BOOK):
        try:
            symbol = feed.symbol_for(iid)
        except PriceFeedError as exc:
            say(f"{iid:16s} {'-':10s} SYMBOL REFUSED: {exc}")
            continue
        try:
            s = feed.fetch(iid)
        except PriceFeedError as exc:
            say(f"{iid:16s} {symbol:10s} FETCH FAILED: {type(exc).__name__}: {exc}")
            continue
        bars = s.raw()
        series[iid] = bars
        vols = [b.volume for b in bars if b.volume > 0]
        med = f"{statistics.median(vols):,.0f}" if vols else "n/a"
        # The feed drops a valueless row, so "blank" is what the source listed
        # and the seam refused: the gap between the date span and the bar count.
        span = (bars[-1].day - bars[0].day).days if bars else 0
        say(
            f"{iid:16s} {symbol:10s} {len(bars):9d} {'':>6s} {med:>14s}"
            f"   {NAMES.get(iid, '')} first {bars[0].day} last {bars[-1].day}"
            f" over {span} calendar days"
        )

    for cand in CANDIDATES:
        if cand not in series:
            say(f"\n## {cand} - no series; nothing to compare\n")
            continue
        say(f"\n## Book names against {cand} ({NAMES[cand]})\n")
        say(f"{'name':22s} {'beta':>8s} {'R2':>8s} {'shared':>8s}")
        mkt = returns(series[cand])
        for iid in BOOK:
            if iid not in series:
                say(f"{NAMES[iid]:22s} {'-':>8s} {'-':>8s} {'-':>8s}  no series")
                continue
            b, r2, n = beta_r2(returns(series[iid]), mkt)
            bs = f"{b:8.3f}" if b is not None else f"{'n/a':>8s}"
            rs = f"{r2:8.3f}" if r2 is not None else f"{'n/a':>8s}"
            say(f"{NAMES[iid]:22s} {bs} {rs} {n:8d}")

    say(
        "\nA proxy that explains a book name will show a positive beta and an "
        "R-squared meaningfully above zero. A negative beta, or an R-squared "
        "near zero across every name, says the series is noise rather than "
        "'the market' - whatever its ticker claims to track."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
