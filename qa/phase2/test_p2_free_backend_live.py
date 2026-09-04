"""Phase 2 - one real answer from every free provider that has a key.

Opt-in twice: `QA_LIVE=1` and a free-provider key (`GROQ_API_KEY`,
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `NVIDIA_API_KEY`), or a
local Ollama answering on its default port. Without both, every test here
skips; an Anthropic key is neither needed nor used. Two calls per provider, on
its CHEAP model, asking for one word: enough to prove the wire format against
the real host, that the ledger prices the call at zero under the model that
answered, and that an unknown model is refused with the variable that fixes it.
Free lineups rotate, so the second test is the one that will fail first when a
default model retires; its message is the whole point.

What answered, how fast and with how many tokens is written to
`qa/artifacts/free-live.json` (gitignored) so a run on a GitHub Actions runner
leaves a record. Meant to run from `.github/workflows/free-backend-probe.yml`
with the keys as repository secrets.
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

providers = pytest.importorskip(
    "core.llm.providers",
    reason="the free-provider backend (PR #32, docs/21) is not on this checkout",
)

from core.guardrails.defaults import default_engine  # noqa: E402
from core.llm.backends import BackendError, OpenAICompatibleBackend, backend_from_env  # noqa: E402
from core.llm.client import InferenceClient  # noqa: E402
from core.llm.tiers import TaskClass, Tier  # noqa: E402
from core.provenance.ledger import ProvenanceLedger  # noqa: E402

PROMPT = "Reply with the single word OK and nothing else."


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1.0):
            return True
    except OSError:
        return False


def _live_names() -> list[str]:
    names = list(providers.configured_names())
    if _ollama_up():
        names.append("ollama")
    return names


LIVE = _live_names()


@pytest.fixture(scope="module")
def free_record(artifacts):
    rows: list[dict] = []
    yield rows
    if rows:
        out = artifacts / "free-live.json"
        out.write_text(
            json.dumps({"at": datetime.now(UTC).isoformat(), "calls": rows}, indent=2),
            encoding="utf-8",
        )


@pytest.fixture(autouse=True)
def only_the_free_path(monkeypatch):
    """Nothing here may fall back to Anthropic or be pinned by a cheap cap."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FINPLANET_CHEAP", raising=False)
    for var in ("LLM_BACKEND", "LLM_BACKEND_REASON", "LLM_BACKEND_BALANCED", "LLM_BACKEND_CHEAP"):
        monkeypatch.delenv(var, raising=False)


def _client(registry, backend):
    ledger = ProvenanceLedger()
    return InferenceClient(backend, default_engine(registry.allowlist()), ledger), ledger


@pytest.mark.skipif(not LIVE, reason="no free-provider key set and no local Ollama")
@pytest.mark.parametrize("name", LIVE)
def test_one_word_from_the_cheap_model_is_ledgered_at_zero_under_its_own_id(
    name, registry, free_record
):
    backend, reason = backend_from_env(name)
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == name
    client, ledger = _client(registry, backend)
    cheap_model = providers.from_env(name).models[Tier.CHEAP]

    t0 = time.perf_counter()
    done = client.complete("a4_news_narrative", TaskClass.NEWS_TRIAGE, PROMPT)
    elapsed = (time.perf_counter() - t0) * 1000

    assert done.text.strip(), f"{name} answered with no text"
    assert done.model_id == cheap_model and done.tier is Tier.CHEAP
    assert done.cost_myr == Decimal(0)
    assert done.usage.output_tokens > 0 and done.usage.input_tokens > 0
    row = next(ledger.calls())
    assert row["model_id"] == cheap_model and Decimal(row["cost_myr"]) == 0
    free_record.append(
        {
            "provider": name,
            "model": done.model_id,
            "text": done.text[:40],
            "input_tokens": done.usage.input_tokens,
            "output_tokens": done.usage.output_tokens,
            "cached_input_tokens": done.usage.cached_input_tokens,
            "latency_ms": round(elapsed, 1),
            "request_id": done.request_id,
            "reason": reason.split(":")[0],
        }
    )


@pytest.mark.skipif(not LIVE, reason="no free-provider key set and no local Ollama")
@pytest.mark.parametrize("name", LIVE)
def test_an_unknown_model_is_refused_with_the_variable_that_fixes_it(name, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CHEAP", "qa-no-such-model-2026")
    backend, _ = backend_from_env(name)
    with pytest.raises(BackendError) as exc:
        backend.complete("qa-no-such-model-2026", PROMPT, None)
    message = str(exc.value)
    assert "qa-no-such-model-2026" in message or "LLM_MODEL" in message, message


@pytest.mark.skipif(not LIVE, reason="no free-provider key set and no local Ollama")
def test_ask_backend_as_a_process_names_the_provider_that_will_answer(run_cli):
    passthrough = {var: os.environ[var] for var in providers.env_vars() if var in os.environ}
    proc = run_cli(["ask.py", "backend"], env_extra=passthrough)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "OpenAICompatibleBackend" in out or "ollama" in out.lower()
    for value in passthrough.values():
        assert value not in out, "a key value reached the screen"
