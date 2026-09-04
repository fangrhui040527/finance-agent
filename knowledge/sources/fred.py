"""FRED: the macro series a discount rate and a regime label are built from.

The review calls FRED "the ultimate repository" and it is also the cheapest:
one free key, a quota nobody hits. Twelve series, chosen for what A6 (regime)
and the attribution engine's currency leg actually read:

  policy and curve   DFF, DGS2, DGS10, T10Y2Y
  prices and labour  CPIAUCSL, UNRATE
  risk               VIXCLS, BAMLH0A0HYM2 (high-yield spread)
  dollar and ringgit DTWEXBGS (broad dollar), DEXMAUS (MYR per USD)
  the tape           SP500, NASDAQCOM

Every point is stored with `known_at = today`, and a revised value later
lands as a new row - `FactBook.series(asof=...)` then returns the vintage that
was knowable, which is what a backtest of a CPI-driven regime needs. FRED
marks a missing value with "." and this adapter drops it rather than reading
it as zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from knowledge.facts import SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES: dict[str, str] = {
    "DFF": "Federal funds effective rate, %",
    "DGS2": "2-year Treasury yield, %",
    "DGS10": "10-year Treasury yield, %",
    "T10Y2Y": "10y minus 2y Treasury spread, %",
    "CPIAUCSL": "US CPI, all items, index 1982-84=100",
    "UNRATE": "US unemployment rate, %",
    "VIXCLS": "CBOE VIX, index",
    "BAMLH0A0HYM2": "ICE BofA US high-yield option-adjusted spread, %",
    "DTWEXBGS": "Broad US dollar index",
    "DEXMAUS": "Malaysian ringgit per US dollar",
    "SP500": "S&P 500, index level",
    "NASDAQCOM": "NASDAQ Composite, index level",
}

#: How far back each pull reaches. Wider than a day on purpose: monthly series
#: publish late and revise, and the store dedupes on (series, date, value).
LOOKBACK = timedelta(days=60)


class FredCollector(Collector):
    name = "fred"
    key_env = "FRED_API_KEY"

    def __init__(self, series: dict[str, str] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.series = dict(series or SERIES)

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self.key
        pull = Pull()
        start = min(since.date(), self.today() - LOOKBACK)
        today = self.today()
        failures: list[str] = []
        for sid, title in self.series.items():
            try:
                payload = self.get_json(
                    URL,
                    {
                        "series_id": sid,
                        "api_key": key,
                        "file_type": "json",
                        "observation_start": start.isoformat(),
                        "sort_order": "asc",
                    },
                )
            except SourceError as e:
                failures.append(f"{sid}: {e}")
                continue
            if isinstance(payload, dict) and payload.get("error_message"):
                failures.append(f"{sid}: {payload['error_message']}")
                continue
            rows = payload.get("observations") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                failures.append(f"{sid}: no observations list")
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                day = parse_date(r.get("date"))
                value = as_decimal(r.get("value"))  # "." -> None
                if day is None or value is None:
                    continue
                pull.series.append(
                    SeriesPoint(
                        self.name, sid, day, value, known_at=today, payload={"title": title}
                    )
                )
        if failures and len(failures) == len(self.series):
            raise SourceError("every series failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull
