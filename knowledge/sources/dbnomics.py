"""DBnomics: the free upstream of the charts MacroMicro sells.

MacroMicro's API starts at USD 5,000 a year and its free tier exposes no data.
The series behind its most-read charts are published by the IMF, the BIS,
the OECD, central banks and statistics offices, and DBnomics
(db.nomics.world) aggregates ninety-odd of those providers behind one keyless
JSON API. This collector takes a curated list of those series - the ones a
book of Malaysian banks, a utility, a petrochemical, an aluminium smelter, a
casino and three US megacaps actually moves on - and stores them beside FRED's.

  commodities   palm oil, aluminium, Brent, Asian LNG (IMF primary commodity
                prices, monthly, USD)
  policy rates  US, euro area, Japan, China, Malaysia (BIS central bank
                policy rates, monthly)
  prices        Malaysia and China CPI (IMF)
  currencies    nominal broad effective exchange rates for MYR, USD, CNY (BIS)

Every id below is a best reading of each provider's dataset codes and is
CONFIRMED BY THE FIRST RUNNER PROBE, not assumed: a series DBnomics does not
know comes back as a note naming the id, never as a silent zero, and the list
is pruned from what the probe says. `known_at` is the fetch day - DBnomics
gives periods, not vintages, so the conservative stamp is the honest one.

ALL FIFTEEN ARE FROZEN UPSTREAM, AND THEY ARE STILL FETCHED. The 2026-09-06
probe (.github/workflows/dbnomics-probe.yml) answered the question these ids
had been raising for a year: the codes are right and the DATASETS stopped -
IMF/PCPS and BIS/WS_CBPOL at 2025-06, BIS/WS_EER and IMF/IFS at 2025-05,
IMF/CPI at 2025-07 - with every sibling code stopping at the same period, so
there is nothing to re-point at. That verdict is recorded once, in
knowledge/sources/freshness.py ENDED, which is what makes `ask.py macro` print
`466d ENDED 2025-06` instead of an age, puts the reason under the table, and
keeps the monitor from opening a nightly alert whose only next step is "buy
macro data somewhere else".

The fetch stays because the verdict has to be falsifiable. One request covers
the whole list, so the cost is a request a sweep was making anyway, and if any
of these prints past its recorded last period the monitor's `series_resumed`
rule says so and asks for the ENDED entry to be deleted. Deleting the ids
instead would leave nothing to notice a restart with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from knowledge.facts import SeriesPoint, as_decimal
from knowledge.sources.base import Collector, Pull, SourceError, parse_date

BASE = "https://api.db.nomics.world/v22/series"


@dataclass(frozen=True)
class DbnSeries:
    series_id: str  # the id inside this fact book, DBN:<NAME>
    provider: str
    dataset: str
    code: str
    title: str

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.dataset}/{self.code}"


SERIES: tuple[DbnSeries, ...] = (
    DbnSeries("DBN:PALM_OIL_USD", "IMF", "PCPS", "M.W00.PPOIL.USD", "Palm oil, USD per tonne"),
    DbnSeries("DBN:ALUMINIUM_USD", "IMF", "PCPS", "M.W00.PALUM.USD", "Aluminium, USD per tonne"),
    DbnSeries("DBN:BRENT_USD", "IMF", "PCPS", "M.W00.POILBRE.USD", "Brent crude, USD per barrel"),
    DbnSeries("DBN:LNG_ASIA_USD", "IMF", "PCPS", "M.W00.PNGASJP.USD", "LNG Asia, USD per mmbtu"),
    DbnSeries("DBN:POLICY_RATE_US", "BIS", "WS_CBPOL", "M.US", "US policy rate, %"),
    DbnSeries("DBN:POLICY_RATE_EA", "BIS", "WS_CBPOL", "M.XM", "Euro area policy rate, %"),
    DbnSeries("DBN:POLICY_RATE_JP", "BIS", "WS_CBPOL", "M.JP", "Japan policy rate, %"),
    DbnSeries("DBN:POLICY_RATE_CN", "BIS", "WS_CBPOL", "M.CN", "China policy rate, %"),
    DbnSeries("DBN:POLICY_RATE_MY", "BIS", "WS_CBPOL", "M.MY", "Malaysia policy rate, %"),
    DbnSeries("DBN:CPI_MY", "IMF", "CPI", "M.MY.PCPI_IX", "Malaysia CPI, index"),
    DbnSeries("DBN:CPI_CN", "IMF", "CPI", "M.CN.PCPI_IX", "China CPI, index"),
    DbnSeries("DBN:NEER_MY", "BIS", "WS_EER", "M.N.B.MY", "MYR nominal effective exchange rate"),
    DbnSeries("DBN:NEER_US", "BIS", "WS_EER", "M.N.B.US", "USD nominal effective exchange rate"),
    DbnSeries("DBN:NEER_CN", "BIS", "WS_EER", "M.N.B.CN", "CNY nominal effective exchange rate"),
    # Risk-free rate for the cost of capital (engines/valuation/cost_of_capital.py):
    # a name earning ringgit should not be discounted off a US Treasury alone.
    # Malaysia only: Taiwan is not an IMF member, so IFS carries no Taiwanese
    # yield (the 2026-09-06 probe answered "no series" for M.TW.FIGB_PA), and no
    # other free upstream on DBnomics does either. Taiwanese names use DGS10
    # with the approximation stated on the surface.
    DbnSeries(
        "DBN:GOVT_YIELD_MY", "IMF", "IFS", "M.MY.FIGB_PA", "Malaysia government bond yield, % p.a."
    ),
)

#: How many observations to keep per series on a pull. Two years of monthly
#: points is enough for a 20-observation change and a regime read.
OBSERVATIONS = 30


class DbnomicsCollector(Collector):
    name = "dbnomics"

    def __init__(self, series: tuple[DbnSeries, ...] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.series = tuple(series or SERIES)

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        today = self.today()
        by_key = {s.key: s for s in self.series}
        # One request for the whole list; DBnomics answers a list of docs, one
        # per series it knows. Anything missing from the answer is asked for
        # once more on its own, so the note names the exact id that failed.
        docs, missing = self._fetch(list(by_key))
        for key in missing:
            one, still = self._fetch([key])
            docs.update(one)
            if still:
                pull.notes.append(f"dbnomics: no series {key} (check the code on db.nomics.world)")
        for key, doc in docs.items():
            spec = by_key.get(key)
            if spec is None:
                continue
            periods = doc.get("period") or doc.get("period_start_day") or []
            values = doc.get("value") or []
            for raw_day, raw_value in zip(periods[-OBSERVATIONS:], values[-OBSERVATIONS:]):
                day = parse_date(_period_to_day(str(raw_day)))
                value = as_decimal(raw_value)
                if day is None or value is None:
                    continue
                pull.series.append(
                    SeriesPoint(
                        self.name,
                        spec.series_id,
                        day,
                        value,
                        known_at=max(today, day),
                        payload={"title": spec.title, "dbnomics": key},
                    )
                )
        if not pull.series and self.series:
            raise SourceError(
                "dbnomics: none of the configured series answered. First: "
                + (pull.notes[0] if pull.notes else "empty reply")
            )
        pull.requests = self.requests
        return pull

    def _fetch(self, keys: list[str]) -> tuple[dict[str, dict], list[str]]:
        payload = self.get_json(
            BASE, {"series_ids": ",".join(keys), "observations": "1", "format": "json"}
        )
        if not isinstance(payload, dict):
            raise SourceError("dbnomics: expected an object")
        if payload.get("message") and not payload.get("series"):
            raise SourceError(f"dbnomics: {str(payload['message'])[:160]}")
        docs = (payload.get("series") or {}).get("docs") or []
        found: dict[str, dict] = {}
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            key = doc.get("series_code")
            if key and doc.get("provider_code") and doc.get("dataset_code"):
                key = f"{doc['provider_code']}/{doc['dataset_code']}/{doc['series_code']}"
            if isinstance(key, str):
                found[key] = doc
        missing = [k for k in keys if k not in found]
        return found, missing


def _period_to_day(period: str) -> str:
    """DBnomics periods: `2026-07`, `2026-Q2`, `2026` -> a first-of-period day."""
    p = period.strip()
    if len(p) == 4 and p.isdigit():
        return f"{p}-01-01"
    if len(p) == 7 and p[4] == "-" and p[5:].isdigit():
        return f"{p}-01"
    if "Q" in p and len(p) == 7:
        year, quarter = p[:4], int(p[6])
        return f"{year}-{(quarter - 1) * 3 + 1:02d}-01"
    return p


__all__ = ["DbnSeries", "DbnomicsCollector", "SERIES", "Decimal"]
