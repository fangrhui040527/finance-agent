"""Every source the collector knows: what it is, when it runs, what it needs.

`config.toml [sources] enabled` names sources; this file says what each name
means. One table, so the scheduler, the sweep, `ask.py sources` and the docs
all read the same facts and cannot disagree about which slot Finnhub runs in
or which key FRED needs.

SLOTS are the four moments a day the collector runs, chosen from when each
source has something new:

  bursa_close   17:20 MYT (09:20 UTC), after Bursa's 17:00 close. Malaysian
                press, Bursa announcements, BNM's daily rate and OPR.
  us_preopen    08:30 ET (12:30 UTC), before the US open. Overnight macro
                prints from FRED, the day's earnings calendar, estimate
                revisions.
  us_close      17:15 ET (21:15 UTC), after the US 16:00 close. US company
                news, insider filings, SEC filings, the day's sentiment.
  weekly        Sunday 02:00 UTC. Fundamentals, consensus estimates,
                transcripts, Malaysian CPI - things that change monthly.

A per-instrument source runs once per name in the book whose market the slot
covers: at bursa_close the Bursa names, at us_close the Nasdaq names. A source
with no market runs on its own terms at the slots it lists.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Slot -> the MICs whose instruments are collected in it. Empty means the
#: slot has no per-instrument work of its own (macro only).
SLOTS: dict[str, frozenset[str]] = {
    "bursa_close": frozenset({"XKLS"}),
    "us_preopen": frozenset(),
    "us_close": frozenset({"XNAS", "XNYS"}),
    "weekly": frozenset({"XKLS", "XNAS", "XNYS"}),
    # Every source, every instrument - `ask.py sweep` with no slot named, the
    # shape the original once-a-day collector had.
    "all": frozenset({"XKLS", "XNAS", "XNYS"}),
}

NEWS = "news"
STRUCTURED = "structured"
MIXED = "mixed"  # articles AND observations (Alpha Vantage's sentiment feed)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str
    description: str
    trust: str
    slots: tuple[str, ...]
    per_instrument: bool = False
    #: MICs the source covers. Empty means any market.
    markets: tuple[str, ...] = ()
    key_env: str | None = None
    docs: str = ""
    #: Most names one run may ask a per-instrument source about. 0 means all of
    #: them, which is right for a source that answers every request. It is not
    #: right for one that throttles: the requests past the limit do not fail
    #: cheaply, they spend the retry budget and the sweep's clock before
    #: failing. `knowledge.sweep._window` tiles the list across days so the cap
    #: costs cadence rather than coverage.
    names_per_run: int = 0

    @property
    def keyless(self) -> bool:
        return self.key_env is None

    def runs_in(self, slot: str) -> bool:
        return slot == "all" or slot in self.slots

    def covers(self, mic: str) -> bool:
        return not self.markets or mic in self.markets


CATALOG: dict[str, SourceSpec] = {
    # -- news --------------------------------------------------------------------------
    "gdelt": SourceSpec(
        "gdelt",
        NEWS,
        "GDELT 2.0 DOC: worldwide, 100+ languages, three names per run",
        "general_news",
        ("bursa_close", "us_close"),
        per_instrument=True,
        docs="https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
        # Three, because asking for more does not get more. Measured over the
        # 30 recorded GDELT sweeps to 2026-09-07: 24 of them had at least one
        # name fail, 84 name-failures in all, a mean of 3.5 names per run
        # refused with HTTP 429 - and a refusal is not free. Each one spends
        # three attempts at RETRY_BASE_SECONDS 5 and up to a 90s read before it
        # gives up, which is how this source came to account for 10,433s of the
        # 10,655s every sweep has ever spent: 98%, at 73.5s per indexed row
        # against google_news's 0.1s.
        #
        # The cap is not a trade of coverage for time. The names it drops are
        # the ones already being refused, and `_window` brings each of them
        # round within two days.
        names_per_run=3,
    ),
    "google_news": SourceSpec(
        "google_news",
        NEWS,
        "Google News search RSS, per company, in the edition that reads its market",
        "general_news",
        ("bursa_close", "us_close"),
        per_instrument=True,
        docs="https://news.google.com/rss",
    ),
    "yahoo_rss": SourceSpec(
        "yahoo_rss",
        NEWS,
        "Yahoo Finance ticker headlines with a summary, per company",
        "general_news",
        ("bursa_close", "us_close"),
        per_instrument=True,
        docs="https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA",
    ),
    "thestar_business": SourceSpec(
        "thestar_business",
        NEWS,
        "The Star (Malaysia) business news RSS",
        "curated_news",
        ("bursa_close",),
        markets=("XKLS",),
        docs="https://www.thestar.com.my/rss",
    ),
    "edge_malaysia": SourceSpec(
        "edge_malaysia",
        NEWS,
        "The Edge Malaysia corporate news RSS",
        "curated_news",
        ("bursa_close",),
        markets=("XKLS",),
        docs="https://theedgemalaysia.com/rss.html",
    ),
    "bernama_business": SourceSpec(
        "bernama_business",
        NEWS,
        "Bernama (national news agency) business RSS",
        "wire",
        ("bursa_close",),
        markets=("XKLS",),
        docs="https://www.bernama.com/en/rssfeed.php",
    ),
    "fmt_business": SourceSpec(
        "fmt_business",
        NEWS,
        "Free Malaysia Today business RSS",
        "general_news",
        ("bursa_close",),
        markets=("XKLS",),
    ),
    "nst_business": SourceSpec(
        "nst_business",
        NEWS,
        "New Straits Times business RSS",
        "curated_news",
        ("bursa_close",),
        markets=("XKLS",),
    ),
    "bnm_press": SourceSpec(
        "bnm_press",
        NEWS,
        "Bank Negara press releases (registered; no live feed exists, see feeds/registry.py)",
        "regulator",
        ("bursa_close",),
        markets=("XKLS",),
    ),
    # -- structured, keyless -----------------------------------------------------------
    "edgar": SourceSpec(
        "edgar",
        STRUCTURED,
        "SEC EDGAR submissions: every filing (8-K, 10-Q, 10-K, 4) with its date, per US name",
        "filings",
        ("us_close",),
        per_instrument=True,
        markets=("XNAS", "XNYS"),
        docs="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    ),
    "bnm_opr": SourceSpec(
        "bnm_opr",
        STRUCTURED,
        "Bank Negara Malaysia Overnight Policy Rate, from the BNM OpenAPI",
        "regulator",
        ("bursa_close",),
        docs="https://apikijangportal.bnm.gov.my/openapi",
    ),
    "dosm_cpi": SourceSpec(
        "dosm_cpi",
        STRUCTURED,
        "Malaysian headline CPI from the Department of Statistics open data API",
        "regulator",
        ("weekly",),
        docs="https://developer.data.gov.my/realtime-api/opendosm",
    ),
    "bursa_announcements": SourceSpec(
        "bursa_announcements",
        STRUCTURED,
        "Bursa Malaysia company announcements, per Bursa name",
        "filings",
        ("bursa_close",),
        per_instrument=True,
        markets=("XKLS",),
        docs="https://www.bursamalaysia.com/market_information/announcements/company_announcement",
    ),
    # -- structured, keyed -------------------------------------------------------------
    "finnhub": SourceSpec(
        "finnhub",
        MIXED,
        "Finnhub: US company news, insider transactions, earnings calendar and surprises, "
        "recommendation trends, basic financials",
        "curated_news",
        ("us_close",),
        per_instrument=True,
        markets=("XNAS", "XNYS"),
        key_env="FINNHUB_API_KEY",
        docs="https://finnhub.io/docs/api",
    ),
    "fmp": SourceSpec(
        "fmp",
        STRUCTURED,
        "Financial Modeling Prep: quarterly statements, analyst estimates, price-target "
        "consensus, rating changes, earnings dates, call transcripts",
        "filings",
        ("weekly", "us_preopen"),
        per_instrument=True,
        markets=("XNAS", "XNYS"),
        key_env="FMP_API_KEY",
        docs="https://site.financialmodelingprep.com/developer/docs",
    ),
    "alphavantage_news": SourceSpec(
        "alphavantage_news",
        MIXED,
        "Alpha Vantage NEWS_SENTIMENT: articles with per-ticker sentiment, one call per "
        "US name a night (25 calls/day plan)",
        "general_news",
        ("us_close",),
        markets=("XNAS", "XNYS"),
        key_env="ALPHAVANTAGE_API_KEY",
        docs="https://www.alphavantage.co/documentation/#news-sentiment",
    ),
    "fred": SourceSpec(
        "fred",
        STRUCTURED,
        "FRED: Fed funds, 2y/10y yields, curve, CPI, unemployment, VIX, dollar index, MYR/USD",
        "regulator",
        ("us_preopen",),
        key_env="FRED_API_KEY",
        docs="https://fred.stlouisfed.org/docs/api/fred/",
    ),
    # -- 2026-09-05: the four sites the operator asked for, by their free routes ---------
    # 金十数据 sells its data; its own pages read two public JSON endpoints. MIXED
    # because the sweep stores a collector's articles the way it stores Alpha
    # Vantage's; one request per slot, Chinese kept (see knowledge/sources/jin10.py).
    "jin10_flash": SourceSpec(
        "jin10_flash",
        MIXED,
        "Jin10 (金十数据) flash news, Chinese, macro and markets; one request per slot",
        "wire",
        ("us_preopen", "bursa_close", "us_close"),
        docs="https://www.jin10.com/",
    ),
    "jin10_calendar": SourceSpec(
        "jin10_calendar",
        STRUCTURED,
        "Jin10 economic calendar: scheduled releases and prints (actual vs consensus) as MACRO:<country> events",
        "wire",
        ("us_preopen", "us_close"),
        docs="https://rili.jin10.com/",
    ),
    # MacroMicro's API starts at USD 5,000 a year; the series behind its charts
    # are on DBnomics, keyless. All fifteen of ours are ENDED upstream (the
    # 2026-09-06 probe: the datasets froze in mid-2025, our codes are right, no
    # sibling code is live). Still enabled, because the fetch is one request and
    # it is what would notice a restart - see knowledge/sources/freshness.ENDED.
    "dbnomics": SourceSpec(
        "dbnomics",
        STRUCTURED,
        "DBnomics: IMF commodity prices (palm oil, aluminium, Brent, LNG), BIS policy rates and NEERs, IMF CPI for MY and CN - ALL ENDED UPSTREAM mid-2025, kept to catch a restart",
        "regulator",
        ("us_preopen", "weekly"),
        docs="https://db.nomics.world/",
    ),
    # Goodinfo bans crawlers and 优分析 is paywalled; the exchange and FinMind
    # publish the same figures. MIXED so the sweep hands these the XTAI names in
    # `[sources] read_only`; they are read and cited, never traded.
    "twse_openapi": SourceSpec(
        "twse_openapi",
        MIXED,
        "TWSE OpenAPI (official, keyless): P/E, P/B, yield, monthly revenue, close and volume for the read-only Taiwan names",
        "regulator",
        ("bursa_close", "weekly"),
        markets=("XTAI",),
        docs="https://openapi.twse.com.tw/",
    ),
    "finmind": SourceSpec(
        "finmind",
        MIXED,
        "FinMind: 24 months of revenue, 8 quarters of statements, foreign net buying for the read-only Taiwan names (FINMIND_TOKEN optional)",
        "general_news",
        ("weekly",),
        markets=("XTAI",),
        docs="https://finmind.github.io/",
    ),
    # -- statement lines for the analyst engines (2026-09-06) ------------------------
    "sec_xbrl": SourceSpec(
        "sec_xbrl",
        STRUCTURED,
        "SEC XBRL company facts: every reported line item with its filing date, per US name; one request per name a week",
        "regulator",
        ("weekly",),
        per_instrument=True,
        markets=("XNAS", "XNYS"),
        docs="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    ),
    "eodhd": SourceSpec(
        "eodhd",
        STRUCTURED,
        "EODHD fundamentals (EODHD_API_KEY optional): quarterly and annual statements; 2 names a day on the free plan, US only until the Fundamentals plan",
        "vendor",
        ("bursa_close", "us_close"),
        per_instrument=True,
        markets=("XKLS", "XNAS", "XNYS", "XTAI"),
        docs="https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds",
    ),
}


def spec(name: str) -> SourceSpec:
    try:
        return CATALOG[name]
    except KeyError:
        raise KeyError(
            f"{name!r} is not in the source catalogue. Known: {', '.join(sorted(CATALOG))}"
        ) from None


def sources_for(slot: str, enabled) -> list[SourceSpec]:
    """The enabled sources that run in this slot, in catalogue order."""
    if slot not in SLOTS:
        raise KeyError(f"unknown slot {slot!r}; known: {', '.join(SLOTS)}")
    wanted = set(enabled)
    return [s for s in CATALOG.values() if s.name in wanted and s.runs_in(slot)]


def instruments_for(slot: str, source: SourceSpec, book) -> tuple[str, ...]:
    """Which names a per-instrument source is asked for in this slot."""
    from markets.registry import mic_of

    if not source.per_instrument:
        return ()
    mics = SLOTS[slot]
    out = []
    for iid in book:
        try:
            mic = mic_of(iid)
        except ValueError:
            continue
        if mic in mics and source.covers(mic):
            out.append(iid)
    return tuple(dict.fromkeys(out))


def describe(enabled=()) -> str:
    """A table for `ask.py sources`."""
    on = set(enabled)
    rows = [f"{'source':<20} {'kind':<10} {'slots':<26} {'key':<22} {'on':<3} description"]
    for s in CATALOG.values():
        rows.append(
            f"{s.name:<20} {s.kind:<10} {','.join(s.slots):<26} "
            f"{(s.key_env or 'none'):<22} {'yes' if s.name in on else 'no':<3} {s.description}"
        )
    return "\n".join(rows)
