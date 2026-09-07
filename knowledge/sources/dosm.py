"""OpenDOSM: Malaysian headline CPI from the Department of Statistics.

Keyless open data (`api.data.gov.my`). Monthly, so it runs in the weekly
slot. The catalogue row shape has changed before; this adapter takes the
fields it recognises (`date`, an index and a year-on-year rate, an optional
`division` it filters to `overall`) and raises with an excerpt when none are
present, so a schema change is a red row that names itself.

ASKING FOR THE NEWEST ROWS, AND PROVING THEY ARE NEW. Until 2026-09-06 this
asked for `limit=36` and stored what came back. The catalogue answers a bare
`limit` with the FIRST rows of the series, and this series starts in 1980, so
every sweep since the source was enabled stored the whole of 1980 to 1982 and
the fact book's "latest" Malaysian CPI read 48.7 dated 1982-12-01 - a figure
sixteen thousand days old, presented as current, next to FRED prints three
days old.

Two changes, because either alone can fail quietly:

  1. The request asks three ways at once - `sort=-date`, a `date_start` window,
     and a limit wide enough to carry the entire monthly series since 1980.
     Any one of them working returns the recent end; the newest ROWS_KEPT are
     then taken in code, so the ordering the host actually applied does not
     matter. A host that rejects the extra parameters outright gets one more
     attempt with `id` and `limit` alone.
  2. The pull is REFUSED when its newest row is older than MAX_AGE_DAYS. A
     monthly series two months behind is normal; one four hundred days behind
     is the bug above, or an upstream that has stopped publishing. Either way
     it must not be stored as the current price level.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from knowledge.facts import SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

URL = "https://api.data.gov.my/data-catalogue"
DATASET = "cpi_headline"

INDEX_KEYS = ("index", "cpi", "value")
YOY_KEYS = ("inflation_yoy", "yoy", "growth_yoy")


class DosmCpiCollector(Collector):
    name = "dosm_cpi"
    key_env = None

    #: Monthly points kept, newest first - three years of history.
    ROWS_KEPT = 36
    #: How far back `date_start` asks. Wider than ROWS_KEPT months so a host
    #: that honours the window but not the sort still returns the recent end.
    WINDOW_DAYS = 1500
    #: Wide enough to carry every monthly point since 1980 in one response,
    #: for a host that honours neither the sort nor the window.
    FULL_SERIES_LIMIT = 5000
    #: The newest row must be inside this, or the pull is refused.
    MAX_AGE_DAYS = 400

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        rows = self._rows()
        pull = Pull()
        today = self.today()
        recognised = self._recognised(rows)
        if rows and not recognised:
            first = rows[0]
            raise SourceError(
                f"dosm {DATASET}: {len(rows)} rows, none with a date and an index or yoy field; "
                f"first row keys: {sorted(first) if isinstance(first, dict) else type(first)}"
            )
        # Newest first, whatever order the host answered in, then the window.
        recognised.sort(key=lambda r: r[0], reverse=True)
        kept = recognised[: self.ROWS_KEPT]
        if kept:
            newest = kept[0][0]
            age = (today - newest).days
            if age > self.MAX_AGE_DAYS:
                raise SourceError(
                    f"dosm {DATASET}: newest row is {newest.isoformat()}, {age} days old "
                    f"(limit {self.MAX_AGE_DAYS}); {len(recognised)} rows read. The catalogue "
                    "answered with the start of the series rather than its end, or DOSM has "
                    "stopped publishing; nothing stored either way."
                )
        for day, index, yoy in kept:
            known_at = max(today, day)
            if index is not None:
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        "DOSM:CPI_HEADLINE",
                        day,
                        index,
                        known_at=known_at,
                        payload={"title": "Malaysia CPI headline, index 2010=100"},
                    )
                )
            if yoy is not None:
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        "DOSM:CPI_YOY",
                        day,
                        yoy,
                        known_at=known_at,
                        payload={"title": "Malaysia CPI, % year on year"},
                    )
                )
        pull.requests = self.requests
        return pull

    # -- the request ---------------------------------------------------------------

    def _rows(self) -> list:
        """The catalogue's rows, asked for newest-first three ways at once.

        A host that rejects the sort or the window parameter is asked again
        without them; the freshness guard, not the request, is what decides
        whether the answer is usable.
        """
        start = self.today() - timedelta(days=self.WINDOW_DAYS)
        rich = {
            "id": DATASET,
            "sort": "-date",
            "date_start": start.isoformat(),
            "limit": self.FULL_SERIES_LIMIT,
        }
        try:
            return self._unwrap(self.get_json(URL, rich))
        except SourceError:
            return self._unwrap(
                self.get_json(URL, {"id": DATASET, "limit": self.FULL_SERIES_LIMIT})
            )

    @staticmethod
    def _unwrap(payload) -> list:
        rows = payload if isinstance(payload, list) else (payload or {}).get("data")
        if not isinstance(rows, list):
            raise SourceError(f"dosm {DATASET}: expected a list, got {str(payload)[:120]!r}")
        return rows

    @staticmethod
    def _recognised(rows: list) -> list[tuple[date, Decimal | None, Decimal | None]]:
        """(day, index, yoy) for every overall row carrying a date and a figure."""
        out: list[tuple[date, Decimal | None, Decimal | None]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            division = str(r.get("division") or "overall").lower()
            if division not in ("overall", "all", "headline"):
                continue
            day = parse_date(r.get("date"))
            if day is None:
                continue
            index = next((as_decimal(r[k]) for k in INDEX_KEYS if k in r), None)
            yoy = next((as_decimal(r[k]) for k in YOY_KEYS if k in r), None)
            if index is None and yoy is None:
                continue
            out.append((day, index, yoy))
        return out
