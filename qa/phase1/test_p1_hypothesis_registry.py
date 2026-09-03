"""Phase 1 - the hypothesis registry's append-only contract, armed in advance.

Peer contract (finance-agent-robustness-pass, 2026-08-31): three new tables in
`data/learning.db` - `hypotheses`, `hypothesis_events`, `hypothesis_links` -
INSERT-only, triggers on all three, written through their own connection.

The registry has not landed yet, so this file arms a tripwire rather than
asserting into thin air: it SKIPS while no product source creates the tables,
and the moment one does, every creating file must ship UPDATE- and DELETE-
blocking triggers for each table, and any store that actually builds them must
refuse an UPDATE and a DELETE at runtime. Landing the schema without the
triggers turns this from a skip into a failure with no edit here.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from qa.conftest import ROOT

TABLES = ("hypotheses", "hypothesis_events", "hypothesis_links")

#: Product packages that may legitimately own the registry schema. tests/, qa/
#: and design/ are excluded: naming a table in a test is not creating it.
SEARCH_ROOTS = ("agents", "core", "knowledge", "engines", "markets", "mcp_server", "web")


def _creating_sources() -> dict:
    """Every product file that CREATEs one of the registry tables."""
    out = {}
    for root in SEARCH_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            created = [t for t in TABLES
                       if re.search(rf"CREATE TABLE (IF NOT EXISTS )?{t}\b", text)]
            if created:
                out[path] = (text, created)
    return out


def test_any_hypothesis_registry_schema_ships_with_append_only_triggers():
    creators = _creating_sources()
    if not creators:
        pytest.skip("hypothesis registry not landed yet (peer contract, 2026-08-31)")

    created_anywhere = {t for _, (_, created) in creators.items() for t in created}
    assert created_anywhere == set(TABLES), (
        f"registry landed partially: {sorted(created_anywhere)} - the contract names all three")

    for path, (text, created) in creators.items():
        for table in created:
            for verb in ("UPDATE", "DELETE"):
                pattern = rf"BEFORE {verb} ON {table}\b"
                assert re.search(pattern, text), (
                    f"{path.relative_to(ROOT)} creates {table} without a BEFORE {verb} "
                    f"trigger: the registry is INSERT-only by contract")
        assert "RAISE(ABORT" in text, (
            f"{path.relative_to(ROOT)}: triggers must RAISE(ABORT, ...), not warn")


def test_a_built_registry_refuses_updates_and_deletes(tmp_path):
    """Behavioural half, on the real constructor the peer shipped.

    Populated first, deliberately: a BEFORE UPDATE/DELETE trigger fires per
    affected row, so on an EMPTY table `DELETE FROM t` touches nothing, no
    trigger fires, and the statement "succeeds" - a vacuous pass. One
    hypothesis, one link and the create-event give every table a row for the
    triggers to defend."""
    try:
        from agents.learning.hypotheses import HypothesisStore
    except ImportError:
        pytest.skip("hypothesis registry module not present (peer contract, 2026-08-31)")

    with HypothesisStore(tmp_path / "learning.db") as s:
        hid = s.create("Banks re-rate", "MYX banks re-rate as NIM stabilises")
        s.link(hid, "p1")
        for table in TABLES:
            n = s.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n >= 1, f"{table} has no row; the trigger check would be vacuous"

        cases = (
            ("hypotheses", "immutable"),
            ("hypothesis_events", "append-only"),
            ("hypothesis_links", "append-only"),
        )
        for table, word in cases:
            cols = [r[1] for r in s.db.execute(f"PRAGMA table_info({table})")]
            with pytest.raises(sqlite3.DatabaseError, match=word):
                s.db.execute(f"UPDATE {table} SET {cols[-1]} = {cols[-1]}")
            with pytest.raises(sqlite3.DatabaseError):
                s.db.execute(f"DELETE FROM {table}")

        assert s.get(hid).title == "Banks re-rate", "the registry still reads back after refusing"
