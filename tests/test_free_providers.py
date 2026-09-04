"""The free-provider catalogue, backend selection, the per-tier split, and what
each surface discloses about them (docs/21).

Everything here is offline and keyless: a key set by a test is a fake, and the
only backend that ever completes is fed by a scripted opener.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from core.guardrails.defaults import default_engine
from core.llm import providers
from core.llm.backends import (
    AnthropicBackend,
    AuthError,
    BackendError,
    OpenAICompatibleBackend,
    SplitBackend,
    backend_from_env,
    backend_name,
    effort_reaches,
    model_of,
    models_by_tier,
    pricing_of,
)
from core.llm.client import EchoBackend, InferenceClient
from core.llm.tiers import MESSAGES_TIERS, MODEL_IDS, TaskClass, Tier
from core.provenance.ledger import ProvenanceLedger
from tests.conftest import scripted_opener

KEY = "sk-ant-test-not-a-real-key"


def reply(text="ok", usage=None):
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 3},
        }
    )


# --- the catalogue --------------------------------------------------------------------


def test_every_provider_is_reachable_by_name_and_names_are_unique():
    names = providers.names()
    assert len(names) == len(set(names))
    for p in providers.PROVIDERS:
        assert providers.lookup(p.name) is p
        for alias in p.aliases:
            assert providers.lookup(alias) is p, alias


def test_aliases_never_collide_with_each_other_or_a_name():
    seen: dict[str, str] = {}
    for p in providers.PROVIDERS:
        for key in (p.name, *p.aliases):
            assert key not in seen, f"{key!r} claimed by {seen.get(key)} and {p.name}"
            seen[key] = p.name


def test_lookup_is_case_insensitive_and_unknown_is_none():
    assert providers.lookup("GROQ") is providers.lookup("groq")
    assert providers.lookup("Google") is providers.lookup("gemini")
    assert providers.lookup("grok-9000") is None
    assert not providers.is_provider_name("")


def test_every_hosted_provider_has_a_key_variable_an_https_endpoint_and_three_models():
    for p in providers.PROVIDERS:
        if p.name in ("ollama", "openai-compatible"):
            continue
        assert p.key_env and p.key_env.endswith("_API_KEY"), p.name
        assert p.base_url.startswith("https://") and not p.base_url.endswith("/"), p.name
        assert set(p.models) == set(MESSAGES_TIERS), p.name
        assert p.note, p.name


def test_auto_selectable_providers_are_all_keyed_and_hosted():
    for name in providers.AUTO_SELECTABLE:
        p = providers.lookup(name)
        assert p is not None and p.key_env is not None and p.base_url, name
    assert "ollama" not in providers.AUTO_SELECTABLE
    assert "openai-compatible" not in providers.AUTO_SELECTABLE


def test_env_vars_names_every_key_and_override_so_the_fixture_can_clear_them():
    vars_ = providers.env_vars()
    for p in providers.PROVIDERS:
        if p.key_env:
            assert p.key_env in vars_
    for v in (
        "LLM_MODEL",
        "LLM_MODEL_REASON",
        "LLM_MODEL_BALANCED",
        "LLM_MODEL_CHEAP",
        "LLM_BASE_URL",
    ):
        assert v in vars_


def test_configured_means_keyless_or_key_present(monkeypatch):
    groq, ollama = providers.lookup("groq"), providers.lookup("ollama")
    assert groq is not None and ollama is not None
    assert ollama.configured() and ollama.key() is None
    assert not groq.configured()
    monkeypatch.setenv("GROQ_API_KEY", "  ")
    assert not groq.configured(), "whitespace is not a key"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    assert groq.configured() and groq.key() == "gsk_x"


# --- from_env: the preset plus the operator's overrides ---------------------------------


def test_from_env_returns_the_preset_when_nothing_is_overridden():
    p = providers.from_env("groq")
    assert p.base_url == "https://api.groq.com/openai/v1"
    assert p.models[Tier.CHEAP] == "llama-3.1-8b-instant"


def test_a_per_tier_model_override_outranks_the_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CHEAP", "qwen/qwen3-32b")
    p = providers.from_env("groq")
    assert p.models[Tier.CHEAP] == "qwen/qwen3-32b"
    assert p.models[Tier.BALANCED] == "llama-3.3-70b-versatile"


def test_llm_model_pins_every_tier_and_a_per_tier_override_still_wins(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "one-model")
    monkeypatch.setenv("LLM_MODEL_REASON", "big-model")
    p = providers.from_env("groq")
    assert p.models == {
        Tier.REASON: "big-model",
        Tier.BALANCED: "one-model",
        Tier.CHEAP: "one-model",
    }


def test_base_url_override_applies_to_any_preset_and_loses_its_trailing_slash(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.5:11434/v1/")
    assert providers.from_env("ollama").base_url == "http://10.0.0.5:11434/v1"


def test_the_generic_preset_requires_a_base_url_and_names_the_variable():
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        providers.from_env("openai-compatible")


def test_the_generic_preset_requires_a_model_per_tier(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8000/v1")
    with pytest.raises(ValueError, match="LLM_MODEL_REASON"):
        providers.from_env("custom")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    p = providers.from_env("custom")
    assert p.models[Tier.REASON] == "local-model" and p.name == "openai-compatible"


def test_from_env_refuses_an_unknown_name():
    with pytest.raises(ValueError, match="unknown provider"):
        providers.from_env("grok-9000")


def test_first_configured_takes_the_first_keyed_provider_in_catalogue_order(monkeypatch):
    assert providers.first_configured() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-x")
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    chosen = providers.first_configured()
    assert chosen is not None and chosen.name == "gemini"
    assert providers.configured_names() == ["gemini", "openrouter"]


# --- backend_from_env -----------------------------------------------------------------------


def test_naming_a_provider_without_its_key_raises_rather_than_falling_back():
    with pytest.raises(AuthError, match="GROQ_API_KEY"):
        backend_from_env("groq")


def test_naming_a_provider_with_its_key_builds_the_free_backend_and_says_so(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    backend, reason = backend_from_env("groq")
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == "groq"
    assert reason.startswith("groq (explicitly selected, free tier):")
    assert "llama-3.1-8b-instant" in reason and "priced at zero" in reason
    # ask.py labels a narrative with reason.split(":")[0]; the URL's own colon
    # must come after the first one, or the label ends mid-word.
    assert reason.split(":")[0] == "groq (explicitly selected, free tier)"


def test_llm_backend_env_selects_a_provider_by_alias(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    monkeypatch.setenv("LLM_BACKEND", "google")
    backend, reason = backend_from_env()
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == "gemini"
    assert "gemini-flash-lite-latest" in reason


def test_free_takes_the_first_keyed_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-x")
    backend, reason = backend_from_env("free")
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == "openrouter"
    assert "chosen by LLM_BACKEND=free" in reason


def test_free_with_no_key_anywhere_raises_and_lists_the_keys():
    with pytest.raises(AuthError, match="GROQ_API_KEY") as exc:
        backend_from_env("free")
    assert "GEMINI_API_KEY" in str(exc.value) and "ollama" in str(exc.value)


def test_ollama_is_keyless_and_only_ever_selected_by_name():
    backend, reason = backend_from_env("ollama")
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == "ollama"
    assert "localhost:11434" in reason
    # nothing set: ollama is NOT auto-picked, the stub is
    assert type(backend_from_env()[0]).__name__ == "EchoBackend"


def test_a_free_key_alone_selects_that_provider_and_the_reason_says_anthropic_is_unset(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "m-x")
    backend, reason = backend_from_env()
    assert isinstance(backend, OpenAICompatibleBackend) and backend.name == "mistral"
    assert "MISTRAL_API_KEY is set and ANTHROPIC_API_KEY is not" in reason


def test_an_anthropic_key_still_wins_and_the_other_key_is_disclosed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    backend, reason = backend_from_env()
    assert isinstance(backend, AnthropicBackend)
    assert "a free-provider key is also set (groq)" in reason
    assert "LLM_BACKEND=groq selects it instead" in reason


def test_no_key_at_all_is_the_stub_and_the_reason_names_both_absences():
    backend, reason = backend_from_env()
    assert isinstance(backend, EchoBackend)
    assert "no ANTHROPIC_API_KEY and no free-provider key" in reason and "NOT a model" in reason


def test_an_unknown_name_lists_what_would_have_worked():
    with pytest.raises(ValueError, match="unknown LLM_BACKEND") as exc:
        backend_from_env("grok-9000")
    assert "groq" in str(exc.value) and "'free'" in str(exc.value)


def test_a_model_pin_is_still_disclosed_on_a_free_backend(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    _, reason = backend_from_env("groq")
    assert "cheapest model" in reason


# --- the per-tier split ----------------------------------------------------------------------


def test_a_cheap_override_puts_the_free_model_behind_triage_and_leaves_the_thesis_alone(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    backend, reason = backend_from_env()
    assert isinstance(backend, SplitBackend)
    models = models_by_tier(backend)
    assert models[Tier.REASON] == "claude-opus-5"
    assert models[Tier.BALANCED] == "claude-sonnet-5"
    assert models[Tier.CHEAP] == "llama-3.1-8b-instant"
    assert models[Tier.EMBED] == MODEL_IDS[Tier.EMBED]
    assert (
        "split by tier" in reason and "cheap=OpenAICompatibleBackend llama-3.1-8b-instant" in reason
    )
    assert backend_name(backend, Tier.CHEAP) == "OpenAICompatibleBackend"
    assert backend_name(backend, Tier.REASON) == "AnthropicBackend"
    assert effort_reaches(backend, Tier.REASON) and not effort_reaches(backend, Tier.CHEAP)
    assert pricing_of(backend, Tier.CHEAP) == (Decimal(0), Decimal(0))
    assert pricing_of(backend, Tier.REASON) is None


def test_the_split_works_over_the_stub_too(monkeypatch):
    """No Anthropic key, one free key, and a wish to keep the free model off the
    thesis: reason and balanced stay on the stub, cheap goes free."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND", "echo")
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    backend, _ = backend_from_env()
    assert isinstance(backend, SplitBackend)
    assert isinstance(backend.by_tier[Tier.REASON], EchoBackend)
    assert isinstance(backend.by_tier[Tier.CHEAP], OpenAICompatibleBackend)


def test_two_tiers_naming_the_same_provider_share_one_backend(monkeypatch):
    """One instance per provider: the pacing clock must see every call to it."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND", "echo")
    monkeypatch.setenv("LLM_BACKEND_REASON", "groq")
    monkeypatch.setenv("LLM_BACKEND_BALANCED", "groq")
    backend, _ = backend_from_env()
    assert isinstance(backend, SplitBackend)
    assert backend.by_tier[Tier.REASON] is backend.by_tier[Tier.BALANCED]
    assert isinstance(backend.by_tier[Tier.CHEAP], EchoBackend)


def test_an_override_naming_the_auto_selected_provider_reuses_it(monkeypatch):
    """GROQ_API_KEY alone makes groq the base; LLM_BACKEND_CHEAP=groq must not
    build a second groq with its own pacing clock."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    backend, _ = backend_from_env()
    assert isinstance(backend, SplitBackend)
    assert backend.by_tier[Tier.CHEAP] is backend.by_tier[Tier.REASON]
    assert backend.by_tier[Tier.CHEAP] is backend.by_tier[Tier.BALANCED]


def test_an_explicit_choice_ignores_the_split(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    backend, _ = backend_from_env("echo")
    assert isinstance(backend, EchoBackend)


def test_a_split_override_without_its_key_fails_loudly(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    with pytest.raises(AuthError, match="GROQ_API_KEY"):
        backend_from_env()


def test_split_needs_every_messages_tier():
    with pytest.raises(ValueError, match="missing"):
        SplitBackend({Tier.CHEAP: EchoBackend()})


def test_split_refuses_two_backends_serving_one_model_id():
    groq = providers.lookup("groq")
    assert groq is not None
    same = providers.Provider(
        **{
            **groq.__dict__,
            "models": {Tier.REASON: "same", Tier.BALANCED: "same", Tier.CHEAP: "other"},
        }
    )
    a = OpenAICompatibleBackend(same, opener=scripted_opener([]))
    b = OpenAICompatibleBackend(same, opener=scripted_opener([]))
    with pytest.raises(ValueError, match="two different backends"):
        SplitBackend({Tier.REASON: a, Tier.BALANCED: b, Tier.CHEAP: a})
    # the same instance behind two tiers is fine: one owner per model id
    SplitBackend({Tier.REASON: a, Tier.BALANCED: a, Tier.CHEAP: a})


def test_split_dispatches_by_model_id_and_refuses_a_stranger():
    groq = providers.from_env("groq")
    free = OpenAICompatibleBackend(groq, opener=scripted_opener([reply("from groq")]))
    echo = EchoBackend()
    split = SplitBackend({Tier.REASON: echo, Tier.BALANCED: echo, Tier.CHEAP: free})
    text, _ = split.complete("llama-3.1-8b-instant", "q", None)
    assert text == "from groq" and split.last_request_id == "chatcmpl-1"
    text, _ = split.complete("claude-opus-5", "q", None)
    assert text.startswith("[claude-opus-5]")
    with pytest.raises(BackendError, match="no backend in the split"):
        split.complete("gpt-9", "q", None)
    assert split.model_for(Tier.EMBED) is None and split.name_for(Tier.EMBED) == "SplitBackend"


# --- the client: what lands in the ledger ------------------------------------------------------


def _client(backend, budget=None):
    led = ProvenanceLedger()
    return InferenceClient(backend, default_engine({"a4": {"llm_complete"}}), led, budget), led


def test_a_free_call_is_ledgered_under_its_own_model_at_zero_cost():
    free = OpenAICompatibleBackend(providers.from_env("groq"), opener=scripted_opener([reply()]))
    client, led = _client(free)
    done = client.complete("a4", TaskClass.NEWS_TRIAGE, "tag this")
    assert done.model_id == "llama-3.1-8b-instant" and done.tier is Tier.CHEAP
    assert done.cost_myr == Decimal(0)
    row = next(led.calls())
    assert row["model_id"] == "llama-3.1-8b-instant" and row["tier"] == "cheap"
    assert Decimal(row["cost_myr"]) == Decimal(0) and row["input_tokens"] == 10
    assert done.request_id == "chatcmpl-1"


def test_a_zero_cost_call_never_trips_the_budget_rail():
    free = OpenAICompatibleBackend(
        providers.from_env("groq"), opener=scripted_opener([reply(), reply(), reply()])
    )
    client, _ = _client(free, budget=Decimal("0.000001"))
    for _ in range(3):
        client.complete("a4", TaskClass.NEWS_TRIAGE, "x")


def test_under_the_split_each_tier_lands_on_its_own_backend_and_price():
    free = OpenAICompatibleBackend(providers.from_env("groq"), opener=scripted_opener([reply()]))
    echo = EchoBackend()
    client, led = _client(SplitBackend({Tier.REASON: echo, Tier.BALANCED: echo, Tier.CHEAP: free}))
    triage = client.complete("a4", TaskClass.NEWS_TRIAGE, "tag this")
    thesis = client.complete("a4", TaskClass.THESIS_SYNTHESIS, "x" * 400)
    assert triage.model_id == "llama-3.1-8b-instant" and triage.cost_myr == 0
    assert thesis.model_id == "claude-opus-5" and thesis.cost_myr > 0
    rows = list(led.calls())
    assert [r["model_id"] for r in rows] == ["llama-3.1-8b-instant", "claude-opus-5"]


def test_structured_output_survives_a_thinking_model():
    from pydantic import BaseModel

    class Tag(BaseModel):
        category: str
        relevance: float

    free = OpenAICompatibleBackend(
        providers.from_env("groq"),
        opener=scripted_opener(
            [reply('<think>hmm</think>```json\n{"category": "earnings", "relevance": 0.8}\n```')]
        ),
    )
    client, _ = _client(free)
    parsed, done = client.complete_structured("a4", TaskClass.CATEGORY_CLASSIFY, "classify", Tag)
    assert parsed is not None and parsed.category == "earnings" and parsed.relevance == 0.8
    assert done.model_id == "llama-3.1-8b-instant"


def test_model_of_and_pricing_of_fall_back_to_the_claude_table():
    assert model_of(EchoBackend(), Tier.REASON) == "claude-opus-5"
    assert pricing_of(EchoBackend(), Tier.REASON) is None
    assert models_by_tier(EchoBackend()) == MODEL_IDS
    assert backend_name(EchoBackend()) == "EchoBackend"
    assert effort_reaches(EchoBackend(), Tier.CHEAP)


# --- the disclosure surfaces ---------------------------------------------------------------------


def _run(args, capsys):
    import ask

    code = ask.main(args)
    return code, capsys.readouterr().out


def test_ask_backend_shows_the_free_models_and_that_effort_does_not_reach_them(capsys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    code, out = _run(["backend"], capsys)
    assert code == 0
    assert "OpenAICompatibleBackend" in out and "claude-haiku-4-5" not in out
    assert "llama-3.1-8b-instant" in out and "effort dial not sent" in out


def test_ask_backend_shows_which_backend_answers_each_tier_under_a_split(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    monkeypatch.setenv("LLM_BACKEND_CHEAP", "groq")
    code, out = _run(["backend"], capsys)
    assert code == 0 and "SplitBackend" in out
    assert "claude-opus-5" in out and "llama-3.1-8b-instant" in out
    assert "OpenAICompatibleBackend, max 1024" in out


def test_ask_backend_use_a_provider_without_its_key_exits_three(capsys, monkeypatch):
    code, _ = _run(["backend", "--use", "groq"], capsys)
    assert code == 3


def test_ask_backend_list_prints_the_catalogue_and_key_state(capsys, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    code, out = _run(["backend", "--list"], capsys)
    assert code == 0
    for p in providers.PROVIDERS:
        assert p.name in out
    assert "GEMINI_API_KEY set" in out and "GROQ_API_KEY unset" in out
    assert "keyless" in out and "LLM_MODEL_REASON" in out


def test_web_backend_reports_the_free_models_per_tier(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    monkeypatch.setattr("agents.learning.store.DEFAULT_PATH", tmp_path / "learning.db")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    body = TestClient(create_app()).get("/api/backend").json()
    assert body["data"]["is_stub"] is False
    assert body["data"]["backend"] == "OpenAICompatibleBackend"
    cheap = body["data"]["tiers"]["cheap"]
    assert (
        cheap["model"] == "llama-3.1-8b-instant" and cheap["backend"] == "OpenAICompatibleBackend"
    )
    assert cheap["effort"] is None and cheap["max_tokens"] == 1024
    assert body["data"]["tiers"]["embed"]["model"] == MODEL_IDS[Tier.EMBED]


def test_doctor_does_not_warn_about_a_spend_cap_on_a_free_provider(monkeypatch):
    from core.doctor import run_checks

    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    by_name = {c.name: c for c in run_checks(offline=True)}
    assert by_name["backend"].status == "ok" and "groq" in by_name["backend"].message
    assert by_name["spend-cap"].status == "ok"
    assert "priced at zero" in by_name["spend-cap"].message


def test_doctor_still_warns_about_the_spend_cap_on_anthropic(monkeypatch):
    from core.doctor import run_checks

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    by_name = {c.name: c for c in run_checks(offline=True)}
    assert by_name["spend-cap"].status == "warn"
    assert "each tier's own rate" in by_name["spend-cap"].message
