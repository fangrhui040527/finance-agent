"""SEC EDGAR submissions: every filing a US name made, with the day it was made.

Keyless and authoritative - tier 2 on the trust ladder, the highest anything
the collector reads reaches. One request per company returns the recent
filings list (`data.sec.gov/submissions/CIK##########.json`); the filing's
own document is linked, not fetched, so nothing here is scraped.

EDGAR's one rule: a descriptive User-Agent with a contact address, or 403.
`SEC_USER_AGENT` (falling back to `GDELT_USER_AGENT`) supplies it; without
either the request is still made with the project default and the 403, if it
comes, says exactly why.

Forms kept: 8-K (material events), 10-Q/10-K (results), 4 (insider
transactions, as `insider_filing`), SC 13D/13G (large holders), DEF 14A
(proxy). Everything else is noise for this book.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from knowledge.facts import EventRecord
from knowledge.sources.base import USER_AGENT, Collector, Pull, SourceError, parse_date

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"

#: Instrument -> 10-digit CIK. A name absent here is skipped with a note, never
#: guessed: a wrong CIK is another company's filings, silently.
CIK: dict[str, str] = {
    "XNAS:AAPL": "0000320193",
    "XNAS:MSFT": "0000789019",
    "XNAS:NVDA": "0001045810",
    "XNAS:AMD": "0000002488",
    "XNAS:INTC": "0000050863",
    "XNAS:AVGO": "0001730168",
    "XNAS:AMZN": "0001018724",
}

FORMS = {
    "8-K": "filing",
    "8-K/A": "filing",
    "10-Q": "filing",
    "10-K": "filing",
    "10-Q/A": "filing",
    "10-K/A": "filing",
    "4": "insider_filing",
    "4/A": "insider_filing",
    "SC 13D": "filing",
    "SC 13G": "filing",
    "SC 13D/A": "filing",
    "SC 13G/A": "filing",
    "DEF 14A": "filing",
}


def sec_headers() -> dict[str, str]:
    """The headers every SEC request carries: a descriptive User-Agent with a
    contact address (SEC_USER_AGENT, else GDELT_USER_AGENT, else the project
    default), which data.sec.gov requires or answers 403."""
    ua = (
        os.environ.get("SEC_USER_AGENT", "").strip()
        or os.environ.get("GDELT_USER_AGENT", "").strip()
    )
    return {"User-Agent": ua or USER_AGENT, "Accept": "application/json"}


class EdgarFilings(Collector):
    name = "edgar"
    key_env = None

    def _headers(self) -> dict[str, str]:
        return sec_headers()

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        failures: list[str] = []
        asked = 0
        for iid in instruments:
            cik = CIK.get(iid)
            if cik is None:
                pull.notes.append(f"{iid}: no CIK on file (knowledge/sources/edgar.py); skipped")
                continue
            asked += 1
            try:
                payload = self.get_json(SUBMISSIONS.format(cik=cik), headers=self._headers())
            except SourceError as e:
                failures.append(f"{iid}: {e}")
                continue
            recent = (
                ((payload or {}).get("filings") or {}).get("recent")
                if isinstance(payload, dict)
                else None
            )
            if not isinstance(recent, dict):
                failures.append(f"{iid}: no filings.recent block")
                continue
            cols = {
                k: recent.get(k) or []
                for k in (
                    "accessionNumber",
                    "filingDate",
                    "reportDate",
                    "form",
                    "primaryDocument",
                    "primaryDocDescription",
                    "items",
                )
            }
            n = len(cols["accessionNumber"])
            for i in range(n):
                form = str(cols["form"][i]) if i < len(cols["form"]) else ""
                kind = FORMS.get(form)
                if kind is None:
                    continue
                filed = parse_date(cols["filingDate"][i] if i < len(cols["filingDate"]) else None)
                # A filing carries a date, not a time: one made on the day the
                # window opens is in the window.
                if filed is None or filed < since.date():
                    continue
                accession = str(cols["accessionNumber"][i])
                report = parse_date(cols["reportDate"][i] if i < len(cols["reportDate"]) else None)
                doc = str(cols["primaryDocument"][i]) if i < len(cols["primaryDocument"]) else ""
                desc = (
                    str(cols["primaryDocDescription"][i])
                    if i < len(cols["primaryDocDescription"])
                    else ""
                )
                items = str(cols["items"][i]) if i < len(cols["items"]) else ""
                title = (
                    f"{form}"
                    + (f": {desc}" if desc else "")
                    + (f" (items {items})" if items else "")
                )
                pull.events.append(
                    EventRecord(
                        source=self.name,
                        event_id=accession,
                        instrument_id=iid,
                        kind=kind,
                        announced_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC),
                        effective_at=(
                            datetime(report.year, report.month, report.day, tzinfo=UTC)
                            if report
                            else None
                        ),
                        title=title,
                        payload={
                            "form": form,
                            "items": items,
                            "url": ARCHIVE.format(
                                cik_int=int(cik), accession=accession.replace("-", ""), doc=doc
                            )
                            if doc
                            else "",
                        },
                    )
                )
        if asked and len(failures) == asked:
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull
