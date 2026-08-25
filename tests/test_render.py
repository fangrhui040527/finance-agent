"""P15: the interface must make the honest answer as easy to show as the
confident one."""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agents.base import Finding
from engines.attribution.decompose import Verdict, decompose
from engines.attribution.regression import huber_fit
from ui.render import (
    Annotation, annotated_chart, daily_brief, decomposition_bars, refusal_card,
    thesis_memo,
)

WINDOW = (date(2026, 8, 3), date(2026, 8, 4))
NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def fit(n=250, seed=7):
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(n)]
    y = [1.1 * a + 0.5 * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


def move(local, mkt, sec, iid="MYX:1155"):
    return decompose(iid, WINDOW, mkt, sec, {}, local, 0.0, fit())


# -- decomposition bars ------------------------------------------------------

def test_the_unexplained_share_is_always_on_screen():
    out = decomposition_bars(move(0.072, 0.004, 0.002))
    assert "unexplained" in out
    assert "92%" in out


def test_no_identified_catalyst_gets_a_layout_not_an_empty_slot():
    exp = move(0.072, 0.004, 0.002)
    assert exp.verdict is Verdict.NO_IDENTIFIED_CATALYST
    out = decomposition_bars(exp)
    assert "no catalyst cleared the evidence threshold" in out
    assert "historically reverse" in out


def test_unavailable_attribution_says_so_rather_than_drawing_zeroes():
    exp = decompose("NEW", WINDOW, -0.05, -0.01, {}, -0.06, 0.0, None)
    out = decomposition_bars(exp)
    assert "attribution unavailable" in out
    assert "#" not in out


def test_bars_are_signed_so_a_negative_component_reads_as_negative():
    out = decomposition_bars(move(-0.090, -0.080, -0.020))
    market_line = next(l for l in out.splitlines() if "market" in l)
    assert market_line.index("#") < market_line.index("|")


def test_betas_are_shown_because_the_reader_should_see_the_leverage():
    assert "b=" in decomposition_bars(move(-0.090, -0.080, -0.020))


# -- annotated chart ---------------------------------------------------------

def bars(n=90, start=10.0):
    d = date(2026, 5, 1)
    rng = random.Random(3)
    out, px = [], start
    for i in range(n):
        px *= 1 + rng.gauss(0, 0.01)
        out.append((d + timedelta(days=i), px))
    return out


def test_an_arrow_on_a_chart_is_a_claim_and_needs_a_source():
    with pytest.raises(ValueError, match="is a claim"):
        annotated_chart("MYX:1155", bars(),
                        [Annotation(date(2026, 6, 1), "results beat", "event")])


def test_a_sourced_event_gets_a_numbered_marker_and_a_legend():
    out = annotated_chart("MYX:1155", bars(), [
        Annotation(date(2026, 6, 1), "results beat", "event", 0.71, "bursa_announcement")])
    assert "1. 2026-06-01" in out
    assert "bursa_announcement" in out
    assert "score 0.71" in out


def test_a_breaker_marker_needs_no_source_because_the_user_wrote_it():
    out = annotated_chart("MYX:1155", bars(),
                          [Annotation(date(2026, 6, 15), "margin test", "breaker")])
    assert "margin test" in out


def test_too_little_history_is_stated_not_drawn():
    assert "not enough history" in annotated_chart("X", [(date(2026, 1, 1), 1.0)], [])


# -- thesis memo -------------------------------------------------------------

def memo(**kw):
    base = dict(
        instrument_id="MYX:1155", stance="accumulate", one_sentence="Cheap bank.",
        what_must_be_true=["NIM stabilises"],
        breakers=[("NIM below 2.0%", "nim < 0.020", date(2027, 2, 1)),
                  ("credit cost above 60bps", "credit_cost > 0.006", None)],
        valuation_range=(Decimal("8.50"), Decimal("11.20")),
        uncertainties=["rate path"], gaps=[], challenges=[], confidence=0.62)
    base.update(kw)
    return thesis_memo(**base)


def test_evidence_gaps_are_printed_above_the_conclusion():
    out = memo(gaps=["a6_macro_regime"])
    assert out.index("EVIDENCE GAPS") < out.index("VALUATION RANGE")
    assert "read these before the rest" in out


def test_a_breaker_with_no_review_date_is_visibly_broken():
    assert "NO REVIEW DATE" in memo()


def test_every_breaker_shows_the_query_that_checks_it():
    assert "check: nim < 0.020" in memo()


def test_the_valuation_is_a_range_and_says_why():
    out = memo()
    assert "8.50 to 11.20" in out
    assert "A range, not a target" in out


def test_the_case_against_is_part_of_the_memo_not_an_appendix():
    out = memo(challenges=["This is the consensus view."])
    assert "THE CASE AGAINST" in out
    assert out.index("THE CASE AGAINST") < out.index("KEY UNCERTAINTIES")


def test_every_memo_states_it_cannot_place_orders():
    assert "cannot place orders" in memo()


# -- daily brief -------------------------------------------------------------

def test_a_quiet_day_is_rendered_as_a_quiet_day():
    out = daily_brief(NOW, [move(-0.055, -0.050, -0.010)], [], [])
    assert "Nothing needs a decision today" in out
    assert "most common correct state" in out


def test_market_wide_moves_are_grouped_and_explicitly_not_explained():
    out = daily_brief(NOW, [move(-0.090, -0.080, -0.020)], [], [])
    assert "EVERYTHING ELSE MOVED WITH ITS MARKET" in out
    assert "none is offered" in out


def test_a_move_that_is_not_the_market_is_promoted_to_the_top():
    out = daily_brief(NOW, [move(0.072, 0.004, 0.002)], [], [])
    assert "MOVES THAT ARE NOT THE MARKET" in out
    assert "no identified catalyst" in out


def test_breakers_due_outrank_everything_else():
    out = daily_brief(NOW, [move(0.072, 0.004, 0.002)],
                      [("MYX:1155", "NIM below 2.0%", date(2026, 8, 26))], [])
    assert out.index("BREAKERS DUE") < out.index("MOVES THAT ARE NOT")


def test_risk_breaches_appear_even_on_an_otherwise_quiet_day():
    out = daily_brief(NOW, [move(-0.055, -0.050, -0.010)], [],
                      [Finding("a12_portfolio_risk", "breach", "sector breach: 0.31 vs 0.25")])
    assert "RISK LIMITS" in out
    assert "Nothing needs a decision" not in out


def test_within_limits_is_reported_as_a_positive_state():
    out = daily_brief(NOW, [], [], [Finding("a12_portfolio_risk", "concentration",
                                            "8 positions, HHI 0.14, effective bets 6.10")])
    assert "within limits" in out


# -- refusal -----------------------------------------------------------------

def test_a_refusal_gets_a_card_so_it_does_not_read_as_a_crash():
    out = refusal_card("This system cannot place orders.", "Ask for the analysis instead.")
    assert "CANNOT ANSWER THIS" in out
    assert "What would help" in out


def test_a_scored_but_rejected_candidate_is_never_rendered_as_the_cause():
    """The failure mode this exists to stop: a story that scored 0.11 appearing
    under the move as though it explained it."""
    from datetime import datetime as _dtc
    from engines.events.catalyst import attach
    from engines.events.taxonomy import BaseRateTable, CapBand, Event, EventType
    from engines.events.catalyst import score_candidates

    exp = move(0.072, 0.004, 0.002)
    ts = _dtc(2026, 8, 3, tzinfo=timezone.utc)
    weak = Event("e1", "MYX:1155", EventType.DIVIDEND_CHANGE, ts, market="XKLS",
                 cap_band=CapBand.LARGE, source_doc_id="d1")
    exp = attach(exp, score_candidates(exp, [weak], BaseRateTable(), {"e1": 1}, "XKLS"))
    out = decomposition_bars(exp)
    assert exp.verdict is Verdict.NO_IDENTIFIED_CATALYST
    assert "no catalyst cleared the evidence threshold" in out
    if exp.candidates:
        assert "BELOW THRESHOLD" in out
        assert "scored and rejected" in out
