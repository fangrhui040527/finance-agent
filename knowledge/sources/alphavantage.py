"""Alpha Vantage NEWS_SENTIMENT: articles with a per-ticker sentiment score.

Twenty-five calls a day on the free plan, so this is a nightly batch: ONE
request for all the US names at once. What it adds that the other feeds do
not is article-level, per-ticker sentiment and relevance from the vendor -
the granularity docs/02 A4 calls citable. It is stored two ways:

  * the article itself, into the corpus, linked to every ticker the vendor
    marks relevant (relevance >= 0.2 - below that the ticker is mentioned in
    passing);
  * per ticker per day, an observation `av_news_sentiment` (relevance-weighted
    mean of the vendor's ticker score) and `av_news_count`, so the daily
    digest can show tone without re-reading the articles.

The rate-limit answer is a 200 with an "Information" or "Note" field. That is
a real failure - the day's quota is gone - and is raised as one.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from knowledge.facts import Observation, as_decimal
from knowledge.news.features import Article
from knowledge.sources.base import Collector, Pull, SourceError, local_code, parse_datetime

URL = "https://www.alphavantage.co/query"

#: The vendor answers 200 with an "Information" or "Note" field for two
#: different problems, and its quota wording also mentions the API key ("your
#: API key ... 25 requests per day"). Quota phrasing is checked first so a
#: spent day is not misread as a bad key, and a bad key - a configuration
#: error, not something to wait out - is not misread as a spent day.
_QUOTA_MARKERS = ("rate limit", "requests per day", "call frequency", "premium")
_KEY_MARKERS = ("api key", "apikey")


def classify_notice(text: str) -> str:
    """What a 200-with-notice means: a spent quota, a rejected key, or neither."""
    low = text.lower()
    if any(m in low for m in _QUOTA_MARKERS):
        return "quota exhausted for today"
    if any(m in low for m in _KEY_MARKERS):
        return "rejected the key (check ALPHAVANTAGE_API_KEY)"
    return "refused"


MIN_RELEVANCE = Decimal("0.2")


class AlphaVantageNews(Collector):
    name = "alphavantage_news"
    key_env = "ALPHAVANTAGE_API_KEY"
    LIMIT = 200

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        key = self.key
        pull = Pull()
        if not instruments:
            return pull
        by_symbol = {local_code(i).upper(): i for i in instruments}
        payload = self.get_json(
            URL,
            {
                "function": "NEWS_SENTIMENT",
                "tickers": ",".join(sorted(by_symbol)),
                "time_from": since.strftime("%Y%m%dT%H%M"),
                "limit": self.LIMIT,
                "sort": "LATEST",
                "apikey": key,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError("alphavantage: expected an object")
        for field in ("Information", "Note"):
            if payload.get(field):
                notice = str(payload[field])
                raise SourceError(f"alphavantage {classify_notice(notice)}: {notice[:160]}")
        if payload.get("Error Message"):
            raise SourceError(f"alphavantage: {str(payload['Error Message'])[:160]}")
        feed = payload.get("feed")
        if not isinstance(feed, list):
            raise SourceError(f"alphavantage: no feed list in {str(payload)[:120]!r}")

        weighted: dict[tuple[str, str], list[tuple[Decimal, Decimal]]] = defaultdict(list)
        for item in feed:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            published = parse_datetime(item.get("time_published"))
            if published is None:
                continue
            linked: list[str] = []
            for ts in item.get("ticker_sentiment") or []:
                if not isinstance(ts, dict):
                    continue
                iid = by_symbol.get(str(ts.get("ticker") or "").upper())
                rel = as_decimal(ts.get("relevance_score"))
                score = as_decimal(ts.get("ticker_sentiment_score"))
                if iid is None or rel is None or score is None:
                    continue
                if rel >= MIN_RELEVANCE:
                    linked.append(iid)
                    weighted[(iid, published.date().isoformat())].append((rel, score))
            pull.articles.append(
                Article(
                    doc_id=f"alphavantage:{item.get('url')}",
                    title=str(item["title"]),
                    body=str(item.get("summary") or ""),
                    source_domain=str(item.get("source_domain") or item.get("source") or ""),
                    published_at=published,
                    language="en",
                    instruments=linked,
                    themes=[
                        str(t.get("topic"))
                        for t in item.get("topics") or []
                        if isinstance(t, dict) and t.get("topic")
                    ],
                )
            )
        today = self.today()
        for (iid, day), pairs in sorted(weighted.items()):
            total = sum(r for r, _ in pairs)
            mean = sum(r * s for r, s in pairs) / total if total else Decimal(0)
            from datetime import date as _date

            period = _date.fromisoformat(day)
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "av_news_sentiment",
                    known_at=max(today, period),
                    value=mean.quantize(Decimal("0.0001")),
                    period_end=period,
                    unit="score[-1,1]",
                    payload={"articles": len(pairs)},
                )
            )
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "av_news_count",
                    known_at=max(today, period),
                    value=Decimal(len(pairs)),
                    period_end=period,
                    unit="articles",
                )
            )
        pull.requests = self.requests
        return pull
