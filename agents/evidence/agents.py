"""A1-A8, the evidence layer.

Each owns one primary collection and composes engines already built. docs/02
section 5 lists what each may NOT do; those prohibitions are implemented here as
guards rather than described in a prompt.
"""

from __future__ import annotations

from datetime import date, datetime

from agents.base import Agent, AgentContext, Finding, cite
from core.contracts.answer import TrustTier
from core.llm.tiers import TaskClass
from core.market.pointintime import FactStore, assert_no_lookahead
from core.market.prices import PriceSeries
from engines.events.taxonomy import BaseRateTable, Event


class A1Fundamentals(Agent):
    """Reads the statements as reported, at the time it was knowable."""

    agent_id = "a1_fundamentals"
    collections = ("kb_filings",)
    tools = ("retrieve", "get_statement", "dupont", "accrual_ratio", "restatement_diff")
    tier = TaskClass.FUNDAMENTALS_READ

    def __init__(self, ctx: AgentContext, facts: FactStore) -> None:
        super().__init__(ctx)
        self.facts = facts

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
    tools = ("retrieve", "multiple_vs_history", "reverse_dcf", "peer_multiples")
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


class A8OwnershipFlow(Agent):
    """Who is buying and selling. Routine insider selling is not a signal."""

    agent_id = "a8_ownership_flow"
    collections = ()
    tools = ("insider_activity", "ownership_change", "short_interest_trend")
    tier = TaskClass.FLOW_READ

    INSTITUTIONAL_LAG_DAYS = 45

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
