"""Walk the repository the way the guard tests do, without shelling out to git.

`git grep` and `git ls-files` were used here before. They fail outside a git
working tree - a source tarball, a Docker COPY, a worktree checked out without
history - so a test that depended on them was really testing the presence of
git, not the property it named.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Same set test_no_execution_anywhere.py uses; kept in one place so the two
# walkers cannot drift apart and let a directory through in only one of them.
SKIP_DIRS = frozenset({
    ".git", ".venv", "docs", "__pycache__", ".pytest_cache", "node_modules",
    "debug", "htmlcov", "finance_agent.egg-info",
})


def iter_source_files(root: Path = ROOT, suffixes: tuple[str, ...] = (".py",)) -> Iterator[Path]:
    """Yield tracked-looking source files under root, skipping tool output."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel = path.relative_to(root)
        if SKIP_DIRS & set(rel.parts):
            continue
        yield path
