"""The owner's hard monthly cap: USD 50 a month on the Anthropic API key.

What makes it a cap rather than a report, each pinned here:

  * **It reserves before it spends.** Month-to-date plus the most the next call
    can cost must fit; a check of what is already spent lets the last call of
    the month overshoot by its whole cost.
  * **It counts every recorded source.** The committed evidence spend in the
    corpus (`evidence_searches`, written by the web evidence ingest on a runner
    whose own ledger is thrown away) plus this process's ledger.
  * **It is a calendar month in UTC.** Last month's spend does not count
    against this one, however large.
  * **A refusal is disclosed, never a downgrade.** The call is not re-routed to
    a cheaper model or a lower effort to squeeze under the line.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import ConfigError, load
from core.guardrails.defaults import default_engine
from core.llm import spend
from core.llm.client import BudgetExceeded, InferenceClient, input_estimate
from core.llm.tiers import TaskClass, Tier, Usage, profile_for, worst_case_usd
from core.provenance.ledger import ProvenanceLedger

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_selection(monkeypatch):
    """The reservation depends on the request shape; an exported pin or effort
    from a developer's shell would move it."""
    for name in ("FINPLANET_MODEL", "FINPLANET_EFFORT", "FINPLANET_CHEAP"):
        monkeypatch.delenv(name, raising=False)


def _corpus(path: Path, rows: list[tuple[datetime, str]]) -> Path:
    """A corpus holding only what the cap reads: paid searches, when and what."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE evidence_searches (at TEXT NOT NULL, cost_usd TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO evidence_searches (at, cost_usd) VALUES (?, ?)",
        [(at.isoformat(), usd) for at, usd in rows],
    )
    con.commit()
    con.close()
    return path


class Recorder:
    """A backend that counts its calls, so a refusal can be shown to have
    happened BEFORE the wire rather than after a billed call."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, model_id, prompt, system, profile=None):
        self.calls += 1
        return "ok", Usage(input_tokens=10, output_tokens=5)


def _client(tmp_path: Path, corpus: Path | None, cap: str | None = "50"):
    backend = Recorder()
    ledger = ProvenanceLedger(tmp_path / "ledger.db")
    client = InferenceClient(
        backend,
        default_engine({"a10": {"llm_complete"}}),
        ledger,
        monthly_budget_usd=None if cap is None else Decimal(cap),
        corpus_path=None if corpus is None else str(corpus),
    )
    return client, backend, ledger


PROMPT = "x" * 300


def _reservation() -> Decimal:
    """What a thesis call (REASON, Opus 5.5) of PROMPT reserves."""
    return worst_case_usd(
        "claude-opus-5-5", input_estimate(PROMPT, None), profile_for(Tier.REASON).max_tokens
    )


def _this_month() -> datetime:
    return spend.month_start() + timedelta(minutes=1)


# --- the client --------------------------------------------------------------------


def test_the_reservation_is_the_worst_case_of_the_call_about_to_be_made():
    # 300 characters, no system prompt: 164 tokens reserved at Opus 5.5's dearest
    # input rate, plus the reason tier's whole 16,000-token output cap at $20/M.
    assert input_estimate(PROMPT, None) == 164
    assert input_estimate(PROMPT, "s" * 30) == 174
    assert _reservation() == Decimal(164) / 1_000_000 * Decimal("5.00") + Decimal("0.32")


def test_a_call_whose_worst_case_crosses_the_cap_is_refused_before_the_wire(tmp_path):
    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "49.80")])
    client, backend, ledger = _client(tmp_path, corpus)
    with pytest.raises(BudgetExceeded) as exc:
        client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    message = str(exc.value)
    assert "month-to-date USD 49.8000" in message
    assert f"reservation USD {_reservation():.4f}" in message
    assert "monthly cap USD 50.00" in message
    assert "refused and disclosed, not downgraded" in message
    assert backend.calls == 0, "a refusal must happen before the call, not after it"
    assert list(ledger.calls()) == [], "nothing was spent, so nothing is ledgered"


def test_a_call_whose_worst_case_fits_goes_through(tmp_path):
    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "49.50")])
    client, backend, ledger = _client(tmp_path, corpus)
    done = client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert done.text == "ok" and backend.calls == 1
    assert len(list(ledger.calls())) == 1


def test_last_months_spend_does_not_count_against_this_month(tmp_path):
    """USD 1,000 spent one second before the month began - a month that went
    badly - and this month is untouched by it."""
    last_second = spend.month_start() - timedelta(seconds=1)
    corpus = _corpus(tmp_path / "corpus.db", [(last_second, "1000.00"), (_this_month(), "10.00")])
    client, backend, _ = _client(tmp_path, corpus)
    assert spend.month_to_date_usd(None, corpus) == Decimal("10.00")
    client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 1


def test_this_processes_own_ledger_counts_toward_the_month(tmp_path):
    """The committed corpus is half the month; what this process has spent
    itself is the other half, and a cap blind to it would let one long local
    session spend past the line."""
    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "45.00")])
    client, backend, ledger = _client(tmp_path, corpus)
    # On the corpus alone this call would fit: 45.00 plus its reservation.
    assert Decimal("45.00") + _reservation() < Decimal("50")
    ledger.record_call(
        "a10",
        TaskClass.THESIS_SYNTHESIS,
        Tier.REASON,
        "claude-opus-5-5",
        "earlier",
        Usage(input_tokens=1_200_000, output_tokens=0),  # USD 4.80 at Opus 5.5
        at=_this_month(),
    )
    assert spend.month_to_date_usd(ledger, corpus) == Decimal("49.80")
    with pytest.raises(BudgetExceeded, match="month-to-date USD 49.8000"):
        client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 0


def test_the_reservation_alone_can_be_refused(tmp_path):
    """Nothing spent yet, and still refused: a call that could cost more than
    the whole cap is not made in the hope it will not."""
    client, backend, _ = _client(tmp_path, None, cap="0.10")
    with pytest.raises(BudgetExceeded, match="month-to-date USD 0.0000"):
        client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 0


def test_the_cheap_tier_reserves_at_haikus_rate(tmp_path):
    """Triage at the edge of the month still runs: Haiku 5.5 reserves its own
    worst case, not Opus's."""
    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "49.99")])
    client, backend, _ = _client(tmp_path, corpus)
    client.complete("a10", TaskClass.NEWS_TRIAGE, PROMPT)  # ~USD 0.002 reserved
    assert backend.calls == 1
    with pytest.raises(BudgetExceeded):
        client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)


def test_a_free_provider_reserves_nothing(tmp_path):
    """A backend that declares zero pricing for a tier costs nothing to call, so
    a month at its cap is no reason to refuse it."""

    class Free(Recorder):
        def pricing_for(self, tier):
            return (Decimal(0), Decimal(0))

    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "50.00")])
    backend = Free()
    client = InferenceClient(
        backend,
        default_engine({"a10": {"llm_complete"}}),
        ProvenanceLedger(),
        monthly_budget_usd=Decimal("50"),
        corpus_path=str(corpus),
    )
    client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 1


def test_no_cap_means_no_monthly_check(tmp_path):
    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "9999.00")])
    client, backend, _ = _client(tmp_path, corpus, cap=None)
    client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 1


def test_a_corpus_without_the_evidence_table_is_zero_not_an_error(tmp_path):
    """Before the first paid search the table does not exist yet."""
    bare = tmp_path / "corpus.db"
    con = sqlite3.connect(bare)
    con.execute("CREATE TABLE articles (id TEXT)")
    con.commit()
    con.close()
    client, backend, _ = _client(tmp_path, bare)
    client.complete("a10", TaskClass.THESIS_SYNTHESIS, PROMPT)
    assert backend.calls == 1


# --- every real-backend entrypoint passes the cap ------------------------------------


@pytest.mark.parametrize("path", ["ask.py", "predict.py", "web/api.py"])
def test_every_real_backend_client_is_built_with_the_cap(path):
    """A cap the entrypoints do not pass is a setting that does nothing. Each
    InferenceClient built on `backend_from_env()` carries the configured cap
    and the corpus it reads month-to-date from."""
    src = (ROOT / path).read_text(encoding="utf-8")
    builds = re.findall(r"InferenceClient\((.*?)\n\s*\)", src, re.DOTALL)
    assert builds, f"no InferenceClient construction found in {path}"
    for args in builds:
        assert "monthly_budget_usd=cfg.monthly_budget_usd" in args, args
        assert "corpus_path=cfg.corpus_db" in args, args


# --- config --------------------------------------------------------------------------


def _toml(tmp_path: Path, budget: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(f"[budget]\ndaily_myr = 25.0\n{budget}\n", encoding="utf-8")
    return p


def test_the_shipped_config_caps_the_month_at_fifty_dollars():
    cfg = load(ROOT / "config.toml")
    assert cfg.monthly_budget_usd == Decimal("50")
    assert cfg.evidence_budget_usd == Decimal("10")
    assert "USD 50.00" in cfg.describe()


def test_an_absent_cap_is_no_cap(tmp_path):
    cfg = load(_toml(tmp_path, ""))
    assert cfg.monthly_budget_usd is None and cfg.evidence_budget_usd is None
    assert "no monthly limit" in cfg.describe()


def test_a_cap_of_zero_is_refused_not_read_as_refuse_everything(tmp_path):
    with pytest.raises(ConfigError, match="above zero"):
        load(_toml(tmp_path, "monthly_usd = 0"))
    with pytest.raises(ConfigError, match="above zero"):
        load(_toml(tmp_path, "monthly_usd = 50\nevidence_monthly_usd = 0"))


def test_a_negative_or_absurd_cap_is_out_of_bounds(tmp_path):
    with pytest.raises(ConfigError, match="permitted range"):
        load(_toml(tmp_path, "monthly_usd = -5"))
    with pytest.raises(ConfigError, match="permitted range"):
        load(_toml(tmp_path, "monthly_usd = 50000"))
    with pytest.raises(ConfigError, match="must be a number"):
        load(_toml(tmp_path, 'monthly_usd = "fifty"'))


def test_the_evidence_share_cannot_exceed_or_exist_without_the_whole(tmp_path):
    with pytest.raises(ConfigError, match="cannot exceed the whole"):
        load(_toml(tmp_path, "monthly_usd = 50\nevidence_monthly_usd = 60"))
    with pytest.raises(ConfigError, match="set the monthly cap first"):
        load(_toml(tmp_path, "evidence_monthly_usd = 10"))
    cfg = load(_toml(tmp_path, "monthly_usd = 20\nevidence_monthly_usd = 20"))
    assert cfg.evidence_budget_usd == cfg.monthly_budget_usd == Decimal("20")


# --- doctor --------------------------------------------------------------------------


def _doctor_cfg(tmp_path: Path, corpus: Path, cap: str | None = "50"):
    return replace(
        load(ROOT / "config.toml"),
        provenance_db=str(tmp_path / "no-ledger.db"),
        corpus_db=str(corpus),
        monthly_budget_usd=None if cap is None else Decimal(cap),
    )


def test_doctor_reports_the_cap_and_the_month_so_far(tmp_path):
    from core.doctor import OK, spend_cap

    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "10.00")])
    result = spend_cap(_doctor_cfg(tmp_path, corpus))
    assert result.status == OK
    assert "USD 10.00 of the USD 50.00 monthly cap" in result.message and "20%" in result.message
    assert not (tmp_path / "no-ledger.db").exists(), "a preflight must not create the ledger"


def test_doctor_warns_from_85_percent_of_the_cap(tmp_path):
    from core.doctor import WARN, spend_cap

    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "42.50")])
    result = spend_cap(_doctor_cfg(tmp_path, corpus))
    assert result.status == WARN and "85%" in result.message


def test_doctor_warns_when_no_monthly_cap_is_set(tmp_path):
    from core.doctor import WARN, spend_cap

    corpus = _corpus(tmp_path / "corpus.db", [])
    result = spend_cap(_doctor_cfg(tmp_path, corpus, cap=None))
    assert result.status == WARN and "no monthly cap" in result.message


def test_doctor_reads_the_local_ledger_too(tmp_path):
    from core.doctor import WARN, spend_cap

    corpus = _corpus(tmp_path / "corpus.db", [(_this_month(), "40.00")])
    cfg = replace(_doctor_cfg(tmp_path, corpus), provenance_db=str(tmp_path / "ledger.db"))
    with ProvenanceLedger(cfg.provenance_db) as led:
        led.record_call(
            "a10",
            TaskClass.THESIS_SYNTHESIS,
            Tier.REASON,
            "claude-opus-5-5",
            "p",
            Usage(input_tokens=1_000_000, output_tokens=0),  # USD 4.00
            at=_this_month(),
        )
    result = spend_cap(cfg)
    assert "USD 44.00" in result.message and result.status == WARN


# --- the refusal reaches the person who asked ------------------------------------------
#
# A key is set so the real backend is chosen; the cap is a cent, so the very first
# call's reservation crosses it and is refused BEFORE anything is sent - no network.


def _tiny_cap(tmp_path: Path):
    return replace(
        load(ROOT / "config.toml"),
        provenance_db=str(tmp_path / "ledger.db"),
        monthly_budget_usd=Decimal("0.01"),
        evidence_budget_usd=Decimal("0.01"),
    )


def test_ask_thesis_narrate_says_the_cap_refused_it(tmp_path, monkeypatch, capsys):
    import ask

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr(ask, "load_config", lambda *a, **k: _tiny_cap(tmp_path))
    code = ask.main(
        [
            "thesis",
            "MYX:1155",
            "--breaker",
            "NIM below 2.0%|nim < 0.020|kb_filings",
            "--breaker",
            "CASA below 25%|casa < 0.25|kb_filings",
            "--stance",
            "accumulate",
            "--narrate",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "narrative refused by the spend cap" in out
    assert "monthly cap USD 0.01" in out and "not downgraded" in out
    assert "the analysis above stands" in out


def test_the_web_narrative_reports_the_cap_refusal_as_content(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    monkeypatch.setattr("agents.learning.store.DEFAULT_PATH", tmp_path / "learning.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr("core.config.load", lambda *a, **k: _tiny_cap(tmp_path))
    body = (
        TestClient(create_app())
        .post(
            "/api/thesis",
            json={
                "instrument": "MYX:1155",
                "breakers": [
                    {"statement": "NIM under 2.0", "query": "nim<2.0", "store": "kb_filings"},
                    {"statement": "CASA under 22", "query": "casa<22", "store": "kb_filings"},
                ],
                "stance": "accumulate",
                "narrate": True,
            },
            headers={"X-Requested-With": "FinPlanet"},
        )
        .json()
    )
    narrative = body["data"]["narrative"]
    assert narrative["refused"] is True and narrative["backend"] == "AnthropicBackend"
    assert "monthly cap USD 0.01" in narrative["reason"]
