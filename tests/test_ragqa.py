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


def test_the_audit_runs_end_to_end_and_reports_every_phase(tmp_path):
    """The whole thing, against whatever stores this checkout has. It must not
    raise, and it must reach all four phases even when a store is missing.

    The missing path lives under tmp_path because it used to be a bare
    "does-not-exist.db" at the repository root - which the retrieval phase then
    CREATED, so the test stopped exercising the missing-store branch after its
    first run and left a tracked empty database behind."""
    from ragqa.run import cleaning, owasp, phase1, retrieval

    absent = tmp_path / "no-such-corpus.db"
    rep = Report()
    phase1(rep, str(absent))
    retrieval(rep, str(absent))
    cleaning(rep, str(absent), ("MYX:1155",))
    owasp(rep)
    phases = {c.phase for c in rep.checks}
    assert len(phases) == 4, f"expected all four phases, got {phases}"
    assert rep.render()
    assert not absent.exists(), "a read-only audit must not create the store it measures"


def test_the_audit_writes_nothing_where_it_is_pointed(tmp_path):
    """Pinned as a property, not as one path: an audit that can write can also
    corrupt what it is auditing, and this one runs against the live stores.

    Deliberately NOT run from a different working directory - the audit reads
    the registry and the gold set by repo-relative path, and moving it would
    test the harness rather than the audit."""
    from ragqa.run import cleaning, phase1, retrieval

    rep = Report()
    absent = tmp_path / "absent.db"
    phase1(rep, str(absent))
    retrieval(rep, str(absent))
    cleaning(rep, str(absent), ("MYX:1155",))
    assert list(tmp_path.iterdir()) == [], "the audit created something where it was pointed"


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
