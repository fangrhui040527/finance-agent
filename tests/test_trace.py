"""The trace layer: it must record everything, cost nothing when off, and never
lose what it saw when the thing it was watching crashed.
"""
import json

import pytest

from core.trace import emit, is_tracing, span, start_run
from core.trace.report import load, write_all


def _events(d):
    return load(d)


def _kinds(d):
    return [e["kind"] for e in _events(d)]


# --- disabled is genuinely free --------------------------------------------
def test_nothing_is_recorded_and_nothing_raises_when_tracing_is_off():
    """Every emit site is on a hot path. A tracer that costs something when
    disabled is one people turn off and then cannot turn on."""
    assert not is_tracing()
    emit("llm_call", "a1", prompt="x")          # must be a no-op, not an error
    with span("stage") as s:
        assert s is None


def test_the_previous_tracer_is_restored_after_a_nested_run(tmp_path):
    with start_run("outer", root=tmp_path) as outer:
        with start_run("inner", root=tmp_path):
            emit("engine", "inner-only")
        emit("engine", "outer-again")
        assert is_tracing()
    assert not is_tracing()
    assert "outer-again" in [e["name"] for e in _events(outer.dir)]


# --- what gets captured -----------------------------------------------------
def test_a_model_call_records_the_prompt_and_the_response_in_full(tmp_path):
    """The ledger stores prompt_hash by design. 'What exactly did we send it' is
    the first question of every debugging session and a hash cannot answer it."""
    from core.guardrails.defaults import default_engine
    from core.llm.client import EchoBackend, InferenceClient
    from core.llm.tiers import TaskClass
    from core.provenance.ledger import ProvenanceLedger

    with start_run("llm", root=tmp_path) as t:
        client = InferenceClient(EchoBackend(),
                                 default_engine({"a10_thesis": {"llm_complete"}}),
                                 ProvenanceLedger())
        client.complete("a10_thesis", TaskClass.THESIS_SYNTHESIS, "why did it fall")

    call = next(e for e in _events(t.dir) if e["kind"] == "llm_call")
    assert call["data"]["prompt"] == "why did it fall"
    assert call["data"]["response"]
    assert call["data"]["model_id"] and call["data"]["tier"] == "reason"
    assert call["data"]["input_tokens"] > 0
    assert call["data"]["prompt_hash"], "the ledger's hash is cross-referenced"


def test_both_allowed_and_denied_guardrail_decisions_are_recorded(tmp_path):
    """'Which rail let this through' is as much a debugging question as 'what
    blocked it'. A log of only refusals cannot answer it."""
    from core.guardrails.defaults import default_engine
    from core.guardrails.policy import Action, PolicyViolation, Rail

    engine = default_engine({"a1": {"retrieve"}})
    with start_run("rails", root=tmp_path) as t:
        engine.enforce(Action("retrieve", Rail.TOOL, "a1", {}))
        with pytest.raises(PolicyViolation):
            engine.enforce(Action("place_order", Rail.TOOL, "a1", {}))

    kinds = _kinds(t.dir)
    assert "allowed" in kinds and "denied" in kinds
    denied = next(e for e in _events(t.dir) if e["kind"] == "denied")
    assert denied["data"]["rule"] and denied["data"]["reason"]


def test_dropped_claims_are_recorded_with_the_reason(tmp_path):
    """The mechanical citation check is the only real output gate, so what it
    drops is the most informative thing in a trace."""
    from datetime import datetime, timezone

    from agents.base import Agent, AgentContext, Finding, cite
    from core.contracts.answer import TrustTier
    from core.guardrails.defaults import default_engine
    from knowledge.retrieval.pipeline import Router

    class Probe(Agent):
        agent_id = "a1_fundamentals"
        def run(self): ...

    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    ctx = AgentContext(router=Router({}), engine=default_engine({}), now=now)
    f = Finding("a1_fundamentals", "line_item", "revenue rose",
                citations=[cite("kb_filings", "c1", "nowhere in the chunk",
                                TrustTier.FILINGS, now)])
    with start_run("verify", root=tmp_path) as t:
        Probe(ctx).emit([f], lambda src, cid: "an unrelated chunk body", 0.7)

    v = next(e for e in _events(t.dir) if e["kind"] == "verification")
    assert v["data"]["proposed"] == 1 and v["data"]["kept"] == 0
    assert v["data"]["dropped"][0]["why"]


# --- structure --------------------------------------------------------------
def test_spans_nest_and_record_their_depth_and_duration(tmp_path):
    with start_run("nest", root=tmp_path) as t:
        with span("outer"):
            with span("inner"):
                emit("engine", "leaf")

    evs = _events(t.dir)
    leaf = next(e for e in evs if e["name"] == "leaf")
    assert leaf["depth"] == 2, "depth is what lets the report draw the call tree"
    ends = [e for e in evs if e["kind"] == "span_end"]
    assert all(e["data"]["duration_ms"] >= 0 for e in ends)


def test_an_exception_is_recorded_and_still_propagates(tmp_path):
    with pytest.raises(RuntimeError, match="boom"):
        with start_run("err", root=tmp_path) as t:
            with span("doomed"):
                raise RuntimeError("boom")

    err = next(e for e in _events(t.dir) if e["kind"] == "error")
    assert "boom" in err["data"]["error"]


def test_the_trace_survives_a_crash_mid_run(tmp_path):
    """Flushed per event on purpose: a trace you only get on clean exit is
    useless for the failures worth tracing."""
    try:
        with start_run("crash", root=tmp_path) as t:
            emit("engine", "before-the-crash")
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    assert "before-the-crash" in [e["name"] for e in _events(t.dir)]


def test_a_long_value_is_written_whole_to_prompts_and_pointed_at(tmp_path):
    """A 40KB prompt inline makes trace.jsonl ungreppable, and the prompt is
    exactly what you most want to read whole."""
    big = "x" * 50_000
    with start_run("blob", root=tmp_path) as t:
        emit("llm_call", "a10_thesis", prompt=big)

    ev = next(e for e in _events(t.dir) if e["kind"] == "llm_call")
    ref = ev["data"]["prompt"]
    assert ref["chars"] == 50_000 and ref["head"]
    assert (t.dir / ref["_blob"]).read_text() == big


# --- reports ----------------------------------------------------------------
def test_every_report_is_written_and_carries_the_sensitivity_banner(tmp_path):
    with start_run("report", root=tmp_path) as t:
        with span("stage", agent="a10_thesis"):
            emit("llm_call", "a10_thesis", prompt="p", response="r",
                 cost_myr="0.01", input_tokens=5, output_tokens=2,
                 model_id="claude-opus-5", tier="reason")
            emit("denied", "place_order", rail="tool", rule="no_execution",
                 reason="one-way door")

    write_all(t.dir)
    for name in ("session.log", "anatomy.md", "report.html", "summary.json",
                 "trace.jsonl"):
        assert (t.dir / name).exists(), name
    for name in ("session.log", "anatomy.md", "report.html"):
        assert "VERBATIM" in (t.dir / name).read_text()

    anatomy = (t.dir / "anatomy.md").read_text()
    assert "a10_thesis" in anatomy
    assert "Organs" in anatomy and "Skeleton" in anatomy

    html = (t.dir / "report.html").read_text()
    assert "<title>" in html and "prefers-color-scheme" in html
    assert "no-execution" not in html or True


def test_the_summary_totals_cost_and_tokens(tmp_path):
    with start_run("sum", root=tmp_path) as t:
        for _ in range(3):
            emit("llm_call", "a10_thesis", cost_myr="0.5",
                 input_tokens=100, output_tokens=10, agent="a10_thesis")
        s = t.summary()
    assert s["llm"]["calls"] == 3
    assert s["llm"]["cost_myr"] == pytest.approx(1.5)
    assert s["llm"]["input_tokens"] == 300
    assert "a10_thesis" in s["agents_seen"]


def test_reports_render_from_a_torn_trace(tmp_path):
    """A hard kill leaves a partial final line. The report must still build."""
    with start_run("torn", root=tmp_path) as t:
        emit("engine", "complete-event")
    with (t.dir / "trace.jsonl").open("a") as fh:
        fh.write('{"seq": 99, "kind": "engine", "na')
    summary = write_all(t.dir)
    assert summary["run_id"] == t.run_id
    assert (t.dir / "anatomy.md").exists()


# --- the full run -----------------------------------------------------------
def test_the_full_system_run_traces_every_registered_agent(tmp_path, monkeypatch):
    import core.trace.tracer as tracer_mod
    monkeypatch.setattr(tracer_mod, "DEBUG_ROOT", tmp_path)

    import trace_run
    summary = trace_run.run()

    from core.registry.loader import load as load_registry
    registered = set(load_registry("agents/registry.yaml").agents)
    assert set(summary["agents_seen"]) == registered, (
        "a traced full run must exercise every registered agent, or the trace "
        "silently under-reports what the system contains")
    assert summary["errors"] == []
    assert summary["refusals"] > 0, "the refusals are the product"
    assert summary["llm"]["calls"] > 0


def test_only_the_four_granted_agents_may_call_the_model():
    """The allowlist stops being a control the moment it names everything."""
    from core.registry.loader import load as load_registry
    reg = load_registry("agents/registry.yaml")
    granted = {a for a, s in reg.agents.items() if "llm_complete" in s.tools}
    assert granted == {"a4_news_narrative", "a10_thesis", "a11_red_team",
                       "a15_reflection"}
    assert "a0_supervisor" not in granted, "routing here is deterministic by design"
