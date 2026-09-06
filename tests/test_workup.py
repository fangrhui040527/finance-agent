"""The twelve-step workup: always twelve steps, each with an honest status, never a stance.

Runs on the SEC-shaped fixture from test_analyst_tools (two fiscal years, eight
quarters, a balance sheet) and on a Bursa name with nothing stored.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from engines.analysis.workup import STATUSES, STEPS, run_workup, workup_text
from knowledge.facts import FactBook
from mcp_server.tools import analyst_workup, compose_thesis, context, peer_set
from tests.test_analyst_tools import AAPL, ASAT
from tests.test_analyst_tools import analyst_book as _analyst_book

#: the SEC-shaped fixture, registered here under its own name
analyst_book = _analyst_book

ASOF = date.fromisoformat(ASAT)
TWO_BREAKERS = [
    {"statement": "gross margin below 35%", "query": "gross_margin < 0.35", "store": "facts"},
    {"statement": "revenue falls year on year", "query": "revenue_yoy < 0", "store": "facts"},
]
FULL_EVIDENCE = [
    {"agent": "a1_fundamentals", "text": "margins stable"},
    {"agent": "a5_catalyst_events", "text": "results in six weeks"},
    {"agent": "a6_macro_regime", "text": "rates flat"},
]


def _workup(db, iid, archetype=None):
    with FactBook(db) as book:
        return run_workup(iid, book, context(), asof=ASOF, archetype=archetype, currency="USD")


def test_a_filled_name_gets_twelve_steps_with_the_record_answering_most(analyst_book):
    w = _workup(analyst_book, AAPL, "software")
    assert [s.n for s in w.steps] == [n for n, _ in STEPS]
    assert [s.title for s in w.steps] == [t for _, t in STEPS]
    assert all(s.status in STATUSES for s in w.steps)
    assert w.step(1).status == "done" and "USD" in w.step(1).summary
    assert w.step(2).status == "manual" and w.step(3).status == "manual"
    assert w.gates["comprehensibility"] == "manual"
    # the quality gate ran on the stored lines and reached a verdict
    assert w.step(4).status in ("done", "partial") and w.gates["quality"] in ("clean", "flag")
    assert any(f.kind in ("quality", "quality_flag") for f in w.step(4).findings)
    # two fiscal years: history is partial, and says how many years the method wants
    assert w.step(5).status == "partial" and "2 annual years" in w.step(5).summary
    assert "5-10" in w.step(5).summary or "revenue CAGR" in w.step(5).summary
    assert w.step(6).status in ("done", "partial") and "cumulative FCF" in w.step(6).summary
    assert w.step(7).status in ("done", "partial") and "ROIC" in w.step(7).summary
    assert w.step(9).status == "done" and len(w.step(9).findings) == 4
    assert w.step(10).status == "done" and "scenario range" in w.step(10).summary
    assert w.step(11).status == "unavailable" and "snapshots" in w.step(11).summary
    assert w.step(12).status == "done" and 2 <= len(w.breakers) <= 4
    assert all(b.check_by == ASOF.replace(month=5, day=30) for b in w.breakers)
    assert all(b.store == "facts" and "SELECT" in b.query for b in w.breakers)
    assert w.coc is not None and w.coc.ke is not None


def test_an_empty_name_still_runs_every_step_and_names_the_filling_source(analyst_book):
    w = _workup(analyst_book, "MYX:1155", "bank")
    assert len(w.steps) == 12
    c = w.counts()
    assert c["unavailable"] >= 7 and c["manual"] == 2 and c["done"] >= 1
    assert w.gates["quality"] == "unavailable"
    assert "eodhd" in " ".join(w.step(5).fills) or "eodhd" in " ".join(w.step(4).fills)
    assert w.step(12).status == "unavailable" and not w.breakers
    text = workup_text(w)
    assert text.startswith("WORKUP  MYX:1155") and "12 steps:" in text
    assert "unavailable" in text and "SUGGESTED BREAKERS" not in text


def test_workup_text_is_ordered_and_never_reads_as_a_stance(analyst_book):
    w = _workup(analyst_book, AAPL, "software")
    text = workup_text(w)
    positions = [text.index(f"{n:>2}. {t}") for n, t in STEPS]
    assert positions == sorted(positions)
    assert "SUGGESTED BREAKERS" in text and "nothing here is a stance" in text
    assert re.search(r"\b(buy|sell|recommend|target price)\b", text.lower()) is None


def test_the_tool_and_the_cli_carry_the_disclaimer_and_refuse_bad_input(analyst_book, capsys):
    import ask

    text = analyst_workup(AAPL, as_at=ASAT, archetype="software")
    assert text.startswith("WORKUP  XNAS:AAPL") and "Not financial advice" in text
    assert ask.main(["workup", AAPL, "--as-at", ASAT, "--archetype", "software"]) == 0
    assert "12 steps:" in capsys.readouterr().out
    assert ask.main(["workup", "NOPE:1"]) == 2
    assert ask.main(["workup", AAPL, "--as-at", "yesterday"]) == 2


def test_peer_set_tool_reads_the_graph_or_refuses_without_one(monkeypatch, tmp_path):
    import knowledge.graph.build as build

    if not Path(build.DEFAULT_DB).exists():
        pytest.skip("no built graph in this checkout")
    text = peer_set("MYX:1155", as_at=ASAT)
    assert text.startswith("MYX:1155 peers as of") and "competes_with" in text
    assert "cites kb_supply_chain:curated:supply_chain#" in text
    assert "Not financial advice" in text
    monkeypatch.setattr(build, "DEFAULT_DB", str(tmp_path / "absent.db"))
    refused = peer_set("MYX:1155", as_at=ASAT)
    assert refused.startswith("REFUSED: no graph") and "make graph" in refused


def test_the_thesis_takes_the_engines_range_and_never_a_typed_one(analyst_book):
    # typed a2 evidence covers the gap but carries no range: the red team's fatal challenge
    typed = [*FULL_EVIDENCE, {"agent": "a2_valuation", "text": "looks cheap"}]
    without = compose_thesis(AAPL, evidence=typed, breakers=TWO_BREAKERS, stance="accumulate")
    assert "stance reached: accumulate" in without
    assert "Accumulating with no valuation range" in without
    derived = compose_thesis(
        AAPL,
        evidence=FULL_EVIDENCE,
        breakers=TWO_BREAKERS,
        stance="accumulate",
        derive_valuation=True,
        as_at=ASAT,
        archetype="software",
    )
    assert "valuation range, derived by the engine" in derived
    assert "a range, not a target" in derived
    assert "Accumulating with no valuation range" not in derived
    assert "stance reached: accumulate" in derived


def test_a_name_with_no_statements_gets_no_range_and_says_why(analyst_book):
    text = compose_thesis(
        "MYX:1155",
        evidence=FULL_EVIDENCE,
        breakers=TWO_BREAKERS,
        stance="accumulate",
        derive_valuation=True,
        as_at=ASAT,
    )
    assert "valuation range: none derived" in text and "no statements stored" in text
    # an agent saying "unavailable" is not coverage: the stance falls to no view
    assert "stance reached: no_view" in text
    assert "cannot accumulate without fundamentals and a valuation range" in text


def test_the_cli_thesis_flag_mirrors_the_tool(analyst_book, capsys):
    import ask

    code = ask.main(
        [
            "thesis",
            AAPL,
            "--derive-valuation",
            "--as-at",
            ASAT,
            "--archetype",
            "software",
            "--stance",
            "accumulate",
            "--evidence",
            "a1_fundamentals=margins stable",
            "--evidence",
            "a5_catalyst_events=results soon",
            "--evidence",
            "a6_macro_regime=rates flat",
            "--breaker",
            "gross margin below 35%|gross_margin < 0.35|facts",
            "--breaker",
            "revenue falls|revenue_yoy < 0|facts",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "valuation range, derived by the engine" in out
    assert "Accumulating with no valuation range" not in out


def test_graph_peers_cli_prints_the_curated_rows(capsys):
    import ask
    import knowledge.graph.build as build

    if not Path(build.DEFAULT_DB).exists():
        pytest.skip("no built graph in this checkout")
    assert ask.main(["graph", "--peers", "MYX:1155"]) == 0
    out = capsys.readouterr().out
    assert "MYX:1155 peers as of" in out and "[curated:supply_chain#" in out
    assert ask.main(["graph", "--peers", "Atlantis"]) == 2
