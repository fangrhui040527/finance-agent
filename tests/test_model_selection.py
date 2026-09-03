"""Model selection and reasoning effort: the two dials, and what they may not do.

`FINPLANET_MODEL` picks which of the three Claude models answers; `FINPLANET_EFFORT`
picks how hard it thinks. They are independent on purpose - raising effort on
Haiku is far cheaper than routing the same question to Opus, and no cost table
can tell you which is the better trade for a given question.

The negative cases are the point of this file:

  * Haiku 4.5 must NEVER receive `output_config.effort` or adaptive thinking -
    both are a 400, and a 400 mid-plan is a plan the supervisor already budgeted
    for. It gets the older budgeted form instead, so the same five levels reach
    it in the one shape it accepts.
  * A thinking budget must sit strictly INSIDE the output cap. A budget that
    equals or exceeds max_tokens buys thinking that is then truncated: the spend
    is real and the answer is an exception.
  * An unreadable selection fails at startup. A typo that silently selected Opus
    would be found on the invoice, not on the screen.
  * The defaults do not move. Nothing set means exactly what it meant before.
"""

from __future__ import annotations

import pytest

from core.llm.tiers import (
    CHEAP_EFFORT_BUDGET,
    EFFORT_ORDER,
    MESSAGES_TIERS,
    MODEL_IDS,
    REQUEST_PROFILES,
    Effort,
    ModelSelectionError,
    RequestProfile,
    Tier,
    Usage,
    cheap_capped,
    cost_usd,
    effective_tier,
    pinned_tier,
    profile_for,
    selected_effort,
    selection_note,
)

#: `ask.main` writes these itself, so monkeypatch's own bookkeeping is not
#: enough - it never saw the assignment and would leave it set for the next
#: test. A selection leaking out of this file would silently re-shape every
#: request the rest of the suite makes, which is the same failure mode the
#: feature exists to make visible.
_SELECTION = ("FINPLANET_MODEL", "FINPLANET_EFFORT", "FINPLANET_CHEAP")


@pytest.fixture(autouse=True)
def _no_selection():
    """Every test states its own selection; an inherited one is a false pass."""
    import os

    before = {name: os.environ.get(name) for name in _SELECTION}
    for name in _SELECTION:
        os.environ.pop(name, None)
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# --- nothing selected ------------------------------------------------------------


def test_with_nothing_set_the_profiles_are_the_default_table():
    """The feature is opt-in. An installation that sets neither variable makes
    exactly the calls it made before either existed."""
    assert pinned_tier() is None
    assert selected_effort() is None
    assert selection_note() == ""
    for tier in Tier:
        assert profile_for(tier) == REQUEST_PROFILES[tier]
        assert effective_tier(tier) is tier


# --- effort on the models that take it -------------------------------------------


@pytest.mark.parametrize("effort", [e.value for e in EFFORT_ORDER])
@pytest.mark.parametrize("tier", [Tier.REASON, Tier.BALANCED])
def test_opus_and_sonnet_take_all_five_effort_levels(monkeypatch, effort, tier):
    monkeypatch.setenv("FINPLANET_EFFORT", effort)
    shape = profile_for(tier)
    assert shape.effort == effort
    assert shape.adaptive_thinking is True
    assert shape.thinking_budget is None, "the two thinking forms are exclusive"


def test_a_bigger_effort_gets_a_bigger_cap_to_land_in(monkeypatch):
    """Raising effort without raising max_tokens buys thinking that is truncated.
    Truncated raises AND carries the usage, so the spend is real and the answer
    is not - the most expensive shape a request can have."""
    caps = []
    for effort in EFFORT_ORDER:
        monkeypatch.setenv("FINPLANET_EFFORT", effort.value)
        caps.append(profile_for(Tier.REASON).max_tokens)
    assert caps == sorted(caps), f"cap must not fall as effort rises: {caps}"
    monkeypatch.setenv("FINPLANET_EFFORT", "max")
    assert profile_for(Tier.REASON).stream, "a 64k cap on a non-streaming call can time out"


# --- Haiku, the model with no effort parameter -----------------------------------


@pytest.mark.parametrize("effort", [e.value for e in EFFORT_ORDER])
def test_haiku_never_receives_effort_or_adaptive_thinking(monkeypatch, effort):
    """Haiku 4.5 returns a 400 for both. Whatever is selected, the cheap tier's
    request must not carry either one."""
    monkeypatch.setenv("FINPLANET_EFFORT", effort)
    shape = profile_for(Tier.CHEAP)
    assert shape.effort is None
    assert shape.adaptive_thinking is False


@pytest.mark.parametrize("effort", list(EFFORT_ORDER))
def test_haiku_gets_the_same_five_levels_as_a_thinking_budget(monkeypatch, effort):
    """The dial reaches all three models. Dropping it on the floor for Haiku
    would make the setting decoration on the tier most likely to be running."""
    monkeypatch.setenv("FINPLANET_EFFORT", effort.value)
    shape = profile_for(Tier.CHEAP)
    assert shape.thinking_budget == CHEAP_EFFORT_BUDGET[effort][0]
    assert 1024 <= shape.thinking_budget < shape.max_tokens


def test_the_cheap_budget_table_is_ordered_and_inside_its_caps():
    budgets = [CHEAP_EFFORT_BUDGET[e][0] for e in EFFORT_ORDER]
    assert budgets == sorted(budgets)
    for effort, (budget, cap) in CHEAP_EFFORT_BUDGET.items():
        assert budget < cap, f"{effort.value}: budget {budget} must fit inside cap {cap}"


# --- the profile refuses to express an impossible request ------------------------


def test_a_profile_cannot_carry_both_thinking_forms():
    with pytest.raises(ValueError, match="cannot be combined"):
        RequestProfile(max_tokens=8000, adaptive_thinking=True, thinking_budget=2048)
    with pytest.raises(ValueError, match="cannot be combined"):
        RequestProfile(max_tokens=8000, effort="high", thinking_budget=2048)


def test_a_thinking_budget_must_fit_inside_the_output_cap():
    with pytest.raises(ValueError, match="under max_tokens"):
        RequestProfile(max_tokens=2048, thinking_budget=2048)
    with pytest.raises(ValueError, match="at least 1024"):
        RequestProfile(max_tokens=8000, thinking_budget=512)


def test_a_profile_cannot_carry_an_effort_the_api_does_not_have():
    with pytest.raises(ValueError, match="unknown effort"):
        RequestProfile(max_tokens=8000, adaptive_thinking=True, effort="ultra")


# --- model selection -------------------------------------------------------------


@pytest.mark.parametrize(
    "written,expected",
    [
        ("haiku", Tier.CHEAP),
        ("claude-haiku-4-5", Tier.CHEAP),
        ("sonnet", Tier.BALANCED),
        ("claude-sonnet-5", Tier.BALANCED),
        ("opus", Tier.REASON),
        ("claude-opus-5", Tier.REASON),
        ("OPUS", Tier.REASON),
    ],
)
def test_every_legal_spelling_reaches_one_model(monkeypatch, written, expected):
    """The `MYX`/`XKLS` lesson: accepting one spelling and silently ignoring
    another is how a setting stops being a setting."""
    monkeypatch.setenv("FINPLANET_MODEL", written)
    assert pinned_tier() is expected
    for tier in MESSAGES_TIERS:
        assert effective_tier(tier) is expected


def test_a_pin_never_touches_the_non_messages_tiers(monkeypatch):
    monkeypatch.setenv("FINPLANET_MODEL", "opus")
    assert effective_tier(Tier.EMBED) is Tier.EMBED
    assert effective_tier(Tier.LOCAL) is Tier.LOCAL


def test_price_moves_with_the_model_not_with_the_task_class(monkeypatch):
    """The ledger must record what was truly spent. A pin that moved the model
    and left the price would over-bill a Haiku run at Opus rates and trip the
    budget rail early - a wrong refusal, and an unexplainable invoice."""
    million = Usage(input_tokens=1_000_000, output_tokens=0)
    monkeypatch.setenv("FINPLANET_MODEL", "haiku")
    on_haiku = cost_usd(effective_tier(Tier.REASON), million)
    monkeypatch.setenv("FINPLANET_MODEL", "opus")
    on_opus = cost_usd(effective_tier(Tier.REASON), million)
    assert on_opus == on_haiku * 5


def test_the_legacy_cheap_flag_still_means_haiku(monkeypatch):
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    assert pinned_tier() is Tier.CHEAP
    assert cheap_capped()
    assert MODEL_IDS[effective_tier(Tier.REASON)] == "claude-haiku-4-5"


def test_a_named_model_outranks_the_legacy_flag(monkeypatch):
    """Both set is not a contradiction to resolve by coin flip: the explicit
    name is the more specific instruction, and the disclosure says which won."""
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    monkeypatch.setenv("FINPLANET_MODEL", "sonnet")
    assert pinned_tier() is Tier.BALANCED
    assert not cheap_capped()
    assert "claude-sonnet-5" in selection_note()


# --- junk fails loudly -----------------------------------------------------------


def test_an_unknown_model_is_refused_not_guessed(monkeypatch):
    monkeypatch.setenv("FINPLANET_MODEL", "gpt-4")
    with pytest.raises(ModelSelectionError, match="unknown FINPLANET_MODEL"):
        pinned_tier()


def test_an_unknown_effort_is_refused_not_guessed(monkeypatch):
    monkeypatch.setenv("FINPLANET_EFFORT", "very high")
    with pytest.raises(ModelSelectionError, match="unknown FINPLANET_EFFORT"):
        selected_effort()


# --- disclosure ------------------------------------------------------------------


def test_a_selection_is_never_silent(monkeypatch):
    """A run that quietly thought at `low` because a shell still had the
    variable exported is indistinguishable from one that thought hard."""
    monkeypatch.setenv("FINPLANET_MODEL", "haiku")
    monkeypatch.setenv("FINPLANET_EFFORT", "low")
    note = selection_note()
    assert "claude-haiku-4-5" in note
    assert "low" in note
    assert "thinking budget" in note


def test_the_backend_command_names_the_model_and_the_reasoning(monkeypatch, capsys):
    import ask

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    assert ask.main(["--model", "sonnet", "--effort", "xhigh", "backend"]) == 0
    out = capsys.readouterr().out
    assert "claude-sonnet-5" in out
    assert "effort xhigh" in out
    assert "pinned from claude-opus-5" in out


def test_the_backend_command_refuses_a_junk_selection(monkeypatch, capsys):
    import ask

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    assert ask.main(["--effort", "ultra", "backend"]) == 2
    assert "unknown FINPLANET_EFFORT" in capsys.readouterr().err


# --- the seam actually sends it --------------------------------------------------


def test_the_client_sends_the_resolved_shape_not_the_default_table(monkeypatch):
    """profile_for, not REQUEST_PROFILES. Reading the raw table at the seam is
    exactly how an effort setting becomes decoration."""
    from core.guardrails.defaults import default_engine
    from core.llm.client import InferenceClient
    from core.llm.tiers import TaskClass
    from core.provenance.ledger import ProvenanceLedger

    sent: dict = {}

    class Recorder:
        def complete(self, model_id, prompt, system, profile=None):
            sent["model"] = model_id
            sent["profile"] = profile
            return "ok", Usage(input_tokens=10, output_tokens=5)

    monkeypatch.setenv("FINPLANET_MODEL", "haiku")
    monkeypatch.setenv("FINPLANET_EFFORT", "high")
    client = InferenceClient(
        backend=Recorder(),
        engine=default_engine({"a4": {"llm_complete"}}),
        ledger=ProvenanceLedger(),
        daily_budget_myr=None,
    )
    client.complete("a4", TaskClass.THESIS_SYNTHESIS, "why did it move")
    assert sent["model"] == "claude-haiku-4-5"
    assert sent["profile"] == profile_for(Tier.CHEAP)
    assert sent["profile"].thinking_budget == CHEAP_EFFORT_BUDGET[Effort.HIGH][0]
    assert sent["profile"].effort is None


# --- the disclosure names the variable that actually caused the pin ----------------


def test_the_note_names_the_variable_that_actually_pinned_the_tier(monkeypatch):
    """`pinned_tier` collapses two spellings into one answer because the router
    does not care which was written. The disclosure does.

    Until this was fixed the note said FINPLANET_MODEL whatever the cause, so an
    operator capped by FINPLANET_CHEAP was told to unset a variable that was not
    set, would see the run still on Haiku, and had been sent to the one place the
    fault is not. `cheap_capped`'s own docstring names this failure - "telling
    the truth about the variable and lying about the run" - and the note had it.
    """
    from core.llm.tiers import pin_source, selection_note

    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    assert pin_source() == "FINPLANET_CHEAP"
    assert "(FINPLANET_CHEAP)" in selection_note()
    assert "FINPLANET_MODEL" not in selection_note()

    monkeypatch.delenv("FINPLANET_CHEAP")
    monkeypatch.setenv("FINPLANET_MODEL", "haiku")
    assert pin_source() == "FINPLANET_MODEL"
    assert "(FINPLANET_MODEL)" in selection_note()
    assert "FINPLANET_CHEAP" not in selection_note()


def test_the_more_specific_spelling_is_the_one_named(monkeypatch):
    """Both set resolves to the same tier, and FINPLANET_MODEL is the more
    specific instruction, so it is the one that wins AND the one named. A note
    that credited the other would send an operator to unset the variable that
    is not deciding."""
    from core.llm.tiers import pin_source, selection_note

    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    monkeypatch.setenv("FINPLANET_MODEL", "haiku")
    assert pin_source() == "FINPLANET_MODEL"
    assert "(FINPLANET_MODEL)" in selection_note()


def test_no_pin_names_nothing(monkeypatch):
    from core.llm.tiers import pin_source, selection_note

    for k in ("FINPLANET_CHEAP", "FINPLANET_MODEL", "FINPLANET_EFFORT"):
        monkeypatch.delenv(k, raising=False)
    assert pin_source() is None
    assert selection_note() == ""
