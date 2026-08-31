"""The seven dimensions: quality, efficiency, maintainability, reasoning, scorecard.

The rule these share with the rest of the system: a dimension with no
evidence says CANNOT SCORE. A dashboard that shows green for something it
never measured is the most expensive kind of comfort.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from core.llm.tiers import TaskClass, Tier, Usage
from core.provenance.ledger import ProvenanceLedger
from mcp_server import observability as O

REPORTS = (
    "quality_report",
    "efficiency_report",
    "maintainability_report",
    "reasoning_report",
    "scorecard",
)


def _ledger(path: Path, rows: list[dict]) -> Path:
    led = ProvenanceLedger(path)
    for r in rows:
        led.record_call(
            agent=r.get("agent", "a10_thesis"),
            task_class=TaskClass.THESIS_SYNTHESIS,
            tier=r.get("tier", Tier.CHEAP),
            model_id=r.get("model", "claude-haiku-4-5"),
            prompt="p",
            usage=Usage(
                input_tokens=r.get("in", 100),
                output_tokens=r.get("out", 50),
                cached_input_tokens=r.get("cache_read", 0),
                cache_write_tokens=r.get("cache_write", 0),
            ),
            fx_rate=Decimal("4.15"),
            latency_ms=r.get("latency", 100.0),
            stop_reason=r.get("stop", "end_turn"),
            request_id=r.get("req", "req_x"),
        )
    led.close()
    return path


def _run(root: Path, name: str, events: list[dict]) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (d / "summary.json").write_text(
        json.dumps({"run_id": name, "events": len(events)}), encoding="utf-8"
    )


# --- all five are registered and read-only ---------------------------------------


def test_every_report_is_on_the_wire():
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert set(REPORTS) <= names


def test_the_instructions_route_to_the_scorecard_first():
    from mcp_server.server import INSTRUCTIONS

    assert "Start at scorecard" in INSTRUCTIONS
    assert "has not passed - it has not been measured" in INSTRUCTIONS


# --- efficiency -------------------------------------------------------------------


def test_efficiency_computes_cache_hit_rate_and_names_a_zero(tmp_path):
    db = _ledger(tmp_path / "l.db", [{"in": 100, "cache_read": 0} for _ in range(3)])
    out = O.efficiency_report(days=7, db=str(db))
    assert "hit rate 0%" in out
    assert "NOTHING is being cached" in out
    assert "the prefix is changing" in out


def test_efficiency_reports_a_real_hit_rate(tmp_path):
    db = _ledger(tmp_path / "l.db", [{"in": 50, "cache_read": 150, "cache_write": 10}])
    out = O.efficiency_report(days=7, db=str(db))
    assert "hit rate 75%" in out  # 150 of 200
    assert "10 written at 1.25x" in out


def test_efficiency_counts_wasted_spend(tmp_path):
    db = _ledger(
        tmp_path / "l.db",
        [{"stop": "end_turn"}, {"stop": "refusal"}, {"stop": "truncated"}],
    )
    out = O.efficiency_report(days=7, db=str(db))
    assert "2 call(s) billed" in out
    assert "returned nothing usable" in out


def test_efficiency_on_an_empty_window_says_so(tmp_path):
    db = _ledger(tmp_path / "l.db", [])
    out = O.efficiency_report(days=7, db=str(db))
    assert "nothing to be" in out and "efficient or wasteful" in out


# --- quality ----------------------------------------------------------------------


def test_quality_refuses_to_score_calibration_below_the_minimum(tmp_path):
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.quality_report(days=30, db=str(db), root=str(tmp_path / "none"))
    assert "CANNOT SCORE" in out
    assert "measures luck" in out


def test_quality_reports_claim_survival_and_its_reasons(tmp_path):
    path = tmp_path / "l.db"
    _ledger(path, [{}])
    led = ProvenanceLedger(path)
    led.record_claim("a10", "kept", [], survived=True)
    led.record_claim("a10", "no", [], survived=False, dropped_reason="span not in chunk")
    led.close()
    out = O.quality_report(days=30, db=str(path), root=str(tmp_path / "none"))
    assert "1 of 2 claims survived" in out
    assert "span not in chunk" in out


def test_quality_counts_verdict_codes_and_ignores_prose(tmp_path):
    root = tmp_path / "debug"
    _run(
        root,
        "20260830T100000-aaaaaa",
        [
            {"kind": "engine", "name": "a", "data": {"verdict_code": "market_driven"}},
            {"kind": "engine", "name": "b", "data": {"verdict_code": "not_significant"}},
            {"kind": "engine", "name": "c", "data": {"verdict_code": "no_identified_catalyst"}},
            # a run from before verdict_code existed: prose under `verdict`
            {"kind": "engine", "name": "d", "data": {"verdict": "MYX:1155 returned -9.0% ..."}},
        ],
    )
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.quality_report(days=30, db=str(db), root=str(root))
    assert "market_driven" in out and "not_significant" in out
    assert "67% of moves needed no company story" in out
    assert "1 event(s) carried a verdict as prose" in out


def test_quality_reports_the_eval_ratchet_without_claiming_pass_rates(tmp_path):
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.quality_report(days=30, db=str(db), root=str(tmp_path / "none"))
    assert "16 agent(s)" in out
    assert "suite SHAPE, not pass rate" in out


# --- maintainability ----------------------------------------------------------------


def test_maintainability_without_a_graph_says_how_to_build_one(tmp_path):
    out = O.maintainability_report(db=str(tmp_path / "nope.db"))
    assert "make codegraph" in out
    assert "Nothing to report is not the same as nothing wrong" in out


def test_maintainability_separates_packages_from_modules_and_counts_edges():
    graph = Path("data/codegraph.db")
    if not graph.is_file():  # built by `make codegraph`; CI builds it
        return
    out = O.maintainability_report()
    assert "package root(s)" in out
    assert "test edges" in out and "doc edges" in out
    assert "counts edges, not coverage" in out
    assert "runtime dependencies" in out


# --- reasoning ------------------------------------------------------------------------


def test_reasoning_counts_how_turns_ended(tmp_path):
    db = _ledger(
        tmp_path / "l.db",
        [{"stop": "end_turn"}, {"stop": "end_turn"}, {"stop": "refusal"}, {"stop": "truncated"}],
    )
    out = O.reasoning_report(runs=5, db=str(db), root=str(tmp_path / "none"))
    assert "2  end_turn" in out
    assert "the model declined" in out
    assert "paid for and discarded" in out


def test_reasoning_reports_what_the_rails_stopped(tmp_path):
    root = tmp_path / "debug"
    _run(
        root,
        "20260830T100000-aaaaaa",
        [
            {"kind": "denied", "name": "place_order", "data": {"rule": "no_execution"}},
            {"kind": "denied", "name": "ingest", "data": {"rule": "injection_scan"}},
            {"kind": "refusal", "name": "a0", "data": {"reason": "price target requested"}},
        ],
    )
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.reasoning_report(runs=5, db=str(db), root=str(root))
    assert "no_execution" in out and "injection_scan" in out
    assert "A denial is the chain working" in out
    assert "price target requested" in out


def test_reasoning_refuses_to_treat_refusal_rate_as_a_score(tmp_path):
    root = tmp_path / "debug"
    _run(
        root,
        "20260830T100000-aaaaaa",
        [
            {"kind": "refusal", "name": "a0", "data": {"reason": "x"}},
            {"kind": "agent", "name": "a1", "data": {"answered": True}},
        ],
    )
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.reasoning_report(runs=5, db=str(db), root=str(root))
    assert "refusal rate" in out
    assert "optimises refusal PRECISION" in out


# --- the faithfulness check the reasoning report rests on ------------------------------


def test_unsupported_numbers_finds_an_invention_and_forgives_a_rounding():
    from agents.synthesis.narrate import unsupported_numbers

    source = "confidence: 0.27\nNIM 2.31\ntarget none"
    assert unsupported_numbers("NIM was 2.3 and confidence 0.27", source) == []
    assert "7.40" in unsupported_numbers("it will reach RM 7.40", source)


def test_unsupported_numbers_ignores_ordinary_small_counts():
    from agents.synthesis.narrate import unsupported_numbers

    assert unsupported_numbers("three legs, 2 breakers, 1 stance", "nothing numeric") == []


# --- the scorecard --------------------------------------------------------------------


def test_the_scorecard_covers_every_dimension(tmp_path):
    db = _ledger(tmp_path / "l.db", [{}])
    out = O.scorecard(db=str(db), root=str(tmp_path / "none"))
    for dim in (
        "robustness",
        "performance",
        "efficiency",
        "quality",
        "maintainability",
        "usability",
        "reasoning",
    ):
        assert dim in out, dim


def test_the_scorecard_says_cannot_score_rather_than_green(tmp_path):
    empty = tmp_path / "empty.db"
    ProvenanceLedger(empty).close()
    out = O.scorecard(db=str(empty), root=str(tmp_path / "none"))
    assert "CANNOT SCORE" in out
    assert "expensive kind of comfort" in out
    assert "statement about the evidence, not about the system" in out


def test_the_scorecard_carries_evidence_for_every_line(tmp_path):
    db = _ledger(tmp_path / "l.db", [{"latency": 50.0}])
    out = O.scorecard(db=str(db), root=str(tmp_path / "none"))
    for line in out.splitlines():
        if line.startswith("  ") and any(
            line.strip().startswith(d) for d in ("performance", "efficiency", "reasoning")
        ):
            # dimension, verdict, and something after it
            assert len(line.split()) >= 3, line


# --- the ledger columns these rest on ---------------------------------------------------


def test_stop_reason_and_request_id_are_recorded(tmp_path):
    db = _ledger(tmp_path / "l.db", [{"stop": "refusal", "req": "req_abc"}])
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    conn.close()
    assert row["stop_reason"] == "refusal"
    assert row["request_id"] == "req_abc"


def test_an_older_ledger_without_the_columns_still_reports(tmp_path):
    """The migration adds them; a row written before it has NULL, and every
    report must survive that rather than crash on the newest thing."""
    path = tmp_path / "old.db"
    led = ProvenanceLedger(path)
    led.conn.execute(
        "INSERT INTO llm_calls (at, agent, task_class, tier, model_id, prompt_hash, run_id,"
        " input_tokens, output_tokens, cached_tokens, cache_write_tokens, cost_usd,"
        " cost_myr, fx_rate, fx_asof, latency_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(UTC).isoformat(),
            "a10",
            "thesis_synthesis",
            "cheap",
            "m",
            "h",
            "",
            10,
            5,
            0,
            0,
            "0.01",
            "0.04",
            "4.15",
            datetime.now(UTC).isoformat(),
            1.0,
        ),
    )
    led.conn.commit()
    led.close()
    assert "unrecorded" in O.reasoning_report(runs=1, db=str(path), root=str(tmp_path / "none"))
    assert O.efficiency_report(days=7, db=str(path))
    assert O.scorecard(db=str(path), root=str(tmp_path / "none"))
