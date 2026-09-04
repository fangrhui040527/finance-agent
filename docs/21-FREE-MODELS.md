# 21 — Free models: a zero-cost first month

The question this answers: *can the first month of knowledge-building run
without billing the Anthropic API — the reasoning done by my Claude session,
the cleaning and distribution done by an open-weight model I do not pay for?*

**Yes, and most of it was already true.** What was missing was one backend.

## 1. Where the reasoning already runs

docs/15 and docs/20 put the reasoning on the operator's Claude session, not on
an API key held by this repository:

| Reasoning | Runs on | Costs |
|---|---|---|
| Interactive research — you ask, Claude calls the MCP tools | your Claude session (Max plan) | nothing per token |
| The nightly feedback page (docs/20) | a scheduled Claude Routine | nothing per token |
| The engines — attribution, sizing, caps, refusals | this process, deterministic | nothing |

That covers "the reasoning will be done by you". Claude reads what the
collector kept, reasons over the pack, writes the page, and the engines
decide every number. No API key is involved.

## 2. What still went to a key, and where it goes now

Some calls are made by **this process itself**, through
`InferenceClient.complete()`, whenever a surface runs a model rather than
handing the question to your session:

| Task classes | Tier | Examples |
|---|---|---|
| `NEWS_TRIAGE`, `ENTITY_TAG`, `DEDUP_ADJUDICATE`, `CATEGORY_CLASSIFY`, `INTENT_ROUTING` | cheap | the cleaning and distribution work — sorting articles, tagging names, judging near-duplicates |
| `DAILY_BRIEF`, `MACRO_READ`, `ADHOC_QUERY`, … | balanced | the evidence agents when run from `ask.py` |
| `THESIS_SYNTHESIS`, `RED_TEAM` | reason | `ask.py thesis` narrating a thesis, `predict.py --second-opinion` |

Before this document, every one of those needed `ANTHROPIC_API_KEY`, or fell
back to `EchoBackend` — a deterministic stub that returns placeholder text.

Now `core/llm/backends.py` has a second real backend,
**`OpenAICompatibleBackend`**, which reaches any provider in the catalogue
below. They all serve the same `chat/completions` wire format, so one class
serves all of them, and it uses stdlib `urllib` — no new dependency.

**The prompts are still Claude's.** Every system prompt these calls carry is a
byte-stable constant in the agent modules (`agents/synthesis/narrate.py` is the
template), written and reviewed in a Claude session. The free model executes
the instruction; it does not author it. That is what "you guiding the LLM"
means in code.

## 3. Setup

One key is enough. Put it in `.env`:

```
GROQ_API_KEY=gsk_...
```

and check what will answer:

```
python ask.py backend
```

```
backend   OpenAICompatibleBackend
reason    groq (GROQ_API_KEY is set and ANTHROPIC_API_KEY is not, free tier): reason=openai/gpt-oss-120b balanced=llama-3.3-70b-versatile cheap=llama-3.1-8b-instant; ...
  reason    openai/gpt-oss-120b      max 16000, effort dial not sent to this provider
  balanced  llama-3.3-70b-versatile  max 8000, effort dial not sent to this provider
  cheap     llama-3.1-8b-instant     max 1024, effort dial not sent to this provider
```

Selection precedence, the same rule as before with one more rung:

1. `LLM_BACKEND` names one: `anthropic`, `echo`, `free` (the first free
   provider with a key), or a provider name.
2. Otherwise `ANTHROPIC_API_KEY` wins if set — and the reason says which free
   key is also present.
3. Otherwise the first free-provider key that is set.
4. Otherwise echo, and the reason says **NOT a model**.

`python ask.py backend --list` prints the catalogue with which keys are set.
`make doctor`, the MCP `system_health` tool and the web `/api/backend` screen
all name the backend and, per tier, the model that will actually answer.

## 4. The catalogue

From [12britz/awesome-free-models](https://github.com/12britz/awesome-free-models),
section *Free API Providers*, last checked there 2026-09-03. Only providers
with a **permanent** free tier are wired; trial credits that expire (Cerebras,
SambaNova's $5, Together, Nebius, Fireworks) are a countdown, not a tier, and
a backend that stops on day 31 is the EchoBackend problem with a delay.

| `LLM_BACKEND` | Key | Free tier (as catalogued) | Default models (reason / balanced / cheap) |
|---|---|---|---|
| `groq` | `GROQ_API_KEY` | no card; Llama 3.x/4, Qwen, GPT-OSS; ~30 req/min and a per-model daily cap | `openai/gpt-oss-120b` / `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` |
| `gemini` (`google`) | `GEMINI_API_KEY` | Google AI Studio; no card; Flash and Flash-Lite, rate-limited per model and per day | `gemini-flash-latest` / `gemini-flash-latest` / `gemini-flash-lite-latest` |
| `openrouter` | `OPENROUTER_API_KEY` | `:free` models; 20 req/min, ~50/day (200 once the account has ever held $10); a balance must exist, may be $0 | `deepseek/deepseek-r1:free` / `meta-llama/llama-3.3-70b-instruct:free` / `meta-llama/llama-3.2-3b-instruct:free` |
| `mistral` | `MISTRAL_API_KEY` | 1 req/s, 500K tok/min, 1B tok/month; phone verification and data-use opt-in | `mistral-large-latest` / `mistral-medium-latest` / `mistral-small-latest` |
| `nvidia` (`nim`) | `NVIDIA_API_KEY` | 100+ hosted open models, ~40 req/min | `deepseek-ai/deepseek-r1` / `meta/llama-3.3-70b-instruct` / `meta/llama-3.1-8b-instruct` |
| `ollama` (`local`) | none | local, unmetered; `ollama pull` the models first; `LLM_BASE_URL` for another host | `qwen3:14b` / `qwen3:8b` / `llama3.2:3b` |
| `openai-compatible` (`custom`, `litellm`, `vllm`, `lmstudio`) | `LLM_API_KEY` (optional) | anything that speaks the format; `LLM_BASE_URL` required | set with `LLM_MODEL` or `LLM_MODEL_<TIER>` |

Also in the catalogue and reachable through `openai-compatible` with their own
base URL: AnyAPI (100K tokens/day), OVHcloud AI Endpoints (EU-hosted, 12
req/min, an anonymous tier), Alibaba DashScope (1M tokens/month), Cloudflare
Workers AI, Hugging Face Inference Providers, SambaNova (20 req/min, 200K
tokens/day while its credit lasts), OrcaRouter (`orcarouter/free`), Kimi.

**Model ids rot.** OpenRouter's free set in particular changes month to
month. A 404 from a provider names the variable to set:

```
LLM_MODEL_CHEAP=qwen/qwen3-32b          # one tier
LLM_MODEL=llama-3.3-70b-versatile       # every tier
```

## 5. What changes on a free provider, and what does not

**Does not change**

- The routing table. A task class still lands on its tier; the tier lands on
  the provider's model for it. Callers still cannot pick a tier or a model.
- The guardrail chain, the budget rail, the ledger. Every call is enforced,
  every call is recorded — under the model that answered (`llama-3.1-8b-instant`,
  not `claude-haiku-4-5`), at the price it cost, which is zero. `operating_report`
  shows the models actually called.
- Refusal semantics. `finish_reason=content_filter` is a `Declined` carrying
  its usage; `finish_reason=length` is a `Truncated`, never half an answer.
  Nothing re-routes a refusal to a model that might comply (commitment 6).
- `FINPLANET_MODEL=cheap|balanced|reason` still pins every Messages tier to
  that tier's model — on this provider.

**Changes**

- **The effort dial is not sent.** `FINPLANET_EFFORT` and the thinking
  budget are Anthropic parameters; no free endpoint takes them by that name
  and an unknown parameter is a 400 on most. Only the output cap from the
  request profile reaches the wire, and `ask.py backend` says so per tier.
- **No prompt caching.** The system prompt is sent as a plain system message.
  Cached tokens are read back when a provider reports them and split out of
  the prompt count, so the `cached_tokens` column means the same thing whichever
  backend wrote the row.
- **Calls are paced.** Each provider's requests-per-minute is in the
  catalogue and the backend spaces calls to stay under it. A triage burst of
  forty articles on Groq takes eighty seconds rather than forty 429s.
- **Reasoning models think out loud.** DeepSeek-R1, Qwen3 and GPT-OSS on some
  hosts put their chain of thought in `<think>…</think>` in the reply. It is
  stripped before the text is returned; a reply that is *all* thinking (the
  model was cut off mid-thought) is "no text", not an answer.
- **Structured output is less reliable.** `complete_structured` asks for JSON
  and validates it; a smaller open model fails validation more often than
  Haiku does. That is a `BackendError` naming the schema, never a guessed
  object — the same rule as before, hit more often. Route a task that needs
  strict JSON to the strongest model the provider serves (`LLM_MODEL_CHEAP=…`)
  before blaming the pipeline.

## 6. The month-one shape, and after

Month one, keyless on the Anthropic side:

```
GROQ_API_KEY=gsk_...        # or any one of the five
```

Everything this process calls goes free. The thesis narrative, if you run
`ask.py thesis` rather than asking your session, is written by the
provider's reason-tier model and labelled with the backend name on screen —
the same label that makes EchoBackend's placeholder unmistakable.

When you decide a thesis is worth Opus but triage is not, split by tier:

```
ANTHROPIC_API_KEY=sk-ant-...
GROQ_API_KEY=gsk_...
LLM_BACKEND_CHEAP=groq      # reason and balanced stay on Claude
```

```
backend   SplitBackend
  reason    claude-opus-5            AnthropicBackend, effort high, max 16000, streamed
  balanced  claude-sonnet-5          AnthropicBackend, effort medium, max 8000
  cheap     llama-3.1-8b-instant     OpenAICompatibleBackend, max 1024, effort dial not sent to this provider
```

Each tier's calls land on their own backend and their own price. Two tiers
naming the same provider share one instance, so the pacing clock sees every
call to it.

## 7. What to watch

- `python ask.py backend` before any live run, as before. A free provider
  that quietly answered nothing would be the EchoBackend failure with a
  network hop; the reason line and the per-tier table exist so it cannot be
  quiet.
- The ledger's `model_id` column. If it says `claude-*` while you believed
  you were on a free tier, `LLM_BACKEND` or `ANTHROPIC_API_KEY` is set
  somewhere — an exported variable wins over `.env`.
- 429s in `recent_failures`. The pacing is per process; the web app, the MCP
  server and a CLI run in parallel each have their own clock. Under a shared
  daily cap (OpenRouter's 50), that is the number to plan around, not the
  per-minute one.
- Terms. Several free tiers train on what you send (Mistral asks for the
  opt-in explicitly; Google's free tier states it). Nothing in the cheap tier
  is your book — but the thesis narrative is your thesis. If that matters,
  keep the reason tier on Claude or on `ollama`, which leaves the machine
  only if you point `LLM_BASE_URL` somewhere.

## 8. Where it lives

| | |
|---|---|
| Catalogue, env overrides | `core/llm/providers.py` |
| The backend, the split, the selector | `core/llm/backends.py` — `OpenAICompatibleBackend`, `SplitBackend`, `backend_from_env` |
| Model and price resolved through the backend | `core/llm/client.py` — `model_of`, `pricing_of`; `core/llm/tiers.py` — `cost_usd(pricing=)`; `core/provenance/ledger.py` |
| Disclosure | `ask.py backend [--list]`, `web/api.py /api/backend`, `core/doctor.py` |
| Tests | `tests/test_openai_compatible_backend.py`, `tests/test_free_providers.py` |
