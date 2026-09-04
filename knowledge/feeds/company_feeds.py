"""Per-company keyless news feeds: Google News search RSS and Yahoo Finance ticker RSS.

GDELT is a worldwide net and answers a Bursa mid-cap with nothing most days -
measured across every sweep on 2026-09-03/04, not one Malaysian name linked a
single article. These two are the opposite shape: narrow, per company, and
keyless.

  * **Google News RSS** (`news.google.com/rss/search`) indexes the Malaysian
    business press - The Edge, The Star, Bernama, NST, FMT - and returns one
    item per story with the publisher named. Queried per company in the MY
    edition for Bursa names and the US edition for Nasdaq names. The item link
    is a Google redirect, which is fine as a stable id; the body is empty
    because Google's description is a link list repeating the headline.
  * **Yahoo Finance ticker RSS** (`feeds.finance.yahoo.com/rss/2.0/headline`)
    is company news keyed by the symbol Yahoo already knows - `1155.KL`,
    `NVDA` - with a real one-paragraph summary. The symbol mapping is the one
    `core.market.feed.YahooFeed` already validates for prices, so a name that
    can be priced can be read about, and one that cannot raises rather than
    silently reading another company's news.

Both are `RssFeed` subclasses: they inherit the transport, retry, breaker, the
undated-item refusal and the HTML-page diagnosis, and add only URL construction
and per-item cleanup.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode, urlparse

from knowledge.feeds.rss import RssFeed
from knowledge.news.clean import split_publisher, strip_html

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
YAHOO_TICKER_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"

#: Google News editions: hl (interface language), gl (country), ceid.
EDITIONS: dict[str, tuple[str, str, str]] = {
    "MY": ("en-MY", "MY", "MY:en"),
    "US": ("en-US", "US", "US:en"),
    "SG": ("en-SG", "SG", "SG:en"),
    "GB": ("en-GB", "GB", "GB:en"),
}

#: Which edition reads a market's press. Bursa names are written about in
#: Malaysia; a Nasdaq name in the US edition.
EDITION_FOR_MIC = {"XKLS": "MY", "XSES": "SG", "XLON": "GB", "XNAS": "US", "XNYS": "US"}

#: Narrows "Apple" to the company rather than the fruit, without excluding a
#: product story: any ONE of these words in the article is enough.
FINANCE_TERMS = ("stock", "shares", "earnings", "profit", "revenue", "Bursa", "Nasdaq", "market")


def finance_query(name: str, extra_terms=FINANCE_TERMS) -> str:
    """`"Maybank" (stock OR shares OR earnings ...)` - Google's own operators."""
    return f'"{name}" (' + " OR ".join(extra_terms) + ")"


class GoogleNewsFeed(RssFeed):
    """One Google News search, as RSS. Keyless; polite at a handful of calls a day."""

    trust = "general_news"
    cadence = timedelta(hours=6)

    def __init__(
        self,
        query: str = "Bursa Malaysia",
        edition: str = "MY",
        when: str = "2d",
        name: str = "",
        opener=None,
        sleep=None,
        extractor=None,
    ) -> None:
        if edition not in EDITIONS:
            raise ValueError(f"unknown Google News edition {edition!r}; known: {sorted(EDITIONS)}")
        hl, gl, ceid = EDITIONS[edition]
        url = f"{GOOGLE_NEWS_RSS}?{urlencode({'q': f'{query} when:{when}', 'hl': hl, 'gl': gl, 'ceid': ceid})}"
        super().__init__(
            url,
            name=name or f"google_news:{edition}",
            trust="general_news",
            opener=opener,
            sleep=sleep,
            extractor=extractor,
        )
        self.query = query
        self.edition = edition

    def _item_to_row(self, item) -> dict | None:
        row = super()._item_to_row(item)
        if row is None:
            return None
        # <source url="https://www.thestar.com.my">The Star</source>
        source = item.find("source")
        publisher = (source.text or "").strip() if source is not None else ""
        source_url = source.get("url") if source is not None else None
        title, tail = split_publisher(row["title"])
        row["title"] = title
        row["publisher"] = publisher or tail or ""
        if source_url:
            row["domain"] = urlparse(source_url).netloc
        # Google's description is `<a>Headline</a> <font>Publisher</font>`: the
        # headline again, not a summary. Keeping it would index every title
        # twice and score every story as if it had a body.
        row["body"] = ""
        return row

    def _to_article(self, rec):
        art = super()._to_article(rec)
        publisher = rec.payload.get("publisher")
        if art is not None and publisher and not art.source_domain:
            art.source_domain = publisher
        return art


class YahooTickerFeed(RssFeed):
    """Yahoo Finance headlines for one instrument, by its Yahoo symbol."""

    trust = "general_news"
    cadence = timedelta(hours=6)

    def __init__(
        self,
        instrument_id: str = "XNAS:AAPL",
        name: str = "",
        opener=None,
        sleep=None,
        extractor=None,
    ) -> None:
        from core.market.feed import YahooFeed

        symbol = YahooFeed().symbol_for(instrument_id)  # raises SymbolUnmappable, never guesses
        url = f"{YAHOO_TICKER_RSS}?{urlencode({'s': symbol, 'region': 'US', 'lang': 'en-US'})}"
        super().__init__(
            url,
            name=name or f"yahoo_rss:{instrument_id}",
            trust="general_news",
            opener=opener,
            sleep=sleep,
            extractor=extractor,
        )
        self.instrument_id = instrument_id
        self.symbol = symbol

    def _item_to_row(self, item) -> dict | None:
        row = super()._item_to_row(item)
        if row is None:
            return None
        row["body"] = strip_html(row.get("body") or "")
        return row
