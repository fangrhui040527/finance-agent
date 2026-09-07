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
    """The real settings, with the repository's own data stores taken away.

    Same lesson as the debug_root isolation below, learned twice more on
    2026-09-06: data/corpus.db and data/facts.db are TRACKED, and they age. A
    corpus whose last successful sweep is 31 hours old trips `sweep_silence`,
    and a fact book carrying a series a year stale trips `series_stale` - from
    outside the case, on a clock nobody set. Each rule's own tests point at a
    store they built; every other test here sees none.
    """
    over.setdefault("corpus_db", "tests/no-such-corpus.db")
    over.setdefault("facts_db", "tests/no-such-facts.db")
    return replace(load_config(), **over)


#: The nightly pages are tracked too, and their questions age. Every case that
#: is not about them sees a directory that does not exist; the ones that are
#: pass `feedback_root` explicitly, which wins over this.
NO_PAGES = "tests/no-such-feedback"


@pytest.fixture(autouse=True)
def _no_real_pages(monkeypatch):
    monkeypatch.setattr("core.monitor.FEEDBACK_DIR", NO_PAGES)


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
    # An explicit clock, one day after the newest run. Without it this test read
    # the wall clock and would pass or fail depending on how long after the
    # fixture dates it happened to be run - the same fragility that made three
    # monitor tests go red on main when the tracked corpus aged.
    day_after = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    alerts = evaluate(_cfg(), db=str(empty), debug_root=str(root), now=day_after)
    meth = [x for x in alerts if x.rule == "methodology_changed"]
    assert meth and "system prompt changed" in meth[0].detail


def test_a_method_change_nobody_has_run_since_stops_being_news(tmp_path):
    """`methodology_changed` is a TRANSITION alert. It compares the two newest
    traced runs, so once tracing stops it compares the same pair forever and can
    never clear - which is how a tool being added on 4 September, entirely
    expected after a deploy, was still sitting open three days later."""
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
    weeks_later = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    alerts = evaluate(_cfg(), db=str(empty), debug_root=str(root), now=weeks_later)
    assert not [x for x in alerts if x.rule == "methodology_changed"]


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

    # The ledger and the alert store were isolated; the TRACES were not, so this
    # read the repository's own debug/ directory and the case went red the first
    # time a real run left a methodology change there. An empty root is what
    # "nothing tripped" was always supposed to mean.
    from core import monitor as _m

    # ...and the same for the tracked data stores, which age: cmd_watch loads
    # the real config, so the settings have to be replaced here rather than
    # passed in.
    _real_check = _m.check
    monkeypatch.setattr(
        _m,
        "check",
        lambda cfg=None, **kw: _real_check(
            _cfg(), **{**kw, "debug_root": str(tmp_path / "no-traces")}
        ),
    )

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


# --- sweep_silence: the death detector for the scheduled sweep --------------------
#
# `silence` watches the model ledger. `ask.py sweep` makes no model calls, so the
# day a sweep goes on a timer the existing rule is watching the wrong thing.


def _swept(path: Path, source: str = "gdelt", status: str = "ok", at: datetime | None = None):
    from knowledge.corpus import Corpus

    with Corpus(path) as c:
        c.record_sweep("r", source, at or datetime.now(UTC), status, at=at)
    return path


def _sweep_cfg(tmp_path: Path, hours: int = 30, sources: tuple[str, ...] = ("gdelt",), **over):
    return _cfg(
        alert_sweep_silence_hours=hours,
        corpus_db=str(tmp_path / "corpus.db"),
        sources=sources,
        **over,
    )


def test_a_sweep_that_stopped_is_an_alert(tmp_path):
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", at=now - timedelta(hours=50))
    alerts = evaluate(_sweep_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    sweep = [a for a in alerts if a.rule == "sweep_silence"]
    assert sweep and sweep[0].severity == ALERT
    assert "gdelt" in sweep[0].title
    assert "50h ago" in sweep[0].title


def test_a_sweep_inside_the_window_is_not_an_alert(tmp_path):
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", at=now - timedelta(hours=20))
    alerts = evaluate(_sweep_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "sweep_silence"]


def test_a_corpus_nobody_has_filled_yet_is_not_a_stop(tmp_path):
    """The same discipline `silence` uses on the ledger. A system nobody turned
    on is not a system that died, and alerting on it teaches you to ignore it."""
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", status="failed", at=now - timedelta(hours=50))
    alerts = evaluate(_sweep_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "sweep_silence"]


def test_a_week_of_refused_sweeps_trips_the_same_rule(tmp_path):
    """A failed sweep is not a successful one, so a network that has refused
    every request since Tuesday stops producing ok rows exactly as a dead
    scheduler does - and both want the same look from a person."""
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    path = tmp_path / "corpus.db"
    _swept(path, at=now - timedelta(days=7))
    for day in range(6):
        _swept(path, status="failed", at=now - timedelta(days=day))
    alerts = evaluate(_sweep_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert [a for a in alerts if a.rule == "sweep_silence"]


def test_the_sweep_rule_is_off_by_default(tmp_path):
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", at=now - timedelta(days=30))
    alerts = evaluate(_sweep_cfg(tmp_path, hours=0), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "sweep_silence"]


# --- questions the pages have carried too long ------------------------------------------------


def _pages(directory: Path, days_old: int) -> Path:
    """One page carrying a question first asked `days_old` days before it."""
    import json as _json
    from datetime import date as _date

    directory.mkdir(parents=True, exist_ok=True)
    day = _date(2026, 9, 25)
    asked = day - timedelta(days=days_old)
    (directory / f"{day}.json").write_text(
        _json.dumps(
            {
                "day": str(day),
                "open_questions_carried": [
                    f"The six Bursa names have no fact-book coverage (since {asked})"
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_a_question_carried_past_three_weeks_is_a_finding_not_a_question(tmp_path):
    """The pages ask the right questions and carry them correctly. What nothing
    noticed was how long one had stood - and an old one is rarely a hard
    question, it is a source nobody wired."""
    now = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    root = _pages(tmp_path / "pages", days_old=30)
    alerts = evaluate(
        _cfg(), db=str(_ledger(tmp_path / "led.db")), now=now, feedback_root=str(root)
    )
    (q,) = [a for a in alerts if a.rule == "open_question_stale"]
    assert "30d" in q.title and "Bursa" in q.title
    assert q.evidence["questions"][0]["age_days"] == 30


def test_a_question_asked_last_week_is_just_a_question(tmp_path):
    now = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    root = _pages(tmp_path / "pages", days_old=7)
    alerts = evaluate(
        _cfg(), db=str(_ledger(tmp_path / "led.db")), now=now, feedback_root=str(root)
    )
    assert not [a for a in alerts if a.rule == "open_question_stale"]


def test_no_pages_directory_is_not_an_error(tmp_path):
    now = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    alerts = evaluate(
        _cfg(), db=str(_ledger(tmp_path / "led.db")), now=now, feedback_root=str(tmp_path / "none")
    )
    assert not [a for a in alerts if a.rule == "open_question_stale"]


# --- series staleness ------------------------------------------------------------------------


def _facts_with_series(path: Path, rows: list[tuple[str, str]]):
    """A fact book holding one point per (series_id, obs_date)."""
    from datetime import date as _date

    from knowledge.facts import FactBook, SeriesPoint

    with FactBook(str(path)) as book:
        book.add_series(
            [
                SeriesPoint(
                    "t",
                    sid,
                    _date.fromisoformat(day),
                    Decimal("1"),
                    known_at=_date.fromisoformat(day),
                )
                for sid, day in rows
            ]
        )
    return path


def _series_cfg(tmp_path, **over):
    return _cfg(
        alert_silence_hours=0,
        alert_sweep_silence_hours=0,
        facts_db=str(tmp_path / "facts.db"),
        **over,
    )


def test_a_series_past_its_cadence_is_an_alert_and_names_the_worst_first(tmp_path):
    """The 2026-09-06 finding: sixteen series 432-493 days old, and Malaysian
    CPI reading 1982, while every sweep beside them reported ok. sweep_silence
    cannot see this - the sweep is not silent."""
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    _facts_with_series(
        tmp_path / "facts.db",
        [
            ("DOSM:CPI_HEADLINE", "1982-12-01"),
            ("DBN:NEER_MY", "2025-05-01"),
            ("DGS10", "2026-09-03"),
        ],
    )
    alerts = evaluate(_series_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    stale = [a for a in alerts if a.rule == "series_stale"]
    assert stale and stale[0].severity == ALERT
    assert stale[0].title.startswith("2 macro series past their cadence: DOSM:CPI_HEADLINE")
    ids = [row["series_id"] for row in stale[0].evidence["stale"]]
    assert ids == ["DOSM:CPI_HEADLINE", "DBN:NEER_MY"]  # DGS10, three days old, is not named


def test_a_series_inside_its_cadence_is_quiet(tmp_path):
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    _facts_with_series(
        tmp_path / "facts.db",
        [("DGS10", "2026-09-03"), ("CPIAUCSL", "2026-07-01"), ("BNM:OPR", "2026-07-10")],
    )
    alerts = evaluate(_series_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "series_stale"]


def test_a_series_with_no_declared_cadence_is_not_judged(tmp_path):
    """Guessing a limit from a prefix produces confident alerts about a rhythm
    nobody checked. An unknown id is added to freshness.py deliberately or not
    at all."""
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    _facts_with_series(tmp_path / "facts.db", [("SOMETHING:NEW", "2019-01-01")])
    alerts = evaluate(_series_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "series_stale"]


def test_a_missing_fact_book_is_not_an_error(tmp_path):
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    alerts = evaluate(_series_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "series_stale"]


def test_a_missing_corpus_is_not_an_error(tmp_path):
    """Enabled before the first sweep ever ran. It must not crash the monitor -
    the check that dies is the failure a monitor exists to catch."""
    now = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    alerts = evaluate(_sweep_cfg(tmp_path), db=str(_ledger(tmp_path / "led.db")), now=now)
    assert not [a for a in alerts if a.rule == "sweep_silence"]


def test_the_shipped_config_turns_the_sweep_rule_on(tmp_path):
    """It is scheduled, so it is watched. `silence_hours` stays off because a
    personal tool may sit idle; the sweep may not."""
    assert load_config().alert_sweep_silence_hours == 30


# --- a rule that is wrong on a timetable is worse than no rule -------------------


def test_a_daily_source_keeps_the_configured_allowance():
    """No behaviour change for the sources the rule was written against."""
    from core.monitor import sweep_allowance_hours

    for daily in ("gdelt", "google_news", "edgar", "finnhub"):
        assert sweep_allowance_hours(daily, 30) == 30


def test_a_weekday_only_source_is_given_the_weekend():
    """`fred` runs at us_preopen, weekdays. Friday to Monday is three days, so a
    30-hour line made it 'silent' every Saturday, Sunday and Monday morning
    while the collector was running perfectly."""
    from core.monitor import sweep_allowance_hours

    assert sweep_allowance_hours("fred", 30) == 78
    assert sweep_allowance_hours("dbnomics", 30) == 78


def test_a_weekly_source_is_given_its_week():
    """dosm_cpi, sec_xbrl and finmind fire once on a Sunday. A 30-hour rule
    calls them dead six days out of every seven."""
    from core.monitor import sweep_allowance_hours

    for weekly in ("dosm_cpi", "sec_xbrl", "finmind"):
        assert sweep_allowance_hours(weekly, 30) == 174


def test_the_most_frequent_slot_sets_the_expectation():
    """A source in both a daily and a weekly slot should still report daily."""
    from core.monitor import sweep_allowance_hours

    assert sweep_allowance_hours("twse_openapi", 30) == 30  # bursa_close + weekly
    assert sweep_allowance_hours("eodhd", 30) == 30  # bursa_close + us_close


def test_a_source_the_catalogue_does_not_know_falls_back():
    from core.monitor import sweep_allowance_hours

    assert sweep_allowance_hours("not_a_real_source", 30) == 30


def test_a_weekday_source_silent_over_one_weekend_is_not_an_alert(tmp_path):
    """The exact 2026-09-07 false alarm, pinned: Friday's us_preopen run to
    Monday morning is 68 hours, over the old flat 30-hour line, and the
    collector was working perfectly the whole time."""
    friday = datetime(2026, 9, 4, 12, 30, tzinfo=UTC)
    monday = datetime(2026, 9, 7, 8, 38, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", source="fred", at=friday)
    cfg = _sweep_cfg(tmp_path, sources=("fred",))
    assert (monday - friday).total_seconds() / 3600 > 30, "the old rule would have fired"
    alerts = evaluate(cfg, db=str(_ledger(tmp_path / "led.db")), now=monday)
    assert not [a for a in alerts if a.rule == "sweep_silence"]


def test_a_weekday_source_silent_past_its_own_cadence_still_alerts(tmp_path):
    """The rule must not have been softened into uselessness."""
    now = datetime(2026, 9, 7, 8, 38, tzinfo=UTC)
    _swept(tmp_path / "corpus.db", source="fred", at=now - timedelta(hours=90))
    cfg = _sweep_cfg(tmp_path, sources=("fred",))
    fired = [
        a
        for a in evaluate(cfg, db=str(_ledger(tmp_path / "led.db")), now=now)
        if a.rule == "sweep_silence"
    ]
    assert fired and "allowed 78h" in fired[0].title
