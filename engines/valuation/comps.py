"""Comparables: a multiple in its three contexts, or a refusal to place it.

A multiple alone is a number. The valuation method note says it needs three
contexts before it is a finding: the name's own history, its peers, and its
growth. This module supplies the first two from the fact book and the third
from the reverse DCF:

  own history   every snapshot of the multiple the collectors have stored for
                the name (Finnhub and TWSE publish one per run, so the record
                deepens with time), and where today's value sits in it;
  peers         the median and span of the same multiple across the peer set
                on the same date, each member's figure with its source and
                date; a peer set below the minimum is refused, not averaged;
  growth        the growth the price already requires, from the reverse DCF,
                when a price and an earnings base are supplied.

Peers come from the caller (slice 3 derives them from the knowledge graph);
this module does not guess them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from engines.valuation.dcf import implied_growth

MIN_PEERS = 2


@dataclass(frozen=True)
class PeerBand:
    concept: str
    n: int
    median: Decimal
    low: Decimal
    high: Decimal
    members: dict[str, tuple[Decimal, date, str]]  # instrument -> (value, known_at, source)
    missing: tuple[str, ...] = ()

    def text(self) -> str:
        who = ", ".join(f"{k} {v[0]:.2f} ({v[2]}, {v[1]})" for k, v in sorted(self.members.items()))
        miss = f"; no figure for {', '.join(self.missing)}" if self.missing else ""
        return f"peers ({self.n}): median {self.median:.2f}, {self.low:.2f} to {self.high:.2f}: {who}{miss}"


def peer_multiples(
    book, peers: set[str], concept: str, asof: date, min_n: int = MIN_PEERS
) -> tuple[PeerBand | None, str]:
    """The peer band, or (None, why). A set of one is refused: it is a comparison
    with a single company, not a market."""
    values: dict[str, tuple[Decimal, date, str]] = {}
    missing: list[str] = []
    for p in sorted(peers):
        obs = book.latest(p, concept, asof=asof)
        if obs is not None and obs.value is not None:
            values[p] = (obs.value, obs.known_at, obs.source)
        else:
            missing.append(p)
    if len(values) < min_n:
        why = f"peer set of {len(values)} with a stored {concept}, below the minimum of {min_n}"
        if missing:
            why += f" (no {concept} stored for {', '.join(missing)})"
        return None, why
    vals = sorted(v[0] for v in values.values())
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return PeerBand(concept, len(vals), median, vals[0], vals[-1], values, tuple(missing)), ""


def own_history(book, instrument_id: str, concept: str, asof: date) -> list[tuple[date, Decimal]]:
    """Every stored snapshot of the multiple knowable on `asof`, oldest first, one per day."""
    rows = book.observations(instrument_id, concept, asof=asof, limit=5000)
    by_day: dict[date, Decimal] = {}
    for o in rows:
        if o.value is not None:
            by_day[o.known_at] = o.value
    return sorted(by_day.items())


def percentile(history: list[tuple[date, Decimal]], current: Decimal) -> Decimal | None:
    if not history:
        return None
    below = sum(1 for _, v in history if v <= current)
    return Decimal(below) / Decimal(len(history))


@dataclass(frozen=True)
class ThreeContexts:
    concept: str
    current: Decimal | None
    current_known: date | None
    history_n: int
    history_percentile: Decimal | None
    history_low: Decimal | None
    history_high: Decimal | None
    peer_band: PeerBand | None
    peer_reason: str
    implied_growth: Decimal | None
    caveats: tuple[str, ...] = field(default_factory=tuple)

    def text(self) -> str:
        rows = []
        if self.current is None:
            rows.append(f"{self.concept}: no current figure stored")
        else:
            rows.append(f"{self.concept}: {self.current:.2f} (known {self.current_known})")
        if self.history_percentile is not None:
            rows.append(
                f"  own history: {self.history_percentile:.0%} percentile of {self.history_n} snapshots "
                f"({self.history_low:.2f} to {self.history_high:.2f})"
            )
        else:
            rows.append(f"  own history: {self.history_n} snapshot(s); too few to place")
        rows.append(
            "  "
            + (self.peer_band.text() if self.peer_band else f"peers: refused - {self.peer_reason}")
        )
        if self.implied_growth is not None:
            rows.append(
                f"  growth the price requires (reverse DCF): {self.implied_growth:.1%} a year"
            )
        for c in self.caveats:
            rows.append(f"  caveat: {c}")
        return "\n".join(rows)


def three_contexts(
    book,
    instrument_id: str,
    peers: set[str],
    concept: str,
    asof: date,
    price: Decimal | None = None,
    earnings: Decimal | None = None,
    discount: Decimal | None = None,
    years: int = 10,
) -> ThreeContexts:
    hist = own_history(book, instrument_id, concept, asof)
    current = hist[-1] if hist else None
    pct = percentile(hist, current[1]) if current and len(hist) >= 3 else None
    band, reason = (
        peer_multiples(book, peers - {instrument_id}, concept, asof)
        if peers
        else (None, "no peer set supplied")
    )
    growth = None
    caveats: list[str] = []
    if price is not None and earnings is not None and discount is not None:
        if earnings > 0 and price > 0:
            growth = implied_growth(price, earnings, discount, years)
        else:
            caveats.append("reverse DCF needs positive price and earnings")
    if current and len(hist) < 3:
        caveats.append(
            "the multiple's own history is short; snapshots accumulate with each collection run"
        )
    return ThreeContexts(
        concept,
        current[1] if current else None,
        current[0] if current else None,
        len(hist),
        pct,
        hist[0][1] if hist else None,
        None if not hist else max(v for _, v in hist),
        band,
        reason,
        growth,
        tuple(caveats),
    )
