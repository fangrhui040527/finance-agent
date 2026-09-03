"""Which lesson gets read first, and whether the system can score itself.

Both are docs/13 section 5 items that were specified and never built. The
fitness one matters most for what it REFUSES: three of its seven terms cannot
be computed from anything recorded today, and a partial average would be a
number that looks like fitness and is not.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agents.learning.reflection import Calibration, Lesson, Status
from agents.learning.scoring import (
    CHANCE,
    MIN_DISTINCT,
    MIN_INSTANCES,
    RECENCY_HALFLIFE_DAYS,
    evidence,
    rank,
    recency,
    relevance,
    score,
)
from core.contracts.provenance_marker import Author, ProvenanceMarker
from core.llm.tiers import TaskClass, Tier, Usage
from core.provenance.fitness import DEFAULT_WEIGHTS, compute
from core.provenance.ledger import ProvenanceLedger

NOW = datetime(2026, 8, 30, tzinfo=UTC)
MARK = ProvenanceMarker(created_by=Author.AGENT, created_at=NOW)


def lesson(
    lid="l1",
    *,
    instances=20,
    distinct=8,
    hit=0.8,
    confirmed_days=1,
    pattern="bursa",
    status=Status.ACTIVE,
    created_days=400,
):
    return Lesson(
        lid,
        f"lesson {lid}",
        pattern,
        instances,
        distinct,
        hit,
        NOW - timedelta(days=created_days),
        MARK,
        status=status,
        last_confirmed=NOW - timedelta(days=confirmed_days),
    )


# -- recency ------------------------------------------------------------------


def test_recency_decays_from_last_confirmation_not_from_creation():
    """A lesson written a year ago and confirmed last week is current. Decaying
    from created_at would retire exactly the rules that keep being right."""
    old_but_confirmed = lesson(created_days=400, confirmed_days=1)
    young_but_stale = lesson(created_days=10, confirmed_days=400)
    assert recency(old_but_confirmed, NOW) > 0.9
    assert recency(young_but_stale, NOW) < 0.1


def test_recency_halves_over_the_halflife():
    l = lesson(confirmed_days=int(RECENCY_HALFLIFE_DAYS))
    assert recency(l, NOW) == pytest.approx(0.5, abs=0.01)


def test_a_lesson_never_confirmed_decays_from_when_it_was_written():
    never = Lesson("x", "t", "p", 20, 8, 0.8, NOW - timedelta(days=180), MARK)
    assert recency(never, NOW) == pytest.approx(0.25, abs=0.02)


# -- evidence -----------------------------------------------------------------


def test_a_lesson_on_one_instrument_is_not_a_rule():
    assert evidence(lesson(distinct=1)) == 0.0


def test_a_lesson_below_the_write_gate_scores_zero():
    assert evidence(lesson(instances=MIN_INSTANCES - 1)) == 0.0
    assert evidence(lesson(distinct=MIN_DISTINCT - 1)) == 0.0


def test_a_hit_rate_at_chance_carries_no_information():
    assert evidence(lesson(hit=CHANCE)) == 0.0
    assert evidence(lesson(hit=CHANCE - 0.2)) == 0.0


def test_breadth_and_depth_both_saturate_so_neither_can_carry_the_score():
    """A rule seen on eighty names is not ten times the rule seen on eight."""
    assert evidence(lesson(distinct=80)) == pytest.approx(evidence(lesson(distinct=8)))
    assert evidence(lesson(instances=2000)) == pytest.approx(
        evidence(lesson(instances=200)), abs=0.05
    )


def test_a_lesson_exactly_at_the_write_gate_scores_low_but_not_zero():
    """The gate decides what may be WRITTEN; this decides what is READ first.
    Both being pass/fail on the same numbers would make the second redundant."""
    e = evidence(lesson(instances=MIN_INSTANCES, distinct=MIN_DISTINCT, hit=0.60))
    assert 0.0 < e < 0.2


# -- relevance ----------------------------------------------------------------


def test_a_lesson_whose_pattern_does_not_match_is_demoted_not_hidden():
    l = lesson(pattern="bursa")
    assert relevance(l, "why did BURSA move") == 1.0
    assert 0.0 < relevance(l, "a question about nasdaq") < 0.2


def test_with_no_context_a_lesson_ranks_on_merit_alone():
    assert relevance(lesson(), "") == 1.0


# -- the composite ------------------------------------------------------------


def test_the_score_is_multiplicative_so_failing_any_term_sinks_a_lesson():
    """Additive would let a great hit rate on one instrument outrank a real
    rule. A lesson has to be recent AND applicable AND evidenced."""
    good = score(lesson(), NOW)
    narrow = score(lesson(distinct=1), NOW)
    assert good.score > 0.3
    assert narrow.score == 0.0
    assert narrow.recency > 0.9  # strong on one term, still zero overall


def test_ranking_puts_the_broad_recent_well_evidenced_lesson_first():
    ls = [
        lesson("stale", confirmed_days=365),
        lesson("thin", instances=MIN_INSTANCES, distinct=MIN_DISTINCT, hit=0.6),
        lesson("best"),
        lesson("narrow", distinct=1),
    ]
    assert [s.lesson.lesson_id for s in rank(ls, NOW)][0] == "best"
    assert [s.lesson.lesson_id for s in rank(ls, NOW)][-1] == "narrow"


def test_an_archived_lesson_never_ranks_because_it_was_retired_for_cause():
    ls = [lesson("gone", status=Status.ARCHIVED), lesson("live")]
    assert [s.lesson.lesson_id for s in rank(ls, NOW)] == ["live"]


def test_a_stale_lesson_is_excluded_by_default_and_can_be_asked_for():
    """Stale means unconfirmed, which is a reason to stop leading with it - not
    a reason to pretend it was never written."""
    ls = [lesson("s", status=Status.STALE), lesson("a")]
    assert [x.lesson.lesson_id for x in rank(ls, NOW)] == ["a"]
    assert len(rank(ls, NOW, include_stale=True)) == 2


def test_ranking_is_stable_when_scores_tie():
    ls = [lesson("b"), lesson("a")]
    assert [s.lesson.lesson_id for s in rank(ls, NOW)] == ["a", "b"]


def test_a_limit_returns_the_best_n():
    ls = [lesson(f"l{i}", distinct=i + 3) for i in range(6)]
    assert len(rank(ls, NOW, limit=2)) == 2


def test_an_empty_store_ranks_to_nothing():
    assert rank([], NOW) == []


# -- fitness: what it refuses ------------------------------------------------


def ledger_with_claims():
    led = ProvenanceLedger(run_id="nightly")
    led.record_claim("a10", "cited", [{"source": "f", "chunk_id": "c"}], survived=True)
    led.record_claim("a10", "uncited", [], survived=True)
    led.record_claim("a10", "dropped", [], survived=False, dropped_reason="no citation")
    led.record_call(
        agent="a10",
        task_class=TaskClass.THESIS_SYNTHESIS,
        tier=Tier.REASON,
        model_id="claude-opus-5",
        prompt="p" * 400,
        usage=Usage(input_tokens=1000, output_tokens=200),
    )
    return led


def test_a_partial_fitness_refuses_to_produce_a_headline_number():
    """The whole point. Averaging the computable terms gives a number that looks
    like fitness, moves when the system changes, and is not fitness."""
    f = compute(ledger_with_claims())
    assert f.score is None
    assert "NO SCORE" in f.describe()


def test_it_names_exactly_what_is_missing_and_what_would_supply_it():
    f = compute(ledger_with_claims())
    missing = {t.name for t in f.missing}
    assert missing == {
        "refusal_precision",
        "attribution_accuracy",
        "forecast_calibration",
        "p95_latency",
    }
    text = f.describe()
    assert "elapsed time, not effort" in text  # calibration needs P16
    assert "200 historical moves" in text  # attribution needs labels
    assert "excluded rather than counted as instant" in text  # untimed rows


def test_groundedness_and_citation_validity_come_straight_from_the_ledger():
    f = compute(ledger_with_claims())
    by = {t.name: t for t in f.terms}
    assert by["groundedness"].value == pytest.approx(2 / 3)
    assert by["citation_validity"].value == pytest.approx(1 / 2)


def test_cost_per_query_is_spend_divided_by_runs():
    f = compute(ledger_with_claims())
    cost = next(t for t in f.terms if t.name == "cost_per_query")
    assert cost.available and "over 1 runs" in cost.detail


def test_a_complete_set_of_inputs_produces_a_score():
    f = compute(
        ledger_with_claims(),
        calibration=Calibration(n=40, brier=0.18, buckets=()),
        latencies_ms=[1200.0] * 20,
        labelled_moves=[("earnings", "earnings"), ("m&a", "index")],
        labelled_refusals=[(True, True), (True, False)],
    )
    assert f.missing == []
    assert f.score is not None
    assert "FITNESS" in f.describe() and "NO SCORE" not in f.describe()


def test_brier_is_an_error_so_fitness_takes_one_minus_it():
    f = compute(ledger_with_claims(), calibration=Calibration(n=10, brier=0.25, buckets=()))
    cal = next(t for t in f.terms if t.name == "forecast_calibration")
    assert cal.value == pytest.approx(0.75)


def test_an_untimed_call_is_excluded_rather_than_counted_as_instant():
    """0.0 is the column default for rows written before latency_ms existed.
    Counting them would make the p95 look better the more untimed history the
    ledger holds - a metric that improves by aging is not a metric."""
    led = ledger_with_claims()  # record_call without a latency
    assert led.latencies_between(datetime(2000, 1, 1, tzinfo=UTC), NOW + timedelta(days=1)) == []
    lat = next(t for t in compute(led).terms if t.name == "p95_latency")
    assert not lat.available


def test_a_timed_call_reaches_the_fitness_function_without_tracing():
    """The number was measured either way and reached the trace only when
    tracing was on - so an ordinary run, the only kind production has, threw
    away exactly what docs/01 section 10 asks for."""
    from core.guardrails.defaults import default_engine
    from core.llm.client import EchoBackend, InferenceClient

    led = ProvenanceLedger(run_id="r1")
    InferenceClient(
        EchoBackend(), default_engine({"a4": {"llm_complete"}}), led, daily_budget_myr=Decimal("25")
    ).complete("a4", TaskClass.NEWS_TRIAGE, "headline")
    lat = next(t for t in compute(led).terms if t.name == "p95_latency")
    assert lat.available


def test_latency_and_cost_are_penalties_and_bounded():
    """One eight-minute request must not make the month's fitness negative."""
    assert DEFAULT_WEIGHTS["p95_latency"] < 0
    assert DEFAULT_WEIGHTS["cost_per_query"] < 0
    f = compute(ledger_with_claims(), latencies_ms=[10_000_000.0])
    lat = next(t for t in f.terms if t.name == "p95_latency")
    assert lat.value == 1.0


def test_an_empty_ledger_says_nothing_has_run_rather_than_scoring_zero():
    f = compute(ProvenanceLedger())
    g = next(t for t in f.terms if t.name == "groundedness")
    assert not g.available and "nothing has run" in g.unavailable_because


def test_the_weights_are_written_down_where_changing_them_is_visible():
    assert set(DEFAULT_WEIGHTS) == {t.name for t in compute(ProvenanceLedger()).terms}
    positive = {k: v for k, v in DEFAULT_WEIGHTS.items() if v > 0}
    assert sum(positive.values()) == pytest.approx(0.90)
