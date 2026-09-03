"""Phase E: the documents make checkable promises, so they are checked.

`details/10-STATUS-AND-GAPS.md` listed the capital waterfall as "complete" while
no user could reach it, and carried a tool count and a subcommand count that had
both been stale for months. A number in a status table is worth having only if
something fails when it stops being true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = (ROOT / "details/09-RUNBOOK.md").read_text(encoding="utf-8")
STATUS = (ROOT / "details/10-STATUS-AND-GAPS.md").read_text(encoding="utf-8")
GUIDE = (ROOT / "docs/user-guide.html").read_text(encoding="utf-8")


def _subcommands() -> set[str]:
    """Every subcommand the CLI actually exposes, from the parser itself."""
    import argparse

    import ask

    names: set[str] = set()
    real = argparse.ArgumentParser.add_subparsers

    def spy(self, *a, **kw):
        subs = real(self, *a, **kw)
        add = subs.add_parser

        def wrapped(name, *aa, **kk):
            names.add(name)
            return add(name, *aa, **kk)

        subs.add_parser = wrapped
        return subs

    argparse.ArgumentParser.add_subparsers = spy
    try:
        with pytest.raises(SystemExit):
            ask.main(["--help"])
    finally:
        argparse.ArgumentParser.add_subparsers = real
    return names


def test_every_cli_subcommand_is_in_the_runbook():
    missing = sorted(c for c in _subcommands() if f"ask.py {c}" not in RUNBOOK)
    assert not missing, f"undocumented subcommands: {missing}"


def test_the_status_table_counts_the_subcommands_that_exist():
    n = len(_subcommands())
    stated = int(re.search(r"CLI — (\d+) subcommands", STATUS).group(1))
    assert stated == n, f"status says {stated} subcommands, the parser exposes {n}"


def test_the_status_table_counts_the_mcp_tools_that_are_registered():
    from mcp_server.server import S

    n = len(S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"])
    stated = int(re.search(r"MCP server — (\d+) tools", STATUS).group(1))
    assert stated == n, f"status says {stated} tools, the server registers {n}"


@pytest.mark.parametrize(
    "doc",
    ["README.md", "docs/README.md", "details/10-STATUS-AND-GAPS.md"],
)
def test_the_boundary_is_stated_where_a_reader_starts(doc):
    """The one thing a new user most needs to know, and the only claim in these
    documents that the supervisor enforces in code."""
    text = (ROOT / doc).read_text(encoding="utf-8").lower()
    assert "does not pick stocks" in text


def test_the_architecture_diagram_marks_the_screen_unbuilt():
    arch = (ROOT / "docs/01-SYSTEM-ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "CROSS-SECTIONAL SCREEN — NOT BUILT" in arch
    assert "is not built and is not planned" in arch


def test_the_status_note_records_that_the_waterfall_was_unreachable():
    assert "complete, correct, and unreachable" in STATUS
    assert "bypassed by construction" in STATUS


@pytest.mark.parametrize("cmd", ["capital", "allocate", "rebalance"])
def test_the_money_commands_reach_the_user_guide(cmd):
    assert f"ask.py {cmd}" in GUIDE


def test_the_guide_carries_the_two_numbers_a_bursa_user_hits_first():
    """The minimum economic position and the portfolio it implies. Without
    them, "no position" and "no allocation" read as malfunctions."""
    assert "4,705.88" in GUIDE
    assert "58,824" in GUIDE


def test_no_documented_command_carries_a_mangled_escape():
    r"""A Windows path was written into docs/14 through something that
    interpreted its escapes: the \f of \finance-agent became a FORM FEED and
    the \a of \ask.py a BELL, so the shipped Task Scheduler command read
    `C:\path<FF>inance-agent` and `<BEL>sk.py`. Invisible in a rendered diff
    and fatal on paste - the reader gets a task that runs nothing.
    """
    control = {chr(7), chr(8), chr(11), chr(12), chr(27)}
    offenders = []
    for path in sorted(ROOT.glob("docs/*.md")) + sorted(ROOT.glob("details/*.md")):
        text = path.read_text(encoding="utf-8")
        found = sorted({hex(ord(ch)) for ch in text if ch in control})
        if found:
            offenders.append(f"{path.relative_to(ROOT)}: {', '.join(found)}")
    assert not offenders, "control characters in shipped documentation: " + "; ".join(offenders)


def test_the_scheduled_command_sets_its_own_working_directory():
    """`ask.py watch` resolves data/alerts.db relative to cwd. Scheduled from
    the wrong directory it does not fail - it writes a second, empty alert
    store and reports a quiet system it never looked at."""
    runbook = (ROOT / "docs/14-OPERATIONS-RUNBOOK.md").read_text(encoding="utf-8")
    assert "schtasks /create" in runbook
    assert "cd /d" in runbook, "the Windows task must set its working directory"
