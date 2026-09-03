"""P5: the append-only hypothesis registry."""

from __future__ import annotations

import sqlite3

import pytest

from agents.learning.hypotheses import STATUSES, HypothesisStore


def _store(tmp_path) -> HypothesisStore:
    return HypothesisStore(tmp_path / "learning.db")


def test_create_starts_exploring_and_is_immutable(tmp_path):
    with _store(tmp_path) as s:
        hid = s.create("Banks re-rate", "MYX banks re-rate as NIM stabilises above 2.25%")
        v = s.get(hid)
        assert v.status == "exploring" and v.title == "Banks re-rate"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            s.db.execute("UPDATE hypotheses SET title='x' WHERE hypothesis_id=?", (hid,))
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            s.db.execute("DELETE FROM hypotheses WHERE hypothesis_id=?", (hid,))


def test_current_status_is_the_last_event_and_history_is_kept(tmp_path):
    with _store(tmp_path) as s:
        hid = s.create("t", "thesis")
        s.transition(hid, "testing", "first predictions linked")
        s.transition(hid, "rejected", "cohort went 1 for 6 against benchmark")
        v = s.get(hid)
        assert v.status == "rejected"
        assert [h[1] for h in v.history] == ["exploring", "testing", "rejected"]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            s.db.execute("DELETE FROM hypothesis_events")


def test_a_verdict_without_a_reason_is_refused(tmp_path):
    with _store(tmp_path) as s:
        hid = s.create("t", "thesis")
        for verdict in ("validated", "rejected"):
            with pytest.raises(ValueError, match="requires a note"):
                s.transition(hid, verdict)
        with pytest.raises(ValueError, match="status must be one of"):
            s.transition(hid, "paused")


def test_links_accumulate_and_deduplicate(tmp_path):
    with _store(tmp_path) as s:
        hid = s.create("t", "thesis")
        s.link(hid, "p1")
        s.link(hid, "p2")
        s.link(hid, "p1")  # linking twice is not an event
        assert s.get(hid).prediction_ids == ("p1", "p2")


def test_unknown_ids_are_errors_not_silent_rows(tmp_path):
    with _store(tmp_path) as s:
        with pytest.raises(KeyError):
            s.transition("H-nope", "testing")
        with pytest.raises(KeyError):
            s.link("H-nope", "p1")
        with pytest.raises(ValueError):
            s.create("  ", "thesis")


def test_list_filters_by_status(tmp_path):
    with _store(tmp_path) as s:
        a = s.create("a", "ta")
        s.create("b", "tb")
        s.transition(a, "testing", "linked")
        assert [v.hypothesis_id for v in s.all(status="testing")] == [a]
        assert len(s.all()) == 2
        assert set(STATUSES) >= {v.status for v in s.all()}


def test_the_mcp_tool_registers_and_refuses_blanks(tmp_path):
    from mcp_server import tools as T

    out = T.log_hypothesis(
        title="Banks re-rate", thesis="NIM stabilises", db=str(tmp_path / "l.db")
    )
    assert "registered H-" in out and "exploring" in out
    with pytest.raises(Exception, match="falsifiable"):
        T.log_hypothesis(title=" ", thesis="x", db=str(tmp_path / "l.db"))


def test_the_tool_is_on_the_wire(tmp_path):
    import json

    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "log_hypothesis" in names
    call = S.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "log_hypothesis",
                "arguments": {
                    "title": "Banks re-rate",
                    "thesis": "NIM stabilises above 2.25%",
                    "db": str(tmp_path / "l.db"),
                },
            },
        }
    )
    assert "error" not in call, json.dumps(call)[:300]
    assert "registered H-" in call["result"]["content"][0]["text"]
