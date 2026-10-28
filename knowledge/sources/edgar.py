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

An 8-K with Item 2.02 (Results of Operations) is also stored as a
`results_released` event, known at the SEC's acceptance time: the most
authoritative record there is of WHEN a company's results came out, and the
top of the authority order in knowledge/earnings_date.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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


#: The 8-K item that announces results, and the kind its release is stored as.
RESULTS_ITEM = "2.02"
RELEASED = "results_released"

#: EDGAR's acceptance clock runs on Eastern time. The submissions JSON writes
#: `acceptanceDateTime` as "2026-07-30T16:30:41.000Z", and the "Z" is taken to
#: be a formatting suffix, not UTC: Apple's results 8-Ks, released at 4:30pm New
#: York time, read T16:30 there. Read as UTC they would be dated four or five
#: hours early - before the release they record, a look-ahead. Assumed, not
#: re-checked live when this was written (data.sec.gov was not reachable from
#: the machine): a trailing "Z" is dropped and the time read as New York's; an
#: explicit numeric offset, should one ever appear, is kept as given.
SEC_TZ = ZoneInfo("America/New_York")


def acceptance_time(raw) -> datetime | None:
    """An EDGAR acceptanceDateTime as a UTC instant, or None."""
    s = str(raw or "").strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1]
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEC_TZ)
    return parsed.astimezone(UTC)


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
                    "acceptanceDateTime",
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
                        # Public on the day it was filed; the period or trade
                        # date it reports on goes in the payload. It sat in
                        # effective_at until 2026-10-10, which windowed a Form 4
                        # filed today on last week's trade date: today's
                        # filings never reached "recent", and an as-of read
                        # saw them days before they were made.
                        announced_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC),
                        title=title,
                        payload={
                            "form": form,
                            "items": items,
                            "report_date": report.isoformat() if report else None,
                            "url": ARCHIVE.format(
                                cik_int=int(cik), accession=accession.replace("-", ""), doc=doc
                            )
                            if doc
                            else "",
                        },
                    )
                )
                # 8-K only: an 8-K/A amending a results filing is not the release.
                accepted = (
                    acceptance_time(cols["acceptanceDateTime"][i])
                    if form == "8-K"
                    and i < len(cols["acceptanceDateTime"])
                    and RESULTS_ITEM in (x.strip() for x in items.split(","))
                    else None
                )
                if accepted is not None:
                    # Known at the acceptance instant, not the filing day: a
                    # release after the close is public that evening.
                    pull.events.append(
                        EventRecord(
                            source=self.name,
                            event_id=f"{accession}:{RESULTS_ITEM}",
                            instrument_id=iid,
                            kind=RELEASED,
                            announced_at=accepted,
                            title=(
                                "results released: 8-K accepted "
                                f"{accepted.astimezone(SEC_TZ):%Y-%m-%d %H:%M} ET"
                            ),
                            payload={
                                "form": form,
                                "items": items,
                                "accession": accession,
                                "acceptance": str(cols["acceptanceDateTime"][i]),
                                "url": pull.events[-1].payload["url"],
                            },
                        )
                    )
        if asked and len(failures) == asked:
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull
