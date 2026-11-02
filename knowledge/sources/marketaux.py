"""Marketaux news: articles with a per-entity sentiment score, for the US names.

Added 2026-11-02 in place of `alphavantage_news`, whose NEWS_SENTIMENT call
became premium-only for this key on 2026-10-07 (every pull since read
"refused a premium endpoint"). Marketaux was checked from a runner on
2026-10-29 with the key in `MARKETAUX_API_KEY`: NVDA over seven days answered
113 articles, each with a match score and a sentiment score; each of the six
Bursa names (`1155.KL` and the rest) over thirty days answered none. So this
collector covers the US names only, and the Bursa names get their tone from
`finbert_tone` instead (knowledge/sources/finbert_tone.py).

The free plan answers 100 requests a day and THREE articles per request, so a
name is read page by page, newest first, and the pages are capped
(`MAX_PAGES`): three names at eight pages is 24 requests a run, a quarter of
the day. A run that uses every page and is still inside the window has not read
its oldest day to the end, and that day is not aggregated (the same rule as
Alpha Vantage's `LIMIT`), because a partial day's mean would supersede a whole
one.

Stored two ways, as Alpha Vantage's were:

  * each article into the corpus, with the symbol that fetched it as
    `fetched_for` (provenance). The adapter's linker attaches it to a name only
    when its text names the company; the vendor's entity list is not trusted
    for that, for the reason in alphavantage.py (151 of 395 "AAPL" stories
    there never named Apple);
  * per name per day, an observation `mx_news_sentiment` (the mean of the
    vendor's `sentiment_score` for that symbol, -1..1, over the day's
    articles that list it as an entity) and `mx_news_count`. Each pull reads
    from the START of the UTC day its window opens in, so a later vintage of
    a day is never smaller than an earlier one.

Errors: Marketaux answers a bad token with 401 (raised as `KeyRejected` by the
transport), a spent daily allowance with 402 (`PlanExcluded`, recorded as a
skip with its reason) and a burst with 429.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from knowledge.facts import Observation, as_decimal
from knowledge.news.features import Article
from knowledge.redact import scrub
from knowledge.sources.base import (
    Collector,
    KeyRejected,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_datetime,
)

URL = "https://api.marketaux.com/v1/news/all"

#: Words in the vendor's `error.code` that mean the token is what is wrong.
_KEY_CODES = ("invalid_api_token", "missing_api_token")


class MarketauxNews(Collector):
    name = "marketaux_news"
    key_env = "MARKETAUX_API_KEY"
    #: Articles per page on the free plan. A page shorter than this is the last.
    PAGE_SIZE = 3
    #: Pages per name per run: 3 names x 8 pages = 24 of the 100 a day.
    MAX_PAGES = 8

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self.key
        pull = Pull()
        if not instruments:
            return pull
        by_symbol = {local_code(i).upper(): i for i in instruments}
        since = since if since.tzinfo else since.replace(tzinfo=UTC)
        # The whole of the window's first day, so a day's aggregate is over the
        # whole day as far as this pull saw it; stories before `since` count
        # toward the aggregate only and are not returned (the corpus has them).
        start = since.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        scores: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
        seen: set[str] = set()
        cut: set[tuple[str, str]] = set()
        errors: list[SourceError] = []
        pull.asked = [by_symbol[s] for s in sorted(by_symbol)]
        symbols = sorted(by_symbol)
        for n, symbol in enumerate(symbols):
            iid = by_symbol[symbol]
            oldest: date | None = None
            try:
                for page in range(1, self.MAX_PAGES + 1):
                    data = self._data_of(
                        self.get_json(
                            URL,
                            {
                                "symbols": symbol,
                                "filter_entities": "true",
                                "language": "en",
                                "published_after": start.strftime("%Y-%m-%dT%H:%M"),
                                "page": page,
                                "api_token": key,
                            },
                        )
                    )
                    for item in data:
                        published = self._take(item, iid, symbol, start, since, seen, scores, pull)
                        if published is not None:
                            d = published.date()
                            oldest = d if oldest is None or d < oldest else oldest
                    if len(data) < self.PAGE_SIZE:
                        break
                else:
                    # Every page was full: the run stopped before the window's
                    # start, so its oldest day is partial.
                    if oldest is not None:
                        cut.add((iid, oldest.isoformat()))
            except KeyRejected:
                raise  # a bad token is the same for every name; say so once
            except PlanExcluded as e:
                # HTTP 402: the day's allowance is spent, for every name after
                # this one too. Refused, not failed - the sweep reads a pull
                # whose every name was refused as a skip with its reason - and
                # not asked again today.
                for rest in symbols[n:]:
                    pull.refused.append((by_symbol[rest], str(e)))
                pull.notes.append(f"{iid} and after: {e}")
                break
            except SourceError as e:
                errors.append(e)
                pull.notes.append(f"{iid}: {e}")
                pull.failed.append((iid, str(e)))
                continue
        if errors and len(errors) == len(by_symbol):
            raise errors[0]
        today = self.today()
        for (iid, day), values in sorted(scores.items()):
            if (iid, day) in cut:
                pull.notes.append(
                    f"{iid}: {day} not aggregated - {self.MAX_PAGES} full pages ended "
                    f"part-way through that day"
                )
                continue
            period = date.fromisoformat(day)
            mean = sum(values, Decimal(0)) / len(values)
            known = max(today, period)
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "mx_news_sentiment",
                    known_at=known,
                    value=mean.quantize(Decimal("0.0001")),
                    period_end=period,
                    unit="score[-1,1]",
                    payload={"articles": len(values)},
                )
            )
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "mx_news_count",
                    known_at=known,
                    value=Decimal(len(values)),
                    period_end=period,
                    unit="articles",
                )
            )
        pull.requests = self.requests
        return pull

    def _take(self, item, iid, symbol, start, since, seen, scores, pull) -> datetime | None:
        """File one article: its score toward the day, the article itself if new."""
        if not isinstance(item, dict) or not item.get("title"):
            return None
        published = parse_datetime(item.get("published_at"))
        if published is None or published < start:
            # Before the day this pull reads from: a day it saw only the end of,
            # whose partial mean would supersede the whole day's.
            return None
        url = str(item.get("url") or item.get("uuid") or item["title"])
        day = published.date().isoformat()
        for ent in item.get("entities") or []:
            if not isinstance(ent, dict) or str(ent.get("symbol") or "").upper() != symbol:
                continue
            score = as_decimal(ent.get("sentiment_score"))
            if score is not None and (iid, url) not in seen:
                scores[(iid, day)].append(score)
            break
        if (iid, url) in seen:
            return published
        seen.add((iid, url))
        if published < since or any(a.doc_id == f"marketaux:{url}" for a in pull.articles):
            return published  # counted toward the day; stored already or by another name
        pull.articles.append(
            Article(
                doc_id=f"marketaux:{url}",
                title=str(item["title"]),
                body=str(item.get("description") or item.get("snippet") or ""),
                source_domain=str(item.get("source") or ""),
                published_at=published,
                language=str(item.get("language") or "en"),
                fetched_for=iid,
            )
        )
        return published

    @staticmethod
    def _data_of(payload: object) -> list:
        """The `data` list, or the refusal the vendor wrapped in a reply."""
        if not isinstance(payload, dict):
            raise SourceError("marketaux: expected an object")
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = scrub(str(error.get("message") or code))[:160]
            if code in _KEY_CODES:
                raise KeyRejected(
                    f"marketaux rejected the key (check MARKETAUX_API_KEY): {message}"
                )
            raise SourceError(f"marketaux {code or 'error'}: {message}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise SourceError(f"marketaux: no data list in {str(payload)[:120]!r}")
        return data
