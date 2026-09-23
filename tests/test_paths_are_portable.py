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
# `.claude/` holds nested worktrees: a second copy of this whole tree, whose
# copy of this file is not on the allowlist below.
SKIP_DIRS = {
    ".git",
    ".venv",
    ".claude",
    "docs",
    "debug",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
}

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
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if POSIX_SCRATCH.search(line):
                offenders.append(f"{rel.as_posix()}:{n}")
    assert not offenders, (
        f"hardcoded POSIX scratch path (breaks on Windows; use tmp_path): {offenders}"
    )


# --- text files are UTF-8, and every read or write says so ------------------------------

TEXT_CALL = ".read_text(", ".write_text("


def _calls_without_encoding(source: str) -> list[int]:
    """Line numbers of read_text/write_text calls whose argument list names no encoding."""
    offenders = []
    for needle in TEXT_CALL:
        i = 0
        while (j := source.find(needle, i)) >= 0:
            k = j + len(needle)
            if j and source[j - 1] in "\"'":
                i = k  # a pattern inside a string literal, not a call
                continue
            depth, m = 1, k
            while m < len(source) and depth:
                c = source[m]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                elif c in "\"'":
                    q = c
                    m += 1
                    while m < len(source) and source[m] != q:
                        m += 2 if source[m] == "\\" else 1
                m += 1
            if "encoding=" not in source[k : m - 1]:
                offenders.append(source.count("\n", 0, j) + 1)
            i = m
    return sorted(offenders)


def test_every_text_read_and_write_names_utf8():
    """Windows opens text files as cp1252 unless told otherwise. config.toml, the
    runbook and the QA notes carry Chinese (the sites they name are Chinese), so
    a bare `read_text()` passes on every Linux runner and fails on the Windows
    one - which is how the 2026-09-05 branch went red on Windows only. TOML is
    UTF-8 by specification; so is every text file this repository writes."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts) or rel.as_posix() in ALLOWED_PATHS:
            continue  # this file spells the bare call out in its own docstring
        for n in _calls_without_encoding(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(f"{rel.as_posix()}:{n}")
    assert not offenders, f"read_text/write_text without encoding='utf-8': {offenders}"
