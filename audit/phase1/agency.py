"""S1-S4: OWASP LLM08, excessive agency - the blast radius of the tool surface.

The blueprint asks for least privilege on tool arguments and a human in the loop
before anything destructive. The question for this system is narrower and
sharper than "can the model do damage": the model here is given 26 tools by an
MCP client it does not control, and the only thing standing between a
mis-routed call and a real consequence is what those tools are able to do at
all.

So these check capability, not intent:

  * **S1** No tool can place, cancel or amend an order. The repository already
    greps its own source for this; the audit asks the running server instead,
    because a tool could acquire the capability through a dependency.
  * **S2** No tool takes a filesystem path it will write to, and no tool
    argument escapes the project directory.
  * **S3** Every store a tool can write to is append-only at the database level,
    so "delete my losing predictions" is not expressible.
  * **S4** The destructive-sounding surface is read-only in fact.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from pathlib import Path

from audit._support.scorecard import Check

ROOT = Path(__file__).resolve().parents[2]

#: Words that would name an execution capability if one existed.
EXECUTION = re.compile(
    r"\b(place|submit|cancel|amend|execute)[_ ](an[_ ])?(order|trade)\b"
    r"|\border_(id|type|side)\b|\bbroker\b|\bfix_session\b",
    re.I,
)


def _tools() -> list[dict]:
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return resp["result"]["tools"]


def s1_no_tool_can_transact() -> Check:
    c = Check(
        "S1",
        "Security",
        1,
        "No published tool can place, cancel or amend an order",
        "OWASP LLM08: least privilege - the capability does not exist to be misused",
    )
    offenders = []
    for tool in _tools():
        blob = json.dumps(tool)
        # A description that REFUSES to transact is the opposite of a finding,
        # so the disclaimer's own words are excluded before matching.
        cleaned = blob.replace("No order is placed by any tool here", "")
        cleaned = cleaned.replace("cannot place orders", "")
        if EXECUTION.search(cleaned):
            offenders.append(tool["name"])
    if offenders:
        return c.failed(f"tool(s) expose an execution vocabulary: {', '.join(offenders)}")
    c.evidence = f"{len(_tools())} tools; no order, broker or session capability in any schema"
    return c.ok()


def s2_no_tool_writes_where_it_is_told() -> Check:
    """A path argument is an arbitrary-write primitive unless it is bounded."""
    c = Check(
        "S2",
        "Security",
        1,
        "No tool takes a caller-supplied path it writes to",
        "unsanitised tool scope: a path argument is a write primitive",
        blocking=False,
    )
    risky = []
    for tool in _tools():
        for arg, spec in (tool.get("inputSchema", {}).get("properties") or {}).items():
            desc = str(spec.get("description", "")).lower()
            if arg.endswith(("_path", "_file", "_dir")) or "path" in arg.lower():
                risky.append(f"{tool['name']}.{arg}")
            elif "path" in desc and "write" in desc:
                risky.append(f"{tool['name']}.{arg}")
    if risky:
        return c.failed(
            f"caller-supplied path argument(s): {', '.join(risky)}. Each is only as safe as "
            f"its own validation, and nothing confines them to the project directory"
        )
    c.evidence = "no tool accepts a path from the caller"
    return c.ok()


def s3_agent_writable_stores_are_append_only() -> Check:
    c = Check(
        "S3",
        "Security",
        1,
        "Every agent-writable store refuses UPDATE and DELETE",
        "an agent that can rewrite its own record has no record",
    )
    from core.llm.tiers import TaskClass, Tier, Usage
    from core.provenance.ledger import ProvenanceLedger

    # ignore_cleanup_errors: on Windows a still-open handle turns a real result
    # into a PermissionError from the cleanup, which masks what was measured.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "audit.db"
        ledger = ProvenanceLedger(db)
        try:
            ledger.record_call(
                agent="a10_thesis",
                task_class=TaskClass.THESIS_SYNTHESIS,
                tier=Tier.CHEAP,
                model_id="claude-haiku-4-5",
                prompt="an audit row",
                usage=Usage(input_tokens=10, output_tokens=5),
            )
        finally:
            ledger.close()

        conn = sqlite3.connect(db)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            unguarded = []
            for table in tables:
                triggers = " ".join(
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                        (table,),
                    )
                ).lower()
                if "update" not in triggers or "delete" not in triggers:
                    unguarded.append(table)
            if unguarded:
                return c.failed(
                    f"table(s) without both an UPDATE and a DELETE guard: {', '.join(unguarded)}"
                )
            # And prove a guard FIRES rather than trusting it is named well.
            # An UPDATE against an empty table matches no row and trips
            # nothing, so only a populated table can be fired at.
            refused, fired_on = 0, []
            for table in tables:
                rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if not rows:
                    continue
                fired_on.append(table)
                for sql in (f"UPDATE {table} SET rowid=rowid", f"DELETE FROM {table}"):
                    try:
                        conn.execute(sql)
                    except sqlite3.DatabaseError:
                        refused += 1
                    else:
                        return c.failed(f"{table} accepted: {sql}")
            conn.rollback()
            if not fired_on:
                return c.failed("no table had a row to fire a trigger against")
        finally:
            conn.close()
    c.evidence = f"{len(tables)} table(s); {refused} write attempts each refused by a trigger"
    return c.ok()


def s4_the_read_only_surface_is_read_only() -> Check:
    """The observability tools see everything; they must change nothing."""
    c = Check(
        "S4",
        "Security",
        1,
        "The observability tools cannot write",
        "least privilege on the surface with the widest read scope",
    )
    import mcp_server.observability as obs

    src = Path(obs.__file__).read_text(encoding="utf-8")
    writes = re.findall(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE)\s+(INTO|TABLE|FROM)?", src, re.I)
    if writes:
        return c.failed(f"the read-only surface contains {len(writes)} write statement(s)")
    if ".write_text(" in src or "open(" in src and '"w"' in src:
        return c.failed("the read-only surface opens a file for writing")
    c.evidence = f"{len(src.splitlines())} lines, no write statement of any kind"
    return c.ok()


CHECKS = (
    s1_no_tool_can_transact,
    s2_no_tool_writes_where_it_is_told,
    s3_agent_writable_stores_are_append_only,
    s4_the_read_only_surface_is_read_only,
)
