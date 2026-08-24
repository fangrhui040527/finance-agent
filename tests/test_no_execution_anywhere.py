"""Structural guarantee: no order-placement code exists in the repository.

docs/07 section 4, done-ness criterion 9. This is a grep, deliberately - it
catches a broker client someone adds later, which a policy unit test would not.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(
    r"\b(place_order|submit_order|execute_trade|broker\.(buy|sell)|"
    r"alpaca|ib_insync|ccxt\.)\b", re.I,
)
SKIP_DIRS = {".git", ".venv", "docs", "__pycache__", ".pytest_cache", "node_modules"}
# The policy that denies these names, and this test, must both mention them.
ALLOWED_FILES = {"policy.py", "test_no_execution_anywhere.py", "test_guardrail_chain.py",
                 "registry.yaml", "verify.py"}


def test_no_execution_code_in_repo():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".toml"}:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if path.name in ALLOWED_FILES:
            continue
        if FORBIDDEN.search(path.read_text(errors="replace")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"execution-adjacent code found in: {offenders}"
