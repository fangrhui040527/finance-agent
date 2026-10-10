"""An open alert says what is failing now, and an old traced error says its age.

What now holds:

  * A rule that rolls many members into one alert (sweep_silence here) and
    stays open while its members change writes an `updated` event carrying the
    new title and evidence. The same members with a new title (an age that
    moved) write nothing. `check()` reports it as UPDATED, never as NEW, and
    every reader of the open state - `AlertLog.open_rules`, `ask.py alerts`,
    the `open_alerts` MCP tool and the web `/alerts` endpoint - shows the
    latest title, while "open since" stays the time it first opened.
  * `run_errors` on a traced run older than RUN_ERRORS_FRESH_DAYS is a WARN
    whose title states the run's age; inside the window it stays an ALERT. It
    is never resolved by age, and the downgrade of a rule already open is
    recorded as an `updated` event.

Each test fails on the code before 2026-10-10:

  * monitor-ops-4: a change of members under an open aggregated rule was put
    in `still_open` and nothing was written, so every reader kept printing the
    title of the row that opened it - naming a source that had recovered and
    never the one that had died.
  * monitor-ops-5: `run_errors` read the newest run whatever its age, as an
    ALERT with no age in it, so an error in a run nobody had traced for weeks
    read as current every hour.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import monitor
from core.monitor import ALERT, WARN, Alert, AlertLog, check

T0 = datetime(2026, 9, 9, 22, 0, tzinfo=UTC)


def _silence(*sources: str, hours: int = 33) -> Alert:
    named = ", ".join(f"{s} ({hours}h ago, allowed 30h)" for s in sorted(sources))
    return Alert(
        rule="sweep_silence",
        severity=ALERT,
        title=f"past its own cadence: {named}",
        detail="d",
        next_step="n",
        evidence={"silence_hours": 30, "sources": sorted(sources)},
    )


def _check(monkeypatch, adb: str, alert: Alert, at: datetime):
    monkeypatch.setattr(monitor, "evaluate", lambda *a, **k: [alert])
    return check(object(), alerts_db=adb, now=at)


def _states(adb: str) -> list[str]:
    with AlertLog(adb) as log:
        return [r["state"] for r in reversed(log.history())]


def test_a_second_source_dying_under_an_open_rule_is_recorded(tmp_path, monkeypatch):
    adb = str(tmp_path / "alerts.db")
    first = _check(monkeypatch, adb, _silence("twse_openapi"), T0)
    assert [a.rule for a in first.opened] == ["sweep_silence"]

    second = _check(monkeypatch, adb, _silence("twse_openapi", "gdelt"), T0 + timedelta(hours=1))
    assert second.opened == [] and second.still_open == []
    assert [a.rule for a in second.updated] == ["sweep_silence"]
    assert second.any_open
    text = second.render()
    assert "UPDATED" in text and "NEW" not in text and "gdelt" in text

    assert _states(adb) == ["opened", "updated"]
    with AlertLog(adb) as log:
        row = log.open_rules()["sweep_silence"]
    assert "gdelt" in row["title"]
    assert json.loads(row["evidence_json"])["sources"] == ["gdelt", "twse_openapi"]
    assert row["opened_at"] == T0.isoformat()  # open since it opened, not since it moved

    # twse recovers while gdelt stays dead: another change, another event.
    _check(monkeypatch, adb, _silence("gdelt"), T0 + timedelta(hours=2))
    assert _states(adb) == ["opened", "updated", "updated"]
    with AlertLog(adb) as log:
        assert "twse" not in log.open_rules()["sweep_silence"]["title"]

    # And it still resolves once nothing fires.
    monkeypatch.setattr(monitor, "evaluate", lambda *a, **k: [])
    done = check(object(), alerts_db=adb, now=T0 + timedelta(hours=3))
    assert done.resolved == ["sweep_silence"]
    with AlertLog(adb) as log:
        assert log.open_rules() == {}


def test_the_same_members_with_a_new_age_write_nothing(tmp_path, monkeypatch):
    adb = str(tmp_path / "alerts.db")
    _check(monkeypatch, adb, _silence("twse_openapi", hours=33), T0)
    again = _check(monkeypatch, adb, _silence("twse_openapi", hours=34), T0 + timedelta(hours=1))
    assert again.updated == [] and len(again.still_open) == 1
    assert "still open" in again.render() and "UPDATED" not in again.render()
    assert _states(adb) == ["opened"]


def test_every_reader_shows_the_latest_title(tmp_path, monkeypatch, capsys):
    import ask
    from mcp_server.observability import open_alerts

    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)  # the web endpoint reads the default data/alerts.db
    adb = str(Path("data") / "alerts.db")
    _check(monkeypatch, adb, _silence("twse_openapi"), T0)
    _check(monkeypatch, adb, _silence("gdelt"), T0 + timedelta(days=1))

    out = open_alerts(alerts_db=adb)
    current = out.split("history")[0]
    assert "gdelt" in current and "twse_openapi" not in current
    assert f"open since {T0.isoformat()[:19]}" in current and "updated" in current
    assert "updated" in out.split("history")[1]

    assert ask.main(["alerts", "--alerts-db", adb]) == 0
    cli = capsys.readouterr().out.split("history")[0]
    assert "gdelt" in cli and "twse_openapi" not in cli and "updated" in cli

    from web.api import alerts

    (item,) = alerts().data["open"]
    assert "gdelt" in item["title"] and "twse_openapi" not in item["title"]
    assert item["since"] == T0.isoformat()
    assert item["updated"] == (T0 + timedelta(days=1)).isoformat()


# --- run_errors ---------------------------------------------------------------------

RUN = "20260904T061546-eefefe"
STARTED = datetime(2026, 9, 4, 6, 15, 46, tzinfo=UTC)


@pytest.fixture
def traced(tmp_path) -> str:
    root = tmp_path / "debug"
    d = root / RUN
    d.mkdir(parents=True)
    (d / "trace.jsonl").write_text(
        json.dumps({"kind": "error", "name": "a9", "error": "boom"}) + "\n", encoding="utf-8"
    )
    (d / "summary.json").write_text(json.dumps({"run_id": RUN, "events": 1}), encoding="utf-8")
    return str(root)


def _run_errors(root: str, now: datetime) -> Alert:
    (alert,) = [a for a in monitor._trace_rules(root, now=now) if a.rule == "run_errors"]
    return alert


def test_an_error_in_a_recent_run_is_an_alert(traced):
    alert = _run_errors(traced, STARTED + timedelta(days=1))
    assert alert.severity == ALERT
    assert "boom" in alert.detail


def test_an_error_in_a_run_nobody_has_traced_since_is_a_warning_with_its_age(traced):
    later = STARTED + timedelta(days=monitor.RUN_ERRORS_FRESH_DAYS + 1)
    alert = _run_errors(traced, later)  # never resolved: it is still reported
    assert alert.severity == WARN
    assert f"{monitor.RUN_ERRORS_FRESH_DAYS + 1}d old" in alert.title
    assert "trace_run.py" in alert.next_step
    assert _run_errors(traced, STARTED + timedelta(days=365)).severity == WARN


def test_the_downgrade_of_an_open_run_error_is_recorded(tmp_path, traced, monkeypatch):
    adb = str(tmp_path / "alerts.db")
    real = monitor._trace_rules
    monkeypatch.setattr(monitor, "evaluate", lambda *a, now=None, **k: real(traced, now=now))
    check(object(), alerts_db=adb, now=STARTED + timedelta(days=1))
    aged = check(object(), alerts_db=adb, now=STARTED + timedelta(days=40))
    assert [a.severity for a in aged.updated] == [WARN]
    with AlertLog(adb) as log:
        row = log.open_rules()["run_errors"]
    assert row["severity"] == WARN and "40d old" in row["title"]
