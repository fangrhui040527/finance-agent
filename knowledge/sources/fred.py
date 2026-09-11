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

from datetime import UTC, datetime, timedelta

from knowledge.facts import EventRecord, SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

URL = "https://api.stlouisfed.org/fred/series/observations"
RELEASES_URL = "https://api.stlouisfed.org/fred/releases/dates"
#: How far ahead the release calendar is read. Two weeks covers every
#: weekly page's "what to watch" without a second request.
RELEASE_HORIZON = timedelta(days=14)
#: FRED lists every release it carries - 371 dates in a fortnight on the first
#: probe, most of them daily rate tables. Only the ones a book moves on are
#: kept, matched on the release name so a renumbered id cannot silence one.
MAJOR_RELEASES: tuple[str, ...] = (
    "Employment Situation",
    "Consumer Price Index",
    "Producer Price Index",
    "Gross Domestic Product",
    "Personal Income and Outlays",
    "Advance Monthly Sales for Retail",
    "Industrial Production",
    "New Residential Construction",
    "Unemployment Insurance Weekly Claims",
    "Job Openings and Labor Turnover",
    "FOMC",
    "Federal Open Market Committee",
    "Consumer Sentiment",
    "Employment Cost Index",
    "Productivity and Costs",
    "International Trade in Goods and Services",
    "Import and Export Price",
    "Consumer Credit",
)

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
    # Commodities, added 2026-09-11 to replace DBnomics ids that read 13 months
    # older than these. The 2026-09-06 probe found our DBnomics copies frozen and
    # the conclusion drawn from it - "the publishers stopped" - was WRONG. IMF
    # PCPS is still publishing; DBnomics stopped carrying it. FRED answers
    # 2026-07 where DBnomics answers 2025-06, from the same IMF series.
    #
    # PALUMUSDM is the one that cost something: Press Metal (MYX:8869) is an
    # aluminium smelter and its input price read 2,525.96 when the market was
    # 3,158.27 - a quarter low, under every valuation of that name.
    "PALUMUSDM": "Aluminium, USD per tonne (IMF PCPS via FRED)",
    # Brent from the EIA rather than the IMF: daily instead of monthly, and two
    # days old instead of seventy. A different publisher entirely, which is why
    # it never froze when the IMF route did.
    "DCOILBRENTEU": "Brent crude, USD per barrel (EIA, daily)",
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
        if slot in ("us_preopen", "weekly", "all"):
            self._release_dates(pull, key)
        pull.requests = self.requests
        return pull

    def _release_dates(self, pull: Pull, key: str) -> None:
        """The next two weeks of US release dates, as `macro_release` events.

        FRED's release calendar is the official, keyless-in-spirit answer to
        "what prints this week": every BLS, BEA and Fed release it carries, with
        the date it is scheduled. FRED does not publish the time of day, and
        this adapter says so in the payload rather than inventing 08:30 ET.
        A calendar failure is a note; the series above are the pull.
        """
        today = self.today()
        try:
            payload = self.get_json(
                RELEASES_URL,
                {
                    "api_key": key,
                    "file_type": "json",
                    "realtime_start": today.isoformat(),
                    "realtime_end": (today + RELEASE_HORIZON).isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc",
                },
            )
        except SourceError as e:
            pull.notes.append(f"release calendar: {e}")
            return
        rows = payload.get("release_dates") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            pull.notes.append("release calendar: no release_dates list in the reply")
            return
        for r in rows:
            if not isinstance(r, dict):
                continue
            day = parse_date(r.get("date"))
            name = str(r.get("release_name") or "").strip()
            if day is None or not name or day < today:
                continue
            if not any(m.lower() in name.lower() for m in MAJOR_RELEASES):
                continue
            pull.events.append(
                EventRecord(
                    source=self.name,
                    event_id=f"fred:{r.get('release_id')}:{day}",
                    instrument_id="MACRO:US",
                    kind="macro_release",
                    announced_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                    title=name,
                    payload={
                        "country": "US",
                        "indicator": name,
                        "release_id": r.get("release_id"),
                        "time": "not published by FRED",
                    },
                )
            )
