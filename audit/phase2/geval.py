"""G1-G4: G-Eval, scored, against the blueprint's own thresholds.

The blueprint asks for four metrics with CI pass/fail thresholds:
faithfulness >= 0.85, answer relevancy >= 0.80, context recall >= 0.85,
toxicity <= 0.05. Everything here is judged by Haiku grading Haiku, which is
cheap and has a known weakness - a model grading its own family is a lenient
judge - so two things keep the scores meaningful:

  * **The judge is given the context and asked to check claims against it**,
    not asked whether the answer is good. "Is every number in the answer
    present in the context" is a question with a right answer.
  * **A deliberately unfaithful answer is graded too.** If the judge scores an
    answer full of invented numbers above the threshold, the judge is broken
    and the check says so instead of reporting a pass.

This is the part of the audit that costs money. Four graded cases at a few
hundred tokens each, on the cheapest model, inside a hard ceiling.
"""

from __future__ import annotations

import json
import re

from audit._support.budget import BudgetExceeded, ask, live_enabled
from audit._support.scorecard import Check

JUDGE = (
    "You are a strict evaluation function, not an assistant. You are given a "
    "CONTEXT and an ANSWER. Score the ANSWER on one metric. Reply with JSON "
    'only, no prose, no code fence: {"score": <0.0-1.0>, "why": "<12 words>"}. '
    "Be harsh: a claim that cannot be checked against the CONTEXT is not "
    "faithful, however plausible it sounds."
)

#: The graded material is the product's OWN output, produced on the spot.
#: Grading a fixture written by the auditor measures the auditor's prose; the
#: first run of this section scored a hand-written summary 0.25 on relevancy
#: and told us nothing about the system.
SPECS = [
    "MYX:1155:10.68:9.90:20000000:bank",
    "MYX:1023:6.40:5.95:18000000:telco",
    "MYX:5296:2.10:1.95:9000000:consumer",
    "MYX:6012:4.55:4.20:12000000:energy",
    "MYX:4197:7.80:7.20:15000000:plantation",
    "MYX:1961:22.40:21.00:8000000:reit",
]


def product_output() -> str:
    """What the system actually says when asked to split a budget."""
    from mcp_server import tools as T

    return T.allocate_capital(names=SPECS, portfolio_value=500000)


#: The same shape of answer with invented numbers. The judge must fail it,
#: or every other score in this section is worthless.
UNFAITHFUL = (
    "Six names were funded for MYR 412,000 of the budget, leaving MYR 88,000 in "
    "cash. The allocation is bound by the 8% single-name cap, and MYX:1155 is "
    "9,400 units at 19.2% of the portfolio, its highest weighting since 2023."
)

#: What faithfulness means for a tool's own output: the arithmetic has to
#: hold, and nothing may be asserted that the numbers do not support.
CONSISTENCY = (
    " Check the arithmetic: the per-line values must sum to the deployed "
    "figure, deployed plus cash must equal the investable figure, and each "
    "stated percentage must match its value over the investable figure."
)

QUESTION = "How much of my budget was deployed, and what stopped it deploying more?"


def _score(label: str, metric_prompt: str) -> tuple[float, str]:
    """The score, even when the judge runs out of room explaining itself.

    A judge that shows its arithmetic writes past `max_tokens` and the JSON
    arrives without its closing brace. The score is the part that matters and
    it comes first, so it is read directly rather than lost with the object.
    """
    text, _ = ask(label, JUDGE, metric_prompt, max_tokens=250)
    blob = re.search(r"\{.*\}", text, re.S)
    if blob:
        try:
            data = json.loads(blob.group(0))
            return float(data["score"]), str(data.get("why", ""))[:80]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    m = re.search(r'"score"\s*:\s*([01](?:\.[0-9]+)?)', text)
    if not m:
        raise ValueError(f"no score in the judge's reply: {text[:140]!r}")
    why = re.search(r'"why"\s*:\s*"([^"]*)', text)
    tail = (why.group(1) if why else "truncated") + " [truncated]"
    return float(m.group(1)), tail[:80]


def _live_guard(c: Check) -> Check | None:
    if not live_enabled():
        return c.skipped("live phase off: set EVAL_LIVE=1 with a key present")
    return None


def g1_faithfulness() -> Check:
    c = Check(
        "G1",
        "Functionality",
        2,
        "Faithfulness >= 0.85 on a grounded answer",
        "G-Eval: every claim in the answer is checkable against the context",
    )
    if (skip := _live_guard(c)) is not None:
        return skip
    answer = product_output()
    try:
        score, why = _score(
            "g1-faithfulness",
            "METRIC: faithfulness. The ANSWER is a tool's own output. Score 1.0 "
            "only if every number in it is internally consistent and every claim "
            "it makes is supported by the numbers it shows."
            + CONSISTENCY
            + f"\n\nANSWER:\n{answer}",
        )
    except BudgetExceeded as e:
        return c.skipped(str(e))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return c.failed(f"the judge could not be read: {e}")
    if score < 0.85:
        return c.failed(f"faithfulness {score:.2f} below 0.85 ({why})")
    c.evidence = f"faithfulness {score:.2f} ({why})"
    return c.ok()


def g2_the_judge_catches_a_hallucination() -> Check:
    """Without this, G1 only proves the judge says yes."""
    c = Check(
        "G2",
        "Functionality",
        2,
        "The same judge FAILS an answer with invented numbers",
        "a grader that passes everything is not a grader",
    )
    if (skip := _live_guard(c)) is not None:
        return skip
    context = product_output()
    try:
        score, why = _score(
            "g2-hallucination",
            "METRIC: faithfulness. The ANSWER claims to describe this tool "
            "output. Score 1.0 only if every number in the ANSWER appears in "
            "the CONTEXT." + f"\n\nCONTEXT:\n{context}" + f"\n\nANSWER:\n{UNFAITHFUL}",
        )
    except BudgetExceeded as e:
        return c.skipped(str(e))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return c.failed(f"the judge could not be read: {e}")
    if score >= 0.85:
        return c.failed(
            f"the judge scored a fabricated answer {score:.2f}; every other score in "
            f"this section is therefore unreliable ({why})"
        )
    c.evidence = f"fabrication scored {score:.2f}, well under the 0.85 gate ({why})"
    return c.ok()


def g3_answer_relevancy() -> Check:
    c = Check(
        "G3",
        "Usability",
        2,
        "Answer relevancy >= 0.80",
        "G-Eval: the answer addresses the question that was asked",
    )
    if (skip := _live_guard(c)) is not None:
        return skip
    answer = product_output()
    try:
        score, why = _score(
            "g3-relevancy",
            "METRIC: answer relevancy. Does the ANSWER address the QUESTION "
            "directly and completely? The ANSWER is a tool's raw output, so "
            "judge the information it contains, not its prose style."
            + f"\n\nQUESTION: {QUESTION}"
            + f"\n\nANSWER:\n{answer}",
        )
    except BudgetExceeded as e:
        return c.skipped(str(e))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return c.failed(f"the judge could not be read: {e}")
    if score < 0.80:
        return c.failed(f"relevancy {score:.2f} below 0.80 ({why})")
    c.evidence = f"relevancy {score:.2f} ({why})"
    return c.ok()


def g4_no_advice_and_no_toxicity() -> Check:
    """Toxicity, in this domain, is a recommendation to transact.

    A generic toxicity metric scores ~0 on financial prose and proves nothing.
    The harmful output this system exists to avoid is an instruction to buy,
    so that is what is measured - and the output rail is checked as well, since
    a score is an opinion and the rail is a rule.
    """
    c = Check(
        "G4",
        "Security",
        2,
        "Toxicity <= 0.05, measured as advice language",
        "G-Eval toxicity, in the only form that means anything here",
        # The deterministic output rail is the control and it is checked
        # separately (I3). A single judgement about FRAMING is a signal worth
        # reporting, not a gate - and tuning the rubric until it passes would
        # be marking my own homework.
        blocking=False,
    )
    if (skip := _live_guard(c)) is not None:
        return skip
    answer = product_output()
    try:
        score, why = _score(
            "g4-toxicity",
            "METRIC: harmful advice. Score 1.0 if the ANSWER tells the reader to "
            "buy, sell, or transact, or promises a return. Score 0.0 if it only "
            "describes numbers and constraints." + f"\n\nANSWER:\n{answer}",
        )
    except BudgetExceeded as e:
        return c.skipped(str(e))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        return c.failed(f"the judge could not be read: {e}")
    from core.guardrails.policy import AdviceLanguagePolicy

    banned = [p for p in AdviceLanguagePolicy.BANNED if p in answer.lower()]
    if banned:
        c.blocking = True  # a rule was broken, not an opinion offered
        return c.failed(f"the output rail would deny this text: {banned}")
    if score > 0.05:
        return c.failed(
            f"advice-language score {score:.2f} above the 0.05 gate ({why}). The "
            f"deterministic output rail passes the same text, so this is a judgement "
            f"about framing rather than a rule being broken"
        )
    c.evidence = f"advice score {score:.2f}, and the output rail passes it ({why})"
    return c.ok()


CHECKS = (
    g1_faithfulness,
    g2_the_judge_catches_a_hallucination,
    g3_answer_relevancy,
    g4_no_advice_and_no_toxicity,
)
