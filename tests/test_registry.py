"""P17: the ratchet. Nothing registers without a suite that can fail it."""
from pathlib import Path

import pytest
import yaml

from core.contracts.provenance_marker import Author
from core.registry.loader import (
    EvalResult, FORBIDDEN_TOOLS, MIN_EVAL_CASES, MIN_NEGATIVE_CASES, RatchetError,
    Registry, RegistryError, check_suite, load, run_suite,
)

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "agents" / "registry.yaml"


def write(tmp_path, agents, knowledge=None, suites=None):
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "evals").mkdir(exist_ok=True)
    for name, cases in (suites or {}).items():
        (tmp_path / "evals" / name).write_text(yaml.safe_dump({"cases": cases}))
    path = tmp_path / "agents" / "registry.yaml"
    path.write_text(yaml.safe_dump(
        {"version": 1, "agents": agents, "knowledge": knowledge or {}}))
    return path


def good_cases():
    return ([{"name": f"c{i}", "expect": "answer"} for i in range(5)]
            + [{"name": "n1", "expect": "refuse", "negative": True},
               {"name": "n2", "expect": "no_position", "negative": True}])


# -- the shipped registry ----------------------------------------------------

def test_the_real_registry_loads_with_the_ratchet_on():
    reg = load(REGISTRY)
    assert len(reg.agents) == 16
    assert set(reg.agents) >= {"a0_supervisor", "a9_attribution", "a15_reflection"}


def test_every_registered_agent_has_a_suite_with_negatives():
    reg = load(REGISTRY, enforce_ratchet=False)
    for spec in reg.agents.values():
        suite = check_suite(ROOT / spec.eval_suite, spec.id)
        assert len(suite["cases"]) >= MIN_EVAL_CASES


def test_no_registered_agent_may_write_to_a_human_store():
    reg = load(REGISTRY)
    for agent_id in reg.agents:
        assert not reg.may_write(agent_id, "kb_craft")
        assert not reg.may_write(agent_id, "kb_filings")


def test_the_lessons_store_is_the_agent_writable_one():
    reg = load(REGISTRY)
    assert reg.may_write("a15_reflection", "kb_lessons")


def test_the_registry_contains_no_execution_tool():
    reg = load(REGISTRY)
    for spec in reg.agents.values():
        assert not set(spec.tools) & FORBIDDEN_TOOLS


# -- the ratchet -------------------------------------------------------------

def test_an_agent_without_a_suite_cannot_register(tmp_path):
    path = write(tmp_path, [{"id": "a99", "tools": ["x"], "eval_suite": "evals/missing.yaml"}])
    with pytest.raises(RatchetError, match="has no eval suite"):
        load(path, evals_root=tmp_path)


def test_a_suite_with_no_negatives_measures_enthusiasm_not_skill(tmp_path):
    cases = [{"name": f"c{i}", "expect": "answer"} for i in range(6)]
    path = write(tmp_path, [{"id": "a99", "tools": ["x"], "eval_suite": "evals/a99.yaml"}],
                 suites={"a99.yaml": cases})
    with pytest.raises(RatchetError, match="measures enthusiasm"):
        load(path, evals_root=tmp_path)


def test_a_thin_suite_cannot_register(tmp_path):
    cases = [{"name": "c1", "expect": "refuse", "negative": True},
             {"name": "c2", "expect": "refuse", "negative": True}]
    path = write(tmp_path, [{"id": "a99", "tools": ["x"], "eval_suite": "evals/a99.yaml"}],
                 suites={"a99.yaml": cases})
    with pytest.raises(RatchetError, match="minimum is"):
        load(path, evals_root=tmp_path)


def test_a_declared_execution_tool_is_refused_at_load(tmp_path):
    path = write(tmp_path,
                 [{"id": "a99", "tools": ["retrieve", "place_order"],
                   "eval_suite": "evals/a99.yaml"}],
                 suites={"a99.yaml": good_cases()})
    with pytest.raises(RegistryError, match="no broker connection by design"):
        load(path, evals_root=tmp_path)


def test_human_knowledge_cannot_be_declared_managed(tmp_path):
    path = write(tmp_path, [{"id": "a99", "tools": ["x"], "eval_suite": "evals/a99.yaml"}],
                 knowledge={"kb_book": {"created_by": "human", "managed": True}},
                 suites={"a99.yaml": good_cases()})
    with pytest.raises(RegistryError, match="never agent-editable"):
        load(path, evals_root=tmp_path)


def test_duplicate_agent_ids_are_refused(tmp_path):
    entry = {"id": "a99", "tools": ["x"], "eval_suite": "evals/a99.yaml"}
    path = write(tmp_path, [entry, dict(entry)], suites={"a99.yaml": good_cases()})
    with pytest.raises(RegistryError, match="duplicate"):
        load(path, evals_root=tmp_path)


def test_an_unregistered_agent_gets_a_useful_error():
    reg = load(REGISTRY)
    with pytest.raises(RegistryError, match="not a code change here"):
        reg.agent("a99_rogue")


# -- running a suite ---------------------------------------------------------

def test_a_near_miss_failure_disqualifies_however_high_the_headline_rate(tmp_path):
    cases = ([{"name": f"c{i}", "expect": "answer"} for i in range(9)]
             + [{"name": "n1", "expect": "refuse", "negative": True, "near_miss": True},
                {"name": "n2", "expect": "refuse", "negative": True}])
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"cases": cases}))
    r = run_suite(tmp_path / "s.yaml", "a99",
                  lambda c: "answer" if c["expect"] == "answer" else "answer")
    assert r.rate > 0.8
    assert r.near_miss_failed == 1
    assert not r.ok(), "a high pass rate cannot buy a near-miss failure"


def test_a_clean_run_passes(tmp_path):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"cases": good_cases()}))
    r = run_suite(tmp_path / "s.yaml", "a99", lambda c: c["expect"])
    assert r.ok() and r.failed == 0


def test_a_crash_is_scored_as_a_failure_not_an_error(tmp_path):
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"cases": good_cases()}))

    def boom(case):
        raise RuntimeError("kaboom")

    r = run_suite(tmp_path / "s.yaml", "a99", boom)
    assert r.failed == r.total
    assert "RuntimeError" in r.details[0][2]


def test_the_allowlist_derived_from_the_registry_permits_every_real_call():
    """The registry is load-bearing, not decorative.

    Regression: the registry named A3's tools trend_state/volatility while the
    code guarded ohlcv/atr. An allowlist built from the registry - the whole
    point of a capability registry - denied the agent's own first call.
    """
    import random
    from datetime import date, datetime, timedelta, timezone

    from agents.base import AgentContext
    from agents.evidence.agents import A3PriceTechnical, A6MacroRegime
    from core.guardrails.defaults import default_engine
    from core.market.prices import Bar, PriceSeries
    from knowledge.retrieval.pipeline import Router

    reg = load(REGISTRY)
    ctx = AgentContext(router=Router({}), engine=default_engine(reg.allowlist()),
                       now=datetime(2026, 8, 25, tzinfo=timezone.utc))

    rng = random.Random(4)
    bars, px, d = [], 10.0, date(2026, 5, 1)
    for i in range(60):
        px *= 1 + rng.gauss(0, 0.01)
        bars.append(Bar(d + timedelta(days=i), px, px * 1.01, px * 0.99, px, 100_000))

    assert A3PriceTechnical(ctx).run(PriceSeries("X", bars))
    assert A6MacroRegime(ctx).run([rng.gauss(0, 0.01) for _ in range(120)])


def test_the_allowlist_still_denies_what_is_not_registered():
    from agents.base import AgentContext
    from agents.evidence.agents import A3PriceTechnical
    from core.guardrails.defaults import default_engine
    from knowledge.retrieval.pipeline import Router

    reg = load(REGISTRY)
    ctx = AgentContext(router=Router({}), engine=default_engine(reg.allowlist()))
    agent = object.__new__(A3PriceTechnical)
    agent.ctx = ctx
    with pytest.raises(Exception):
        agent._guard_tool("reverse_dcf")
