"""The collector workflow: the properties a run's commit depends on.

`collect.yml` is the only workflow that pushes, and what it pushes is binary
SQLite stores that git cannot merge. So a run has to start from the branch as
it stands when the run starts, and runs have to queue rather than overlap.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "collect.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runs_queue_behind_each_other_and_are_never_cancelled():
    group = _workflow()["concurrency"]
    assert group["group"] == "collect"
    assert group["cancel-in-progress"] is False


def test_a_queued_run_checks_out_the_branch_head_not_the_event_commit():
    """On 2026-09-23 a bursa_close run dispatched behind us_close checked out
    dc6e822, the commit its event carried, collected for two minutes, and could
    not commit: us_close had pushed new copies of the same stores meanwhile."""
    steps = _workflow()["jobs"]["sweep"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("ref") == "${{ github.ref_name }}"
