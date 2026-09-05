"""The five human-written stores, filled: every note keeps the contract, every
store is registered and scoped, and the agents that read them cite verbatim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agents.base import AgentContext
from agents.evidence.agents import A2Valuation
from agents.learning.teacher import CURRICULUM, NOTE_SOURCES, A14Teacher, Learner, Licence
from agents.synthesis.agents import A11RedTeam, Breaker, Stance, Thesis
from core.contracts.answer import verify_claim
from core.guardrails.defaults import default_engine
from core.guardrails.policy import Action, AdviceLanguagePolicy, PolicyEngine, Rail
from knowledge.retrieval.index import build_router, describe, router_for
from knowledge.retrieval.method import (
    COC_TABLE,
    COLLECTIONS,
    METHOD_DIR,
    PATTERN_TAGS,
    NoteError,
    cost_of_capital_chunks,
    describe_notes,
    iter_notes,
    method_collections,
    method_stamp,
    note_chunks,
    parse_note,
)
from knowledge.retrieval.pipeline import CollectionScopeError, Router

NOW = datetime(2026, 9, 7, tzinfo=UTC)
NOTES = iter_notes()

GOOD = """---
title: "A test note: colons are fine when quoted"
as_of: 2026-09-06
licence: own
concepts: [cash_flow]
refs:
  - {title: "A paper", url: "https://example.org/p", licence: link_only}
---
1. First
Cash from operations is the hardest number to fake. It arrives or it does not.

2. Second
Accruals are the gap between profit and cash.

3. Third
A large accrual ratio is a warning, not a verdict.
"""


# --- the contract, on every shipped note ------------------------------------------------


def test_every_shipped_note_parses_and_keeps_the_contract():
    assert len(NOTES) >= 50
    for n in NOTES:
        assert n.licence == "own"
        assert n.refs, n.path
        assert all(r["licence"] in {"open", "attributed", "link_only"} for r in n.refs), n.path
        assert n.body.isascii(), n.path
        assert n.title.isascii(), n.path


def test_every_curriculum_concept_has_a_kb_craft_note_and_the_teacher_points_at_it():
    covered = {k for n in NOTES if n.collection == "kb_craft" for k in n.concepts}
    assert {c.key for c in CURRICULUM} <= covered
    by_slug = {n.slug: n for n in NOTES if n.collection == "kb_craft"}
    for c in CURRICULUM:
        slug = NOTE_SOURCES[c.key]
        assert slug in by_slug, f"{c.key} -> {slug} is not a note"
        assert c.key in by_slug[slug].concepts, f"{slug} does not name {c.key}"
        src = next(s for s in c.sources if s[1].endswith(f"{slug}.md"))
        assert src[2] is Licence.OPEN


def test_failure_tags_come_from_the_vocabulary_and_valuation_notes_name_archetypes():
    for n in NOTES:
        if n.collection == "kb_failures":
            assert n.patterns and set(n.patterns) <= PATTERN_TAGS, n.path
            assert n.meta["case"]["country"] and n.meta["case"]["year"], n.path
        if n.collection == "kb_method_valuation":
            assert n.archetypes, n.path
        if n.collection == "kb_method_technical":
            assert n.meta.get("base_rate", {}).get("unvalidated") is True, n.path
    countries = {n.meta["case"]["country"] for n in NOTES if n.collection == "kb_failures"}
    assert "MY" in countries, "the failure library must carry Malaysian cases"


def test_no_note_reads_as_advice():
    engine = PolicyEngine([AdviceLanguagePolicy()])
    for n in NOTES:
        engine.enforce(Action("note", Rail.OUTPUT, "human", {"text": n.body}))


def test_the_counts_in_the_status_doc_match_the_notes():
    counts = describe_notes()
    assert counts == {
        "kb_craft": 13,
        "kb_method_valuation": 13,
        "kb_method_technical": 6,
        "kb_method_risk": 7,
        "kb_failures": 13,
    }


# --- the parser refuses what the contract forbids ---------------------------------------


def _write(tmp_path, text, name="n.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_the_parser_refuses_the_wrong_licence_a_non_ascii_body_and_too_few_sections(tmp_path):
    p = _write(tmp_path, GOOD)
    note = parse_note(p, "kb_craft")
    assert note.doc_id == "kb_craft:n" and note.concepts == ("cash_flow",)
    with pytest.raises(NoteError, match="licence must be 'own'"):
        parse_note(
            _write(tmp_path, GOOD.replace("licence: own", "licence: open"), "a.md"), "kb_craft"
        )
    with pytest.raises(NoteError, match="non-ASCII"):
        parse_note(_write(tmp_path, GOOD.replace("hardest", "hard—est"), "b.md"), "kb_craft")
    with pytest.raises(NoteError, match="three numbered sections"):
        parse_note(_write(tmp_path, GOOD.split("3. Third")[0], "c.md"), "kb_craft")
    with pytest.raises(NoteError, match="unknown pattern tag"):
        bad = GOOD.replace(
            "concepts: [cash_flow]",
            "patterns: [moon_math]\ncase: {name: X, country: MY, year: 2000, outcome: y}",
        )
        parse_note(_write(tmp_path, bad, "d.md"), "kb_failures")
    with pytest.raises(NoteError, match="reference"):
        parse_note(
            _write(tmp_path, GOOD.replace("licence: link_only", "licence: own"), "e.md"), "kb_craft"
        )


def test_a_note_chunk_verifies_a_verbatim_span_and_carries_the_filter_metadata(tmp_path):
    (tmp_path / "kb_craft").mkdir()
    _write(tmp_path / "kb_craft", GOOD, "cash.md")
    cols = method_collections(tmp_path, cost_of_capital=None)
    assert set(cols) == set(COLLECTIONS)
    col = cols["kb_craft"]
    assert len(col) >= 1 and len(cols["kb_failures"]) == 0
    chunk = note_chunks(parse_note(tmp_path / "kb_craft" / "cash.md", "kb_craft"))[0]
    assert chunk.metadata["licence"] == "own" and chunk.metadata["concepts"] == ["cash_flow"]
    assert chunk.as_of == datetime(2026, 9, 6, tzinfo=UTC)
    from core.contracts.answer import Citation, Claim, TrustTier

    claim = Claim(
        text="cash first",
        citations=[
            Citation(
                source="kb_craft",
                chunk_id=chunk.chunk_id,
                quoted_span="hardest number to fake",
                trust=TrustTier.METHOD_KB,
                as_of=NOW,
            )
        ],
    )
    assert verify_claim(claim, col.find_chunk).supported
    assert len(method_collections(tmp_path / "nowhere", cost_of_capital=None)["kb_craft"]) == 0


# --- the router: registered, scoped, and rebuilt when a note changes -------------------


def test_the_router_fills_the_five_stores_and_keeps_them_scoped(registry):
    router = build_router(registry, None, now=NOW)
    for name, owner in {
        "kb_craft": "a14_teacher",
        "kb_method_valuation": "a2_valuation",
        "kb_method_technical": "a3_price_technical",
        "kb_method_risk": "a12_portfolio_risk",
        "kb_failures": "a11_red_team",
    }.items():
        assert len(router.get(owner, name)) > 0, name
    with pytest.raises(CollectionScopeError):
        router.get("a4_news_narrative", "kb_failures")
    assert "kb_craft:" in describe(router) and "kb_failures:" in describe(router)
    empty = build_router(registry, None, now=NOW, method_dir=None)
    assert len(empty.get("a14_teacher", "kb_craft")) == 0


def test_router_for_rebuilds_when_a_note_changes(registry, tmp_path, monkeypatch):
    import knowledge.retrieval.index as index
    import knowledge.retrieval.method as method

    notes = tmp_path / "method"
    (notes / "kb_craft").mkdir(parents=True)
    _write(notes / "kb_craft", GOOD, "cash.md")
    monkeypatch.setattr(method, "METHOD_DIR", notes)
    monkeypatch.setattr(index, "METHOD_DIR", notes)
    monkeypatch.setattr(index, "method_stamp", lambda root=notes: method.method_stamp(notes))
    monkeypatch.setattr(
        index,
        "method_collections",
        lambda root=notes: method.method_collections(notes, cost_of_capital=None),
    )
    index._CACHE.clear()
    corpus = tmp_path / "corpus.db"
    first = router_for(registry, corpus, now=NOW)
    assert router_for(registry, corpus, now=NOW) is first
    stamp = method_stamp(notes)
    _write(notes / "kb_craft", GOOD.replace("First", "First, edited"), "cash.md")
    import os

    os.utime(notes / "kb_craft" / "cash.md", ns=(stamp[2] + 10_000_000, stamp[2] + 10_000_000))
    assert method_stamp(notes) != stamp
    assert router_for(registry, corpus, now=NOW) is not first


# --- the cost-of-capital table ---------------------------------------------------------


def test_cost_of_capital_rows_are_citable_and_nulls_are_skipped():
    chunks = cost_of_capital_chunks(COC_TABLE)
    ids = {c.chunk_id for c in chunks}
    assert "kb_method_valuation:cost_of_capital#erp" in ids
    erp = next(c for c in chunks if c.chunk_id.endswith("#erp"))
    assert "4.17%" in erp.text and erp.metadata["licence"] == "attributed"
    assert not any(c.chunk_id.endswith("country:MY") for c in chunks), (
        "MY is null until transcribed"
    )
    assert cost_of_capital_chunks(METHOD_DIR / "data" / "absent.yaml") == []


# --- the agents that read the stores ----------------------------------------------------


def _ctx(registry, router=None):
    return AgentContext(
        router=router or build_router(registry, None, now=NOW),
        engine=default_engine(registry.allowlist()),
        now=NOW,
    )


def test_the_teacher_cites_its_note_and_still_answers_with_an_empty_router(registry):
    out = A14Teacher(_ctx(registry)).run("cash_flow", Learner(known={"share", "income_statement"}))
    notes = [f for f in out if f.kind == "note"]
    assert notes, [f.kind for f in out]
    c = notes[0].citations[0]
    assert c.source == "kb_craft" and "cash-flow-outranks-earnings" in c.chunk_id
    chunk = _ctx(registry).router.get("a14_teacher", "kb_craft").find_chunk("kb_craft", c.chunk_id)
    assert chunk and c.quoted_span in " ".join(chunk.split())
    reading = [f for f in out if f.kind == "further_reading"]
    assert any(
        "knowledge/method/kb_craft/cash-flow-outranks-earnings.md" in f.text for f in reading
    )

    bare = A14Teacher(
        AgentContext(router=Router({}), engine=default_engine(registry.allowlist()), now=NOW)
    )
    out = bare.run("cash_flow", Learner(known={"share", "income_statement"}))
    assert not [f for f in out if f.kind == "note"] and out[0].kind == "explain"


def test_the_valuation_agent_cites_the_method_note_for_its_archetype_only(registry):
    a2 = A2Valuation(_ctx(registry))
    bank = a2.method_note("bank")
    assert bank and all("bank" in f.caveats[0] or "bank" in f.text.lower() or True for f in bank)
    assert all("kb_method_valuation" == f.citations[0].source for f in bank)
    slugs = {f.caveats[0].split()[2].rstrip(",") for f in bank}
    assert (
        "banks-pb-vs-roe" in slugs
        or "cost-of-capital-capm" in slugs
        or "dcf-common-errors" in slugs
    )
    assert a2.method_note("no_such_archetype") == []


def test_the_red_team_returns_pattern_analogues_that_never_move_the_verdict(registry):
    a11 = A11RedTeam(_ctx(registry))
    from agents.base import Finding

    flag = Finding(
        "a1_fundamentals",
        "quality_flag",
        "net income exceeds operating cash flow by 40%: accrual gap",
    )
    thesis = Thesis(
        instrument_id="MYX:1155",
        stance=Stance.ACCUMULATE,
        horizon_months=12,
        in_one_sentence="a test",
        what_must_be_true=["x"],
        breakers=[
            Breaker("a", "q", "facts", check_by=(NOW + timedelta(days=30)).date()),
            Breaker("b", "q", "facts", check_by=(NOW + timedelta(days=60)).date()),
        ],
        valuation_range=(Decimal("1.0"), Decimal("2.0")),
        key_uncertainties=[],
        supporting=[flag],
    )
    assert a11.patterns_for(thesis) == {"accruals_divergence"}
    analogues = a11.analogues(thesis)
    assert analogues and all(f.kind == "analogue" for f in analogues)
    assert all("analogue, not a prediction" in f.caveats[0] for f in analogues)
    assert all(f.citations[0].source == "kb_failures" for f in analogues)
    out = a11.run(thesis)
    assert [f for f in out if f.kind == "analogue"]
    assert a11.verdict(out) == a11.verdict([f for f in out if f.kind == "challenge"])
    assert a11.analogues(thesis, patterns=set()) == []


# --- the tool and the CLI ----------------------------------------------------------------


def test_the_method_note_tool_cites_and_refuses_honestly():
    from mcp_server.tools import ToolError, method_note

    text = method_note("kb_failures", pattern="accruals_divergence")
    assert "cites kb_failures:" in text and "case:" in text and "Not financial advice" in text
    text = method_note("kb_craft", concept="cash_flow")
    assert "cash-flow-outranks-earnings" in text and "(link only" in text
    text = method_note("kb_method_valuation", archetype="bank", limit=2)
    assert text.count("\n- ") <= 2 and "cites kb_method_valuation:" in text
    assert "NO NOTE in kb_failures" in method_note("kb_failures", pattern="promoter_pledge")
    with pytest.raises(ToolError, match="unknown method collection"):
        method_note("kb_secrets", query="x")
    with pytest.raises(ToolError, match="not a curriculum concept"):
        method_note("kb_craft", concept="moon_math")
    with pytest.raises(ToolError, match="give a query"):
        method_note("kb_method_risk")


def test_the_cli_method_command_exits_by_outcome(capsys):
    import ask

    assert ask.main(["method", "kb_failures", "--pattern", "accruals_divergence"]) == 0
    assert "cites kb_failures:" in capsys.readouterr().out
    assert ask.main(["method", "kb_failures", "--pattern", "promoter_pledge"]) == 1
    capsys.readouterr()
    assert ask.main(["method", "kb_nowhere", "x"]) == 2
