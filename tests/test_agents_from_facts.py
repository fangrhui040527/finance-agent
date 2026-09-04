"""The evidence agents read what the collector stored, through the fact book."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from agents.base import AgentContext
from agents.evidence.agents import (
    A1Fundamentals,
    A5CatalystEvents,
    A6MacroRegime,
    A8OwnershipFlow,
    event_from_record,
    event_type_for,
)
from core.guardrails.defaults import default_engine
from engines.events.taxonomy import EventType
from knowledge.facts import EventRecord, FactBook, Observation, SeriesPoint
from knowledge.retrieval.pipeline import Router

NOW = datetime(2026, 9, 4, 22, 0, tzinfo=UTC)
ALLOW = {
    "a1_fundamentals": {"get_statement"},
    "a5_catalyst_events": {"events_in_window", "blackout_check"},
    "a6_macro_regime": {"series", "regime_label"},
    "a8_ownership_flow": {"insider_activity"},
}


@pytest.fixture
def ctx():
    return AgentContext(router=Router({}), engine=default_engine(ALLOW), now=NOW)


@pytest.fixture
def book(tmp_path):
    with FactBook(tmp_path / "facts.db") as b:
        b.add_observations(
            [
                Observation(
                    "fmp",
                    "XNAS:AAPL",
                    "revenue",
                    date(2026, 8, 1),
                    Decimal("94036000000"),
                    period_end=date(2026, 6, 28),
                    currency="USD",
                ),
                Observation(
                    "fmp",
                    "XNAS:AAPL",
                    "net_income",
                    date(2026, 8, 1),
                    Decimal("23434000000"),
                    period_end=date(2026, 6, 28),
                    currency="USD",
                ),
                Observation(
                    "fmp",
                    "XNAS:AAPL",
                    "cash_from_operations",
                    date(2026, 8, 1),
                    Decimal("27867000000"),
                    period_end=date(2026, 6, 28),
                    currency="USD",
                ),
            ]
        )
        b.add_events(
            [
                EventRecord(
                    "finnhub",
                    "aapl:earnings:2026-10-29",
                    "XNAS:AAPL",
                    "earnings_result",
                    NOW - timedelta(days=10),
                    "Q4 2026 results after the close",
                    effective_at=NOW + timedelta(days=25),
                ),
                EventRecord(
                    "finnhub",
                    "s1",
                    "XNAS:AAPL",
                    "insider_sell",
                    NOW - timedelta(days=3),
                    "Cook Timothy sold 50,000 shares",
                    payload={"name": "Cook Timothy"},
                ),
                EventRecord(
                    "finnhub",
                    "b1",
                    "XNAS:AAPL",
                    "insider_buy",
                    NOW - timedelta(days=5),
                    "Levinson Arthur bought 1,000 shares",
                    payload={"name": "Levinson Arthur"},
                ),
                EventRecord(
                    "finnhub",
                    "b2",
                    "XNAS:AAPL",
                    "insider_buy",
                    NOW - timedelta(days=6),
                    "Gore Albert bought 500 shares",
                    payload={"name": "Gore Albert"},
                ),
                EventRecord(
                    "finnhub",
                    "b3",
                    "XNAS:AAPL",
                    "insider_buy",
                    NOW - timedelta(days=7),
                    "Jung Andrea bought 200 shares",
                    payload={"name": "Jung Andrea"},
                ),
                EventRecord(
                    "edgar",
                    "0000320193-26-000090",
                    "XNAS:AAPL",
                    "filing",
                    NOW - timedelta(days=1),
                    "8-K: 8-K (items 2.02,9.01)",
                ),
                EventRecord(
                    "edgar",
                    "0000320193-26-000091",
                    "XNAS:AAPL",
                    "filing",
                    NOW - timedelta(days=1),
                    "SC 13G: Vanguard",
                ),
                EventRecord(
                    "bursa_announcements",
                    "1155:33921",
                    "MYX:1155",
                    "announcement",
                    NOW - timedelta(hours=6),
                    "Quarterly report for the period ended 30 June 2026",
                ),
                EventRecord(
                    "bursa_announcements",
                    "1155:33922",
                    "MYX:1155",
                    "announcement",
                    NOW - timedelta(hours=5),
                    "Changes in Sub. S-hldr's Int (29B)",
                ),
            ]
        )
        pts = []
        level = 100.0
        for i in range(80):
            day = date(2026, 5, 1) + timedelta(days=i)
            level *= 1.001 if i % 3 else 0.998
            pts.append(SeriesPoint("fred", "SP500", day, Decimal(str(round(level, 2))), NOW.date()))
        pts += [
            SeriesPoint("fred", "DFF", date(2026, 8, 1), Decimal("4.33"), NOW.date()),
            SeriesPoint("fred", "DFF", date(2026, 9, 3), Decimal("4.08"), NOW.date()),
            SeriesPoint("bnm_opr", "BNM:OPR", date(2026, 7, 9), Decimal("2.75"), NOW.date()),
        ]
        b.add_series(pts)
        yield b


# --- A1 -----------------------------------------------------------------------------


def test_a1_reads_collected_statements_point_in_time(ctx, book):
    a1 = A1Fundamentals.from_fact_book(ctx, book, ["XNAS:AAPL"])
    findings = a1.run("XNAS:AAPL", ["revenue", "net_income"], date(2026, 8, 15))
    assert all(f.kind == "line_item" for f in findings)
    assert "94036000000" in findings[0].text and "first knowable 2026-08-01" in findings[0].text
    early = a1.run("XNAS:AAPL", ["revenue"], date(2026, 7, 15))
    assert early[0].kind == "unavailable", "not public before the filing date"
    (quality,) = a1.earnings_quality("XNAS:AAPL", date(2026, 8, 15))
    assert "supported by operating cash flow" in quality.text


# --- A5 -----------------------------------------------------------------------------


def test_event_kinds_map_to_the_taxonomy_and_titles_type_the_rest():
    assert event_type_for("earnings_result", "") is EventType.EARNINGS_RESULT
    assert event_type_for("insider_buy", "") is EventType.INSIDER_BUY
    assert event_type_for("filing", "8-K: 8-K (items 2.02,9.01)") is EventType.EARNINGS_RESULT
    assert (
        event_type_for("announcement", "Quarterly report for the period ended 30 June 2026")
        is EventType.EARNINGS_RESULT
    )
    assert (
        event_type_for("announcement", "Proposed final dividend of 32 sen")
        is EventType.DIVIDEND_CHANGE
    )
    assert event_type_for("announcement", "Changes in Sub. S-hldr's Int (29B)") is None
    assert event_type_for("insider_filing", "4: FORM 4") is None
    assert event_type_for("filing", "SC 13G: Vanguard") is None


def test_a5_reads_collected_events_and_skips_what_it_cannot_type(ctx, book):
    a5 = A5CatalystEvents.from_fact_book(ctx, book, ["XNAS:AAPL", "MYX:1155"])
    types = sorted(e.event_type.value for e in a5.events)
    assert types == [
        "earnings_result",
        "earnings_result",
        "earnings_result",
        "insider_buy",
        "insider_buy",
        "insider_buy",
        "insider_sell",
    ]
    assert all(e.citable for e in a5.events), "stored events carry a source and a date"
    window = a5.run("MYX:1155", NOW - timedelta(days=1), NOW)
    assert len(window) == 1 and "earnings_result" in window[0].text
    (soon,) = a5.upcoming("XNAS:AAPL", NOW)
    assert "earnings_result in 25 day(s)" in soon.text and soon.numbers["days_to_event"] == 25.0


def test_an_effective_date_before_the_announcement_is_dropped_not_raised():
    rec = EventRecord(
        "x",
        "e",
        "XNAS:AAPL",
        "earnings_result",
        NOW,
        "results",
        effective_at=NOW - timedelta(days=1),
    )
    ev = event_from_record(rec)
    assert ev is not None and ev.effective_at is None


# --- A6 -----------------------------------------------------------------------------


def test_a6_describes_the_recorded_policy_series_and_labels_a_regime(ctx, book):
    a6 = A6MacroRegime(ctx)
    policy = a6.read_policy(book)
    texts = [f.text for f in policy]
    assert any(t.startswith("Fed funds effective 4.08 %") and "-0.25" in t for t in texts)
    assert any("BNM Overnight Policy Rate 2.75" in t for t in texts)
    assert all("vintage knowable" in f.caveats[0] for f in policy)
    (regime,) = a6.run_from_series(book, "SP500")
    assert (
        regime.text.startswith("regime is ") and "from SP500, 80 recorded levels" in regime.caveats
    )


def test_a6_says_so_when_nothing_is_recorded(ctx, tmp_path):
    with FactBook(tmp_path / "empty.db") as empty:
        (f,) = A6MacroRegime(ctx).read_policy(empty)
    assert "no macro series recorded yet" in f.text


# --- A8 -----------------------------------------------------------------------------


def test_a8_counts_collected_insider_transactions_and_never_reports_zero_short_interest(ctx, book):
    findings = A8OwnershipFlow(ctx).from_fact_book(book, "XNAS:AAPL", NOW)
    kinds = [f.kind for f in findings]
    assert kinds.count("short_interest") == 1
    short = next(f for f in findings if f.kind == "short_interest")
    assert "not collected" in short.text and not short.numbers
    assert any("cluster buying: 3" in f.text for f in findings)
    assert any("1 non-plan insider sales" in f.text for f in findings)
    assert any(
        "buyers in the last 90 days" in f.text and "Levinson Arthur" in f.text for f in findings
    )
