"""金十数据 (Jin10): Chinese-language flash news and the economic calendar.

Jin10 sells its data (mcp.jin10.com is a commercial platform) and publishes no
free API. What its own web pages read is a pair of public, unauthenticated JSON
endpoints that RSSHub and AkShare have used for years. The terms are a gray
zone, so this module holds itself to a posture rather than a licence:

  * one flash request per collection slot and one calendar request per slot
    that runs it - three or four calls a day, not a poll;
  * the repository's own User-Agent, never a browser's;
  * personal research, nothing redistributed;
  * a page, a captive portal or a refusal RAISES (`SourceError`), so a day
    the endpoint changes is recorded as a failure, not as a quiet day.

Two collectors, because the two kinds of thing are read by different parts
of the system:

  jin10_flash     the 快讯 stream -> corpus Articles in Chinese (`language="zh"`;
                  config `[sources] languages` already keeps Chinese). These are
                  macro and market flashes - central banks, prints, oil, chips -
                  and mostly arrive unlinked to a name in the book. The RAG can
                  still retrieve them, and the digest counts them as unlinked
                  rather than pretending they name Maybank.
  jin10_calendar  today's 财经日历 -> fact-book events. A scheduled release is
                  `macro_release` (announced_at = publication time, payload
                  consensus/previous); once the vendor fills `actual` the same
                  release is stored again as `macro_print` with the surprise,
                  under its own event id, so the insert-or-ignore store keeps
                  both the expectation and the outcome. Events hang off
                  `MACRO:<country>` rather than an instrument: the fact book's
                  events table takes any id, and the reports that list "what to
                  watch" read the `MACRO:` prefix.

Times: Jin10 stamps everything in Beijing time (UTC+8, no DST). Every
timestamp here is converted to UTC before it is stored.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from knowledge.facts import EventRecord, as_decimal
from knowledge.news.features import Article
from knowledge.sources.base import Collector, Pull, SourceError

FLASH_URL = "https://flash-api.jin10.com/get_flash_list"
#: The two paths the calendar has lived at; the newer CDN first.
CALENDAR_URLS = (
    "https://cdn-rili.jin10.com/web_data/{y}/daily/{m:02d}/{d:02d}/economics.json",
    "https://rili.jin10.com/datas/{y}/{m:02d}{d:02d}/economics.json",
)
#: The app id Jin10's own pages send. Public in the page source; not a secret.
HEADERS = {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"}
BEIJING = timezone(timedelta(hours=8))
MACRO_PREFIX = "MACRO:"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def beijing_to_utc(raw: Any) -> datetime | None:
    """`2026-09-05 21:30:00` (Beijing) -> aware UTC datetime, or None."""
    if not raw:
        return None
    s = str(raw).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[: len(fmt) + 2], fmt).replace(tzinfo=BEIJING).astimezone(UTC)
        except ValueError:
            continue
    return None


def _plain(text: Any) -> str:
    return _WS.sub(" ", _TAG.sub(" ", str(text or ""))).strip()


class Jin10FlashCollector(Collector):
    name = "jin10_flash"
    #: Jin10 pages 20-100 items per call; one call is the whole point.
    LIMIT = 100

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        payload = self.get_json(FLASH_URL, {"channel": "-8200", "vip": "1"}, headers=HEADERS)
        rows = self._rows(payload)
        for row in rows:
            if not isinstance(row, dict):
                continue
            published = beijing_to_utc(row.get("time"))
            if published is None or published < since:
                continue
            raw_data = row.get("data")
            data: dict = raw_data if isinstance(raw_data, dict) else {}
            content = _plain(data.get("content") or data.get("title") or row.get("content"))
            if not content:
                continue
            title = _plain(data.get("title")) or content[:80]
            ident = str(row.get("id") or hashlib.sha1(content.encode("utf-8")).hexdigest()[:16])
            themes = [str(t) for t in row.get("tags") or [] if t]
            if row.get("important"):
                themes.append("important")
            pull.articles.append(
                Article(
                    doc_id=f"jin10:{ident}",
                    title=title,
                    body=content,
                    source_domain="jin10.com",
                    published_at=published,
                    language="zh",
                    countries=["CN"],
                    themes=themes,
                )
            )
        pull.requests = self.requests
        return pull

    def _rows(self, payload: Any) -> list:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise SourceError("jin10_flash: expected an object or a list")
        status = payload.get("status")
        if status not in (None, 200, "200"):
            raise SourceError(
                f"jin10_flash refused: status {status} {str(payload.get('message', ''))[:120]}"
            )
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("data")
        if not isinstance(data, list):
            raise SourceError(f"jin10_flash: no item list in {str(payload)[:120]!r}")
        return data


class Jin10CalendarCollector(Collector):
    name = "jin10_calendar"

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        # The calendar is a document PER DAY. The collector reads today's, in
        # Beijing terms, which is the day most of the world's prints land on
        # from where this runs.
        day = self._clock().astimezone(BEIJING).date()
        rows = self._calendar(day)
        for row in rows:
            if not isinstance(row, dict):
                continue
            when = beijing_to_utc(row.get("pub_time") or row.get("time_period"))
            if when is None:
                continue
            country = _plain(row.get("country")) or "world"
            indicator = _plain(row.get("name") or row.get("indicator_name"))
            if not indicator:
                continue
            actual = as_decimal(row.get("actual"))
            consensus = as_decimal(row.get("consensus"))
            previous = as_decimal(row.get("previous"))
            revised = as_decimal(row.get("revised"))
            star = row.get("star")
            base_id = str(row.get("id") or f"{when:%Y%m%d}:{country}:{indicator}")
            payload = {
                "country": country,
                "indicator": indicator,
                "consensus": _text(consensus),
                "previous": _text(previous),
                "revised": _text(revised),
                "star": star,
                "unit": _plain(row.get("unit")),
                "period": _plain(row.get("time_period")),
            }
            if actual is not None:
                surprise = actual - consensus if consensus is not None else None
                pull.events.append(
                    EventRecord(
                        source=self.name,
                        event_id=f"{base_id}:print",
                        instrument_id=f"{MACRO_PREFIX}{country}",
                        kind="macro_print",
                        announced_at=when,
                        title=f"{country} {indicator}: {_text(actual)}"
                        + (f" vs {_text(consensus)} expected" if consensus is not None else ""),
                        payload={**payload, "actual": _text(actual), "surprise": _text(surprise)},
                    )
                )
            else:
                pull.events.append(
                    EventRecord(
                        source=self.name,
                        event_id=f"{base_id}:scheduled",
                        instrument_id=f"{MACRO_PREFIX}{country}",
                        kind="macro_release",
                        announced_at=when,
                        title=f"{country} {indicator}"
                        + (f" (consensus {_text(consensus)})" if consensus is not None else ""),
                        payload=payload,
                    )
                )
        pull.requests = self.requests
        return pull

    def _calendar(self, day) -> list:
        last: SourceError | None = None
        for template in CALENDAR_URLS:
            url = template.format(y=day.year, m=day.month, d=day.day)
            try:
                payload = self.get_json(url)
            except SourceError as e:
                last = e
                continue
            if isinstance(payload, dict):
                payload = payload.get("data") or payload.get("list") or payload.get("economics")
            if isinstance(payload, list):
                return payload
            last = SourceError(f"jin10_calendar: no item list at {url}")
        raise last or SourceError("jin10_calendar: no calendar path answered")


def _text(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")
