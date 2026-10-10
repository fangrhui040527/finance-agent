"""What a logged prediction means, and how it is graded, holds together.

- An MCP `log_prediction` id is unique per view: a second view of the same
  instrument, day and horizon gets its own id and is stored, and a refusal from
  the store comes back as REFUSED, never as an uncaught ValueError.
- `predict.py reflect` counts its distinct-instrument gate over the GRADED
  linked predictions only: pending links contribute no outcome, so no name.
- A horizon of N is N exchange sessions on the instrument's own calendar (the
  book's markets for an all-cash night), in predict.py, the MCP tool and the
  paper book alike: a 1d call made on a Friday grades on the Monday.
- A paper grade measures the name and the control over ONE window: from the
  close of the session before the fill day to the close on the grading day.
- A due paper prediction that cannot be priced is reported by `ask.py paper
  grade` as skipped, with its reason, and the command exits 3.
- A paper decision superseded the same day withdraws its predictions: they are
  never graded and stay out of `calibration_pairs`, and an all-cash night that
  is superseded withdraws its CASH call too.

Each test fails on the code before 2026-10-10:

- attribution-learning-6: the id was instrument-date-horizon, so the second
  view raised `ValueError: ... is already logged` through the MCP layer.
- attribution-learning-5: five graded calls on one name plus two pending calls
  on others wrote a lesson "across 3 instruments".
- attribution-learning-3: grade_on was horizon*7/5 calendar days, so a Friday
  1d call graded on the Sunday after zero sessions and 5d over Hari Raya fell
  short of five Bursa sessions.
- paper-6: the name ran from the fill day's open and the control from its
  fill-day close, so a +5% fill-day move counted for the name only.
- cross-cutting-3: the reason was dropped and the command printed "nothing
  due: no paper prediction has reached its grading date" and exited 0.
- attribution-learning-2: the superseded predictions stayed pending and were
  graded, MYX:5183 twice, and all four counted in calibration.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import mcp_server.tools as T
import predict
from agents.learning.hypotheses import HypothesisStore
from agents.learning.reflection import Horizon, Outcome, Prediction
from agents.learning.store import LearningStore


def test_two_views_of_one_name_day_and_horizon_are_both_logged(tmp_path):
    db = str(tmp_path / "p.db")
    first = T.log_prediction("XNAS:NVDA", 1, 21, 0.6, "AI capex", db=db)
    second = T.log_prediction("XNAS:NVDA", -1, 21, 0.55, "export curbs news", db=db)
    assert first.startswith("logged XNAS:NVDA-") and second.startswith("logged XNAS:NVDA-")
    with LearningStore(db) as s:
        pending = s.pending()
    assert len(pending) == 2
    assert len({p.prediction_id for p in pending}) == 2
    assert sorted(p.direction for p in pending) == [-1, 1]


def test_a_store_refusal_is_an_answer_not_a_traceback(tmp_path, monkeypatch):
    def refuse(self, p):
        raise ValueError(f"{p.prediction_id} is already logged.")

    monkeypatch.setattr(LearningStore, "record", refuse)
    out = T.log_prediction("XNAS:NVDA", 1, 21, 0.6, "AI capex", db=str(tmp_path / "p.db"))
    assert out.startswith("REFUSED: ") and "already logged" in out


def test_reflect_counts_only_the_names_that_were_graded(tmp_path, capsys):
    db = str(tmp_path / "learning.db")
    made = datetime(2026, 1, 5, 12, tzinfo=UTC)
    with LearningStore(db) as s, HypothesisStore(db) as h:
        hid = h.create("banks rerate", "MYX banks re-rate as NIM stabilises")
        for i in range(5):
            pid = f"g{i}"
            s.record(
                Prediction(
                    pid,
                    "MYX:1155",
                    "human",
                    made + timedelta(days=i),
                    Horizon.D21,
                    "s",
                    1,
                    0.6,
                    date(2026, 2, 10),
                )
            )
            s.record_outcome(Outcome(pid, date(2026, 2, 10), 0.05, 0.01, True))
            h.link(hid, pid)
        for iid in ("MYX:1295", "MYX:5819"):
            pid = "p-" + iid
            s.record(
                Prediction(pid, iid, "human", made, Horizon.D252, "s", 1, 0.6, date(2027, 1, 5))
            )
            h.link(hid, pid)
    assert predict.main(["--db", db, "reflect", hid]) == 0
    out = capsys.readouterr().out
    assert "linked 7, graded 5" in out
    assert "[no_lesson]" in out and "only 1 distinct instruments" in out
    assert "lesson_written" not in out


def test_a_friday_one_session_call_grades_on_the_monday(tmp_path, monkeypatch, capsys):
    friday = datetime(2026, 10, 9, 21, 30, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return friday

    monkeypatch.setattr(predict, "datetime", Clock)
    monkeypatch.setattr(T, "datetime", Clock)
    db = str(tmp_path / "p.db")
    assert predict.main(["--db", db, "log", "XNAS:NVDA", "1", "1d", "0.6", "s"]) == 0
    assert "grades on 2026-10-12" in capsys.readouterr().out
    assert "gradeable on or after 2026-10-12" in T.log_prediction(
        "XNAS:NVDA", 1, 1, 0.6, "s", db=db
    )


def test_a_horizon_counts_exchange_holidays_out(tmp_path, monkeypatch, capsys):
    # Monday 16 March 2026: Bursa shuts on the 20th and the 23rd (Hari Raya),
    # Nasdaq does not, so five sessions end on different days.
    monday = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return monday

    monkeypatch.setattr(predict, "datetime", Clock)
    db = str(tmp_path / "p.db")
    assert predict.main(["--db", db, "log", "MYX:1155", "1", "5d", "0.6", "s"]) == 0
    assert predict.main(["--db", db, "log", "XNAS:NVDA", "1", "5d", "0.6", "s"]) == 0
    with LearningStore(db) as s:
        by_name = {p.instrument_id: p.grade_on for p in s.pending()}
    assert by_name == {"MYX:1155": date(2026, 3, 25), "XNAS:NVDA": date(2026, 3, 23)}


def test_the_paper_book_counts_sessions_per_name_and_on_every_market_for_cash(paper_env):
    from decimal import Decimal

    from engines.paper.book import decide
    from engines.paper.store import CASH

    env = paper_env

    def at(d):
        return datetime(d.year, d.month, d.day, 22, 30, tzinfo=UTC)

    # Friday 13 March 2026, an observe week. Five Bursa sessions run to the 24th
    # (Hari Raya shuts the 20th and the 23rd); five Nasdaq sessions to the 20th.
    friday = env.week(2, 4)
    d = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=friday,
        weights={"MYX:5183": Decimal("0.11"), "XNAS:NVDA": Decimal("0.23")},
        thesis="t",
        horizon=5,
        learning=env.learning,
        now=at(friday),
    )
    assert not d.refused, d.refusals
    assert {p.instrument_id: p.grade_on for p in d.predictions} == {
        "MYX:5183": date(2026, 3, 24),
        "XNAS:NVDA": date(2026, 3, 20),
    }
    # An all-cash night is a claim about the whole book: one session on EVERY
    # market it trades, so Thursday the 19th grades on Bursa's next, the 24th.
    thursday = date(2026, 3, 19)
    cash = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=thursday,
        weights={},
        thesis="t",
        horizon=1,
        learning=env.learning,
        now=at(thursday),
    )
    assert not cash.refused and cash.all_cash, cash.refusals
    (p,) = cash.predictions
    assert p.instrument_id == CASH and p.grade_on == date(2026, 3, 24)


def _at(d, h=22, m=30):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _mark_through(env, first, last):
    from engines.paper.book import mark

    day = first
    while day <= last:
        if day.weekday() < 5:
            mark(env.store, env.cfg, env.feed, env.fx, day=day, slot="us_close", now=_at(day))
        day += timedelta(days=1)


def test_a_grade_measures_name_and_control_over_one_window(paper_env):
    from decimal import Decimal

    from engines.paper.book import decide
    from engines.paper.grade import grade_due
    from engines.paper.pricing import last_close
    from engines.paper.store import CONTROL
    from tests.conftest import PAPER_PRICES, SyntheticFeed

    env = paper_env
    d16 = env.week(3)
    fill = d16 + timedelta(days=1)
    # A steady drift (flat bars read as holiday fillers) and +5% on every name
    # on the fill day.
    env.feed = SyntheticFeed(
        date(2025, 9, 1),
        date(2026, 7, 31),
        drift=0.0002,
        wobble=0.0,
        shocks={(iid, fill): 0.05 for iid in PAPER_PRICES},
    )
    _mark_through(env, env.week(1), env.week(2, 4))
    d = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={"MYX:5183": Decimal("0.11"), "MYX:8869": Decimal("0.21")},
        thesis="t",
        horizon=5,
        learning=env.learning,
        now=_at(d16),
    )
    assert not d.refused
    grade_day = max(p.grade_on for p in d.predictions)
    _mark_through(env, d16, grade_day)
    assert {c.day for c in env.store.changes("decided")} == {fill}
    graded = grade_due(env.store, env.cfg, env.feed, env.fx, day=grade_day, learning=env.learning)
    assert sorted(g.instrument_id for g in graded) == ["MYX:5183", "MYX:8869"]
    ctl_ref = env.store.mark_before(CONTROL, fill)
    ctl_now = env.store.latest_mark(CONTROL, on_or_before=grade_day)
    assert ctl_ref is not None and ctl_now is not None and ctl_ref.day == d16
    control = float(ctl_now.equity_usd / ctl_ref.equity_usd - 1)
    for g in graded:
        before, before_day = last_close(env.feed, g.instrument_id, d16)
        after, _ = last_close(env.feed, g.instrument_id, grade_day)
        assert before_day == d16
        # the name: the whole fill-day move, from the close before it
        assert abs(g.realised - float(after / before - 1)) < 1e-9
        assert g.realised > 0.05
        # the control: the same days, from its mark before the fill day
        assert abs(g.benchmark - control) < 1e-12


def test_an_unpriceable_due_prediction_is_reported_and_exits_3(tmp_path, capsys, monkeypatch):
    import ask

    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    db, ldb = str(tmp_path / "p.db"), str(tmp_path / "l.db")
    common = ["--db", db, "--learning-db", ldb]
    assert ask.main(["paper", "init", *common, "--start", "2026-03-02"]) == 0
    with LearningStore(ldb) as s:
        s.record(
            Prediction(
                "paper-2026-03-02-xnaszzzz-5d-0000",
                "XNAS:ZZZZ",
                "paper",
                datetime(2026, 3, 2, 22, 30, tzinfo=UTC),
                Horizon.D5,
                "XNAS:ZZZZ target 5%",
                1,
                0.55,
                date(2026, 3, 9),
                {"paper": True, "decided_on": "2026-03-02"},
            )
        )
    capsys.readouterr()
    assert ask.main(["paper", "grade", *common, "--date", "2026-03-18"]) == 3
    out = capsys.readouterr().out
    assert "nothing due" not in out
    assert "SKIPPED 1" in out and "paper-2026-03-02-xnaszzzz-5d-0000" in out
    assert "cannot price" in out and "XNAS:ZZZZ" in out
    with LearningStore(ldb) as s:
        assert [p.prediction_id for p in s.pending()] == ["paper-2026-03-02-xnaszzzz-5d-0000"]


def test_a_superseded_decision_withdraws_its_predictions(paper_env):
    from decimal import Decimal

    from engines.paper.book import decide
    from engines.paper.grade import grade_due

    env = paper_env
    d16 = env.week(3)
    _mark_through(env, env.week(2, 4), env.week(2, 4))
    first = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={
            "MYX:5183": Decimal("0.11"),
            "MYX:8869": Decimal("0.21"),
            "MYX:3182": Decimal("0.06"),
        },
        thesis="t",
        horizon=5,
        learning=env.learning,
        now=_at(d16),
    )
    second = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={"MYX:5183": Decimal("0.11")},
        thesis="t2",
        horizon=5,
        learning=env.learning,
        now=_at(d16) + timedelta(minutes=5),
        supersede=True,
    )
    assert not first.refused and not second.refused and second.superseded == 3
    old = {p.prediction_id for p in first.predictions}
    (live,) = [p.prediction_id for p in second.predictions]
    assert env.learning.withdrawn() == old
    assert [p.prediction_id for p in env.learning.pending()] == [live]
    grade_day = max(p.grade_on for p in second.predictions)
    _mark_through(env, d16, grade_day)
    graded = grade_due(env.store, env.cfg, env.feed, env.fx, day=grade_day, learning=env.learning)
    assert [g.prediction_id for g in graded] == [live]
    assert len(env.learning.calibration_pairs()) == 1
    assert env.learning.counts()["withdrawn"] == 3


def test_a_superseded_all_cash_night_withdraws_its_cash_call(paper_env):
    from decimal import Decimal

    from engines.paper.book import decide
    from engines.paper.store import CASH

    env = paper_env
    d16 = env.week(3)
    _mark_through(env, env.week(2, 4), env.week(2, 4))
    cash = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={},
        thesis="t",
        horizon=5,
        learning=env.learning,
        now=_at(d16),
    )
    again = decide(
        env.store,
        env.cfg,
        env.feed,
        env.fx,
        day=d16,
        weights={"MYX:5183": Decimal("0.11")},
        thesis="t2",
        horizon=5,
        learning=env.learning,
        now=_at(d16) + timedelta(minutes=5),
        supersede=True,
    )
    assert cash.all_cash and not again.refused
    assert env.learning.withdrawn() == {p.prediction_id for p in cash.predictions}
    assert CASH not in {p.instrument_id for p in env.learning.pending()}
