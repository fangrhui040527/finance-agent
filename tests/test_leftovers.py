"""The buildable leftovers: context overflow, impact citation, --fetch-fx,
the news command, hypothesis-cohort reflection, trace blobs."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests._anthropic_double import FakeAnthropic
from tests.conftest import opener_for

# --- context overflow -----------------------------------------------------------


def test_a_context_overflow_400_is_its_own_error():
    import anthropic
    import httpx2

    from core.llm.backends import AnthropicBackend, BackendError, ContextOverflow

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overflow = anthropic.BadRequestError(
        "prompt is too long: 250000 tokens > 200000 maximum",
        response=httpx2.Response(400, request=req),
        body=None,
    )
    fake = FakeAnthropic([overflow])
    with pytest.raises(ContextOverflow, match="[Ss]hrink"):
        AnthropicBackend(client=fake, sleep=lambda _s: None).complete("claude-haiku-4-5", "q", None)
    assert len(fake.calls) == 1  # never retried: the same prompt hits the same wall

    plain = anthropic.BadRequestError(
        "invalid field: max_tokens", response=httpx2.Response(400, request=req), body=None
    )
    with pytest.raises(BackendError) as exc:
        AnthropicBackend(client=FakeAnthropic([plain]), sleep=lambda _s: None).complete(
            "claude-haiku-4-5", "q", None
        )
    assert not isinstance(exc.value, ContextOverflow)


# --- sizing cites its impact regime ----------------------------------------------


def _a13():
    from agents.base import AgentContext
    from agents.portfolio.agents import A13Sizing
    from core.guardrails.defaults import default_engine
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router

    engine = default_engine(load_registry("agents/registry.yaml").allowlist())
    return A13Sizing(AgentContext(router=Router({}), engine=engine, now=datetime.now(UTC)))


def _flat(v: Decimal) -> Decimal:
    return v * Decimal("0.001")


def test_the_liquidity_finding_names_its_impact_model():
    _, findings = _a13().caps(
        portfolio_value=Decimal("200000"),
        stop_distance_frac=Decimal("0.10"),
        adv_20d=Decimal("900000"),
        round_trip_cost_at=_flat,
        mic="XKLS",
    )
    text = " ".join(f.text for f in findings) + " ".join(c for f in findings for c in f.caveats)
    assert "linear participation model" in text


def test_above_the_linear_regime_the_note_says_so():
    _, findings = _a13().caps(
        portfolio_value=Decimal("200000"),
        stop_distance_frac=Decimal("0.10"),
        adv_20d=Decimal("900000"),
        round_trip_cost_at=_flat,
        participation=Decimal("0.10"),
        mic="XKLS",
    )
    text = " ".join(f.text for f in findings) + " ".join(c for f in findings for c in f.caveats)
    assert "square-root" in text and "true cost is higher" in text


# --- ask.py size --fetch-fx ------------------------------------------------------


def test_fetch_fx_end_to_end(monkeypatch, capsys):
    import ask
    from core.market import fx as fxmod

    body = json.dumps(
        {
            "data": [
                {
                    "currency_code": "USD",
                    "unit": 1,
                    "rate": {"middle_rate": "4.20", "date": "2026-08-29"},
                }
            ]
        }
    )
    real = fxmod.BnmFxFeed
    monkeypatch.setattr(fxmod, "BnmFxFeed", lambda: real(opener=opener_for(body)))
    code = ask.main(
        [
            "size",
            "XNAS:NVDA",
            "--portfolio",
            "200000",
            "--price",
            "120",
            "--stop",
            "108",
            "--adv",
            "900000",
            "--fetch-fx",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "BNM middle rate, 2026-08-29" in out
    assert "4.20" in out


def test_without_fx_or_the_flag_the_refusal_now_mentions_the_flag(capsys):
    import ask

    code = ask.main(
        [
            "size",
            "XNAS:NVDA",
            "--portfolio",
            "200000",
            "--price",
            "120",
            "--stop",
            "108",
            "--adv",
            "900000",
        ]
    )
    out = capsys.readouterr().out
    assert code in (0, 2)
    assert "--fetch-fx" in out


# --- ask.py news -----------------------------------------------------------------

RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Biz</title>
<item><title>Bank posts record quarter</title>
  <link>https://example.com/a1</link>
  <description>NIM improved.</description>
  <pubDate>Sun, 30 Aug 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_pulls_a_registered_source(monkeypatch, capsys):
    import ask
    from knowledge.feeds import registry
    from knowledge.feeds.rss import RssFeed

    monkeypatch.setattr(
        registry,
        "adapter_for",
        lambda name, **kw: RssFeed(
            "https://example.com/rss", name=name, opener=opener_for(RSS_BODY)
        ),
    )
    code = ask.main(["news", "reuters_business", "--hours", "999999"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Bank posts record quarter" in out
    assert "kept 1" in out


def test_news_refuses_an_unknown_source(capsys):
    import ask

    code = ask.main(["news", "bloomberg_terminal"])
    err = capsys.readouterr().err
    assert code == 2
    assert "no adapter registered" in err


# --- predict.py reflect ----------------------------------------------------------


def test_reflect_grades_a_hypothesis_cohort(tmp_path, capsys):
    import predict
    from agents.learning.hypotheses import HypothesisStore
    from agents.learning.reflection import Horizon, Outcome, Prediction
    from agents.learning.store import LearningStore

    db = tmp_path / "learning.db"
    with LearningStore(db) as store, HypothesisStore(db) as hstore:
        hid = hstore.create("Banks re-rate", "NIM stabilises above 2.25%")
        for i in range(5):
            pid = f"p{i}"
            store.record(
                Prediction(
                    prediction_id=pid,
                    instrument_id=f"MYX:{1155 + (i % 3)}",
                    agent="t",
                    made_at=datetime(2026, 1, 1, tzinfo=UTC),
                    horizon=Horizon.D21,
                    statement="s",
                    direction=1,
                    confidence=0.6,
                    grade_on=date(2026, 2, 1),
                )
            )
            store.record_outcome(Outcome(pid, date(2026, 3, 1), 0.01, 0.0, i % 2 == 0, ""))
            hstore.link(hid, pid)

    code = predict.main(["--db", str(db), "reflect", hid])
    out = capsys.readouterr().out
    assert code == 0
    assert "linked 5, graded 5" in out
    assert "no_lesson" in out or "NO LESSON" in out or "lesson" in out.lower()


def test_reflect_on_an_unknown_hypothesis_is_a_clean_refusal(tmp_path, capsys):
    import predict

    code = predict.main(["--db", str(tmp_path / "l.db"), "reflect", "H-nope"])
    assert code == 2
    assert "H-nope" in capsys.readouterr().err


# --- trace blobs -----------------------------------------------------------------


def test_trace_blob_round_trip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    monkeypatch.chdir(tmp_path)
    run = tmp_path / "debug" / "20260831T120000-abc123" / "prompts"
    run.mkdir(parents=True)
    (run / "0001-narrate-prompt.txt").write_text("the full prompt text", encoding="utf-8")

    client = TestClient(create_app())
    ok = client.get("/api/trace/runs/20260831T120000-abc123/blob/0001-narrate-prompt.txt")
    assert ok.status_code == 200
    assert ok.json()["text"] == "the full prompt text"

    bad = client.get("/api/trace/runs/20260831T120000-abc123/blob/..%2f..%2fsecrets.txt")
    assert bad.status_code == 404
