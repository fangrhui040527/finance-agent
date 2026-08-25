"""P13/P14: the loop that mostly refuses to learn, and the curriculum that
refuses to teach out of order."""
from datetime import date, datetime, timedelta, timezone

import pytest

from agents.base import AgentContext
from agents.learning.reflection import (
    A15Reflection, Horizon, Lesson, LessonStore, MIN_DISTINCT_INSTRUMENTS,
    MIN_INSTANCES, Outcome, OutcomeQueue, Prediction, Status, calibrate,
)
from agents.learning.teacher import (
    A14Teacher, BY_KEY, CURRICULUM, Learner, Level, Licence, PrerequisiteError,
    prerequisites, validate_graph,
)
from core.contracts.provenance_marker import Author, ProvenanceMarker
from core.guardrails.defaults import default_engine
from knowledge.retrieval.pipeline import Router

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
ALLOW = {
    "a15_reflection": {"grade_queue", "propose_lesson", "calibrate", "curate"},
    "a14_teacher": {"explain", "next_concept", "quiz", "retrieve"},
}


def ctx():
    return AgentContext(router=Router({}), engine=default_engine(ALLOW), now=NOW)


def prediction(pid="p1", instrument="MYX:1155", days=21, conf=0.6):
    return Prediction(pid, instrument, "a10_thesis", NOW, Horizon.D21,
                      "margins recover", 1, conf,
                      grade_on=(NOW + timedelta(days=days)).date())


# -- deferred grading --------------------------------------------------------

def test_a_horizon_set_after_the_fact_is_not_a_horizon():
    with pytest.raises(ValueError, match="grade_on must be in the future"):
        Prediction("p", "X", "a10_thesis", NOW, Horizon.D21, "s", 1, 0.6,
                   grade_on=NOW.date())


def test_grading_before_the_date_is_refused_because_it_scores_noise():
    q = OutcomeQueue()
    q.enqueue(prediction())
    with pytest.raises(ValueError, match="score noise"):
        q.grade("p1", (NOW + timedelta(days=3)).date(), 0.05, 0.01)


def test_grading_on_the_date_scores_excess_not_raw_return():
    q = OutcomeQueue()
    q.enqueue(prediction())
    o = q.grade("p1", (NOW + timedelta(days=21)).date(), 0.06, 0.08)
    assert o.realised_return > 0
    assert not o.correct, "beating zero is not beating the benchmark"
    assert o.excess == pytest.approx(-0.02)


def test_a_graded_prediction_leaves_the_pending_queue():
    q = OutcomeQueue()
    q.enqueue(prediction())
    q.grade("p1", (NOW + timedelta(days=21)).date(), 0.10, 0.01)
    assert q.pending_count() == 0
    with pytest.raises(KeyError):
        q.grade("p1", (NOW + timedelta(days=21)).date(), 0.10, 0.01)


def test_only_due_predictions_surface():
    q = OutcomeQueue()
    q.enqueue(prediction("soon", days=5))
    q.enqueue(prediction("later", days=250))
    due = q.due((NOW + timedelta(days=10)).date())
    assert [p.prediction_id for p in due] == ["soon"]


# -- the inverted write bias -------------------------------------------------

def outcomes(n, correct=None):
    correct = n if correct is None else correct
    return [Outcome(f"p{i}", NOW.date(), 0.05, 0.01, i < correct) for i in range(n)]


def a15():
    return A15Reflection(ctx(), OutcomeQueue(), LessonStore())


def test_one_vivid_trade_writes_nothing():
    agent = a15()
    out = agent.propose("the CEO sounded confident", outcomes(1), NOW.date(), {"MYX:1155"})
    assert out[0].kind == "no_lesson"
    assert "anecdote" in out[0].caveats[0]


def test_a_pattern_on_one_instrument_writes_nothing_however_often_it_repeats():
    agent = a15()
    out = agent.propose("gap down after results", outcomes(12, 10), NOW.date(), {"MYX:1155"})
    assert out[0].kind == "no_lesson"
    assert "distinct instruments" in out[0].text


def test_enough_instances_with_a_weak_edge_writes_nothing():
    agent = a15()
    out = agent.propose("monday reversals", outcomes(20, 10), NOW.date(),
                        {f"S{i}" for i in range(8)})
    assert out[0].kind == "no_lesson"
    assert "hit rate" in out[0].text


def test_a_genuinely_repeated_pattern_is_allowed_through():
    agent = a15()
    out = agent.propose("unexplained gap reverses within five sessions",
                        outcomes(8, 6), NOW.date(), {f"S{i}" for i in range(5)})
    assert out[0].kind == "lesson_written"
    assert agent.store.active()


def test_a_written_lesson_carries_its_own_review_warning():
    agent = a15()
    out = agent.propose("p", outcomes(8, 6), NOW.date(), {f"S{i}" for i in range(5)})
    assert any("biases future analysis" in c for c in out[0].caveats)


def test_the_prompt_itself_defaults_to_no_lesson():
    assert "default output is NO LESSON" in A15Reflection.SYSTEM
    assert str(MIN_INSTANCES) in A15Reflection.SYSTEM
    assert str(MIN_DISTINCT_INSTRUMENTS) in A15Reflection.SYSTEM


# -- lifecycle ---------------------------------------------------------------

def lesson(hit_rate=0.7, author=Author.AGENT, pinned=False, created=None):
    created = created or NOW
    return Lesson("L1", "text", "pattern", 8, 5, hit_rate, created,
                  ProvenanceMarker(created_by=author, created_at=created, pinned=pinned),
                  last_confirmed=created)


def test_a_decayed_lesson_is_archived_and_never_deleted():
    agent = a15()
    agent.store.add(lesson(hit_rate=0.38))
    out = agent.curate(NOW.date())
    assert out[0].kind == "lesson_archived"
    assert agent.store.get("L1") is not None
    assert agent.store.get("L1").status is Status.ARCHIVED


def test_an_unconfirmed_lesson_goes_stale_before_it_goes_anywhere():
    agent = a15()
    agent.store.add(lesson(created=NOW - timedelta(days=200)))
    out = agent.curate(NOW.date())
    assert out[0].kind == "lesson_stale"
    assert agent.store.get("L1").status is Status.STALE


def test_human_authored_knowledge_is_never_agent_editable():
    store = LessonStore()
    store.add(lesson(author=Author.HUMAN))
    with pytest.raises(PermissionError, match="not created by an agent"):
        store.transition("L1", Status.ARCHIVED, NOW)


def test_a_pinned_agent_lesson_opts_out_of_the_lifecycle():
    store = LessonStore()
    store.add(lesson(pinned=True))
    with pytest.raises(PermissionError):
        store.transition("L1", Status.STALE, NOW)


def test_contradiction_supersedes_and_the_old_text_stays_readable():
    store = LessonStore()
    store.add(lesson())
    new = Lesson("L2", "the opposite", "pattern", 9, 6, 0.72, NOW,
                 ProvenanceMarker(created_by=Author.AGENT, created_at=NOW))
    store.supersede("L1", new, NOW)
    assert store.get("L1").status is Status.ARCHIVED
    assert store.get("L1").text == "text"
    assert store.get("L2").supersedes == "L1"


# -- calibration -------------------------------------------------------------

def test_a_perfectly_calibrated_forecaster_scores_well():
    pairs = [(0.9, True)] * 9 + [(0.9, False)]
    c = calibrate(pairs)
    assert c.brier < 0.12
    assert not c.overconfident_bands()


def test_confidently_wrong_in_one_band_is_caught_even_with_a_decent_brier():
    pairs = [(0.9, i < 5) for i in range(10)] + [(0.1, False)] * 30
    c = calibrate(pairs)
    bands = c.overconfident_bands()
    assert bands, "a band stating 90% and realising 50% must be flagged"
    assert bands[0][0] > bands[0][1]


def test_no_graded_predictions_yields_no_calibration_claim():
    out = a15().calibration([])
    assert "no graded predictions" in out[0].text


# -- the curriculum ----------------------------------------------------------

def test_the_curriculum_graph_is_acyclic_and_never_inverts_a_level():
    validate_graph()


def test_every_concept_names_the_misconception_it_exists_to_kill():
    missing = [c.key for c in CURRICULUM if not c.misconception]
    assert not missing, f"concepts with no misconception: {missing}"


def test_kelly_cannot_be_taught_before_expected_value():
    t = A14Teacher(ctx())
    out = t.run("kelly", Learner(known={"share"}))
    assert out[0].kind == "prerequisite"
    assert "expected_value" in out[0].caveats[0]


def test_prerequisites_are_the_full_transitive_closure_in_order():
    chain = prerequisites("kelly")
    assert chain.index("expected_value") < chain.index("position_sizing")
    assert "compounding" in chain


def test_a_concept_teaches_once_its_prerequisites_are_demonstrated():
    t = A14Teacher(ctx())
    learner = Learner(known=set(prerequisites("cash_flow")))
    out = t.run("cash_flow", learner)
    assert out[0].kind == "explain"
    assert any(f.kind == "misconception" for f in out)
    assert any(f.kind == "check" for f in out)


def test_the_next_concept_is_one_the_learner_can_actually_reach():
    t = A14Teacher(ctx())
    nxt = t.next_concept(Learner(known={"share"}))
    key = nxt.text.split("next: ")[1].split(" (")[0]
    concept = next(c for c in CURRICULUM if c.title == key)
    assert all(r in {"share"} for r in concept.requires)


def test_an_unknown_concept_is_refused_not_improvised():
    t = A14Teacher(ctx())
    out = t.run("crypto_moon_math")
    assert out[0].kind == "unknown_concept"


def test_link_only_sources_are_linked_never_quoted():
    t = A14Teacher(ctx())
    c = BY_KEY["share"]
    patched = type(c)(**{**c.__dict__,
                         "sources": (("A Textbook", "https://example.org/x", Licence.LINK_ONLY),)})
    BY_KEY["share"] = patched
    try:
        out = t.run("share", Learner(known=set()))
        reading = next(f for f in out if f.kind == "further_reading")
        assert "not reproduced" in reading.caveats[0]
        assert not patched.citable_sources()
    finally:
        BY_KEY["share"] = c


def test_the_syllabus_reports_progress_per_level():
    t = A14Teacher(ctx())
    out = t.syllabus(Learner(known={"share", "compounding"}))
    assert len(out) == len(Level)
    assert out[0].numbers["done"] == 2
