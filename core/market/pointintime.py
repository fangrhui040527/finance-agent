"""Point-in-time fundamentals.

docs/07 P3.5, the phase most likely to be rationalised away and the one that
gates everything downstream. docs/06 section 3.2 states it plainly: building the
alpha model before this store exists means every result until then is fiction.

Two bugs this prevents, both of which look completely innocent in a merge():

  1. Look-ahead via restatement. Every fact carries known_at - when it became
     knowable, not what period it describes. Queries filter known_at <= t.
     US 10-K deadlines run 60/75/90 days after fiscal year end by filer class,
     and late filers exceed that; one audit found 11% of ticker-months used
     numbers not yet public under a naive period_end join.

  2. Survivorship. universe_snapshot is built FORWARD and never reconstructed
     from a current listing, because that reconstruction has already deleted
     every bankruptcy.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from markets.contract import AccountingStandard


class LookaheadError(AssertionError):
    """Raised when a query would use a fact that was not yet public."""


@dataclass(frozen=True)
class Fact:
    """One reported figure, stamped with when it became knowable."""

    instrument_id: str
    concept: str
    period_end: date
    known_at: date
    value: Decimal
    currency: str
    accounting_standard: AccountingStandard
    source_doc_id: str
    is_restatement: bool = False

    def __post_init__(self) -> None:
        if self.known_at < self.period_end:
            raise ValueError(
                f"known_at {self.known_at} precedes period_end {self.period_end}: "
                "a figure cannot be public before the period it describes has ended"
            )


class FactStore:
    """Append-only. A restatement adds a row; it never updates one."""

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str], list[Fact]] = {}

    def add(self, fact: Fact) -> None:
        key = (fact.instrument_id, fact.concept)
        series = self._facts.setdefault(key, [])
        series.append(fact)
        series.sort(key=lambda f: (f.known_at, f.period_end))

    def as_known_at(
        self, instrument_id: str, concept: str, asof: date, period_end: date | None = None
    ) -> Fact | None:
        """What was knowable on `asof`. The only query a backtest may use."""
        series = self._facts.get((instrument_id, concept), [])
        candidates = [f for f in series if f.known_at <= asof]
        if period_end is not None:
            candidates = [f for f in candidates if f.period_end == period_end]
        return max(candidates, key=lambda f: (f.period_end, f.known_at), default=None)

    def latest_restated(self, instrument_id: str, concept: str, period_end: date) -> Fact | None:
        """Today's restated figure. Valid for reporting, NEVER for a backtest."""
        series = self._facts.get((instrument_id, concept), [])
        matching = [f for f in series if f.period_end == period_end]
        return max(matching, key=lambda f: f.known_at, default=None)

    def restatement_diff(self, instrument_id: str, concept: str, period_end: date):
        """First reported vs latest. A gap is a quality flag (docs/03 section 4.4)."""
        series = sorted(
            (f for f in self._facts.get((instrument_id, concept), []) if f.period_end == period_end),
            key=lambda f: f.known_at,
        )
        if len(series) < 2:
            return None
        return series[0], series[-1]

    def series_as_known_at(
        self, instrument_id: str, concept: str, asof: date
    ) -> list[Fact]:
        """One fact per period, each the best version knowable on `asof`."""
        series = [f for f in self._facts.get((instrument_id, concept), []) if f.known_at <= asof]
        by_period: dict[date, Fact] = {}
        for f in series:
            cur = by_period.get(f.period_end)
            if cur is None or f.known_at > cur.known_at:
                by_period[f.period_end] = f
        return [by_period[p] for p in sorted(by_period)]


class UniverseSnapshots:
    """Built forward, never backfilled. Contains the dead."""

    def __init__(self) -> None:
        self._snaps: dict[date, frozenset[str]] = {}
        self._days: list[date] = []

    def record(self, d: date, members: list[str]) -> None:
        if self._days and d < self._days[-1]:
            raise ValueError(
                f"snapshot for {d} is older than the last recorded {self._days[-1]}: "
                "universe snapshots are built forward, never backfilled"
            )
        if d not in self._snaps:
            bisect.insort(self._days, d)
        self._snaps[d] = frozenset(members)

    def asof(self, d: date) -> frozenset[str]:
        i = bisect.bisect_right(self._days, d) - 1
        return self._snaps[self._days[i]] if i >= 0 else frozenset()

    def survivorship_safe(self, d: date, still_listed_today: set[str]) -> bool:
        """True when the snapshot retains names that have since died."""
        return bool(self.asof(d) - still_listed_today)


def assert_no_lookahead(fact: Fact, asof: date) -> Fact:
    """Guard for any code path that touches a fact during a simulation."""
    if fact.known_at > asof:
        raise LookaheadError(
            f"{fact.concept} for {fact.instrument_id} became knowable on "
            f"{fact.known_at}, after the query date {asof}"
        )
    return fact
