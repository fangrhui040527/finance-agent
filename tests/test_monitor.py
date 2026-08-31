"""Rules that fire on their own, and an append-only record of when.

The properties that make this a monitor rather than a nuisance: it records
state CHANGES (a rule that stays tripped does not re-fire), it cannot be
rewritten, it distinguishes silence from success, and every alert says what
to look at next.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import load as load_config
from core.llm.tiers import TaskClass, Tier, Usage
from core.monitor import ALERT, AlertLog, check, evaluate
from core.provenance.ledger import ProvenanceLedger


def _cfg(**over):
    return replace(load_config(), **over)


def _ledger(path: Path, calls: int = 3, latency: float = 100.0, cost_scale: int = 100):
    led = ProvenanceLedger(path)
    for _ in range(calls):
        led.record_call(
            agent="a10_thesis",
            task_class=TaskClass.THESIS_SYNTHESIS,
            tier=Tier.REASON,
            model_id="claude-opus-5",
            prompt="p",
            usage=Usage(input_tokens=cost_scale * 1000, output_tokens=cost_scale * 100),
            fx_rate=Decimal("4.15"),
            latency_ms=latency,
        )
    led.close()
    return path


# --- rules ------------------------------------------------------------------------


def test_spend_over_the_fraction_opens_an_alert(tmp_path):
    db = _ledger(tmp_path / "l.db", calls=3, cost_scale=1000)
    alerts = evaluate(
        _cfg(alert_spend_fraction=Decimal("0.0001")), db=str(db), debug_root=str(tmp_path / "none")
    )
    spend = [a for a in alerts if a.rule == "spend_24h"]
    assert spend, [a.rule for a in alerts]
    assert "budget" in spend[0].title
    assert spend[0].next_step  # every alert names a next step


def test_spend_under_the_fraction_is_quiet(tmp_path):
    db = _ledger(tmp_path / "l.db", calls=1, cost_scale=1)
    alerts = evaluate(
        _cfg(alert_spend_fraction=Decimal("0.9")), db=str(db), debug_root=str(tmp_path / "none")
    )
    assert not [a for a in alerts if a.rule == "spend_24h"]


def test_slow_calls_open_a_latency_alert(tmp_path):
    db = _ledger(tmp_path / "l.db", latency=9000.0)
    alerts = evaluate(
        _cfg(alert_p95_latency_ms=1000.0), db=str(db), debug_root=str(tmp_path / "none")
    )
    lat = [a for a in alerts if a.rule == "latency_p95"]
    assert lat and "p95" in lat[0].title


def test_dropped_claims_open_a_quality_alert(tmp_path):
    path = tmp_path / "l.db"
    _ledger(path, calls=1, cost_scale=1)
    led = ProvenanceLedger(path)
    led.record_claim("a10", "kept", [], survived=True)
    led.record_claim("a10", "dropped", [], survived=False, dropped_reason="no chunk")
    led.close()
    alerts = evaluate(
        _cfg(alert_dropped_claim_rate=Decimal("0.4")),
        db=str(path),
        debug_root=str(tmp_path / "none"),
    )
    drop = [a for a in alerts if a.rule == "dropped_claims"]
    assert drop and "50%" in drop[0].title


def test_silence_is_a_rule_but_only_once_something_has_run(tmp_path):
    empty = tmp_path / "empty.db"
    ProvenanceLedger(empty).close()
    quiet = _cfg(alert_silence_hours=1)
    # never run: silence is not suspicious
    assert not [
        a
        for a in evaluate(quiet, db=str(empty), debug_root=str(tmp_path / "none"))
        if a.rule == "silence"
    ]

    # has run, but not lately: a job that dies quietly looks like a quiet week
    old = tmp_path / "old.db"
    led = ProvenanceLedger(old)
    led.record_call(
        agent="a10_thesis",
        task_class=TaskClass.THESIS_SYNTHESIS,
        tier=Tier.CHEAP,
        model_id="claude-haiku-4-5",
        prompt="p",
        usage=Usage(input_tokens=10, output_tokens=5),
        fx_rate=Decimal("4.15"),
        at=datetime.now(UTC) - timedelta(days=3),
    )
    led.close()
    hits = [
        a
        for a in evaluate(quiet, db=str(old), debug_root=str(tmp_path / "none"))
        if a.rule == "silence"
    ]
    assert hits and hits[0].severity == ALERT


def test_silence_is_off_by_default():
    assert load_config().alert_silence_hours == 0


# --- trace-derived rules -----------------------------------------------------------


def _run(root: Path, name: str, events: list[dict], manifest: dict | None = None):
    d = root / name
    d.mkdir(parents=True)
    (d / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    (d / "summary.json").write_text(
        json.dumps({"run_id": name, "events": len(events)}), encoding="utf-8"
    )
    if manifest:
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_an_error_in_the_newest_run_opens_an_alert(tmp_path):
    root = tmp_path / "debug"
    _run(root, "20260830T100000-aaaaaa", [{"kind": "error", "name": "a9", "error": "boom"}])
    empty = tmp_path / "e.db"
    ProvenanceLedger(empty).close()
    alerts = evaluate(_cfg(), db=str(empty), debug_root=str(root))
    err = [a for a in alerts if a.rule == "run_errors"]
    assert err and "boom" in err[0].detail
    assert "20260830T100000-aaaaaa" in err[0].next_step


def test_a_changed_method_between_runs_opens_an_alert(tmp_path):
    root = tmp_path / "debug"
    a = {
        "manifest_hash": "a" * 64,
        "system_prompt_hashes": {"a15": "1"},
        "registry_hash": "r",
        "tools_hash": "t",
        "package_versions": {},
    }
    b = dict(a, manifest_hash="b" * 64, system_prompt_hashes={"a15": "2"})
    _run(root, "20260830T100000-aaaaaa", [{"kind": "span", "name": "x"}], a)
    _run(root, "20260831T100000-bbbbbb", [{"kind": "span", "name": "x"}], b)
    empty = tmp_path / "e.db"
    ProvenanceLedger(empty).close()
    alerts = evaluate(_cfg(), db=str(empty), debug_root=str(root))
    meth = [x for x in alerts if x.rule == "methodology_changed"]
    assert meth and "system prompt changed" in meth[0].detail


# --- the state machine --------------------------------------------------------------


def test_an_open_rule_does_not_refire_and_resolves_when_it_clears(tmp_path):
    db = _ledger(tmp_path / "l.db", calls=3, cost_scale=1000)
    adb = str(tmp_path / "alerts.db")
    hot = _cfg(alert_spend_fraction=Decimal("0.0001"))
    cool = _cfg(alert_spend_fraction=Decimal("100"))
    none = str(tmp_path / "none")

    first = check(hot, db=str(db), alerts_db=adb, debug_root=none)
    assert [a.rule for a in first.opened] == ["spend_24h"]
    assert first.any_open

    second = check(hot, db=str(db), alerts_db=adb, debug_root=none)
    assert second.opened == [] and len(second.still_open) == 1  # no repeat

    third = check(cool, db=str(db), alerts_db=adb, debug_root=none)
    assert third.resolved == ["spend_24h"]
    assert not third.any_open

    fourth = check(hot, db=str(db), alerts_db=adb, debug_root=none)
    assert [a.rule for a in fourth.opened] == ["spend_24h"]  # opens again after clearing


def test_the_history_is_append_only(tmp_path):
    db = _ledger(tmp_path / "l.db", calls=3, cost_scale=1000)
    adb = str(tmp_path / "alerts.db")
    check(
        _cfg(alert_spend_fraction=Decimal("0.0001")),
        db=str(db),
        alerts_db=adb,
        debug_root=str(tmp_path / "none"),
    )
    with AlertLog(adb) as log:
        assert log.history()
        with pytest.raises(sqlite3.IntegrityError, match="not editable"):
            log.db.execute("UPDATE alert_events SET title='x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            log.db.execute("DELETE FROM alert_events")


def test_a_quiet_check_says_what_it_did_not_cover(tmp_path):
    empty = tmp_path / "e.db"
    ProvenanceLedger(empty).close()
    result = check(
        _cfg(), db=str(empty), alerts_db=str(tmp_path / "a.db"), debug_root=str(tmp_path / "none")
    )
    text = result.render()
    assert "nothing tripped" in text
    assert "not a statement that everything is well" in text


# --- surfaces -----------------------------------------------------------------------


def test_the_cli_exit_code_is_the_interface(tmp_path, capsys, monkeypatch):
    import ask

    db = _ledger(tmp_path / "l.db", calls=3, cost_scale=1000)
    monkeypatch.setenv("FINPLANET_NO_DOTENV", "1")
    # nothing tripped -> 0
    assert (
        ask.main(
            ["watch", "--db", str(tmp_path / "empty.db"), "--alerts-db", str(tmp_path / "a1.db")]
        )
        == 0
    )
    capsys.readouterr()

    # a real ledger against the default 80% threshold stays quiet too; force one
    from core import monitor

    monkeypatch.setattr(
        monitor,
        "evaluate",
        lambda *a, **k: [monitor.Alert("x", monitor.ALERT, "t", "d", "n")],
    )
    assert ask.main(["watch", "--db", str(db), "--alerts-db", str(tmp_path / "a2.db")]) == 1


def test_the_mcp_tool_reports_open_alerts_and_says_when_nothing_was_evaluated(tmp_path):
    from mcp_server import observability as O

    fresh = str(tmp_path / "never.db")
    assert "no rule has been evaluated" in O.open_alerts(alerts_db=fresh)

    db = _ledger(tmp_path / "l.db", calls=3, cost_scale=1000)
    adb = str(tmp_path / "a.db")
    check(
        _cfg(alert_spend_fraction=Decimal("0.0001")),
        db=str(db),
        alerts_db=adb,
        debug_root=str(tmp_path / "none"),
    )
    out = O.open_alerts(alerts_db=adb)
    assert "OPEN" in out and "spend_24h" in out and "open since" in out


def test_open_alerts_is_on_the_wire():
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "open_alerts" in {t["name"] for t in resp["result"]["tools"]}


def test_the_thresholds_are_bounded_like_every_other_setting():
    from core.config import HARD_BOUNDS

    keys = {b[0] for b in HARD_BOUNDS}
    assert {
        "monitor.spend_fraction",
        "monitor.p95_latency_ms",
        "monitor.dropped_claim_rate",
        "monitor.silence_hours",
    } <= keys
