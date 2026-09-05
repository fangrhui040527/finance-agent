"""The free-tier provider catalogue: where a zero-cost model can be reached.

docs/21. The reasoning in this system runs on the operator's own Claude
session (docs/15, docs/20), which costs nothing per token. What was still
going to an API key was the CHEAP tier - triage, tagging, dedup, classification
- and, when run from the CLI, the narrative tiers too. Every provider here
serves an OpenAI-compatible `chat/completions` endpoint on a free tier that
needs no card, so those calls can be moved off the Anthropic API entirely for
the first month of knowledge-building without a second transport.

The list is taken from 12britz/awesome-free-models, section "Free API
Providers", last checked there on 2026-09-03. Two rules about what made the cut:

  * **Permanent free tier only.** Trial credits that expire (Cerebras,
    SambaNova's $5, Together, Nebius) are not free, they are a countdown, and a
    backend that stops working on day 31 is the EchoBackend problem with a
    delay. Those are named in docs/21 and absent here.
  * **Model ids rot.** A free lineup changes month to month - OpenRouter's
    `:free` set in particular. The defaults below are a starting point, every
    one can be overridden per tier from the environment, and a 404 from the
    provider names the variable to set rather than guessing a replacement.

Nothing here opens a socket. The backend that does is
`core.llm.backends.OpenAICompatibleBackend`; this module only says where.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from core.llm.tiers import MESSAGES_TIERS, Tier

#: The three Messages tiers a provider must serve. EMBED and LOCAL are not chat
#: models and never route here.
_CHAT_TIERS: tuple[Tier, ...] = MESSAGES_TIERS


@dataclass(frozen=True)
class Provider:
    """One place a free chat model can be reached, and what it serves per tier."""

    name: str
    base_url: str
    #: The environment variable holding the key; None for a keyless endpoint.
    key_env: str | None
    models: dict[Tier, str] = field(default_factory=dict)
    #: Requests per minute the free tier allows, or None for no client-side
    #: pacing. The backend spaces calls to stay under it: news triage arrives in
    #: bursts of dozens, and a burst that trips the limit spends its retries on
    #: 429s instead of answers.
    rpm: int | None = None
    #: One line on the tier's limits, as catalogued. Dated in the module docstring.
    note: str = ""
    aliases: tuple[str, ...] = ()
    #: Headers a provider asks for beyond Authorization (OpenRouter attributes
    #: requests by these; nothing else needs any).
    extra_headers: dict[str, str] = field(default_factory=dict)

    def key(self) -> str | None:
        """The key from the environment, or None when this provider is keyless."""
        if self.key_env is None:
            return None
        return os.environ.get(self.key_env, "").strip() or None

    def configured(self) -> bool:
        """True when a call could be attempted: keyless, or the key is present."""
        return self.key_env is None or self.key() is not None


#: Per-tier model override, checked before the provider's default. `LLM_MODEL`
#: alone pins every chat tier to one model on the selected provider.
MODEL_ENV: dict[Tier, str] = {
    Tier.REASON: "LLM_MODEL_REASON",
    Tier.BALANCED: "LLM_MODEL_BALANCED",
    Tier.CHEAP: "LLM_MODEL_CHEAP",
}
MODEL_ENV_ALL = "LLM_MODEL"
#: Overrides the preset's base URL: a self-hosted Ollama on another machine, a
#: LiteLLM proxy, or the generic `openai-compatible` preset which has none.
BASE_URL_ENV = "LLM_BASE_URL"
#: The key for the generic preset. Optional, because local servers take none.
GENERIC_KEY_ENV = "LLM_API_KEY"

#: Order matters: `LLM_BACKEND=free` takes the first of these whose key is set.
#: Ollama is keyless and would always match, so it is selected by name only.
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        # Read off the live /models list on 2026-09-05 (the free-backend-probe
        # workflow prints it): the Llama chat models had left the lineup, and
        # llama-3.1-8b-instant answered 404. GPT-OSS is what remains that is a
        # plain chat model with its reasoning kept out of the content field;
        # Qwen3 puts its thinking in the reply unless asked not to, and
        # groq/compound is an agent, not a model.
        models={
            Tier.REASON: "openai/gpt-oss-120b",
            Tier.BALANCED: "openai/gpt-oss-20b",
            Tier.CHEAP: "openai/gpt-oss-20b",
        },
        rpm=30,
        note=(
            "free tier, no card: GPT-OSS 120b/20b, Qwen3 27b, Compound (the Llama "
            "chat models left the lineup in 2026-09); about 30 requests a minute "
            "and a per-model daily cap (console.groq.com/settings/limits)"
        ),
    ),
    Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env="GEMINI_API_KEY",
        models={
            Tier.REASON: "gemini-flash-latest",
            Tier.BALANCED: "gemini-flash-latest",
            Tier.CHEAP: "gemini-flash-lite-latest",
        },
        rpm=10,
        note=(
            "Google AI Studio free tier, no card: Gemini Flash and Flash-Lite, "
            "rate-limited per model and per day (ai.google.dev/gemini-api/docs/rate-limits)"
        ),
        aliases=("google", "aistudio", "google-ai-studio"),
    ),
    Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        models={
            Tier.REASON: "deepseek/deepseek-r1:free",
            Tier.BALANCED: "meta-llama/llama-3.3-70b-instruct:free",
            Tier.CHEAP: "meta-llama/llama-3.2-3b-instruct:free",
        },
        rpm=20,
        note=(
            "free models end in ':free'; 20 requests a minute and a small daily cap "
            "(50, or 200 once the account has ever held $10); the account balance "
            "must exist, and may be $0; the free lineup rotates - expect to set "
            "LLM_MODEL_* when a default 404s"
        ),
        extra_headers={"HTTP-Referer": "https://github.com/fangrhui040527/finance-agent"},
    ),
    Provider(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        key_env="MISTRAL_API_KEY",
        models={
            Tier.REASON: "mistral-large-latest",
            Tier.BALANCED: "mistral-medium-latest",
            Tier.CHEAP: "mistral-small-latest",
        },
        rpm=60,
        note=(
            "La Plateforme free tier: 1 request a second, 500K tokens a minute, "
            "1B a month; needs phone verification and the data-use opt-in"
        ),
    ),
    Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        key_env="NVIDIA_API_KEY",
        models={
            Tier.REASON: "deepseek-ai/deepseek-r1",
            Tier.BALANCED: "meta/llama-3.3-70b-instruct",
            Tier.CHEAP: "meta/llama-3.1-8b-instruct",
        },
        rpm=40,
        note=(
            "NVIDIA NIM: 100+ hosted open models on a rate-limited free key, "
            "about 40 requests a minute"
        ),
        aliases=("nim", "nvidia-nim"),
    ),
    Provider(
        name="ollama",
        base_url="http://localhost:11434/v1",
        key_env=None,
        models={
            Tier.REASON: "qwen3:14b",
            Tier.BALANCED: "qwen3:8b",
            Tier.CHEAP: "llama3.2:3b",
        },
        rpm=None,
        note=(
            "local, keyless, unmetered: the models must be pulled first "
            "(`ollama pull qwen3:8b`); LLM_BASE_URL points at another host"
        ),
        aliases=("local",),
    ),
    Provider(
        name="openai-compatible",
        base_url="",
        key_env=GENERIC_KEY_ENV,
        models={},
        rpm=None,
        note=(
            "any OpenAI-compatible endpoint: LLM_BASE_URL is required, "
            "LLM_API_KEY is optional, and every tier's model must be named "
            "with LLM_MODEL or LLM_MODEL_<TIER>"
        ),
        aliases=("openai", "custom", "compatible", "litellm", "vllm", "lmstudio"),
    ),
)

#: Providers `LLM_BACKEND=free` may auto-select: keyed, hosted, permanent tier.
AUTO_SELECTABLE: tuple[str, ...] = ("groq", "gemini", "openrouter", "mistral", "nvidia")


def names() -> tuple[str, ...]:
    return tuple(p.name for p in PROVIDERS)


def env_vars() -> tuple[str, ...]:
    """Every variable this module reads. The test fixture clears all of them,
    so a developer's populated shell cannot turn a unit test into a live call."""
    keys = [p.key_env for p in PROVIDERS if p.key_env]
    return tuple(keys) + tuple(MODEL_ENV.values()) + (MODEL_ENV_ALL, BASE_URL_ENV)


def lookup(name: str) -> Provider | None:
    """A provider by name or alias, case-insensitive; None when nothing matches."""
    key = name.strip().lower()
    for p in PROVIDERS:
        if key == p.name or key in p.aliases:
            return p
    return None


def is_provider_name(name: str) -> bool:
    return lookup(name) is not None


def from_env(name: str) -> Provider:
    """The preset with the environment's overrides applied, ready to call.

    Raises ValueError when the result could not serve a call - no base URL, or
    a chat tier with no model - naming the variable that fixes it. The KEY is
    not checked here: that is the backend's job, and it raises AuthError at
    construction so the failure is the same shape as the Anthropic one.
    """
    preset = lookup(name)
    if preset is None:
        raise ValueError(f"unknown provider {name!r}; expected one of {', '.join(names())}")

    base_url = os.environ.get(BASE_URL_ENV, "").strip() or preset.base_url
    if not base_url:
        raise ValueError(
            f"provider {preset.name!r} has no base URL: set {BASE_URL_ENV} to the "
            "endpoint root, the part before /chat/completions"
        )
    base_url = base_url.rstrip("/")

    every = os.environ.get(MODEL_ENV_ALL, "").strip()
    models: dict[Tier, str] = {}
    for tier in _CHAT_TIERS:
        chosen = os.environ.get(MODEL_ENV[tier], "").strip() or every or preset.models.get(tier, "")
        if not chosen:
            raise ValueError(
                f"provider {preset.name!r} names no model for the {tier.value} tier: "
                f"set {MODEL_ENV[tier]}, or {MODEL_ENV_ALL} for every tier"
            )
        models[tier] = chosen

    return replace(preset, base_url=base_url, models=models)


def first_configured(candidates: tuple[str, ...] = AUTO_SELECTABLE) -> Provider | None:
    """The first auto-selectable provider whose key is present, or None."""
    for name in candidates:
        p = lookup(name)
        if p is not None and p.configured():
            return p
    return None


def configured_names() -> list[str]:
    """Every keyed provider whose key is set right now. For the disclosure line:
    an operator with two keys in .env should be told which one is answering."""
    return [p.name for p in PROVIDERS if p.key_env and p.key() is not None]
