"""The collector workflow: the properties a run's commit depends on.

`collect.yml` is the only workflow that pushes, and what it pushes is binary
SQLite stores that git cannot merge. So a run has to start from the branch as
it stands when the run starts, and two runs must never work at once.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "collect.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runs_never_overlap_and_the_one_working_is_never_cancelled():
    """One group, so two runs never work at once. `cancel-in-progress: false`
    protects the run already working; it does not make a queue. GitHub holds one
    waiting run per group and a third arrival cancels it, which the catch-up
    (`sweep --due`) recovers. The runbook says so; this pins the part in code."""
    group = _workflow()["concurrency"]
    assert group["group"] == "collect"
    assert group["cancel-in-progress"] is False


def test_the_monitor_runs_after_every_writer_and_its_log_is_committed_with_the_sweep():
    """M9. `watch` diffs against data/alerts.db, and the only thing that ran it
    was the nightly routine, in a fresh clone that commits its pages and not the
    log - so every night started from a store whose newest event was from
    2026-09-23, and an alert unchanged for weeks read NEW every night. The
    collector commits the stores; the log has to go with them, written after
    them, and a run with something open must not fail the job."""
    steps = _workflow()["jobs"]["sweep"]["steps"]
    names = [s.get("name", "") for s in steps]
    watch = next(i for i, s in enumerate(steps) if "ask.py watch" in str(s.get("run", "")))
    commit = names.index("Commit what was collected")
    for writer in ("Sweep", "Cache the day's prices", "Mark the paper book", "Write the digest"):
        assert names.index(writer) < watch < commit, writer
    step = steps[watch]
    # On exactly the commit's condition: a log the run throws away would only
    # make the next watch diff against an older state.
    assert step.get("if") == steps[commit].get("if"), (step.get("if"), steps[commit].get("if"))
    assert step["run"].lstrip().startswith("set +e") and step["run"].rstrip().endswith("exit 0")
    # A watch that could not finish leaves a partial log; it is put back, not
    # committed as the next run's baseline.
    failed = step["run"].split('"$code" -eq 2', 1)[1].split("fi", 1)[0]
    assert "git checkout -- data/alerts.db" in failed
    added = steps[commit]["run"].split("for path in", 1)[1].split(";", 1)[0].split()
    assert "data/alerts.db" in added


def test_a_queued_run_checks_out_the_branch_head_not_the_event_commit():
    """On 2026-09-23 a bursa_close run dispatched behind us_close checked out
    dc6e822, the commit its event carried, collected for two minutes, and could
    not commit: us_close had pushed new copies of the same stores meanwhile."""
    steps = _workflow()["jobs"]["sweep"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("ref") == "${{ github.ref_name }}"
