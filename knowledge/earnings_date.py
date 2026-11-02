"""Which earnings date to believe, when the sources holding one disagree.

Every source's claim stays its own row in the fact book - the calendars'
`earnings_result` events and EDGAR's `results_released` - and nothing here
writes. This only reads them and says which one a page should print.

Why it exists: by 2026-10-28 Finnhub had listed Apple's results for 10-28,
10-29 and then 11-02, and the pack printed each calendar row as though it were
the date. A page that names one vendor's date as fact when the vendors do not
agree is the same error as an estimated price.

Authority, highest first:

  1. EDGAR: an 8-K with Item 2.02 (Results of Operations), dated by the SEC's
     acceptance time. A record that the results WERE released - only ever a
     past date, and decisive: a calendar's forecast does not dispute it.
  2. The company's own announced date (`COMPANY_SOURCES`). No collector reads
     an investor-relations calendar yet; a source added there outranks the
     vendors without any other change.
  3. Finnhub's earnings calendar.
  4. FMP's earnings calendar.
  5. Anything else (news) - after every named source.

When the top two claims disagree the date is "unconfirmed (a / b)" with every
claim and its source, wherever it is printed. Two claims agree when they name
the same day, or adjacent days where the earlier is listed after the close (a
vendor dating the report by the session it moves); the line then gives each
source's own date. A one-day gap with nothing to explain it is a disagreement:
it decides which session the report lands in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from knowledge.sources.edgar import RELEASED, SEC_TZ

#: The calendars' kind; EDGAR's record of a release is `RELEASED`.
SCHEDULED = "earnings_result"

#: Sources that carry the company's own announced date. Empty: nothing in this
#: repository collects one yet. The hook for when something does.
COMPANY_SOURCES: frozenset[str] = frozenset()

#: The vendor calendars, in the order they are believed.
VENDORS = ("finnhub", "fmp")

#: Claims this close together are about one release. Quarterly reports are
#: ~90 days apart; the widest vendor disagreement seen is under a week.
SPREAD = timedelta(days=21)


@dataclass(frozen=True)
class Claim:
    source: str
    day: date
    #: Finnhub's timing code (amc, bmo, dmh), or "".
    hour: str = ""
    #: When the claim was made: an EDGAR acceptance instant, or the last time a
    #: calendar listed the date.
    known_at: datetime | None = None
    released: bool = False

    @property
    def rank(self) -> int:
        if self.released:
            return 0
        if self.source in COMPANY_SOURCES:
            return 1
        if self.source in VENDORS:
            return 2 + VENDORS.index(self.source)
        return 2 + len(VENDORS)

    def __str__(self) -> str:
        return f"{self.day} {self.source}"


@dataclass(frozen=True)
class EarningsDate:
    """One release and every source's claim about it, in authority order."""

    claims: tuple[Claim, ...]

    @property
    def released(self) -> bool:
        return bool(self.claims) and self.claims[0].released

    @property
    def confirmed(self) -> bool:
        if len(self.claims) < 2 or self.released:
            return True
        return _agree(self.claims[0], self.claims[1])

    @property
    def day(self) -> date | None:
        """The resolved date, or None when it is unconfirmed."""
        return self.claims[0].day if self.claims and self.confirmed else None

    def label(self) -> str:
        top = self.claims[0]
        if top.released:
            at = top.known_at.astimezone(SEC_TZ) if top.known_at else None
            return f"{top.day} (edgar 8-K item 2.02" + (f", accepted {at:%H:%M} ET)" if at else ")")
        if not self.confirmed:
            return "unconfirmed (" + " / ".join(str(c) for c in self.claims) + ")"
        if len(self.claims) < 2:
            return f"{top.day} ({top.source})"
        second = self.claims[1]
        if second.day == top.day:
            return f"{top.day} ({top.source}, {second.source})"
        # Agreed through the after-close rule: each source's own date, and why.
        theirs = f"{second.day}" + (f" {second.hour}" if second.hour else "")
        if second.day > top.day:
            theirs += ", next session"
        hour = f" {top.hour}" if top.hour else ""
        return f"{top.day}{hour} ({top.source}; {second.source} {theirs})"


def _agree(a: Claim, b: Claim) -> bool:
    if a.day == b.day:
        return True
    early, late = sorted((a, b), key=lambda c: c.day)
    return (late.day - early.day).days == 1 and early.hour == "amc"


def _claim(e, seen: datetime) -> Claim:
    if e.kind == RELEASED:
        return Claim(
            e.source, e.announced_at.astimezone(SEC_TZ).date(), "", e.announced_at, released=True
        )
    when = e.effective_at or e.announced_at
    return Claim(e.source, when.date(), str((e.payload or {}).get("hour") or ""), seen)


def earnings_dates(
    book, instrument_id: str, since: datetime, until: datetime
) -> list[EarningsDate]:
    """Each release with a claim dated in [since, until], oldest first."""
    rows = [
        *book.events(instrument_id, kind=SCHEDULED, since=since - SPREAD, until=until + SPREAD),
        *book.events(instrument_id, kind=RELEASED, since=since - SPREAD, until=until + SPREAD),
    ]
    if not rows:
        return []
    last_seen = getattr(book, "last_seen", None)
    sightings = last_seen([(e.source, e.event_id) for e in rows]) if last_seen else {}
    claims = []
    for e in rows:
        seen = max(e.announced_at, sightings.get((e.source, e.event_id), e.announced_at))
        claims.append(_claim(e, seen))
    claims.sort(key=lambda c: c.day)
    # A calendar's listing that the same calendar has since moved is not a claim
    # any more. Drop it before clustering: a release splits the neighbourhood,
    # and a moved-from date past it would otherwise stand as a release to come.
    claims = [
        c
        for c in claims
        if c.released
        or not any(
            o is not c
            and not o.released
            and o.source == c.source
            and abs(o.day - c.day) <= SPREAD
            and o.known_at
            and c.known_at
            and o.known_at > c.known_at
            for o in claims
        )
    ]

    # A release absorbs the calendar dates on or before it (plus the one-day
    # after-close slack), never the ones after: an Item 2.02 8-K weeks early is
    # a pre-announcement or a warning, and the scheduled report still comes.
    clusters: list[list[Claim]] = []
    for c in claims:
        if (
            clusters
            and (c.day - clusters[-1][0].day) <= SPREAD
            and not any(r.released and (c.day - r.day).days > 1 for r in clusters[-1])
        ):
            clusters[-1].append(c)
        else:
            clusters.append([c])

    out = []
    for cluster in clusters:
        # One claim per source: a calendar's is the date it listed last (it
        # moves a tentative date and the store keeps every one it was shown);
        # EDGAR's is the first acceptance, the release itself.
        best: dict[str, Claim] = {}
        for c in cluster:
            prior = best.get(c.source)
            if prior is None:
                best[c.source] = c
            elif c.released:
                if c.known_at and prior.known_at and c.known_at < prior.known_at:
                    best[c.source] = c
            elif c.known_at and prior.known_at and c.known_at > prior.known_at:
                best[c.source] = c
        kept = tuple(sorted(best.values(), key=lambda c: (c.rank, c.source)))
        if any(since.date() <= c.day <= until.date() for c in kept):
            out.append(EarningsDate(kept))
    return out
