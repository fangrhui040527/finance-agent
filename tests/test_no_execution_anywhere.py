"""Structural guarantee: no order-placement code exists in the repository.

docs/07 section 4, done-ness criterion 9. This is a grep, deliberately - it
catches a broker client someone adds later, which a policy unit test would not.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(
    r"\b(place_order|submit_order|execute_trade|broker\.(buy|sell)|"
    r"alpaca|ib_insync|ccxt\.)\b",
    re.I,
)
# `debug/` is gitignored runtime output: every trace report RENDERS the denied
# place_order refusal by name (the same reason trace_run.py is allowlisted), so
# the day the scan learned .html it started failing on its own evidence.
SKIP_DIRS = {".git", ".venv", "docs", "debug", "__pycache__", ".pytest_cache", "node_modules"}

# Files that must name the forbidden tools in order to deny them. Exact paths,
# not basenames: a basename allowlist would hand a free pass to any new file
# called policy.py anywhere in the tree.
ALLOWED_PATHS = {
    "core/guardrails/policy.py",  # NoExecutionPolicy, denies by name
    "core/registry/loader.py",  # FORBIDDEN_TOOLS, refuses at load
    "verify.py",
    "trace_run.py",  # traces the refusal, by name
    "tests/test_trace.py",  # asserts that refusal is recorded
    # recent_failures must REPORT a denial by name - same reason as
    # trace_run.py. A monitor that could not name what it stopped would
    # be monitoring nothing.
    "tests/test_observability.py",
    "tests/test_no_execution_anywhere.py",
    # The Trace screen RENDERS the refusal - a denied place_order with
    # meta='no_execution' - which is the same reason trace_run.py is here. A
    # design that showed the guardrail working without naming what it stopped
    # would be showing nothing.
    "design/b5.py",
    "design/Trace.dc.html",
    "tests/test_guardrail_chain.py",
    "tests/test_registry.py",
    "tests/test_agents.py",
    "tests/test_evidence_agents.py",
}


def test_no_execution_code_in_repo():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {
            ".py",
            ".yaml",
            ".yml",
            ".toml",
            ".js",
            ".html",
            ".css",
        }:
            continue
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts):
            continue
        if rel.as_posix() in ALLOWED_PATHS:
            continue
        if FORBIDDEN.search(path.read_text(errors="replace")):
            offenders.append(rel.as_posix())
    assert not offenders, f"execution-adjacent code found in: {offenders}"


def test_the_allowlist_has_not_rotted():
    """Every exemption must still exist and still be denying something. An
    allowlist entry for a deleted file is how the next one slips through."""
    for rel in ALLOWED_PATHS:
        path = ROOT / rel
        assert path.exists(), f"allowlisted {rel} no longer exists; remove the exemption"
        assert FORBIDDEN.search(path.read_text()), (
            f"{rel} no longer mentions a forbidden tool; it does not need an exemption"
        )
