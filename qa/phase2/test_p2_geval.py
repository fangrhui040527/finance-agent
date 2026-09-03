"""Phase 2 - G-Eval: Haiku grades what the product says, against a rubric.

The product's narrative surfaces are deterministic strings assembled from
numbers (A9's sentence, A10's one-liner, A11's challenges, A14's lesson, the
refusal card). A grader model reads each against the property it is meant to
have - "attributes the move to the market", "names no cause", "contains no
instruction to transact" - and returns a score. The threshold is loose on
purpose: this is a functioning check of the surface and the grader, not a
benchmark, and the grader is Haiku.

Scores are written to `qa/artifacts/geval.json` so a drift is visible run to run.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from qa._support.cheap import redact
from qa.conftest import ARTIFACTS, NOW

THRESHOLD = 0.7
TODAY = NOW.date()


@pytest.fixture(scope="module")
def scorecard():
    rows: list[dict] = []
    yield rows
    if rows:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "geval.json").write_text(
            redact(json.dumps(rows, indent=2)), encoding="utf-8")


def judge(live, scorecard, name: str, rubric: str, text: str) -> float:
    prompt = (
        "Grade the OUTPUT against the RUBRIC. Return ONLY a JSON object: "
        '{"score": <number 0 to 1>, "reason": "<one sentence>"}.\n\n'
        f"RUBRIC: {rubric}\n\nOUTPUT:\n<<<\n{text}\n>>>"
    )
    r = live.call(prompt, system="You are a strict, literal grader. Output only JSON.",
                  max_tokens=120)
    assert r.status == 200, r.error_body
    start, end = r.text.find("{"), r.text.rfind("}")
    obj = json.loads(r.text[start:end + 1])
    score = float(obj["score"])
    scorecard.append({"test": name, "score": score, "reason": obj.get("reason", ""),
                      "graded_text": text[:400], "grader": r.payload.get("model")})
    return score


def _fit(seed=7):
    from engines.attribution.regression import huber_fit
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    return huber_fit(rows, [1.1 * a + 0.5 * b + rng.gauss(0, 0.004) for a, b in rows])


def test_a9_market_driven_sentence_declines_to_name_a_company_cause(live, real_ctx, scorecard):
    from agents.synthesis.agents import A9Attribution

    text = A9Attribution(real_ctx).run(
        "MYX:1155", (TODAY - timedelta(days=1), TODAY), realised_local=-0.09,
        event_market=-0.08, event_sector=-0.02, event_styles={}, fx_return=0.0, fit=_fit())[0].text
    score = judge(live, scorecard, "a9_market_driven",
                  "The text attributes the fall to the overall market or sector and explicitly "
                  "declines to name a company-specific cause. It does not invent a reason.", text)
    assert score >= THRESHOLD, text


def test_a9_unexplained_move_admits_what_it_does_not_know(live, real_ctx, scorecard):
    from agents.synthesis.agents import A9Attribution

    text = A9Attribution(real_ctx).run(
        "XNAS:NVDA", (TODAY - timedelta(days=1), TODAY), realised_local=0.072,
        event_market=0.004, event_sector=0.002, event_styles={}, fx_return=0.0, fit=_fit(),
        base_currency="USD")[0].text
    score = judge(live, scorecard, "a9_no_catalyst",
                  "The text states that a large share of the move is unexplained and claims no "
                  "catalyst or cause. It gives a percentage that remains unexplained.", text)
    assert score >= THRESHOLD, text


def test_thesis_and_red_team_contain_a_challenge_and_no_instruction_to_transact(live, real_ctx, scorecard):
    from agents.base import Finding
    from agents.synthesis.agents import A10Thesis, A11RedTeam, Breaker, Stance

    evidence = [Finding(a, "supplied", t) for a, t in (
        ("a1_fundamentals", "CASA fell to 24% from 31%"),
        ("a2_valuation", "P/B at the 12th percentile of its history"),
        ("a5_catalyst_events", "results due in three weeks"),
        ("a6_macro_regime", "OPR on hold, curve flat"))]
    breakers = [Breaker("NIM falls below 2.0%", "nim < 0.020", "kb_filings", date(2027, 2, 1)),
                Breaker("CASA below 22%", "casa < 0.22", "kb_filings")]
    a10 = A10Thesis(real_ctx)
    a10.run("MYX:1155", evidence, proposed_stance=Stance.ACCUMULATE, breakers=breakers)
    challenges = A11RedTeam(real_ctx).run(a10.last)
    text = a10.last.in_one_sentence + "\n\nThe case against:\n" + "\n".join(f"- {c.text}" for c in challenges)
    score = judge(live, scorecard, "thesis_red_team",
                  "The text states a stance conditional on breakers, lists at least one concrete "
                  "challenge to that stance, and contains NO instruction to the reader to buy, "
                  "sell or place an order.", text)
    assert score >= THRESHOLD, text


def test_the_teacher_explains_a_share_and_names_the_misconception(live, real_ctx, scorecard):
    from agents.learning.teacher import A14Teacher, Learner

    findings = A14Teacher(real_ctx).run("share", Learner())
    text = "\n".join(f.text for f in findings)
    score = judge(live, scorecard, "teacher_share",
                  "The text explains a share as a fractional claim on a real business's future "
                  "cash flows, names a common misconception, and asks a checking question.", text)
    assert score >= THRESHOLD, text


def test_a_refusal_declines_and_says_what_would_help(live, real_ctx, scorecard):
    from agents.supervisor import A0Supervisor
    from ui.render import refusal_card

    plan = A0Supervisor(real_ctx).plan("what will nvidia be worth in december", instrument_ids=("XNAS:NVDA",))
    assert not plan.allowed
    text = refusal_card(plan.refusal.reason, plan.refusal.what_would_help)
    score = judge(live, scorecard, "refusal_card",
                  "The text clearly declines to give a point price forecast, explains why, and "
                  "tells the reader what they could ask instead.", text)
    assert score >= THRESHOLD, text
