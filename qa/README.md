# qa/ — the two-phase QA suite

Separate from `tests/` on purpose. `tests/` is the product's own suite and must
stay keyless and offline. This suite asks a different question: **does the
system function as shipped**, first with no key and no network, then on the
real API with the cheapest model.

The MCP server is out of scope here by decision. Nothing under `qa/` drives it.

## Phase 1 — keyless

    python -m pytest qa/phase1 -q
    python -m pytest qa/phase1 -q -m "not slow"     # skips the product-suite and code-graph runs

| File | What it proves |
|---|---|
| `test_p1_seam_transport.py` | the exact request `AnthropicBackend` sends, the response fields the live API really returns, damaged bodies, and two measured facts: `retry-after` is not read, backoff is bounded |
| `test_p1_seam_client.py` | `InferenceClient` on the **registry-derived** allowlist: only the four `llm_complete` holders reach the wire, the ledger row matches the wire, the budget refuses before sending, a truncated call is raised and (today) not ledgered, the trace keeps the verbatim prompt |
| `test_p1_entrypoints.py` | every script as a real process: `verify.py`, `stress/run.py`, `trace_run.py`, the graph builds (twice, byte-identical), every `ask.py` subcommand, the `predict.py` round trip, and the product's own suite |
| `test_p1_agents_registry.py` | all sixteen agents on the real allowlist; class `tools` vs registry drift; every `_guard_tool` literal is granted; no agent can place an order or borrow a tool |
| `test_p1_invariants.py` | properties under random input: shares sum to one, bets stay in `[1, n]`, a size never exceeds its cap, append-only stores refuse edits, config bounds cannot be widened, all eleven markets are self-consistent |
| `test_p1_free_backend.py` | the free-provider backend (docs/21) on the PERFUMES axes, against a real loopback listener that speaks the OpenAI wire format (`qa/_support/loopback.py`): a granted agent's call crosses a socket in the OpenAI shape and is ledgered under the answering model at zero cost, a split keeps the thesis off the free model, and `ask.py backend`, `/api/backend` and the doctor say so (Functionality); 429 + Retry-After, 5xx, a dropped connection, a closed port and a captive portal, a 4xx never retried, truncation and a content filter ledgered (Reliability); every refusal names its variable, Malay/Chinese/emoji round-trip, the disclosure label stays before the first colon (Usability); the key is in one header and in no output, ledger row or error - even when the provider echoes it - HTTPS presets, advice verbs meet the OUTPUT rail, an instruction in a reply is content, `.env.example` ships every key empty (Security); no socket at selection, pacing, stdlib only (Efficiency); trailing slashes (Portability); docs/21, README, index and status table agree with the catalogue (Maintainability); a new provider is one entry (Extensibility). Skips as a whole until the backend is on the checkout |
| `test_p1_rag_pipeline.py` | the retrieval pipeline on the PERFUMES axes: a swept article is retrievable by its owning agent with a verifying citation (Functionality); 429s honour Retry-After, 5xx retries, 4xx does not, a failing host opens its breaker (Reliability); Malay, Chinese and emoji text round-trips intact (Usability); an injected article body is stored as data and refused by the INPUT rail, and no source module names an execution tool (Security); indexing is linear and the router is cached (Efficiency); relative paths only (Portability); catalogue, registries and config agree, and a new RSS source is one line (Maintainability, Extensibility) |

## Phase 2 — live, on Haiku

    QA_LIVE=1 python -m pytest qa/phase2 -q

Needs `ANTHROPIC_API_KEY` (environment or `.env`) **and** `QA_LIVE=1`. Without
both, every phase-2 test skips. The one exception is `test_p2_free_backend_live.py`,
which needs `QA_LIVE=1` and a *free-provider* key instead (or a local Ollama) and
never touches the Anthropic API. Every Messages tier resolves to
`claude-haiku-4-5` — model **and** billing — through the product's own
`FINPLANET_CHEAP=1` cap (promoted into `core/llm/tiers.py` from this suite's
old model-table pin), so a reason-tier call proves the reason-tier code path at
Haiku cost and the ledger agrees with the meter. A session that would pass
`QA_MAX_USD` (default 0.50) raises before sending.

The product sits on the official `anthropic` SDK; its test seam is
`client=` (`tests/_anthropic_double.py` offline, `qa/_support/live.py::
metered_client` live). `LiveClient` stays raw urllib on purpose — it measures
the wire, not the vendor library. A model refusal is now content, not an
exception: `Completion(refused=True, refusal_reason=...)`, ledgered like any
answer.

| File | What it proves |
|---|---|
| `test_p2_api_facts.py` | the alias resolves to the pinned snapshot; usage carries cache fields the product ignores; a bad key is 401, free, not retried; `max_tokens=1` is refused as truncation; prompt caching works on the wire and is unreachable from the product; Haiku follows a JSON-only instruction; an injection is blocked by the INPUT rail and, if it got through, A15 still says NO LESSON |
| `test_p2_product_seam_live.py` | the four granted agents complete through the real registry on Haiku; ungranted ones never reach the wire; the ledger overbills a pinned reason call by exactly 5x; the trace captures a real call; `ask.py backend` reports the real backend; the budget stops a live call; real model text meets the OUTPUT rail |
| `test_p2_geval.py` | Haiku grades the product's narrative surfaces against rubrics (scores in `qa/artifacts/geval.json`) |
| `test_p2_feeds_live.py` | Stooq and GDELT, keyless but networked; `ask.py prices`, `ask.py why --fetch`, `trace_run.py --live`; each skips if its host is unreachable |
| `test_p2_free_backend_live.py` | one real answer from every free provider that has a key (docs/21), on its cheap model: the text is non-empty, the ledger row carries the provider's model id at zero cost, an unknown model is refused with the variable that fixes it, and `ask.py backend` as a process names the provider without printing the key. Needs `QA_LIVE=1` and a free key (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `NVIDIA_API_KEY`) or a local Ollama; never an Anthropic key. Writes `qa/artifacts/free-live.json`. Meant to run from `.github/workflows/free-backend-probe.yml` |
| `test_p2_sources_live.py` | every catalogued source, live: GDELT, Google News, Yahoo ticker feeds, the five Malaysian RSS candidates (an index page xfails with the feeds it advertises), EDGAR, BNM's OPR, DOSM's CPI, and - with their keys - Finnhub, FMP, Alpha Vantage, FRED. Skips per unreachable host; xfails a spent quota. Meant to run from a GitHub Actions runner |

## Artefacts

`qa/artifacts/` (gitignored) receives `live-cost.json` — every live call, priced
off the model that actually answered — and `geval.json`. Anything written there
passes through `redact()` first.

## Findings, and where they stand

From the free-provider pass (2026-09-04, against PR #32 at ec8ba34; 41 of 42 held):

- **Fixed.** `OpenAICompatibleBackend` relayed a provider's error words verbatim, so a
  gateway that echoes the `Authorization` header back in its error body put the key
  into the exception text, which reaches logs and the trace's error field. The
  backend now scrubs its own key and any `Bearer <token>` from the message before it
  is raised (`_scrub`). Pinned twice: here by
  `test_p1_free_backend.py::test_a_provider_that_echoes_the_key_in_an_error_does_not_put_it_in_the_exception`
  against a real listener, and in the product suite by
  `tests/test_openai_compatible_backend.py::test_a_provider_that_echoes_the_key_does_not_get_it_into_the_error`.

Fixed after the first live pass, each pinned by `tests/test_qa_findings.py`:

- `retry-after` on a 429 is now honoured (capped at 60s; non-numeric falls back
  to exponential).
- A truncated or refused call is ledgered before the error is raised — the
  spend no longer vanishes with the exception.
- The system prompt now carries a `cache_control` breakpoint, and
  `tiers.cost_usd` prices cache reads (0.1x) and writes (1.25x) with
  `input_tokens` treated as the uncached remainder it actually is.
- The point-forecast refusal covers "what price will it hit" phrasings.
- `bucket_surprise` edges no longer depend on a float ulp.
- `GDELT_DOC_API` can point the news feed at plain HTTP on a network that
  blocks 443 (trade-off documented in `.env.example`).
- Stooq's JavaScript wall is named as such, and `default_feed()` chains
  Stooq → Yahoo (`YahooFeed`, keyless, all eleven markets) so one walled
  source no longer takes the price path down.

Still true, stated rather than hidden:

- The ledger prices by tier, not by the answering model, so a Haiku-pinned QA
  session over-reports cost by the tier ratio (5x on reason). By design; the
  meter in `qa/_support/cost.py` prices reality.
- Stooq-specific live tests xfail while its browser wall is up.
- The Kelly/quality caveats in `details/10-STATUS-AND-GAPS.md` are unchanged —
  they are blocked on time and human work, not code.
