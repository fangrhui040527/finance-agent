"""Structural guarantee: no test hardcodes a POSIX-only scratch directory.

Windows has no `/tmp`, so `Path("/tmp/x.toml").write_text(...)` raises
FileNotFoundError there and passes everywhere else - which is exactly how it
reaches CI unnoticed. This is a grep rather than a unit test for the same
reason test_no_execution_anywhere is: it catches the next one somebody writes,
which no amount of reviewing the current ones would.

pytest's `tmp_path` fixture is the portable replacement, and every writer in
the suite already uses it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A POSIX absolute scratch path inside a string literal. Anchored on the quote
# so `if path == "/tmp"` in a comment about portability does not trip it, and
# so a Windows-safe relative "tmp/..." is left alone.
POSIX_SCRATCH = re.compile(r"""["'](?:/tmp|/var/tmp|/var/folders)[/"']""")
SKIP_DIRS = {".git", ".venv", "docs", "debug", "__pycache__", ".pytest_cache", "node_modules"}

# This file must spell the pattern out in order to forbid it.
ALLOWED_PATHS = {"tests/test_paths_are_portable.py"}


def test_no_hardcoded_posix_scratch_paths():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts):
            continue
        if rel.as_posix() in ALLOWED_PATHS:
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if POSIX_SCRATCH.search(line):
                offenders.append(f"{rel.as_posix()}:{n}")
    assert not offenders, (
        f"hardcoded POSIX scratch path (breaks on Windows; use tmp_path): {offenders}"
    )
