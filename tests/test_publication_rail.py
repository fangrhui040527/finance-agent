"""The fifth rail, and the check that would have said it was never built.

`Rail.PUBLICATION` sat in the rail order and in `DisclaimerPolicy` from the
start. Nothing in production ever constructed an `Action` carrying it, so the
rule was reachable only from the tests that asserted it existed. Four rails
enforced; the fifth agreed with itself.

These tests are in two halves. The first fixes the rule so it reads the text
rather than a flag the caller sets. The second is structural: it walks the
production tree and asserts every rail in `RAIL_ORDER` has a constructor
outside the module that declares it - which is the assertion whose absence let
this last four months.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.guardrails.chain import RAIL_ORDER
from core.guardrails.defaults import default_engine
from core.guardrails.policy import Action, PolicyViolation, Rail
from core.guardrails.publish import PUBLICATION_NOTICE, carries_notice, publish, sign

ROOT = Path(__file__).resolve().parents[1]

#: The three modules that DEFINE the rails rather than use them. A reference
#: here is a declaration, not a construction, and counting one would let the
#: enum satisfy its own coverage test.
_DECLARING = {
    "core/guardrails/policy.py",
    "core/guardrails/defaults.py",
    "core/guardrails/chain.py",
}

#: Directories that are not the running system: suites, audits and the
#: self-check script. `verify.py` and `ragqa/` both construct rails to PROVE
#: they deny, which is the opposite of a production path using one.
_NOT_PRODUCTION = ("tests/", "qa/", "audit/", "ragqa/", "stress/", "verify.py", "trace_run.py")


# --- the rule reads the text ------------------------------------------------------


def test_a_true_flag_no_longer_buys_a_pass_for_unsigned_text():
    """The old rule consulted `payload["disclaimer"]`, which is a check the
    caller passes by asserting it has passed."""
    engine = default_engine({"a0": {"publish"}})
    with pytest.raises(PolicyViolation):
        engine.enforce(
            Action("publish", Rail.PUBLICATION, "a0", {"disclaimer": True, "text": "BUY MAYBANK"})
        )


def test_signed_text_passes():
    engine = default_engine({"a0": {"publish"}})
    res = engine.enforce(
        Action("publish", Rail.PUBLICATION, "a0", {"text": sign("the case for the name")})
    )
    assert res.decision.name != "DENY"


def test_the_flag_still_answers_when_there_is_no_text_to_read():
    """The chain's structural walk passes an empty payload at every rail. There
    is genuinely nothing to read there, and it must still deny."""
    engine = default_engine({"a0": {"publish"}})
    with pytest.raises(PolicyViolation):
        engine.enforce(Action("publish", Rail.PUBLICATION, "a0", {"disclaimer": False}))


def test_sign_is_idempotent():
    once = sign("body")
    assert sign(once) == once
    assert once.count(PUBLICATION_NOTICE) == 1


def test_publish_raises_rather_than_returning_a_verdict_to_ignore():
    engine = default_engine({"a0": {"narrate"}})
    with pytest.raises(PolicyViolation):
        publish(engine, "a0", "narrate", "unsigned prose")


# --- the surfaces sign themselves -------------------------------------------------


def test_the_daily_brief_says_what_it_is():
    """It never did. The memo signed itself and the brief did not, and the
    brief is the one read on the days when there is no thesis."""
    from datetime import UTC, datetime

    from ui.render import daily_brief

    out = daily_brief(datetime(2026, 9, 7, tzinfo=UTC), [], [], [])
    assert carries_notice(out)


def test_the_thesis_memo_still_says_what_it_is():
    from ui.render import PUBLICATION_NOTICE as constant
    from ui.render import thesis_memo

    assert constant is PUBLICATION_NOTICE
    out = thesis_memo(
        instrument_id="MYX:1155",
        stance="watch",
        one_sentence="the deposit franchise is the whole thesis",
        what_must_be_true=["CASA holds above 24%"],
        breakers=[("CASA below 22%", "casa_ratio", None)],
        valuation_range=None,
        uncertainties=[],
        gaps=["no filings"],
        challenges=[],
        confidence=0.4,
    )
    assert carries_notice(out)


# --- the digest, which is the one that is literally published ---------------------


def _digest():
    from knowledge.digest import Digest

    return Digest(day="2026-09-07", generated_at="2026-09-07T12:00:00+00:00", slot="bursa_close")


def test_the_digest_markdown_and_json_both_carry_the_notice():
    d = _digest()
    assert carries_notice(d.to_markdown())
    assert json.loads(d.to_json())["notice"] == PUBLICATION_NOTICE


def test_an_unsigned_digest_is_refused_and_leaves_no_file_behind(tmp_path, monkeypatch):
    """The collector commits the whole directory. A digest that fails the rail
    after `latest.md` is written would publish a signed latest beside an
    unsigned dated copy, so the gate runs before the first byte."""
    from knowledge import digest as D

    monkeypatch.setattr(D.Digest, "to_markdown", lambda self: "# Digest\n\nMAYBANK ★")
    with pytest.raises(PolicyViolation):
        D.write_digest(_digest(), tmp_path)
    assert list(tmp_path.glob("*")) == [], "a refused digest writes nothing at all"


def test_a_written_digest_is_signed_on_disk(tmp_path):
    from knowledge.digest import write_digest

    md, js = write_digest(_digest(), tmp_path)
    assert carries_notice(md.read_text(encoding="utf-8"))
    assert carries_notice((tmp_path / "latest.md").read_text(encoding="utf-8"))
    assert json.loads(js.read_text(encoding="utf-8"))["notice"] == PUBLICATION_NOTICE


# --- the structural check ---------------------------------------------------------


def _production_files() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(".") or "/." in rel or "__pycache__" in rel:
            continue
        if any(rel.startswith(x) or rel == x for x in _NOT_PRODUCTION):
            continue
        out.append(p)
    return out


def _rails_constructed(path: Path) -> set[str]:
    """Rails named inside an `Action(...)` call in this file.

    Parsed, not grepped: a rail named in a docstring or a comment is a mention,
    and a rail that is only mentioned is what this test exists to catch.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return found
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Action":
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "Rail"
            ):
                found.add(sub.attr)
    return found


def test_every_rail_has_a_production_constructor():
    """The assertion that was missing.

    `rails_covered()` asks whether a RULE claims a rail. That was always true
    of publication and told us nothing: a rule nobody hands an action to is a
    rule that never runs. This asks the other question - does anything in the
    running system BUILD one - and names the rail if not.
    """
    built: dict[str, list[str]] = {}
    for path in _production_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _DECLARING:
            continue
        for rail in _rails_constructed(path):
            built.setdefault(rail, []).append(rel)

    missing = [r.name for r in RAIL_ORDER if r.name not in built]
    assert not missing, (
        f"declared but never constructed in production: {missing}. "
        f"A rail with no action built for it is enforced nowhere, whatever "
        f"`rails_covered()` reports."
    )


def test_the_publication_rail_is_built_where_output_actually_leaves():
    """Not just somewhere. The four doors: the file that gets pushed, the
    terminal, the HTTP response, and the MCP tool's return value."""
    callers = {
        p.relative_to(ROOT).as_posix()
        for p in _production_files()
        if "publish(" in p.read_text(encoding="utf-8")
    }
    for expected in ("knowledge/digest.py", "ask.py", "web/api.py", "mcp_server/tools.py"):
        assert expected in callers, f"{expected} does not pass through the publication rail"
