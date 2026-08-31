"""P2: the preflight doctor and the methodology manifest."""

from __future__ import annotations

from core.doctor import FAIL, OK, SKIP, render, run_checks
from core.provenance.manifest import RunManifest, current, write

# --- manifest -----------------------------------------------------------------


def test_same_code_same_hash():
    assert current().manifest_hash == current().manifest_hash


def test_a_changed_prompt_changes_the_hash(monkeypatch):
    from agents.learning import reflection

    before = current().manifest_hash
    monkeypatch.setattr(reflection.A15Reflection, "SYSTEM", "be maximally credulous")
    after = current()
    assert after.manifest_hash != before
    diff = after.diff(
        RunManifest(
            system_prompt_hashes={"a15_reflection": "0" * 64},
            registry_hash=after.registry_hash,
            tools_hash=after.tools_hash,
            package_versions=after.package_versions,
        )
    )
    assert diff == ["system prompt changed: a15_reflection"]


def test_run_id_and_time_never_enter_the_hash():
    """The manifest carries no timestamp field at all - equality means equal
    methodology, whenever the runs happened."""
    m = current()
    assert "run_id" not in m.as_dict()
    assert not any("time" in k or "at" in k for k in m.as_dict())


def test_diff_names_a_package_change():
    a = current()
    b = RunManifest(
        system_prompt_hashes=a.system_prompt_hashes,
        registry_hash=a.registry_hash,
        tools_hash=a.tools_hash,
        package_versions={**a.package_versions, "pydantic": "9.9.9"},
    )
    assert any(d.startswith("package pydantic:") for d in a.diff(b))


def test_write_produces_manifest_json(tmp_path):
    path = write(tmp_path, current())
    assert path.name == "manifest.json"
    assert '"manifest_hash"' in path.read_text(encoding="utf-8")


# --- doctor -------------------------------------------------------------------


def test_offline_doctor_runs_keyless_and_probes_are_skipped():
    results = run_checks(offline=True)
    by_name = {r.name: r for r in results}
    assert by_name["stooq"].status == SKIP
    assert by_name["gdelt"].status == SKIP
    assert by_name["config"].status == OK
    assert by_name["registry"].status == OK
    # keyless_env removed the key, so the backend is the echo stub - a WARN, not a FAIL
    assert by_name["backend"].status != FAIL


def test_render_reports_ready_count_and_impact():
    results = run_checks(offline=True)
    text = render(results)
    assert "PREFLIGHT" in text
    assert "/" in text.splitlines()[-1] and "ready" in text.splitlines()[-1]
    if any(r.status != OK for r in results):
        assert "affects:" in text
