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


def _commit_step() -> dict:
    steps = _workflow()["jobs"]["sweep"]["steps"]
    return next(s for s in steps if s.get("name") == "Commit what was collected")


def test_the_corpus_is_split_into_parts_before_anything_is_staged():
    """GitHub refuses a push holding a file over 100 MiB, and the corpus grows
    about half a megabyte a day. Its parts (knowledge/stores.py) are committed
    beside it; a refused split puts data/stores back and the rest still lands."""
    run = _commit_step()["run"]
    split = run.index("ask.py stores split")
    assert split < run.index("for path in")
    refused = run[split:].split("fi", 1)[0]
    assert "git checkout -- data/stores" in refused and "split_failed=1" in refused
    added = run.split("for path in", 1)[1].split(";", 1)[0].split()
    # The whole file stays committed beside its parts until they have been
    # shown to reassemble it on real runs; the graph too, until it is rebuilt.
    assert {"data/stores", "data/corpus.db", "data/graph.db"} <= set(added)


def test_a_store_at_the_size_github_refuses_is_left_out_and_the_rest_still_lands():
    run = _commit_step()["run"]
    sizes = run.split("too_big=0", 1)[1].split('echo "too_big=', 1)[0]
    assert "99614720" in sizes and "47185920" in sizes  # 95 and 45 MiB
    assert 'git reset -q -- "$f"' in sizes.split("99614720", 1)[1].split("elif", 1)[0]
    assert run.index("too_big=0") < run.index("git commit -m")


def test_a_run_that_cannot_push_leaves_its_commit_on_a_rescue_branch():
    """The runner is thrown away with everything on it. Until 2026-10-22 a run
    that gave up left the day's collection there."""
    run = _commit_step()["run"]
    after = run.split('ours="$(git rev-parse HEAD)"', 1)[1]
    keep = after.split("keep() {", 1)[1].split("\n          }", 1)[0]
    assert 'git push origin "$ours:refs/heads/$rescue"' in keep
    # A re-run keeps the run id; the attempt keeps its branch from colliding.
    assert 'rescue="rescue/collect-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT' in after
    # Every way out but a push reaches it: a failing fetch or reset exits the
    # step under `bash -e`, so the trap is set before any of them can.
    assert after.index("trap keep ERR") < after.index("git pull --rebase")
    assert "trap 'keep; exit 1' INT TERM" in after  # cancelled, or out of time
    gave_up = run.split("for attempt in 1 2 3", 1)[1].split("done", 1)[1]
    assert gave_up.split("keep", 1)[1].strip().endswith("exit 1")
    # A push rejected after a clean rebase falls through to the retry loop
    # instead of ending the step with the commit on the runner.
    rebased = run.split('if git pull --rebase --autostash origin "${GITHUB_REF_NAME}"; then', 1)[1]
    assert rebased.lstrip().startswith("if git push; then")


def test_a_store_not_published_fails_the_job_after_the_commit():
    steps = _workflow()["jobs"]["sweep"]["steps"]
    names = [s.get("name", "") for s in steps]
    fail = names.index("Fail if a store was not published")
    assert names.index("Commit what was collected") < fail
    # Last: a failing step skips every step after it, and the degraded warning
    # and the fx check must still run.
    assert fail == len(steps) - 1
    cond = steps[fail]["if"]
    assert "steps.commit.outputs.split_failed" in cond and "steps.commit.outputs.too_big" in cond
    assert _commit_step()["id"] == "commit"


def test_ci_proves_the_committed_parts_reassemble_the_committed_corpus():
    ci = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    runs = [str(s.get("run", "")) for s in ci["jobs"]["test"]["steps"]]
    verify = next(i for i, r in enumerate(runs) if "ask.py stores verify --whole" in r)
    # Before the tests: one that opens the corpus applies a new migration to the
    # checked-out file, and this compares the committed bytes.
    assert verify < next(i for i, r in enumerate(runs) if "pytest" in r)
    assert any(r.count("ask.py stores graph --out") == 2 for r in runs)


def test_web_evidence_runs_after_the_prices_and_marks_before_the_digest_with_the_key_alone():
    """The trigger is measured from the bars the price step cached, and its rows
    must be committed with the sweep, so it sits between the two. The key goes
    to this step and to the ALL_SECRET reader, nowhere else; and the step can
    never cost the run its commit."""
    steps = _workflow()["jobs"]["sweep"]["steps"]
    names = [s.get("name", "") for s in steps]
    ev = names.index("Search the web for unexplained moves")
    assert names.index("Mark the paper book") < ev < names.index("Write the digest")
    assert ev < names.index("Commit what was collected")
    step = steps[ev]
    assert "ask.py evidence" in step["run"]
    assert step.get("continue-on-error") is True and step.get("timeout-minutes")
    assert step["run"].lstrip().startswith("set +e") and step["run"].rstrip().endswith("exit 0")
    assert "steps.sweep.outputs.code" in step["if"]
    assert step["env"]["FINPLANET_OFFLINE"] == "1"
    with_key = [s.get("name") for s in steps if "ANTHROPIC_API_KEY" in (s.get("env") or {})]
    assert set(with_key) == {
        "Keys from ALL_SECRET, when the named secrets are not set",
        "Search the web for unexplained moves",
    }
