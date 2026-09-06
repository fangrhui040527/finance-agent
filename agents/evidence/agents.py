"""A1-A8, the evidence layer.

Each owns one primary collection and composes engines already built. docs/02
section 5 lists what each may NOT do; those prohibitions are implemented here as
guards rather than described in a prompt.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from agents.base import Agent, AgentContext, Finding, cite, quote_span
from core.contracts.answer import TrustTier
from core.guardrails.policy import PolicyViolation
from core.llm.tiers import TaskClass
from core.market.pointintime import FactStore, assert_no_lookahead
from core.market.prices import PriceSeries
from engines.events.taxonomy import BaseRateTable, Event, EventType

#: Stored event kinds (knowledge/facts.py) -> the taxonomy's types. A kind not
#: here is inferred from the title by `event_type_for`, or left out.
EVENT_KINDS: dict[str, EventType] = {
    "earnings_result": EventType.EARNINGS_RESULT,
    "insider_buy": EventType.INSIDER_BUY,
    "insider_sell": EventType.INSIDER_SELL,
    "rating_change": EventType.RATING_CHANGE,
}

#: Title patterns for the kinds that arrive untyped: filings and announcements.
_TITLE_TYPES: tuple[tuple[re.Pattern[str], EventType], ...] = (
    (
        re.compile(
            r"\b(quarterly|interim|annual|financial) (report|results?)\b|\b10-Q\b|\b10-K\b", re.I
        ),
        EventType.EARNINGS_RESULT,
    ),
    (re.compile(r"\bdividend\b", re.I), EventType.DIVIDEND_CHANGE),
    (re.compile(r"\b(share )?buy-?back\b|\bshares? repurchase", re.I), EventType.BUYBACK),
    (re.compile(r"\b(acqui(re|sition)|takeover|merger)\b", re.I), EventType.MA_ACQUIRER),
    (
        re.compile(r"\b(rights issue|placement|private placement|bond issue|sukuk)\b", re.I),
        EventType.CAPITAL_RAISE,
    ),
    (re.compile(r"\b(contract|award(ed)?|tender)\b", re.I), EventType.CONTRACT_WIN),
    (re.compile(r"\b(resign|appoint|appointment|retire)", re.I), EventType.EXECUTIVE_CHANGE),
    (re.compile(r"\b(litigation|lawsuit|court|arbitration)\b", re.I), EventType.LITIGATION),
    (re.compile(r"\b(suspension|halt|trading halt)\b", re.I), EventType.HALT),
    (re.compile(r"\bguidance\b", re.I), EventType.GUIDANCE_CHANGE),
    (re.compile(r"\bitems? 2\.02\b", re.I), EventType.EARNINGS_RESULT),  # 8-K results item
    (re.compile(r"\bitems? 5\.02\b", re.I), EventType.EXECUTIVE_CHANGE),  # 8-K officer change
    (re.compile(r"\bitems? 1\.01\b", re.I), EventType.CONTRACT_WIN),  # 8-K material agreement
)


def event_type_for(kind: str, title: str) -> EventType | None:
    """The taxonomy type for a stored event, or None when nothing fits."""
    if kind in EVENT_KINDS:
        return EVENT_KINDS[kind]
    if kind == "insider_filing":
        return None  # a Form 4 filing without its direction; Finnhub carries the typed one
    for pattern, kind_ in _TITLE_TYPES:
        if pattern.search(title or ""):
            return kind_
    return None


def event_from_record(rec) -> Event | None:
    """A `knowledge.facts.EventRecord` as a taxonomy `Event`, or None."""
    from markets.registry import mic_of

    et = event_type_for(rec.kind, rec.title)
    if et is None:
        return None
    try:
        market = mic_of(rec.instrument_id)
    except ValueError:
        market = ""
    effective = rec.effective_at
    if effective is not None and effective < rec.announced_at:
        effective = None  # a scheduled date earlier than its announcement is a vendor slip
    return Event(
        event_id=f"{rec.source}:{rec.event_id}",
        instrument_id=rec.instrument_id,
        event_type=et,
        announced_at=rec.announced_at,
        effective_at=effective,
        market=market,
        confirmed=True,
        source_doc_id=f"{rec.source}:{rec.event_id}",
        detail=rec.title,
    )


class A1Fundamentals(Agent):
    """Reads the statements as reported, at the time it was knowable."""

    agent_id = "a1_fundamentals"
    collections = ("kb_filings",)
    tools = (
        "retrieve",
        "get_statement",
        "dupont",
        "accrual_ratio",
        "restatement_diff",
        "ratio_sheet",
        "quality_scores",
    )
    tier = TaskClass.FUNDAMENTALS_READ

    def __init__(self, ctx: AgentContext, facts: FactStore) -> None:
        super().__init__(ctx)
        self.facts = facts

    #: What `from_fact_book` reads for a name when the caller names nothing:
    #: the statements FMP's collector stores, in the order an analyst reads them.
    DEFAULT_CONCEPTS = (
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "eps_diluted",
        "cash_from_operations",
        "free_cash_flow",
        "total_debt",
        "cash",
        "equity",
    )

    @classmethod
    def from_fact_book(cls, ctx: AgentContext, book, instrument_ids=None, asof: date | None = None):
        """A1 over what the collector stored, through the point-in-time bridge.

        `FactBook.as_fact_store` hands over only observations with a period AND
        a numeric value, each stamped with the day it became knowable, so the
        look-ahead guard in `run` holds on collected data exactly as on typed.
        """
        return cls(ctx, book.as_fact_store(instrument_ids, asof=asof))

    def run(self, instrument_id: str, concepts: list[str], asof: date) -> list[Finding]:
        self._guard_tool("get_statement")
        out: list[Finding] = []
        for concept in concepts:
            fact = self.facts.as_known_at(instrument_id, concept, asof)
            if fact is None:
                out.append(
                    Finding(
                        self.agent_id,
                        "unavailable",
                        f"{concept} was not yet public as at {asof}",
                        caveats=["point-in-time query returned nothing"],
                    )
                )
                continue
            assert_no_lookahead(fact, asof)
            out.append(
                Finding(
                    self.agent_id,
                    "line_item",
                    f"{concept} was {fact.value} {fact.currency} for the period ending "
                    f"{fact.period_end}, first knowable {fact.known_at}",
                    numbers={concept: float(fact.value)},
                    as_of=datetime.combine(fact.known_at, datetime.min.time()),
                    caveats=(["figure was later restated"] if fact.is_restatement else []),
                )
            )
        return out

    def ratio_sheet(
        self, instrument_id: str, asof: date, tax_rate: Decimal | None = None
    ) -> list[Finding]:
        """The ratio sheet from stored lines: each ratio a finding carrying its inputs' trail.

        Point-in-time through the same store `run` reads; a ratio whose input is
        not stored is not emitted as a number, and the head finding says how many
        were computable and which collector would fill the rest.
        """
        self._guard_tool("ratio_sheet")
        from engines.fundamentals.ratios import ratio_sheet as _sheet

        sheet = _sheet(self.facts, instrument_id, asof, tax_rate)
        head = Finding(
            self.agent_id,
            "ratio_sheet",
            f"{instrument_id}: {sheet.computable} of {sheet.total} ratios computable from what is "
            f"stored as of {asof}",
            numbers={"computable": float(sheet.computable), "total": float(sheet.total)},
            as_of=datetime(asof.year, asof.month, asof.day, tzinfo=UTC),
            caveats=[
                f"{c} would be filled by {', '.join(w)}" for c, w in sorted(sheet.fillers.items())
            ],
        )
        out = [head]
        for r in sheet.ratios.values():
            if not r.computable or r.value is None:
                continue
            out.append(
                Finding(
                    self.agent_id,
                    "ratio",
                    r.text(),
                    numbers={r.name: float(r.value)},
                    caveats=[f"sources {', '.join(r.sources)}; known {r.known_at}"],
                )
            )
        return out

    def quality_scores(
        self,
        instrument_id: str,
        asof: date,
        market_cap: Decimal | None = None,
        archetype: str | None = None,
    ) -> list[Finding]:
        """Accruals, Beneish M, Piotroski F and Altman Z, each honest about its inputs.

        A flagged score is a `quality_flag` finding, which is what the red team's
        accounting challenge and its failure-pattern analogues read.
        """
        self._guard_tool("quality_scores")
        from engines.fundamentals.quality import quality_report
        from engines.fundamentals.ratios import Statements

        s = Statements.from_store(self.facts, instrument_id, asof)
        out: list[Finding] = []
        for sc in quality_report(s, market_cap, archetype):
            kind = "quality_flag" if sc.verdict.startswith("flag") else "quality"
            caveats = list(sc.caveats)
            if sc.missing:
                caveats.insert(
                    0,
                    f"{sc.computable} of {sc.needed} inputs computable; missing {', '.join(sc.missing)}",
                )
            if sc.sources:
                caveats.append(f"sources {', '.join(sc.sources)}")
            out.append(
                Finding(
                    self.agent_id,
                    kind,
                    f"{sc.name}: {sc.verdict}",
                    numbers={sc.name: float(sc.value)} if sc.value is not None else {},
                    caveats=caveats,
                )
            )
        return out

    def earnings_quality(self, instrument_id: str, asof: date) -> list[Finding]:
        """The cheapest fraud detector that exists: net income vs operating cash flow."""
        ni = self.facts.as_known_at(instrument_id, "net_income", asof)
        cfo = self.facts.as_known_at(instrument_id, "cash_from_operations", asof)
        if not (ni and cfo):
            return [
                Finding(
                    self.agent_id,
                    "quality_flag",
                    "earnings quality check unavailable: missing NI or CFO",
                    caveats=["insufficient point-in-time data"],
                )
            ]
        gap = float(ni.value) - float(cfo.value)
        flagged = float(ni.value) > 0 and gap > abs(float(ni.value)) * 0.30
        return [
            Finding(
                self.agent_id,
                "quality_flag",
                (
                    "net income exceeds operating cash flow by more than 30% of earnings"
                    if flagged
                    else "net income is broadly supported by operating cash flow"
                ),
                numbers={"accrual_gap": gap},
                caveats=(
                    ["most reliable warning sign of aggressive accounting"] if flagged else []
                ),
            )
        ]


class A2Valuation(Agent):
    """Ranges and implied assumptions. Never a point target."""

    agent_id = "a2_valuation"
    collections = ("kb_method_valuation",)
    tools = (
        "retrieve",
        "multiple_vs_history",
        "reverse_dcf",
        "peer_multiples",
        "cost_of_capital",
        "scenario_range",
    )
    tier = TaskClass.VALUATION_COMMENT

    ARCHETYPE_METHODS = {
        "bank": ("P/B vs ROE", {"DCF"}),
        "reit": ("FFO yield / NAV", {"EPS multiples"}),
        "cyclical": ("mid-cycle earnings power", {"trailing P/E"}),
        "software": ("EV/gross profit, reverse-DCF", {"trailing P/E"}),
        "utility": ("regulated asset base, DCF", {"revenue multiples"}),
        "pre_profit": ("reverse-DCF only", {"any forward multiple"}),
        "holding": ("sum-of-parts with a stated discount", {"consolidated multiples"}),
    }

    def run(
        self, instrument_id: str, archetype: str, current_multiple: float, history: list[float]
    ) -> list[Finding]:
        self._guard_tool("multiple_vs_history")
        method, forbidden = self.ARCHETYPE_METHODS.get(archetype, ("EV/EBIT vs history", set()))
        band = sorted(history)
        if not band:
            return [
                Finding(
                    self.agent_id,
                    "valuation",
                    "no multiple history available",
                    caveats=["cannot contextualise"],
                )
            ]
        pct = sum(1 for h in band if h <= current_multiple) / len(band)
        return [
            Finding(
                self.agent_id,
                "valuation",
                f"on {method}, the current {current_multiple:.2f} sits at the "
                f"{pct:.0%} percentile of its own {len(band)}-observation history "
                f"({band[0]:.2f} to {band[-1]:.2f})",
                numbers={
                    "current": current_multiple,
                    "percentile": pct,
                    "band_low": band[0],
                    "band_high": band[-1],
                },
                caveats=[f"{', '.join(sorted(forbidden))} is not applicable to this archetype"]
                if forbidden
                else [],
            )
        ]

    def reverse_dcf(
        self, price: float, current_earnings: float, discount: float, years: int = 10
    ) -> list[Finding]:
        """State what the price already REQUIRES, rather than manufacturing a value."""
        self._guard_tool("reverse_dcf")
        lo, hi = -0.20, 0.60
        for _ in range(60):
            g = (lo + hi) / 2
            pv = sum(
                current_earnings * (1 + g) ** t / (1 + discount) ** t for t in range(1, years + 1)
            )
            if pv < price:
                lo = g
            else:
                hi = g
        return [
            Finding(
                self.agent_id,
                "reverse_dcf",
                f"at {price:.2f} the market is already requiring roughly {hi:.1%} annual "
                f"earnings growth for {years} years at a {discount:.0%} discount rate",
                numbers={"implied_growth": hi},
                caveats=["this is what the price implies, not an estimate of value"],
            )
        ]

    def cost_of_capital(
        self,
        instrument_id: str,
        book,
        table,
        asof: date,
        archetype: str | None = None,
        statements=None,
    ):
        """The discount rate, built and labelled; the table's rows cited verbatim."""
        self._guard_tool("cost_of_capital")
        from engines.valuation.cost_of_capital import cost_of_capital_text, derive

        coc = derive(instrument_id, book, table, asof, archetype, statements)
        citations = []
        col = None
        try:
            col = self.ctx.router.get(self.agent_id, "kb_method_valuation")
        except (KeyError, PermissionError):
            col = None
        if col is not None:
            for chunk_id in coc.citations:
                text = col.find_chunk("kb_method_valuation", chunk_id)
                if text:
                    citations.append(
                        cite(
                            "kb_method_valuation",
                            chunk_id,
                            quote_span(text, 120),
                            TrustTier.METHOD_KB,
                            datetime(
                                table.as_of.year, table.as_of.month, table.as_of.day, tzinfo=UTC
                            ),
                        )
                    )
        rate, which = coc.discount
        finding = Finding(
            self.agent_id,
            "cost_of_capital",
            cost_of_capital_text(coc),
            numbers={
                k: float(v)
                for k, v in (
                    ("rf", coc.rf),
                    ("beta", coc.beta),
                    ("erp", coc.erp),
                    ("ke", coc.ke),
                    ("kd", coc.kd),
                    ("wacc", coc.wacc),
                )
                if v is not None
            },
            citations=citations,
            caveats=[f"discount rate for a DCF: {which}", *coc.caveats],
        )
        return coc, [finding]

    def scenario_range(self, statements, coc, table, country: str, currency: str = ""):
        """Bear, base and bull from the record, through the sanity checks, as a range."""
        self._guard_tool("scenario_range")
        from engines.valuation.dcf import default_scenarios, scenario_range, valuation_text

        scenarios, reasons = default_scenarios(statements, coc, table, country)
        if reasons:
            return None, [
                Finding(
                    self.agent_id,
                    "valuation_unavailable",
                    f"{statements.instrument_id}: no scenario DCF - " + "; ".join(reasons),
                    caveats=[
                        "a range needs two annual margins, a revenue growth rate and a discount rate"
                    ],
                )
            ]
        vr = scenario_range(statements, coc, scenarios, table, country, currency)
        kind = "valuation_refused" if vr.refused else "valuation_range"
        numbers = {}
        if vr.low is not None and vr.high is not None:
            numbers = {"low": float(vr.low), "high": float(vr.high)}
            if vr.base is not None:
                numbers["base"] = float(vr.base)
        return vr, [
            Finding(
                self.agent_id,
                kind,
                valuation_text(vr, scenarios),
                numbers=numbers,
                caveats=["a range, not a target", *vr.caveats],
            )
        ]

    def peer_multiples(
        self,
        book,
        instrument_id: str,
        peers: set[str],
        concept: str,
        asof: date,
        price: Decimal | None = None,
        earnings: Decimal | None = None,
        discount: Decimal | None = None,
    ) -> list[Finding]:
        """The multiple in its three contexts: own history, peers, growth required."""
        self._guard_tool("peer_multiples")
        from engines.valuation.comps import three_contexts

        tc = three_contexts(book, instrument_id, peers, concept, asof, price, earnings, discount)
        numbers = {}
        if tc.current is not None:
            numbers["current"] = float(tc.current)
        if tc.history_percentile is not None:
            numbers["percentile"] = float(tc.history_percentile)
        if tc.peer_band is not None:
            numbers["peer_median"] = float(tc.peer_band.median)
        if tc.implied_growth is not None:
            numbers["implied_growth"] = float(tc.implied_growth)
        return [
            Finding(
                self.agent_id, "valuation", tc.text(), numbers=numbers, caveats=list(tc.caveats)
            )
        ]

    def method_note(self, archetype: str, limit: int = 2) -> list[Finding]:
        """The valuation method note for this archetype, cited from kb_method_valuation.

        Retrieval is on the method label and the hits are filtered on the
        note's own `archetypes`, so a bank note is never quoted for a software
        name (docs/06 section 5.6). Nothing when the store is empty.
        """
        method, _ = self.ARCHETYPE_METHODS.get(archetype, ("EV/EBIT vs history", set()))
        try:
            res = self.retrieve(
                "kb_method_valuation", f"{archetype} {method} valuation method", limit=6
            )
        except (KeyError, PermissionError, PolicyViolation):
            return []
        out: list[Finding] = []
        seen: set[str] = set()
        for h in res.hits:
            meta = h.chunk.metadata
            slug = meta.get("slug")
            if meta.get("kind") != "method_note" or slug in seen:
                continue
            if archetype not in (meta.get("archetypes") or ()):
                continue
            seen.add(str(slug))
            span = quote_span(h.chunk.text)
            out.append(
                Finding(
                    self.agent_id,
                    "method",
                    f"{meta.get('title')}: {span}",
                    citations=[
                        cite(
                            "kb_method_valuation",
                            h.chunk.chunk_id,
                            span,
                            TrustTier.METHOD_KB,
                            h.chunk.as_of or self.ctx.now,
                        )
                    ],
                    caveats=[f"method note {slug}, as of {meta.get('as_of')}"],
                )
            )
            if len(out) >= limit:
                break
        return out


class A3PriceTechnical(Agent):
    """Describes what price did. Never predicts from a pattern."""

    agent_id = "a3_price_technical"
    collections = ("kb_method_technical",)
    tools = ("retrieve", "ohlcv", "atr", "drawdown", "base_rate")
    tier = TaskClass.ADHOC_QUERY

    EARNINGS_BLACKOUT_SESSIONS = 3

    def run(
        self,
        series: PriceSeries,
        index_series: PriceSeries | None = None,
        sessions_to_earnings: int | None = None,
    ) -> list[Finding]:
        self._guard_tool("ohlcv")
        if len(series) < 21:
            return [
                Finding(
                    self.agent_id,
                    "price_context",
                    "insufficient price history to describe a trend",
                    caveats=["fewer than 21 bars"],
                )
            ]
        closes = series.closes()
        atr = series.atr(20)
        peak = max(closes)
        dd = 1.0 - closes[-1] / peak if peak else 0.0
        out = [
            Finding(
                self.agent_id,
                "price_context",
                f"last close {closes[-1]:.4f}, ATR20 {atr:.4f} ({atr / closes[-1]:.1%} of price), "
                f"{dd:.1%} below the period high, 20-day ADV {series.adv(20):,.0f}",
                numbers={
                    "close": closes[-1],
                    "atr20": atr,
                    "drawdown": dd,
                    "adv20": series.adv(20),
                },
            )
        ]
        if (
            sessions_to_earnings is not None
            and abs(sessions_to_earnings) <= self.EARNINGS_BLACKOUT_SESSIONS
        ):
            out.append(
                Finding(
                    self.agent_id,
                    "blackout",
                    f"short-horizon signals are suppressed: {abs(sessions_to_earnings)} sessions "
                    "from a scheduled earnings date",
                    caveats=["event risk dominates every technical feature in this window"],
                )
            )
        return out


class A4NewsNarrative(Agent):
    """Extracts features from text. Never asked whether a price will rise."""

    agent_id = "a4_news_narrative"
    collections = ("kb_news",)
    tools = ("retrieve", "search_news", "extract_features", "source_reliability", "llm_complete")
    tier = TaskClass.NEWS_TRIAGE

    #: The five feature dimensions carried into a Finding's numbers, so the
    #: synthesis layer reads intensity and uncertainty rather than a headline.
    FEATURE_KEYS = ("relevance", "polarity", "intensity", "uncertainty", "forwardness")

    def run(self, instrument_id: str, query: str, max_age=None, limit: int = 5) -> list[Finding]:
        res = self.retrieve("kb_news", query, max_age=max_age, entity=instrument_id, limit=limit)
        if res.refused:
            return [
                Finding(
                    self.agent_id,
                    "news",
                    "no news cleared the relevance and freshness gate",
                    caveats=[res.grade.reason],
                )
            ]
        out = []
        polarity: list[float] = []
        intensity: list[float] = []
        uncertainty: list[float] = []
        domains: set[str] = set()
        for hit in res.hits[:limit]:
            c = hit.chunk
            meta = c.metadata or {}
            features = meta.get("features") or {}
            domain = meta.get("source_domain") or "unknown source"
            when = c.as_of.strftime("%Y-%m-%d") if c.as_of else "undated"
            headline = (meta.get("title") or c.text.splitlines()[0])[:200]
            numbers = {k: float(features[k]) for k in self.FEATURE_KEYS if k in features}
            if "polarity" in numbers:
                polarity.append(numbers["polarity"])
            if "intensity" in numbers:
                intensity.append(numbers["intensity"])
            if "uncertainty" in numbers:
                uncertainty.append(numbers["uncertainty"])
            domains.add(domain)
            out.append(
                Finding(
                    self.agent_id,
                    "news",
                    f"{when} {domain}: {headline}",
                    citations=[
                        cite(
                            "kb_news",
                            c.chunk_id,
                            c.text[:60],
                            TrustTier.CURATED_NEWS,
                            c.as_of or self.ctx.now,
                        )
                    ],
                    numbers=numbers,
                    as_of=c.as_of,
                    caveats=(
                        ["a mention, not a cause: the story names the company"]
                        if numbers.get("relevance", 1.0) < 0.75
                        else []
                    ),
                )
            )
        if out and polarity:
            # docs/02 A4 output contract: an aggregate beside the articles, as a
            # FEATURE - never cited as evidence on its own.
            n = len(polarity)
            out.append(
                Finding(
                    self.agent_id,
                    "news_aggregate",
                    f"{n} stories from {len(domains)} sources: mean polarity "
                    f"{sum(polarity) / n:+.2f}, peak intensity {max(intensity or [0.0]):.2f}, "
                    f"mean uncertainty {sum(uncertainty) / max(1, len(uncertainty)):.2f}",
                    numbers={
                        "n": float(n),
                        "sources": float(len(domains)),
                        "polarity_mean": sum(polarity) / n,
                        "intensity_max": max(intensity or [0.0]),
                        "uncertainty_mean": sum(uncertainty) / max(1, len(uncertainty)),
                    },
                    caveats=["aggregate tone is a feature, not evidence; cite the stories"],
                )
            )
        return out


class A5CatalystEvents(Agent):
    """A time-indexed catalogue, and what each event type has been worth."""

    agent_id = "a5_catalyst_events"
    collections = ("kb_filings",)
    tools = ("retrieve", "events_in_window", "base_rate", "blackout_check")
    tier = TaskClass.CATALYST_MATCH

    def __init__(self, ctx: AgentContext, events: list[Event], table: BaseRateTable) -> None:
        super().__init__(ctx)
        self.events = events
        self.table = table

    @classmethod
    def from_fact_book(
        cls,
        ctx: AgentContext,
        book,
        instrument_ids=None,
        table: BaseRateTable | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ):
        """A5 over the events the collector stored.

        Every stored event carries its source and the day it was announced, so
        it arrives `confirmed` with a `source_doc_id` - citable, in A5's terms.
        A kind the taxonomy has no type for (a generic Bursa announcement whose
        title says nothing recognisable) is left out rather than mis-typed.
        """
        events: list[Event] = []
        wanted = list(instrument_ids or [None])
        for iid in wanted:
            # A5 types events against a taxonomy that has no place for a
            # broker's opinion; capping here keeps the 2000 for the record.
            for rec in book.events(iid, since=since, until=until, limit=2000, opinions=5):
                ev = event_from_record(rec)
                if ev is not None:
                    events.append(ev)
        return cls(ctx, events, table or BaseRateTable())

    def upcoming(self, instrument_id: str, now: datetime, horizon_days: int = 30) -> list[Finding]:
        """What is scheduled: the blackout the technical agent needs to know about."""
        self._guard_tool("blackout_check")
        out = []
        end = now + timedelta(days=horizon_days)
        for e in self.events:
            when = e.effective_at or e.announced_at
            if e.instrument_id == instrument_id and now <= when <= end:
                days = (when.date() - now.date()).days
                out.append(
                    Finding(
                        self.agent_id,
                        "scheduled",
                        f"{e.event_type.value} in {days} day(s), on {when.date()}"
                        + (f": {e.detail}" if e.detail else ""),
                        numbers={"days_to_event": float(days)},
                        as_of=when,
                        caveats=["event risk dominates short-horizon signals inside this window"]
                        if days <= 3
                        else [],
                    )
                )
        return out

    def run(self, instrument_id: str, t0: datetime, t1: datetime) -> list[Finding]:
        self._guard_tool("events_in_window")
        window = [
            e
            for e in self.events
            if e.instrument_id == instrument_id and t0 <= e.announced_at <= t1
        ]
        out = []
        for e in window:
            br = self.table.lookup(e.event_type, e.market, e.cap_band, e.surprise)
            out.append(
                Finding(
                    self.agent_id,
                    "event",
                    f"{e.event_type.value} announced {e.announced_at.date()}"
                    + (
                        f"; historically worth a median {br.median_car:+.2%} (n={br.n})"
                        if br
                        else ""
                    ),
                    numbers={"median_car": br.median_car} if br else {},
                    as_of=e.announced_at,
                    caveats=([] if e.citable else ["unconfirmed: cannot be cited as a cause"])
                    + (["thin sample"] if br and br.thin else []),
                )
            )
        return out


class A6MacroRegime(Agent):
    """The conditions everything else happens inside. Never forecasts a rate."""

    agent_id = "a6_macro_regime"
    collections = ()
    tools = ("series", "regime_label", "country_stress")
    tier = TaskClass.MACRO_READ

    #: The series the collector keeps and what each is, in reading order.
    POLICY_SERIES = (
        ("DFF", "Fed funds effective", "%"),
        ("DGS2", "US 2y yield", "%"),
        ("DGS10", "US 10y yield", "%"),
        ("T10Y2Y", "US 10y-2y spread", "%"),
        ("BNM:OPR", "BNM Overnight Policy Rate", "%"),
        ("DEXMAUS", "MYR per USD", ""),
        ("DTWEXBGS", "broad dollar index", ""),
        ("VIXCLS", "VIX", ""),
        ("BAMLH0A0HYM2", "US high-yield spread", "%"),
        ("CPIAUCSL", "US CPI", "index"),
        ("UNRATE", "US unemployment", "%"),
        ("DOSM:CPI_YOY", "Malaysia CPI", "% y/y"),
    )

    def read_policy(self, book, asof: date | None = None, lookback: int = 20) -> list[Finding]:
        """The rates and prices everything is discounted at, as last recorded.

        One finding per series the fact book holds: the latest point, its date,
        and the change over the last `lookback` points. Descriptive only - the
        one thing this agent is forbidden to do is forecast a rate.
        """
        self._guard_tool("series")
        out: list[Finding] = []
        for sid, label, unit in self.POLICY_SERIES:
            pts = book.series(sid, asof=asof)
            if not pts:
                continue
            latest = pts[-1]
            base = pts[-lookback - 1] if len(pts) > lookback else pts[0]
            change = float(latest.value - base.value)
            out.append(
                Finding(
                    self.agent_id,
                    "series",
                    f"{label} {latest.value}{unit and ' ' + unit} as of {latest.obs_date}, "
                    f"{change:+.4g} over the last {len(pts) - pts.index(base) - 1} observations",
                    numbers={"value": float(latest.value), "change": change},
                    as_of=datetime(
                        latest.obs_date.year, latest.obs_date.month, latest.obs_date.day, tzinfo=UTC
                    ),
                    caveats=[f"vintage knowable {latest.known_at}; {latest.source}"],
                )
            )
        if not out:
            out.append(
                Finding(
                    self.agent_id,
                    "series",
                    "no macro series recorded yet",
                    caveats=["the fred and bnm_opr collectors fill these; check their keys"],
                )
            )
        return out

    def run_from_series(
        self, book, series_id: str = "SP500", vol_window: int = 60
    ) -> list[Finding]:
        """The regime label, from a recorded index level series."""
        pts = book.series(series_id)
        closes = [float(p.value) for p in pts if p.value > 0]
        returns = [b / a - 1.0 for a, b in zip(closes, closes[1:])]
        out = self.run(returns, vol_window)
        for f in out:
            f.caveats.append(f"from {series_id}, {len(closes)} recorded levels")
        return out

    def run(self, market_returns: list[float], vol_window: int = 60) -> list[Finding]:
        self._guard_tool("regime_label")
        if len(market_returns) < vol_window:
            return [
                Finding(
                    self.agent_id,
                    "regime",
                    "insufficient history to label a regime",
                    caveats=["fewer observations than the volatility window"],
                )
            ]
        import statistics

        tail = market_returns[-vol_window:]
        vol = statistics.pstdev(tail) * (252**0.5)
        drift = sum(tail) / len(tail) * 252
        label = (
            "risk_off"
            if (drift < 0 and vol > 0.22)
            else "risk_on"
            if (drift > 0.05 and vol < 0.20)
            else "neutral"
        )
        return [
            Finding(
                self.agent_id,
                "regime",
                f"regime is {label}: {vol_window}-session annualised volatility {vol:.1%}, drift {drift:+.1%}",
                numbers={"vol": vol, "drift": drift},
                caveats=["descriptive label from a stated rule, not a forecast"],
            )
        ]


class A7SectorTechnology(Agent):
    """Did the company do something, or did its industry change underneath it?"""

    agent_id = "a7_sector_technology"
    collections = ("kb_sector",)
    tools = ("retrieve", "traverse", "peers", "sector_primer")
    tier = TaskClass.SECTOR_READ

    def __init__(self, ctx: AgentContext, graph=None, evidence=None) -> None:
        super().__init__(ctx)
        self.graph = graph
        #: source_doc_id -> Citation. The corpus talking: the graph records which
        #: document supports an edge, only the corpus knows its text. Without
        #: this every exposure finding reaches the output gate uncited and is
        #: dropped, so a multi-hop claim could not be emitted at all.
        self.evidence = evidence

    def run(self, event_node: str, holdings: set[str], asof: date | None = None) -> list[Finding]:
        from knowledge.graph.entity_graph import PathRequired, path_to_citations, require_path

        self._guard_tool("traverse")
        if self.graph is None:
            return [
                Finding(
                    self.agent_id,
                    "exposure",
                    "no graph is loaded",
                    caveats=["multi-hop exposure unavailable"],
                )
            ]
        on = asof or self.ctx.now.date()
        out = []
        for iid, path in self.graph.impact_of(event_node, holdings, asof=on):
            require_path(f"{event_node} affects {iid}", path)
            caveats = [f"{path.strength} link"] + (
                ["three or more hops: treat as speculative"] if path.n_hops >= 3 else []
            )
            citations = []
            if self.evidence is not None:
                try:
                    citations = path_to_citations(path, self.evidence)
                except PathRequired as exc:
                    # Emitted uncited on purpose rather than dropped here. The
                    # output gate will refuse it and record WHY in answer.dropped;
                    # skipping it silently would lose the fact that the graph
                    # points at a document the corpus cannot produce.
                    caveats.append(f"evidence unavailable: {exc}")
            out.append(
                Finding(
                    self.agent_id,
                    "exposure",
                    f"{self.graph.label(iid)} is exposed via {path.describe()}",
                    citations=citations,
                    numbers={"path_weight": path.weight, "hops": float(path.n_hops)},
                    caveats=caveats,
                    # One citation per hop, and the conclusion needs all of them. A
                    # chain missing a link is not a weaker claim, it is a different
                    # claim that nothing supports.
                    all_citations_required=True,
                )
            )
        return out

    def peers(
        self, instrument_id: str, asof: date | None = None, same_market: bool = True
    ) -> list[Finding]:
        """Who the graph says the peers are on a date, each with its edge document.

        A stated rivalry (``competes_with``) and a shared sub-sector are kept
        apart and labelled: the second is two hops of classification and reads
        as speculative by the graph's own decay. Nothing is inferred from names.
        """
        from knowledge.graph.peers import peers_of

        self._guard_tool("peers")
        if self.graph is None:
            return [
                Finding(
                    self.agent_id,
                    "peer_set",
                    "no graph is loaded",
                    caveats=["peers unavailable: build the graph with `make graph`"],
                )
            ]
        on = asof or self.ctx.now.date()
        ps = peers_of(self.graph, instrument_id, on, same_market=same_market)
        head = Finding(
            self.agent_id,
            "peer_set",
            ps.text().splitlines()[0],
            numbers={
                "direct": float(len(ps.direct)),
                "same_subsector": float(len(ps.same_subsector)),
                "excluded": float(len(ps.excluded)),
            },
            caveats=[ps.note] if ps.note else [],
        )
        out = [head]
        for p in ps.peers:
            citations = []
            caveats = [f"{p.strength} link"]
            if p.relation == "same_subsector":
                caveats.append("shared classification only: not a stated rivalry")
            if self.evidence is not None:
                for doc in p.evidence:
                    c = self.evidence(doc)
                    if c is None:
                        caveats.append(f"evidence unavailable: {doc}")
                    else:
                        citations.append(c)
            out.append(
                Finding(
                    self.agent_id,
                    "peer",
                    p.describe(),
                    citations=citations,
                    numbers={"weight": p.weight},
                    caveats=caveats,
                    all_citations_required=True,
                )
            )
        return out


class A8OwnershipFlow(Agent):
    """Who is buying and selling. Routine insider selling is not a signal."""

    agent_id = "a8_ownership_flow"
    collections = ()
    tools = ("insider_activity", "ownership_change", "short_interest_trend")
    tier = TaskClass.FLOW_READ

    INSTITUTIONAL_LAG_DAYS = 45

    def from_fact_book(
        self, book, instrument_id: str, now: datetime, days: int = 90
    ) -> list[Finding]:
        """Insider activity from the stored Form 4 / Finnhub transactions.

        Counts open-market purchases and sales in the window. Scheduled (10b5-1)
        sales are not flagged by the sources wired today, so `scheduled_sells`
        is zero and every sale is treated as discretionary - which is the
        conservative direction for a signal that is mostly noise anyway. Short
        interest is not collected yet; it is reported as unavailable rather
        than as zero.
        """
        since = now - timedelta(days=days)
        # `opinions=0`: this counts insider transactions, so a limit spent on
        # broker ratings is a limit spent on nothing it reads.
        events = book.events(instrument_id, since=since, until=now, limit=1000, opinions=0)
        buys = sum(1 for e in events if e.kind == "insider_buy")
        sells = sum(1 for e in events if e.kind == "insider_sell")
        out = self.run(buys, sells, 0, 0.0, 0.0)
        # Short interest is not a collected series; do not let 0.0 read as a fact.
        out = [f for f in out if f.kind != "short_interest"]
        out.append(
            Finding(
                self.agent_id,
                "short_interest",
                "short interest not collected",
                caveats=["no source for short interest is wired; nothing here is zero"],
            )
        )
        names = sorted({str(e.payload.get("name", "")) for e in events if e.kind == "insider_buy"})
        if names:
            out.append(
                Finding(
                    self.agent_id,
                    "insider",
                    f"buyers in the last {days} days: {', '.join(n for n in names if n)[:200]}",
                    numbers={"distinct_buyers": float(len([n for n in names if n]))},
                )
            )
        return out

    def run(
        self,
        insider_buys: int,
        insider_sells: int,
        scheduled_sells: int,
        short_interest_pct: float,
        days_to_cover: float,
        institutional_asof: date | None = None,
    ) -> list[Finding]:
        self._guard_tool("insider_activity")
        out = []
        non_plan = max(0, insider_sells - scheduled_sells)
        if insider_buys >= 3:
            out.append(
                Finding(
                    self.agent_id,
                    "insider",
                    f"cluster buying: {insider_buys} separate insider purchases",
                    numbers={"buys": insider_buys},
                )
            )
        if non_plan > 0:
            out.append(
                Finding(
                    self.agent_id,
                    "insider",
                    f"{non_plan} non-plan insider sales",
                    numbers={"non_plan_sells": non_plan},
                )
            )
        else:
            out.append(
                Finding(
                    self.agent_id,
                    "insider",
                    f"{insider_sells} insider sales, all under scheduled plans",
                    caveats=[
                        "routine insider selling is not treated as a signal: "
                        "plans, tax and diversification dominate the sample"
                    ],
                )
            )
        out.append(
            Finding(
                self.agent_id,
                "short_interest",
                f"short interest {short_interest_pct:.1%} of float, {days_to_cover:.1f} days to cover",
                numbers={"short_pct": short_interest_pct, "days_to_cover": days_to_cover},
            )
        )
        if institutional_asof:
            out.append(
                Finding(
                    self.agent_id,
                    "ownership",
                    f"institutional holdings as at {institutional_asof}",
                    caveats=[
                        f"institutional filings lag by {self.INSTITUTIONAL_LAG_DAYS}+ days "
                        "by construction"
                    ],
                )
            )
        return out
