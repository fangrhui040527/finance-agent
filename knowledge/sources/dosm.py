"""OpenDOSM: Malaysian headline CPI from the Department of Statistics.

Keyless open data (`api.data.gov.my`). Monthly, so it runs in the weekly
slot. The catalogue row shape has changed before; this adapter takes the
fields it recognises (`date`, an index and a year-on-year rate, an optional
`division` it filters to `overall`) and raises with an excerpt when none are
present, so a schema change is a red row that names itself.
"""

from __future__ import annotations

from datetime import datetime

from knowledge.facts import SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

URL = "https://api.data.gov.my/data-catalogue"
DATASET = "cpi_headline"

INDEX_KEYS = ("index", "cpi", "value")
YOY_KEYS = ("inflation_yoy", "yoy", "growth_yoy")


class DosmCpiCollector(Collector):
    name = "dosm_cpi"
    key_env = None
    LIMIT = 36

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        payload = self.get_json(URL, {"id": DATASET, "limit": self.LIMIT})
        rows = payload if isinstance(payload, list) else (payload or {}).get("data")
        if not isinstance(rows, list):
            raise SourceError(f"dosm {DATASET}: expected a list, got {str(payload)[:120]!r}")
        pull = Pull()
        today = self.today()
        recognised = 0
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
            recognised += 1
            if index is not None:
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        "DOSM:CPI_HEADLINE",
                        day,
                        index,
                        known_at=max(today, day),
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
                        known_at=max(today, day),
                        payload={"title": "Malaysia CPI, % year on year"},
                    )
                )
        if rows and not recognised:
            raise SourceError(
                f"dosm {DATASET}: {len(rows)} rows, none with a date and an index or yoy field; "
                f"first row keys: {sorted(rows[0]) if isinstance(rows[0], dict) else type(rows[0])}"
            )
        pull.requests = self.requests
        return pull
