"""The tools that let a model watch this system run.

Two properties matter more than the formatting: absent evidence is reported
as absent (an empty ledger is "nothing has run", never a clean bill of
health), and no tool returns prompt text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.llm.tiers import TaskClass, Tier, Usage
from core.provenance.ledger import ProvenanceLedger
from mcp_server import observability as O
from mcp_server.protocol import ToolError

TOOLS = ("system_health", "operating_report", "recent_failures", "run_anatomy")


# --- registration ----------------------------------------------------------------


def test_all_four_are_on_the_wire():
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert set(TOOLS) <= names


def test_the_instructions_tell_the_model_when_to_use_them():
    from mcp_server.server import INSTRUCTIONS

    assert "system_health" in INSTRUCTIONS and "recent_failures" in INSTRUCTIONS


def test_none_of_them_can_change_state():
    """Read-only by construction: no observability tool takes a write path."""
    import inspect

    src = inspect.getsource(O)
    for verb in ("record_call", "record_claim", "INSERT", "UPDATE ", "DELETE "):
        assert verb not in src, f"{verb} appears in a read-only module"


# --- honesty on an empty installation --------------------------------------------


def test_operating_report_on_an_empty_ledger_says_nothing_ran(tmp_path):
    out = O.operating_report(days=7, db=str(tmp_path / "empty.db"))
    assert "No model calls" in out
    assert "not a clean bill of health" in out


def test_recent_failures_with_no_runs_says_so(tmp_path):
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(tmp_path / "nope"))
    assert "No traced runs" in out
    assert "not the same as nothing wrong" in out


def test_run_anatomy_with_no_runs_says_so(tmp_path):
    assert "No traced runs" in O.run_anatomy(root=str(tmp_path / "nope"))


def test_bad_windows_are_refused(tmp_path):
    with pytest.raises(ToolError, match="days must be at least 1"):
        O.operating_report(days=0, db=str(tmp_path / "e.db"))
    with pytest.raises(ToolError, match="runs must be at least 1"):
        O.recent_failures(runs=0, db=str(tmp_path / "e.db"))


# --- the operating report over real ledger rows ----------------------------------


def _ledger_with_calls(path: Path) -> ProvenanceLedger:
    led = ProvenanceLedger(path)
    for tier, model, latency in (
        (Tier.CHEAP, "claude-haiku-4-5", 100.0),
        (Tier.CHEAP, "claude-haiku-4-5", 200.0),
        (Tier.REASON, "claude-opus-5", 3000.0),
    ):
        led.record_call(
            agent="a10_thesis",
            task_class=TaskClass.THESIS_SYNTHESIS,
            tier=tier,
            model_id=model,
            prompt="p",
            usage=Usage(input_tokens=100, output_tokens=50, cached_input_tokens=10),
            fx_rate=Decimal("4.15"),
            latency_ms=latency,
        )
    return led


def test_operating_report_shows_spend_models_latency_and_cache(tmp_path):
    _ledger_with_calls(tmp_path / "l.db").close()
    out = O.operating_report(days=7, db=str(tmp_path / "l.db"))
    assert "model calls        3" in out
    assert "claude-haiku-4-5" in out and "claude-opus-5" in out
    assert "p50" in out and "p95" in out
    assert "30 read" in out  # 3 calls x 10 cached tokens
    assert "budget" in out


def test_the_report_names_dropped_claims_as_the_quality_signal(tmp_path):
    led = _ledger_with_calls(tmp_path / "l.db")
    led.record_claim("a10", "NIM improved", [], survived=True)
    led.record_claim("a10", "NIM improved 400bps", [], survived=False, dropped_reason="no citation")
    led.close()
    out = O.operating_report(days=7, db=str(tmp_path / "l.db"))
    assert "2 claim(s) checked, 1 dropped (50%)" in out
    assert "no citation" in out


def test_the_cap_is_disclosed_in_the_report(tmp_path, monkeypatch):
    _ledger_with_calls(tmp_path / "l.db").close()
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    assert "FINPLANET_CHEAP=1 is in force" in O.operating_report(days=7, db=str(tmp_path / "l.db"))


# --- failures across traced runs --------------------------------------------------


def _fake_run(root: Path, name: str, events: list[dict], manifest: dict | None = None) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (run / "summary.json").write_text(
        json.dumps({"run_id": name, "label": "test", "events": len(events), "wall_ms": 12.0}),
        encoding="utf-8",
    )
    if manifest is not None:
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def test_failures_report_errors_denials_and_the_run_to_open(tmp_path):
    root = tmp_path / "debug"
    _fake_run(
        root,
        "20260830T100000-aaaaaa",
        [
            {
                "kind": "error",
                "name": "a9_attribution",
                "error": "ZeroDivisionError: division by zero",
            },
            {"kind": "denied", "name": "place_order", "data": {"reason": "one-way door"}},
            {"kind": "refusal", "name": "a0_supervisor"},
        ],
    )
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(root))
    assert "20260830T100000-aaaaaa" in out
    assert "ZeroDivisionError" in out
    assert "denied 1" in out and "place_order" in out
    assert "refusals 1 (an answer, not a fault)" in out
    assert "1 error(s)" in out


def test_a_clean_run_is_named_clean_but_the_scope_is_stated(tmp_path):
    root = tmp_path / "debug"
    _fake_run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "ok"}])
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(root))
    assert "clean" in out
    assert "an untraced run cannot be reported on" in out


def test_failures_never_return_prompt_text(tmp_path):
    root = tmp_path / "debug"
    _fake_run(
        root,
        "20260830T100000-aaaaaa",
        [
            {
                "kind": "llm_call",
                "name": "a10_thesis",
                "data": {"prompt": "SECRET-PORTFOLIO-DETAIL", "response": "SECRET-ANSWER"},
            }
        ],
    )
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(root))
    assert "SECRET-PORTFOLIO-DETAIL" not in out
    assert "SECRET-ANSWER" not in out
    assert "Verbatim prompts and responses are NOT returned" in out


# --- one run, and the methodology question ----------------------------------------

MANIFEST_A = {
    "manifest_hash": "a" * 64,
    "system_prompt_hashes": {"a15_reflection": "1" * 64},
    "registry_hash": "r1",
    "tools_hash": "t1",
    "package_versions": {"anthropic": "1.2.0"},
}
MANIFEST_B = {
    "manifest_hash": "b" * 64,
    "system_prompt_hashes": {"a15_reflection": "2" * 64},
    "registry_hash": "r1",
    "tools_hash": "t2",
    "package_versions": {"anthropic": "1.3.0"},
}


def test_anatomy_reports_an_unchanged_method_as_a_data_difference(tmp_path):
    root = tmp_path / "debug"
    _fake_run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "x"}], MANIFEST_A)
    _fake_run(root, "20260831T100000-bbbbbb", [{"kind": "span", "name": "x"}], MANIFEST_A)
    out = O.run_anatomy(root=str(root))
    assert "unchanged since 20260830T100000-aaaaaa" in out
    assert "came from the DATA, not the method" in out


def test_anatomy_names_what_changed_in_the_method(tmp_path):
    root = tmp_path / "debug"
    _fake_run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "x"}], MANIFEST_A)
    _fake_run(root, "20260831T100000-bbbbbb", [{"kind": "span", "name": "x"}], MANIFEST_B)
    out = O.run_anatomy(root=str(root))
    assert "CHANGED since 20260830T100000-aaaaaa" in out
    assert "system prompt changed: a15_reflection" in out
    assert "tool surface changed" in out
    assert "package anthropic: 1.2.0 -> 1.3.0" in out


def test_anatomy_takes_a_named_run_and_refuses_an_unknown_one(tmp_path):
    root = tmp_path / "debug"
    _fake_run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "x"}])
    assert "20260830T100000-aaaaaa" in O.run_anatomy("20260830T100000-aaaaaa", root=str(root))
    with pytest.raises(ToolError, match="no run"):
        O.run_anatomy("20260101T000000-zzzzzz", root=str(root))


def test_anatomy_run_ids_cannot_traverse(tmp_path):
    root = tmp_path / "debug"
    _fake_run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "x"}])
    with pytest.raises(ToolError):
        O.run_anatomy("../../etc", root=str(root))


# --- health ------------------------------------------------------------------------


def test_system_health_runs_offline_and_states_a_verdict():
    out = O.system_health(offline=True)
    assert "PREFLIGHT" in out
    assert any(v in out for v in ("BLOCKED:", "USABLE, DEGRADED:", "Every check passed."))


# --- the claims reader this rests on ------------------------------------------------


def test_claims_between_reads_what_was_only_ever_written(tmp_path):
    led = ProvenanceLedger(tmp_path / "l.db")
    led.record_claim("a10", "kept", [], survived=True)
    led.record_claim("a10", "dropped", [], survived=False, dropped_reason="no chunk")
    now = datetime.now(UTC)
    rows = led.claims_between(now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(rows) == 2
    assert [bool(r["survived"]) for r in rows] == [True, False]
    assert rows[1]["dropped_reason"] == "no chunk"
