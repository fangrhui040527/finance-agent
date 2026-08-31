"""The prediction log has to survive a restart, or the three-to-six month clock
in docs/14 section 2 restarts with it."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import predict
from agents.learning.reflection import (
    Author,
    Horizon,
    Lesson,
    Outcome,
    Prediction,
    ProvenanceMarker,
    Status,
)
from agents.learning.store import LearningStore

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def db(tmp_path):
    return str(tmp_path / "learning.db")


def prediction(pid="p1", instrument="MYX:1155", days=63, conf=0.62):
    return Prediction(
        pid,
        instrument,
        "a10_thesis",
        NOW,
        Horizon.D63,
        "NIM stabilises",
        1,
        conf,
        grade_on=(NOW + timedelta(days=days)).date(),
    )


# -- persistence -------------------------------------------------------------


def test_a_logged_prediction_survives_the_process(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction())
    with LearningStore(path) as s:  # a different connection entirely
        assert [p.prediction_id for p in s.pending()] == ["p1"]


def test_the_queue_rebuilds_from_disk_with_its_refusal_intact(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction())
    with LearningStore(path) as s:
        queue = s.load_queue()
        with pytest.raises(ValueError, match="score noise"):
            queue.grade("p1", (NOW + timedelta(days=3)).date(), 0.05, 0.01)


def test_grading_moves_a_prediction_out_of_pending(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction())
        q = s.load_queue()
        s.record_outcome(q.grade("p1", (NOW + timedelta(days=63)).date(), 0.05, 0.01))
    with LearningStore(path) as s:
        assert s.pending() == []
        assert len(s.graded()) == 1
        assert s.counts() == {"logged": 1, "graded": 1, "pending": 0, "lessons": 0}


def test_the_original_statement_and_confidence_are_preserved_verbatim(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction(conf=0.62))
        s.record_outcome(Outcome("p1", NOW.date(), 0.05, 0.01, True))
    with LearningStore(path) as s:
        assert s.calibration_pairs() == [(0.62, True)]


# -- immutability ------------------------------------------------------------


def test_a_prediction_cannot_be_edited_after_the_fact(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction(conf=0.55))
        with pytest.raises(sqlite3.IntegrityError, match="becomes a memory"):
            s.db.execute("UPDATE predictions SET confidence = 0.95")


def test_a_prediction_cannot_be_deleted(tmp_path):
    """A log missing its losers produces confident, wrong calibration."""
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction())
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            s.db.execute("DELETE FROM predictions")


def test_an_outcome_cannot_be_flipped(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.record(prediction())
        s.record_outcome(Outcome("p1", NOW.date(), 0.01, 0.05, False))
        with pytest.raises(sqlite3.IntegrityError, match="graded once"):
            s.db.execute("UPDATE outcomes SET correct = 1")


def test_logging_the_same_id_twice_is_refused_with_a_reason(tmp_path):
    with LearningStore(db(tmp_path)) as s:
        s.record(prediction())
        with pytest.raises(ValueError, match="written once"):
            s.record(prediction())


# -- lessons -----------------------------------------------------------------


def lesson(status=Status.ACTIVE):
    return Lesson(
        "L1",
        "unexplained gaps reverse",
        "gap",
        8,
        5,
        0.72,
        NOW,
        ProvenanceMarker(created_by=Author.AGENT, created_at=NOW),
        status=status,
        last_confirmed=NOW,
        evidence=("p1", "p2"),
    )


def test_lessons_round_trip_with_their_provenance(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.save_lesson(lesson())
    with LearningStore(path) as s:
        got = s.load_lessons().get("L1")
        assert got.text == "unexplained gaps reverse"
        assert got.editable(), "an agent-created lesson stays agent-editable across a restart"
        assert got.evidence == ("p1", "p2")


def test_an_archived_lesson_is_still_there_after_a_restart(tmp_path):
    path = db(tmp_path)
    with LearningStore(path) as s:
        s.save_lesson(lesson(status=Status.ARCHIVED))
    with LearningStore(path) as s:
        assert s.load_lessons().get("L1").status is Status.ARCHIVED
        assert s.counts()["lessons"] == 0, "archived lessons are not active"


def test_a_human_authored_lesson_stays_read_only_across_a_restart(tmp_path):
    path = db(tmp_path)
    human = Lesson(
        "L2", "t", "p", 9, 5, 0.7, NOW, ProvenanceMarker(created_by=Author.HUMAN, created_at=NOW)
    )
    with LearningStore(path) as s:
        s.save_lesson(human)
    with LearningStore(path) as s:
        assert not s.load_lessons().get("L2").editable()


# -- the CLI -----------------------------------------------------------------


def run(args, path, capsys):
    code = predict.main(["--db", path] + args)
    return code, capsys.readouterr().out


def test_the_cli_logs_and_reports_pending(tmp_path, capsys):
    path = db(tmp_path)
    code, out = run(["log", "MYX:1155", "1", "63d", "0.62", "NIM stabilises"], path, capsys)
    assert code == 0
    assert "logged" in out and "1 pending" in out
    assert "grades on" in out


def test_the_cli_refuses_to_grade_before_the_horizon(tmp_path, capsys):
    path = db(tmp_path)
    run(["log", "X", "1", "21d", "0.6", "s", "--id", "p1"], path, capsys)
    code, _ = run(["grade", "p1", "--return", "0.05", "--benchmark", "0.01"], path, capsys)
    assert code == 1, "grading early must fail loudly, not quietly succeed"


def test_the_cli_grades_against_the_benchmark_not_zero(tmp_path, capsys):
    path = db(tmp_path)
    run(
        ["log", "X", "1", "21d", "0.6", "s", "--id", "p1", "--grade-on", "2026-09-22"], path, capsys
    )
    code, out = run(
        ["grade", "p1", "--return", "0.031", "--benchmark", "0.048", "--today", "2026-09-22"],
        path,
        capsys,
    )
    assert code == 0
    assert "wrong" in out, "up 3.1% against a benchmark up 4.8% is wrong"


def test_the_cli_says_how_far_from_a_meaningful_calibration(tmp_path, capsys):
    _, out = run(["status"], db(tmp_path), capsys)
    assert "30 more graded calls" in out


def test_the_cli_shows_the_calibration_table_once_there_is_enough(tmp_path, capsys):
    path = db(tmp_path)
    with LearningStore(path) as s:
        for i in range(40):
            s.record(prediction(pid=f"p{i}", instrument=f"S{i % 7}", conf=0.9))
            s.record_outcome(Outcome(f"p{i}", NOW.date(), 0.05, 0.01, i % 2 == 0))
    _, out = run(["status"], path, capsys)
    assert "calibration over 40" in out
    assert "overconfident" in out, "stating 90% and realising 50% must be called out"


def test_due_lists_overdue_items(tmp_path, capsys):
    """Overdue is reached by time passing, never by backdating grade_on - the
    contract refuses that, which is why --today exists instead."""
    path = db(tmp_path)
    run(["log", "X", "1", "5d", "0.6", "s", "--id", "p1", "--grade-on", "2026-09-01"], path, capsys)
    _, out = run(["due", "--today", "2026-09-06"], path, capsys)
    assert "5d overdue" in out


def test_a_grading_date_in_the_past_cannot_be_logged_at_all(tmp_path, capsys):
    with pytest.raises(ValueError, match="not a horizon"):
        run(["log", "X", "1", "5d", "0.6", "s", "--grade-on", "2020-01-01"], db(tmp_path), capsys)
