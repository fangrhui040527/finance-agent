"""Bank Negara Malaysia OpenAPI: the Overnight Policy Rate.

Keyless, official, the rate a Malaysian bank's margin and a Malaysian bond's
yield are both priced off. `core/market/fx.py` already reads the daily
exchange rate from the same API and `ask.py fx` records it; this is the
policy rate beside it. The API needs its own Accept header or answers 406.

Two calls: this year and last, so the first run carries a year of history
and every later run is a no-op until the Monetary Policy Committee moves.
"""

from __future__ import annotations

from datetime import datetime

from core.market.fx import ACCEPT
from knowledge.facts import SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

BASE = "https://api.bnm.gov.my/public"
SERIES_ID = "BNM:OPR"


class BnmOprCollector(Collector):
    name = "bnm_opr"
    key_env = None

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        today = self.today()
        failures: list[str] = []
        for year in (today.year - 1, today.year):
            try:
                payload = self.get_json(f"{BASE}/opr/year/{year}", headers={"Accept": ACCEPT})
            except SourceError as e:
                failures.append(f"{year}: {e}")
                continue
            rows = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                failures.append(f"{year}: no data list in {str(payload)[:120]!r}")
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                day = parse_date(r.get("date"))
                level = as_decimal(r.get("new_opr_level"))
                if day is None or level is None:
                    continue
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        SERIES_ID,
                        day,
                        level,
                        known_at=max(today, day),
                        payload={
                            "change": str(as_decimal(r.get("change_in_opr")) or "0"),
                            "title": "BNM Overnight Policy Rate, %",
                        },
                    )
                )
        if len(failures) == 2:
            raise SourceError("BNM OPR: both years failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull
