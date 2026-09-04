"""Phase 1 - every entry point, run as the process an operator would run.

`tests/` calls `ask.main()` in-process. That proves the functions; it does not
prove the scripts start, find their imports, print, and exit with the code the
runbook documents. These tests spawn the real interpreter on the real files, in
an environment with the key scrubbed, so every one of them is exercising the
EchoBackend path the product ships with when no model is configured.

MCP is out of scope for this suite; `python -m mcp_server.server --selftest` is
deliberately absent.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from qa.conftest import ROOT

#: `tests/test_graph_code.py` has two assertions that compare a POSIX path with
#: what Windows produces. They fail on this platform and nowhere else; the
#: product suite is otherwise green.
KNOWN_WIN32_FAILURES = {
    "tests/test_graph_code.py::test_a_page_naming_a_module_documents_it_and_the_edge_is_citable",
    "tests/test_graph_code.py::test_documents_match_on_the_path_not_the_dotted_name",
}


def ok(proc, code=0):
    assert proc.returncode == code, f"exit {proc.returncode}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"
    return proc.stdout


# -- the five verification programmes -----------------------------------------

def test_verify_py_passes_all_fourteen_sections(run_cli):
    out = ok(run_cli(["verify.py"]))
    assert "\nPASS" in out and "[FAIL]" not in out
    assert out.count("[OK]") >= 40
    assert "14. Surface" in out


def test_stress_suite_holds_with_no_findings_and_only_the_two_standing_notes(run_cli):
    out = ok(run_cli(["stress/run.py"], timeout=600))
    assert "FINDINGS 0" in out
    assert "NOTES 2" in out, "a new note is worth reading, a missing one means a probe went quiet"
    assert "traversing db path" in out
    assert "echoed in thesis output" in out
    held = int(re.search(r"HELD (\d+)", out).group(1))
    assert held >= 150


def test_trace_run_exercises_all_sixteen_agents_offline(run_cli, tmp_path):
    out = ok(run_cli(["trace_run.py"]))
    assert "errors 0" in out

    runs = list((tmp_path / "debug").iterdir())
    assert len(runs) == 1, "one run directory per invocation"
    run = runs[0]
    for name in ("trace.jsonl", "report.html", "anatomy.md", "session.log", "summary.json"):
        assert (run / name).exists(), name
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["errors"] == []
    assert len(summary["agents_seen"]) == 16
    assert summary["refusals"] >= 10, "refusals going DOWN without a reason is a regression"
    assert summary["llm"]["calls"] == 4, "the four llm_complete holders, through the real allowlist"
    assert not list((run / "prompts").iterdir()), "EchoBackend sends nothing; no prompt blobs"

    events = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["data"].get("backend") for e in events if e["kind"] == "llm_call"} == {"EchoBackend"}
    assert any(e["kind"] == "denied" and e["data"].get("agent") == "a0_supervisor" for e in events), \
        "the supervisor's denied llm_complete must be visible in the trace"


def test_the_knowledge_graph_builds_twice_identically(run_cli, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    out = ok(run_cli(["-m", "knowledge.graph.build", "--db", str(a), "--rebuild"]))
    ok(run_cli(["-m", "knowledge.graph.build", "--db", str(b), "--rebuild"]))
    assert a.read_bytes() == b.read_bytes(), "a build that is not reproducible cannot have its diff reviewed"
    nodes, edges = map(int, re.search(r"TOTAL\s+(\d+) nodes\s+(\d+) edges", out).groups())
    citable = int(re.search(r"citable\s+(\d+) of", out).group(1))
    assert nodes > 0 and edges > 0
    assert citable == edges, "the deterministic tier is fully sourced; nothing is traversable-only"


@pytest.mark.slow
def test_the_codebase_graph_builds_twice_identically(run_cli, tmp_path):
    a, b = tmp_path / "ca.db", tmp_path / "cb.db"
    out = ok(run_cli(["-m", "knowledge.graph.build", "--code", "--db", str(a), "--rebuild"], timeout=600))
    ok(run_cli(["-m", "knowledge.graph.build", "--code", "--db", str(b), "--rebuild"], timeout=600))
    assert a.read_bytes() == b.read_bytes()
    assert "PR:" in out, "the code graph names modules as PR:* nodes"


@pytest.mark.slow
def test_the_products_own_suite_is_green_apart_from_the_known_platform_failures(run_cli):
    # pyproject.toml already carries `addopts = "-q"`; a second -q would make it
    # -qq and drop the "N passed" line this test parses.
    proc = run_cli(["-m", "pytest", "tests", "-o", "addopts=", "-q", "-p", "no:cacheprovider",
                    "-rf", "--no-header"], timeout=900)
    failed = {line.split()[1] for line in proc.stdout.splitlines() if line.startswith("FAILED ")}
    allowed = KNOWN_WIN32_FAILURES if sys.platform == "win32" else set()
    assert failed <= allowed, f"unexpected failures: {sorted(failed - allowed)}\n{proc.stdout[-3000:]}"
    m = re.search(r"(\d+) passed", proc.stdout)
    assert m and int(m.group(1)) >= 1000, proc.stdout[-500:]


# -- ask.py, subcommand by subcommand -------------------------------------------

def test_backend_reports_the_stub_when_no_key_is_present(run_cli):
    out = ok(run_cli(["ask.py", "backend"]))
    assert "EchoBackend" in out
    assert "NOT a model" in out
    assert "claude-haiku-4-5" in out and "claude-opus-5" in out


def test_backend_refuses_to_pretend_when_anthropic_is_forced_without_a_key(run_cli):
    proc = run_cli(["ask.py", "backend", "--use", "anthropic"])
    assert proc.returncode == 3
    assert "backend unavailable" in proc.stderr
    assert "ANTHROPIC_API_KEY" in proc.stderr


def test_plan_refuses_an_order_with_exit_2_and_a_card(run_cli):
    proc = run_cli(["ask.py", "plan", "buy 1000 shares of tenaga for me"])
    assert proc.returncode == 2, "a refusal is exit 2, not an error"
    assert "CANNOT ANSWER THIS" in proc.stdout
    assert "cannot place orders" in proc.stdout


def test_plan_routes_a_why_question_to_the_playbook_with_a_ringgit_cost(run_cli):
    out = ok(run_cli(["ask.py", "plan", "why did maybank fall today", "--instrument", "MYX:1155"]))
    assert "why_it_moved" in out
    assert "a9_attribution" in out
    assert re.search(r"about RM \d+\.\d\d", out)


def test_plan_trims_to_a_budget_rather_than_cheapening(run_cli):
    out = ok(run_cli(["ask.py", "plan", "why did maybank fall today",
                      "--instrument", "MYX:1155", "--budget", "0.70"]))
    assert "plan trimmed" in out
    assert "a9_attribution" in out, "the floor agent survives every trim"


def test_why_reports_a_market_move_as_the_market(run_cli):
    out = ok(run_cli(["ask.py", "why", "MYX:1155", "--move", "-0.090",
                      "--market", "-0.080", "--sector", "-0.020"]))
    assert "market_driven" in out
    assert "No cause was sought" in out
    assert "unexplained" in out
    assert "betas are stated, not estimated" in out


def test_why_reports_an_idiosyncratic_move_as_unexplained(run_cli):
    out = ok(run_cli(["ask.py", "why", "XNAS:NVDA", "--move", "0.072",
                      "--market", "0.004", "--sector", "0.002", "--currency", "USD"]))
    assert "no_identified_catalyst" in out
    assert "historically reverse" in out


def test_why_refuses_nan_input_instead_of_reporting_nan_unexplained(run_cli):
    out = ok(run_cli(["ask.py", "why", "MYX:1155", "--move", "nan", "--market", "-0.08"]))
    assert "attribution unavailable" in out.lower()
    assert "nan%" not in out.lower()


def test_thesis_composes_and_is_red_teamed(run_cli):
    out = ok(run_cli(["ask.py", "thesis", "MYX:1155",
                      "--breaker", "NIM below 2.0%|nim < 0.020|kb_filings",
                      "--breaker", "CASA below 25%|casa < 0.25|kb_filings",
                      "--evidence", "a1_fundamentals=CASA fell to 24%",
                      "--evidence", "a2_valuation=P/B at the 12th percentile",
                      "--evidence", "a5_catalyst_events=results due in 3 weeks",
                      "--evidence", "a6_macro_regime=OPR on hold",
                      "--stance", "accumulate"]))
    assert "actionable: yes" in out
    assert "red team" in out and "[" in out.split("red team")[1]


def test_thesis_with_one_breaker_takes_no_stance(run_cli):
    out = ok(run_cli(["ask.py", "thesis", "MYX:1155",
                      "--breaker", "NIM below 2.0%|nim < 0.020|kb_filings", "--stance", "accumulate"]))
    assert "actionable: no" in out and "No view" in out


def test_risk_reports_every_breach(run_cli):
    out = ok(run_cli(["ask.py", "risk", "--position", "MYX:1155:0.22:bank:MY:0.01",
                      "--position", "XNAS:NVDA:0.18:tech:US:0.02",
                      "--equity", "90000", "--peak", "100000"]))
    assert out.count("single_name breach") == 2
    assert "drawdown 10.0%" in out and "risk per trade cut to 75%" in out


def test_size_on_bursa_uses_the_markets_own_schedule_and_finds_lots(run_cli, venue_only_env):
    """The VENUE's schedule and floor. What a moomoo account pays on Bursa is a
    different question, answered in tests/test_broker_fees.py."""
    out = ok(run_cli(["ask.py", "size", "MYX:1155", "--portfolio", "200000",
                      "--price", "6.20", "--stop", "5.60", "--adv", "900000"],
                     env_extra=venue_only_env))
    assert "XKLS fee schedule" in out
    assert "60 bps round trip on XKLS" in out
    assert re.search(r"-> [\d,]+ units", out)


def test_size_on_a_foreign_market_refuses_without_a_rate_and_converts_with_one(run_cli):
    proc = run_cli(["ask.py", "size", "XNAS:NVDA", "--portfolio", "200000",
                    "--price", "100", "--stop", "90", "--adv", "9000000000"])
    assert proc.returncode == 2
    assert "Pass --fx" in proc.stdout
    out = ok(run_cli(["ask.py", "size", "XNAS:NVDA", "--portfolio", "200000",
                      "--price", "100", "--stop", "90", "--adv", "9000000000", "--fx", "4.2"]))
    assert "1 USD = MYR 4.2" in out
    assert "= MYR" in out, "a foreign position is always shown in the book's currency too"


def test_size_refuses_a_stop_above_entry(run_cli):
    proc = run_cli(["ask.py", "size", "MYX:1155", "--portfolio", "200000",
                    "--price", "6.20", "--stop", "6.50", "--adv", "900000"])
    assert proc.returncode == 2 and "not a stop" in proc.stderr


def test_learn_enforces_the_teaching_order(run_cli):
    proc = run_cli(["ask.py", "learn", "kelly"])
    assert proc.returncode == 2
    assert "has to come first" in proc.stdout
    assert "teaching order: share ->" in proc.stdout
    out = ok(run_cli(["ask.py", "learn", "share"]))
    assert "What a share actually is" in out
    out = ok(run_cli(["ask.py", "learn"]))
    assert out.startswith("next:")
    out = ok(run_cli(["ask.py", "learn", "--syllabus"]))
    assert "L1 money" in out and "L8" in out


def test_learn_refuses_an_unknown_concept(run_cli):
    proc = run_cli(["ask.py", "learn", "astrology"])
    assert proc.returncode == 2 and "not in the curriculum" in proc.stdout
    proc = run_cli(["ask.py", "learn", "share", "--mastered", "astrology"])
    assert proc.returncode == 2 and "not in the curriculum" in proc.stderr


def test_fitness_refuses_to_score_itself_and_names_what_is_missing(run_cli, tmp_path):
    out = ok(run_cli(["ask.py", "fitness", "--db", str(tmp_path / "ledger.db")]))
    assert "NO SCORE" in out
    for term in ("groundedness", "forecast_calibration", "attribution_accuracy", "p95_latency"):
        assert term in out


def test_graph_answers_paths_and_impact_from_the_built_database(run_cli, tmp_path):
    db = tmp_path / "g.db"
    ok(run_cli(["-m", "knowledge.graph.build", "--db", str(db), "--rebuild"]))
    out = ok(run_cli(["ask.py", "graph", "--db", str(db), "--path", "CM:aluminium", "CO:XKLS:8869"]))
    assert "--affects-->" in out
    assert "[curated:supply_chain#" in out, "a path prints its citations, or says it is uncitable"
    out = ok(run_cli(["ask.py", "graph", "--db", str(db), "--impact", "CM:aluminium"]))
    assert "Press Metal" in out
    out = ok(run_cli(["ask.py", "graph", "--db", str(db), "--report"]))
    assert "KNOWLEDGE GRAPH" in out and "citable" in out


def test_graph_without_a_database_says_how_to_build_one(run_cli, tmp_path):
    proc = run_cli(["ask.py", "graph", "--db", str(tmp_path / "missing.db"), "--report"])
    assert proc.returncode == 2 and "make graph" in proc.stderr


def test_prices_refuses_an_unmapped_market_naming_every_source_without_the_network(run_cli):
    """XASX used to be the example here; the Yahoo fallback now carries it (a
    QA-findings fix), so the phase-1 offline claim moves to a market NO source
    maps. The chain must name each source's refusal rather than guess."""
    proc = run_cli(["ask.py", "prices", "XFRA:BMW"])
    assert proc.returncode == 3
    assert "stooq" in proc.stderr and "yahoo" in proc.stderr
    assert "guessing a suffix" in proc.stderr


# -- predict.py, the forward record ---------------------------------------------

def test_predict_log_due_grade_status_round_trip(run_cli, tmp_path):
    db = str(tmp_path / "learning.db")
    out = ok(run_cli(["predict.py", "--db", db, "log", "MYX:1155", "1", "21d", "0.62",
                      "NIM stabilises", "--id", "p-1", "--grade-on", "2026-09-21"]))
    assert "logged p-1" in out and "grades on 2026-09-21" in out

    out = ok(run_cli(["predict.py", "--db", db, "due", "--today", "2026-09-01"]))
    assert "nothing due" in out and "Next grades 2026-09-21" in out
    out = ok(run_cli(["predict.py", "--db", db, "due", "--today", "2026-10-01"]))
    assert "1 due for grading" in out and "overdue" in out

    proc = run_cli(["predict.py", "--db", db, "grade", "p-1", "--return", "0.03",
                    "--benchmark", "0.01", "--today", "2026-09-01"])
    assert proc.returncode == 1 and "would score noise" in proc.stderr

    out = ok(run_cli(["predict.py", "--db", db, "grade", "p-1", "--return", "0.03",
                      "--benchmark", "0.01", "--today", "2026-09-21"]))
    assert "graded p-1: correct" in out and "excess +2.00%" in out

    proc = run_cli(["predict.py", "--db", db, "grade", "p-1", "--return", "0.03",
                    "--benchmark", "0.01", "--today", "2026-09-22"])
    assert proc.returncode == 1 and "not pending" in proc.stderr, "an outcome is graded once"

    out = ok(run_cli(["predict.py", "--db", db, "status"]))
    assert "graded   1" in out and "29 more graded calls" in out


def test_predict_refuses_a_duplicate_id_and_a_bad_confidence(run_cli, tmp_path):
    db = str(tmp_path / "learning.db")
    ok(run_cli(["predict.py", "--db", db, "log", "MYX:1155", "1", "5d", "0.5", "x", "--id", "dup"]))
    proc = run_cli(["predict.py", "--db", db, "log", "MYX:1155", "1", "5d", "0.5", "x", "--id", "dup"])
    assert proc.returncode != 0 and "already logged" in (proc.stderr + proc.stdout)
    proc = run_cli(["predict.py", "--db", db, "log", "MYX:1155", "1", "5d", "1.5", "x"])
    assert proc.returncode != 0 and "probability" in (proc.stderr + proc.stdout)

# -- the paper book -------------------------------------------------------------

def test_paper_status_is_no_book_until_init_then_answers_offline(run_cli, tmp_path):
    db = str(tmp_path / "paper.db")
    proc = run_cli(["ask.py", "paper", "status", "--db", db])
    assert proc.returncode == 2 and "NO BOOK" in proc.stderr
    ok(run_cli(["ask.py", "paper", "init", "--db", db, "--start", "2026-03-02"]))
    out = ok(run_cli(["ask.py", "paper", "status", "--db", db, "--date", "2026-03-02"],
                     env_extra={"FINPLANET_OFFLINE": "1"}))
    assert "PAPER BOOK" in out and "fundable at this equity" in out
    assert "buy" not in out.lower().replace("buy-and-hold", "") and "should" not in out.lower()

