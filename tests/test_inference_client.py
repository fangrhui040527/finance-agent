"""P0 DoD: no agent picks a tier; every call is guarded and logged."""

from decimal import Decimal

import pytest

from core.guardrails.defaults import default_engine
from core.guardrails.policy import PolicyViolation
from core.llm.client import BudgetExceeded, EchoBackend, InferenceClient
from core.llm.tiers import TaskClass, Tier
from core.provenance.ledger import ProvenanceLedger


def make(budget=None, allow=None):
    allow = {"a1": {"llm_complete"}} if allow is None else allow
    led = ProvenanceLedger()
    return InferenceClient(EchoBackend(), default_engine(allow), led, budget), led


def test_tier_is_derived_not_chosen():
    client, _ = make()
    assert client.complete("a1", TaskClass.NEWS_TRIAGE, "x").tier is Tier.CHEAP
    assert client.complete("a1", TaskClass.RED_TEAM, "x").tier is Tier.REASON


def test_complete_takes_no_tier_argument():
    """Structural guarantee: there is no parameter through which to override."""
    import inspect

    params = set(inspect.signature(InferenceClient.complete).parameters)
    assert "tier" not in params and "model" not in params and "model_id" not in params


def test_every_call_lands_in_the_ledger():
    client, led = make()
    client.complete("a1", TaskClass.ADHOC_QUERY, "hello")
    assert len(list(led.calls())) == 1


def test_unregistered_agent_cannot_call_the_model():
    client, _ = make(allow={})
    with pytest.raises(PolicyViolation):
        client.complete("ghost", TaskClass.ADHOC_QUERY, "x")


def test_budget_exhaustion_raises_rather_than_downgrading():
    client, led = make(budget=Decimal("0.00001"))
    client.complete("a1", TaskClass.RED_TEAM, "x" * 4000)
    with pytest.raises(BudgetExceeded, match="not downgraded silently"):
        client.complete("a1", TaskClass.RED_TEAM, "x")


def test_prompt_hash_recorded_not_prompt_text():
    client, led = make()
    client.complete("a1", TaskClass.ADHOC_QUERY, "secret holdings 1155.KL")
    row = next(led.calls())
    assert "1155" not in row["prompt_hash"] and len(row["prompt_hash"]) == 16
