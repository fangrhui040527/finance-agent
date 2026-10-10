"""The readers see what the writer wrote, and the manifest covers every prompt and model.

Each test fails on the code before 2026-10-08:

  * `Tracer.span` puts a raised exception's message in the event's `data`,
    and the event's own `error` field is null on disk. `recent_failures`,
    `run_anatomy` and the monitor read only the top level, so every real
    failure printed "None" - the tests passed because they hand-wrote the
    JSON with a top-level `error`;
  * the manifest hashed one system prompt, the reflection agent's, so an
    edit to the a10 narrative prompt left the hash unchanged and
    `run_anatomy` called the changed thesis a data difference;
  * the manifest held no model selection, so a run pinned to the cheapest
    model and an unpinned run had the same hash.
"""

from __future__ import annotations

import json

import pytest

from agents.synthesis import narrate
from core.monitor import _trace_rules
from core.provenance import manifest as M
from core.trace.tracer import Tracer
from mcp_server import observability as O

MESSAGE = "feed refused: HTTP 503 from the upstream"


def _failed_run(root):
    t = Tracer(label="test", root=root)
    with pytest.raises(RuntimeError), t.span("a3_fetch"):
        raise RuntimeError(MESSAGE)
    t.close()
    return t.run_id


def test_a_real_tracer_failure_keeps_its_message_on_disk_where_the_reader_looks(tmp_path):
    run_id = _failed_run(tmp_path)
    events = O._events(tmp_path / run_id)
    (err,) = [e for e in events if e.get("kind") == "error"]
    assert err.get("error") is None, "the writer still puts the text in data, not the top level"
    assert MESSAGE in str((err.get("data") or {}).get("error"))


def test_recent_failures_prints_the_message_not_none(tmp_path):
    run_id = _failed_run(tmp_path)
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(tmp_path))
    assert run_id in out and "1 error(s)" in out
    assert MESSAGE in out
    assert "a3_fetch: None" not in out


def test_run_anatomy_prints_the_message_not_none(tmp_path):
    run_id = _failed_run(tmp_path)
    out = O.run_anatomy(run_id, root=str(tmp_path))
    assert MESSAGE in out
    assert "a3_fetch: None" not in out


def test_the_monitor_alert_quotes_the_message(tmp_path):
    _failed_run(tmp_path)
    (alert,) = [a for a in _trace_rules(str(tmp_path)) if a.rule == "run_errors"]
    assert MESSAGE in alert.detail


def test_a_guardrail_denial_is_not_counted_as_an_error(tmp_path):
    t = Tracer(label="test", root=tmp_path)
    t.emit("denied", "delete_corpus", reason="the corpus is append-only")
    t.close()
    out = O.recent_failures(runs=5, db=str(tmp_path / "e.db"), root=str(tmp_path))
    assert "denied 1" in out
    assert "error(s)" not in out or "0 error(s)" in out
    assert not [a for a in _trace_rules(str(tmp_path)) if a.rule == "run_errors"]


def test_the_manifest_hashes_the_narrative_prompt(monkeypatch):
    before = M.current()
    assert before.system_prompt_hashes["a10_narrate"] == M._sha(narrate.NARRATE_SYSTEM)
    monkeypatch.setattr(narrate, "NARRATE_SYSTEM", narrate.NARRATE_SYSTEM + " Be brief.")
    after = M.current()
    assert after.manifest_hash != before.manifest_hash
    assert "system prompt changed: a10_narrate" in before.diff(after)


def test_the_manifest_names_the_model_selection_and_a_pin_changes_it(monkeypatch):
    for var in ("FINPLANET_MODEL", "FINPLANET_CHEAP", "FINPLANET_EFFORT", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    free = M.current()
    assert free.model_selection["pin"] == ""
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    pinned = M.current()
    assert pinned.model_selection["pin"] == "FINPLANET_CHEAP"
    assert pinned.manifest_hash != free.manifest_hash
    changes = free.diff(pinned)
    assert any(c.startswith("model selection changed: pin") for c in changes)
    assert any(c.startswith("model selection changed: tier:") for c in changes)
    blob = json.dumps(pinned.as_dict())
    assert "sk-" not in blob and "KEY" not in blob, "names, never secrets"


def test_a_stored_manifest_without_models_keeps_its_hash():
    m = M.RunManifest(
        system_prompt_hashes={"a15_reflection": "abc"},
        registry_hash="r",
        tools_hash="t",
        package_versions={"numpy": "2.0"},
    )
    old = json.dumps(
        {
            "system_prompts": {"a15_reflection": "abc"},
            "registry": "r",
            "tools": "t",
            "packages": {"numpy": "2.0"},
        },
        sort_keys=True,
    )
    assert m.manifest_hash == M._sha(old)


def test_anatomy_diffs_a_model_change_between_stored_manifests():
    a = M.RunManifest({"p": "1"}, "r", "t", {}, {"pin": ""}).as_dict()
    b = M.RunManifest({"p": "1"}, "r", "t", {}, {"pin": "FINPLANET_CHEAP"}).as_dict()
    assert O._diff_manifests(a, b) == ["model selection changed: pin  -> FINPLANET_CHEAP"]
