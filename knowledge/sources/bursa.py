"""Bursa Malaysia company announcements - the Bursa equivalent of EDGAR.

There is no public documented API. The exchange's announcements page loads
its table from a JSON endpoint on the same host, and that endpoint is what
this adapter asks, with the headers a browser would send. Whether it answers
from a runner, and in what shape, is settled by `ask.py sources --probe`,
which is why this source is registered and NOT enabled: the catalogue rule is
that an enabled source that ingests nothing every night is indistinguishable
from a quiet market.

The parser is deliberately lenient about field names (`ann_date`/`date`,
`title`/`ann_title`, `id`/`ann_id`) and strict about the result: a response
with no recognisable rows raises with the first row's keys in the message,
so the probe output says what came back.
"""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge.facts import EventRecord
from knowledge.sources.base import Collector, Pull, SourceError, local_code, parse_datetime

SEARCH = "https://www.bursamalaysia.com/api/v1/announcements/search"

DATE_KEYS = ("ann_date", "announcement_date", "date", "ann_datetime")
TITLE_KEYS = ("title", "ann_title", "announcement_title", "subject")
ID_KEYS = ("id", "ann_id", "announcement_id", "ref")


class BursaAnnouncements(Collector):
    name = "bursa_announcements"
    key_env = None
    PER_PAGE = 30

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.bursamalaysia.com/market_information/announcements/company_announcement",
        }

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        failures: list[str] = []
        for iid in instruments:
            code = local_code(iid)
            try:
                payload = self.get_json(
                    SEARCH,
                    {"ann_type": "company", "company": code, "per_page": self.PER_PAGE, "page": 1},
                    headers=self._headers(),
                )
            except SourceError as e:
                failures.append(f"{iid}: {e}")
                continue
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                failures.append(f"{iid}: expected a data list, got {str(payload)[:120]!r}")
                continue
            recognised = 0
            for r in rows:
                if isinstance(r, list):
                    # A positional table: [date, company, title, id, ...] is the
                    # shape the page renders; take the first date-like, the
                    # longest string as title, and any int as id.
                    r = _from_positional(r)
                if not isinstance(r, dict):
                    continue
                when = next((parse_datetime(r[k]) for k in DATE_KEYS if r.get(k)), None)
                title = next((str(r[k]) for k in TITLE_KEYS if r.get(k)), "")
                ann_id = next((str(r[k]) for k in ID_KEYS if r.get(k)), "")
                if when is None or not title:
                    continue
                if when < since:
                    continue
                recognised += 1
                pull.events.append(
                    EventRecord(
                        source=self.name,
                        event_id=f"{code}:{ann_id or when.isoformat()}:{title[:40]}",
                        instrument_id=iid,
                        kind="announcement",
                        announced_at=when,
                        title=title,
                        payload={k: v for k, v in r.items() if isinstance(v, (str, int, float))},
                    )
                )
            if rows and not recognised:
                sample = sorted(rows[0]) if isinstance(rows[0], dict) else rows[0]
                failures.append(
                    f"{iid}: {len(rows)} rows, none recognised; first: {str(sample)[:160]}"
                )
        if instruments and failures and len(failures) == len(instruments):
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull


def _from_positional(row: list) -> dict:
    out: dict = {}
    strings = [c for c in row if isinstance(c, str)]
    for c in strings:
        if parse_datetime(c) is not None and "date" not in out and any(ch.isdigit() for ch in c):
            out["date"] = c
    if strings:
        out["title"] = max(strings, key=len)
    for c in row:
        if isinstance(c, int):
            out["id"] = c
            break
    out["_raw"] = str(row)[:400]
    return out


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)
