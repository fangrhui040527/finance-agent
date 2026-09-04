"""The two read-only paper tools, on the wire, on an empty and on a marked ledger."""

from __future__ import annotations

from datetime import UTC, datetime

from engines.paper.book import mark
from mcp_server.server import S

BANNED = ("order", "buy", "sell", "execute", "trade", "broker")


def _call(name, **args):
    res = S.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return res["result"]["content"][0]["text"]


def test_both_tools_are_registered_under_names_the_stress_probe_allows():
    names = {
        t["name"]
        for t in S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    }
    assert {"paper_status", "paper_report"} <= names
    assert not [n for n in names if any(w in n.lower() for w in BANNED)]


def test_an_empty_ledger_is_no_book_and_cannot_be_scored():
    assert "NO BOOK" in _call("paper_status", db=":memory:")
    report = _call("paper_report", days=1, db=":memory:")
    assert "NO BOOK" in report and "CANNOT SCORE" in report
    assert "Not financial advice" in _call("paper_status", db=":memory:")


def test_a_marked_ledger_reads_back_through_the_tools(paper_env, monkeypatch):
    env = paper_env
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    mark(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=env.week(1),
        slot="us_close",
        now=datetime(2026, 3, 2, tzinfo=UTC),
    )
    text = _call("paper_status", db=str(env.tmp / "paper.db"))
    assert (
        "PAPER BOOK" in text and "opened 2026-03-02" in text and "fundable at this equity" in text
    )
    report = _call("paper_report", days=30, db=str(env.tmp / "paper.db"))
    assert "opened 2026-03-02" in report and "CANNOT SCORE" in report  # one session is not a record
