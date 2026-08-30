"""Phase 0: the things that must hold before anything runs unattended.

Every test here corresponds to a defect that was invisible while the system only
ever ran as a single short-lived interactive process. None of them would have
failed yesterday; all of them would have failed at 3am next month.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agents.learning.reflection import (
    A15Reflection, Horizon, Outcome, OutcomeQueue, Prediction,
)
from agents.learning.store import LearningStore
from core.llm.tiers import TaskClass, Tier, Usage
from core.provenance.ledger import ProvenanceLedger

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _call(led, cost_at=None, **kw):
    return led.record_call(
        agent=kw.get("agent", "a1_fundamentals"),
        task_class=TaskClass.FUNDAMENTALS_READ, tier=Tier.BALANCED,
        model_id="claude-sonnet-5", prompt="p" * 4000,
        usage=Usage(input_tokens=100_000, output_tokens=1000),
        at=cost_at, run_id=kw.get("run_id"),
    )


# --- the budget bug that would brick a daemon -----------------------------
def test_cost_is_windowed_not_lifetime(tmp_path):
    """total_cost_myr() sums every row ever. Compared against a DAILY budget it
    becomes a one-way cap: once cumulative spend passes it the client raises
    forever. Invisible while every ledger was :memory: and died per process."""
    led = ProvenanceLedger(tmp_path / "p.db")
    _call(led, cost_at=NOW - timedelta(days=30))
    _call(led, cost_at=NOW - timedelta(days=10))
    recent = _call(led, cost_at=NOW - timedelta(hours=1))

    lifetime = led.total_cost_myr()
    day = led.cost_since(NOW - timedelta(days=1))
    assert day < lifetime, "a 24h window must not include last month's spend"
    assert day == recent.cost_myr


def test_the_client_uses_the_window_and_recovers_the_next_day(tmp_path):
    from core.guardrails.defaults import default_engine
    from core.llm.client import BudgetExceeded, EchoBackend, InferenceClient

    led = ProvenanceLedger(tmp_path / "p.db")
    # Yesterday's spend, far above any daily budget.
    for _ in range(60):
        _call(led, cost_at=NOW - timedelta(days=3))

    client = InferenceClient(
        EchoBackend(), default_engine({"a1_fundamentals": {"llm_complete"}}),
        led, daily_budget_myr=Decimal("5"),
    )
    # Old spend must not block today. Under the lifetime sum this raised.
    out = client.complete("a1_fundamentals", TaskClass.FUNDAMENTALS_READ, "q")
    assert out.text

    # Today's own spend still binds.
    for _ in range(60):
        _call(led, cost_at=datetime.now(timezone.utc))
    with pytest.raises(BudgetExceeded, match="last"):
        client.complete("a1_fundamentals", TaskClass.FUNDAMENTALS_READ, "q")


# --- run correlation ------------------------------------------------------
def test_calls_can_be_grouped_by_the_run_that_made_them(tmp_path):
    """Without a run id the only grouping is agent plus timestamp proximity,
    which stops being the same question once two jobs overlap."""
    led = ProvenanceLedger(tmp_path / "p.db", run_id="nightly-2026-08-28")
    # Stamped explicitly. Leaving `at` unset takes the wall clock, so the window
    # assertion below silently became false on the day real time walked past NOW.
    _call(led, cost_at=NOW)
    _call(led, cost_at=NOW)
    _call(led, cost_at=NOW, run_id="adhoc")

    assert len(led.calls_for_run("nightly-2026-08-28")) == 2
    assert len(led.calls_for_run("adhoc")) == 1
    assert set(led.runs_between(NOW - timedelta(days=1),
                                NOW + timedelta(days=1))) >= {"adhoc"}


def test_calls_between_bounds_a_window(tmp_path):
    led = ProvenanceLedger(tmp_path / "p.db")
    _call(led, cost_at=NOW - timedelta(days=5))
    _call(led, cost_at=NOW - timedelta(hours=2))
    got = led.calls_between(NOW - timedelta(days=1), NOW + timedelta(days=1))
    assert len(got) == 1


def test_cost_by_agent_is_available_for_the_monthly_review(tmp_path):
    led = ProvenanceLedger(tmp_path / "p.db")
    _call(led, agent="a1_fundamentals")
    _call(led, agent="a10_thesis")
    by_agent = led.cost_by_agent_myr()
    assert set(by_agent) == {"a1_fundamentals", "a10_thesis"}


def test_calls_does_not_mutate_shared_connection_state(tmp_path):
    """calls() used to assign self.conn.row_factory as a side effect, and being a
    generator it only did so on first iteration - so an interleaved read got
    tuples or Rows depending on order. A daemon interleaves constantly."""
    led = ProvenanceLedger(tmp_path / "p.db")
    _call(led)
    list(led.calls())
    row = led.conn.execute("SELECT agent FROM llm_calls").fetchone()
    assert isinstance(row, tuple) and not hasattr(row, "keys")


# --- two processes ---------------------------------------------------------
def test_both_stores_use_wal_so_a_writer_never_blocks_a_reader(tmp_path):
    led = ProvenanceLedger(tmp_path / "p.db")
    assert led.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert led.conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 10_000

    with LearningStore(tmp_path / "l.db") as store:
        assert store.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.db.execute("PRAGMA busy_timeout").fetchone()[0] >= 10_000


def test_a_second_process_can_read_while_the_first_holds_the_file(tmp_path):
    a = ProvenanceLedger(tmp_path / "p.db")
    _call(a)
    b = ProvenanceLedger(tmp_path / "p.db")      # the interactive session
    assert len(list(b.calls())) == 1
    _call(b)
    assert len(list(a.calls())) == 2, "each connection must see the other's commits"


def test_an_older_ledger_without_run_id_still_opens(tmp_path):
    """A ledger written before run_id existed must migrate, not crash - and the
    append-only triggers must not block the schema change."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        agent TEXT NOT NULL, task_class TEXT NOT NULL, tier TEXT NOT NULL,
        model_id TEXT NOT NULL, prompt_hash TEXT NOT NULL, input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
        cost_usd TEXT NOT NULL, cost_myr TEXT NOT NULL, fx_rate TEXT NOT NULL,
        fx_asof TEXT NOT NULL);
        CREATE TABLE claims (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        agent TEXT NOT NULL, claim_text TEXT NOT NULL, citations_json TEXT NOT NULL,
        survived INTEGER NOT NULL, dropped_reason TEXT);
    """)
    old.execute("INSERT INTO llm_calls VALUES (1,'2026-01-01T00:00:00','a','t','balanced',"
                "'m','h',1,1,0,'0.1','0.4','4.15','2026-01-01T00:00:00')")
    old.commit()
    old.close()

    led = ProvenanceLedger(path)
    rows = list(led.calls())
    assert len(rows) == 1
    assert rows[0]["run_id"] == "", "rows from before runs existed belong to no run"
    _call(led, run_id="new")
    assert len(led.calls_for_run("new")) == 1


def test_append_only_still_holds_after_the_migration(tmp_path):
    led = ProvenanceLedger(tmp_path / "p.db")
    _call(led)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        led.conn.execute("UPDATE llm_calls SET agent='x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        led.conn.execute("DELETE FROM llm_calls")


# --- the inverted gate that stopped inverting ------------------------------
def _outcomes(n, correct=None):
    correct = n if correct is None else correct
    return [Outcome(f"p{i}", date(2026, 8, 1), 0.05, 0.01, i < correct)
            for i in range(n)]


def _agent():
    from agents.base import AgentContext
    from agents.learning.reflection import LessonStore
    from core.guardrails.defaults import default_engine
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router
    reg = load_registry("agents/registry.yaml")
    ctx = AgentContext(router=Router({}), engine=default_engine(reg.allowlist()), now=NOW)
    return A15Reflection(ctx, OutcomeQueue(), LessonStore())


def test_a_cohort_with_no_instruments_refuses_instead_of_passing_the_gate():
    """distinct used to fall back to n, making the distinct-instrument bar
    identical to the instance bar and therefore always satisfied. docs/14 6 names
    it: 'lessons accumulating fast: the inverted gate stopped inverting.'"""
    out = _agent().propose("pattern", _outcomes(8), date(2026, 8, 28))
    assert out[0].kind == "no_lesson"
    assert "were not supplied" in out[0].text


def test_run_passes_instruments_through_so_the_gate_still_applies():
    a = _agent()
    cohort = {"pattern": _outcomes(8)}
    # One instrument: below MIN_DISTINCT_INSTRUMENTS, must refuse.
    out = a.run(date(2026, 8, 28), cohort, instruments={"pattern": {"MYX:1155"}})
    assert any(f.kind == "no_lesson" for f in out)


def test_run_without_instruments_refuses_rather_than_learning_freely():
    out = _agent().run(date(2026, 8, 28), {"pattern": _outcomes(8)})
    assert any(f.kind == "no_lesson" for f in out)


def test_a_genuinely_repeated_pattern_still_gets_through():
    out = _agent().propose("pattern", _outcomes(8), date(2026, 8, 28),
                           {"MYX:1155", "XNAS:NVDA", "XSES:D05"})
    assert any(f.kind != "no_lesson" for f in out), "the gate must not be impassable"


# --- a no-view prediction must not score -----------------------------------
def _pred(pid, direction, days=21):
    return Prediction(pid, "MYX:1155", "human", NOW, Horizon.D21, "s",
                      direction, 0.6, grade_on=(NOW + timedelta(days=days)).date())


def test_a_no_view_prediction_is_not_graded_correct():
    """direction 0 expresses no view, so it can be neither right nor wrong. It
    used to be recorded as CORRECT unconditionally - a free hit."""
    q = OutcomeQueue()
    q.enqueue(_pred("p0", 0))
    o = q.grade("p0", (NOW + timedelta(days=22)).date(), realised=-0.10, benchmark=0.05)
    assert o.correct is False
    assert "not scored" in o.note


def test_a_no_view_prediction_is_excluded_from_calibration(tmp_path):
    """A run of honest 'I don't know' entries must not move the hit rate."""
    with LearningStore(tmp_path / "l.db") as store:
        store.record(_pred("view", 1))
        store.record(_pred("noview", 0))
        for pid, correct in (("view", True), ("noview", False)):
            store.record_outcome(Outcome(pid, (NOW + timedelta(days=22)).date(),
                                         0.05, 0.01, correct))
        pairs = store.calibration_pairs()
    assert len(pairs) == 1, "only the prediction that expressed a view may score"
    assert pairs[0][1] is True


def test_a_directional_prediction_is_still_graded_against_the_benchmark():
    """Up 6% in a month the index rose 8% is being wrong."""
    q = OutcomeQueue()
    q.enqueue(_pred("up", 1))
    o = q.grade("up", (NOW + timedelta(days=22)).date(), realised=0.06, benchmark=0.08)
    assert o.correct is False


# --- the escalation gate needs names to match ------------------------------
def test_config_says_out_loud_when_nothing_can_ever_escalate():
    from core.config import Config
    from engines.risk.concentration import Limits
    c = Config(base_currency="MYR", markets=("XKLS",), fx_myr_per_usd=Decimal("4.15"),
               risk_per_trade=Decimal("0.0075"), target_volatility=Decimal("0.20"),
               max_participation=Decimal("0.05"), limits=Limits(), emergency_months=6,
               debt_hurdle=Decimal("0.08"), daily_budget_myr=Decimal("25"),
               per_question_budget_myr=Decimal("5"), database="d", 
               min_graded_for_calibration=30)
    assert "can never fire" in c.describe()


def test_holdings_and_watchlist_are_validated_at_load(tmp_path):
    from core.config import ConfigError, load
    p = tmp_path / "config.toml"
    p.write_text('[account]\nholdings = ["1155"]\n')
    with pytest.raises(ConfigError, match="no market prefix"):
        load(p)


def test_a_duplicated_watchlist_entry_is_refused(tmp_path):
    from core.config import ConfigError, load
    p = tmp_path / "config.toml"
    p.write_text('[account]\nwatchlist = ["MYX:1155", "MYX:1155"]\n')
    with pytest.raises(ConfigError, match="more than once"):
        load(p)


def test_valid_holdings_load_and_reach_the_gate(tmp_path):
    from core.config import load
    from knowledge.news.features import Features, should_escalate
    p = tmp_path / "config.toml"
    p.write_text('[account]\nholdings = ["MYX:1155"]\nwatchlist = ["XNAS:NVDA"]\n')
    c = load(p)
    assert c.holdings == ("MYX:1155",)
    f = Features(relevance=0.9, polarity=-0.5, intensity=0.5,
                 uncertainty=0.1, forwardness=0.2, extractor="t")
    assert should_escalate(f, ["MYX:1155"], set(c.holdings), set(c.watchlist))
    assert not should_escalate(f, ["XKLS:9999"], set(c.holdings), set(c.watchlist))
