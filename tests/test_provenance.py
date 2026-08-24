"""P0 DoD: append-only ledger, cost in both currencies, provenance markers."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.contracts.provenance_marker import Author, ProvenanceMarker, is_managed
from core.llm.tiers import TaskClass, Tier, Usage
from core.provenance.ledger import ProvenanceLedger


def test_call_is_recorded_with_both_currencies():
    led = ProvenanceLedger()
    rec = led.record_call(
        "a10", TaskClass.THESIS_SYNTHESIS, Tier.REASON, "claude-opus-5",
        "prompt", Usage(1_000_000, 0),
    )
    assert rec.cost_usd == Decimal("5.00")
    assert rec.cost_myr == Decimal("5.00") * Decimal("4.15")


def test_ledger_rejects_update():
    led = ProvenanceLedger()
    led.record_call("a1", TaskClass.ADHOC_QUERY, Tier.BALANCED, "m", "p", Usage(10, 10))
    with pytest.raises(Exception, match="append-only"):
        led.conn.execute("UPDATE llm_calls SET agent='tamper'")


def test_ledger_rejects_delete():
    led = ProvenanceLedger()
    led.record_call("a1", TaskClass.ADHOC_QUERY, Tier.BALANCED, "m", "p", Usage(10, 10))
    with pytest.raises(Exception, match="append-only"):
        led.conn.execute("DELETE FROM llm_calls")


def test_cost_by_tier_supports_the_monthly_report():
    led = ProvenanceLedger()
    led.record_call("a4", TaskClass.NEWS_TRIAGE, Tier.CHEAP, "m", "p", Usage(1_000_000, 0))
    led.record_call("a10", TaskClass.THESIS_SYNTHESIS, Tier.REASON, "m", "p", Usage(1_000_000, 0))
    by_tier = led.cost_by_tier_myr()
    assert by_tier["reason"] > by_tier["cheap"]


def test_dropped_claims_are_logged_too():
    led = ProvenanceLedger()
    led.record_claim("a9", "made up number", [], survived=False, dropped_reason="no citation")
    rows = led.conn.execute("SELECT survived, dropped_reason FROM claims").fetchall()
    assert rows == [(0, "no citation")]


def test_human_authored_is_never_agent_editable():
    m = ProvenanceMarker(created_by=Author.HUMAN, created_at=datetime.now(timezone.utc))
    assert m.managed is False


def test_agent_created_is_editable_unless_pinned():
    now = datetime.now(timezone.utc)
    assert ProvenanceMarker(created_by=Author.AGENT, created_at=now).managed is True
    assert ProvenanceMarker(created_by=Author.AGENT, created_at=now, pinned=True).managed is False


def test_absent_marker_is_never_adopted_by_default():
    """docs/13 section 2.1: being in the right directory is not provenance."""
    assert is_managed(None) is False
