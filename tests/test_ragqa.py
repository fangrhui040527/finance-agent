"""The RAG and cleaning audit must not flatter the system it measures.

`ragqa/run.py` prints a readiness percentage, and a percentage is exactly the
kind of number that becomes comfortable and stops being read. These pin the
properties that keep it honest: a check it cannot compute is not a pass, a
finding is not silently dropped from the score, and the rail scan does not
count a rule's own declaration as evidence that something calls it.
"""

from __future__ import annotations

from ragqa.run import FAIL, FINDING, PASS, SKIP, Check, Report


def test_a_check_it_cannot_compute_is_not_counted_as_a_pass():
    rep = Report()
    rep.add("p", "measurable", PASS)
    rep.add("p", "needs a model", SKIP, "no backend")
    assert rep.readiness == 1.0
    assert len([c for c in rep.checks if c.scored]) == 1, "a skip must not enter the denominator"


def test_a_finding_lowers_the_score():
    rep = Report()
    rep.add("p", "good", PASS)
    rep.add("p", "bad", FINDING, "something is wrong")
    assert rep.readiness == 0.5


def test_a_failure_lowers_the_score():
    rep = Report()
    rep.add("p", "good", PASS)
    rep.add("p", "broken", FAIL)
    assert rep.readiness == 0.5


def test_an_empty_report_does_not_divide_by_zero():
    assert Report().readiness == 0.0


def test_what_is_wrong_names_every_non_passing_check():
    rep = Report()
    rep.add("p", "fine", PASS)
    rep.add("p", "a finding", FINDING, "detail here")
    rep.add("p", "a failure", FAIL, "other detail")
    text = rep.render()
    assert "WHAT IS WRONG" in text
    assert "a finding" in text and "a failure" in text


def test_a_skip_is_shown_but_not_in_what_is_wrong():
    rep = Report()
    rep.add("p", "fine", PASS)
    rep.add("p", "unmeasurable", SKIP, "needs a model")
    text = rep.render()
    assert "unmeasurable" in text
    assert "not measurable" in text
    assert "WHAT IS WRONG" not in text


def test_the_scored_property_matches_the_status():
    assert Check("p", "n", PASS).scored
    assert Check("p", "n", FAIL).scored
    assert Check("p", "n", FINDING).scored
    assert not Check("p", "n", SKIP).scored


def test_the_audit_runs_end_to_end_and_reports_every_phase():
    """The whole thing, against whatever stores this checkout has. It must not
    raise, and it must reach all four phases even when a store is missing."""
    from ragqa.run import cleaning, owasp, phase1, retrieval

    rep = Report()
    phase1(rep, "does-not-exist.db")
    retrieval(rep, "does-not-exist.db")
    cleaning(rep, "does-not-exist.db", ("MYX:1155",))
    owasp(rep)
    phases = {c.phase for c in rep.checks}
    assert len(phases) == 4, f"expected all four phases, got {phases}"
    assert rep.render()


def test_the_owasp_phase_checks_both_injection_directions():
    """Direct and indirect are different vectors and the audit must ask both:
    the guide's LLM01 covers prompts hidden inside retrieved documents, which
    is the half a rule scanning only user input cannot see."""
    from ragqa.run import owasp

    rep = Report()
    owasp(rep)
    names = [c.name for c in rep.checks]
    assert any("LLM01 direct" in n for n in names)
    assert any("LLM01 indirect" in n for n in names)
