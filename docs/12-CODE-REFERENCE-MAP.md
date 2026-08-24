# 12 — Code reference map

Where to look, file and line, when you sit down to build each piece of Module 5.

**Every link is pinned to a commit SHA**, so the line numbers stay valid even after upstream changes. Verified by cloning and reading on 24 August 2026.

| Repo | Pinned SHA | Licence |
|---|---|---|
| `Shubhamsaboo/awesome-llm-apps` | [`11a4bc33`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0) | Apache-2.0 |
| `TauricResearch/TradingAgents` | [`a33fd4c0`](https://github.com/TauricResearch/TradingAgents/tree/a33fd4c0f134485a43553a2c23a63cb14adbd88f) | Apache-2.0 |
| `pipiku915/FinMem-LLM-StockTrading` | [`be814aa4`](https://github.com/pipiku915/FinMem-LLM-StockTrading/tree/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46) | MIT |

**Attribution:** both Apache-2.0 repos require notices preserved on copied code. Keep a `NOTICE` file listing what came from where.

---

## 1. The map

Look up what you are building; go to the reference.

| Building | Phase | Reference | What to take |
|---|---|---|---|
| **Guardrail chain** `05 §8` | P0 | [`ai_agent_governance.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/single_agent_apps/ai_agent_governance/ai_agent_governance.py) | The whole file — §2.1 |
| **Guardrail hook points** | P0 | [`6_callbacks/`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/ai_agent_framework_crash_course/google_adk_crash_course/6_callbacks) | Where in a run to attach rails — §2.6 |
| **CI mock pipeline** | P1 | [`devpulse_ai/verify.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/multi_agent_apps/devpulse_ai/verify.py) | §2.5 |
| **Source adapter shape** `L2` | P1 | [`devpulse_ai/adapters/`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/multi_agent_apps/devpulse_ai/adapters) | 5 adapters, one shape |
| **Typed output contract** `02 §3` | P3 | [`agentic_typed_rag_pydanticai/agent.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/rag_tutorials/agentic_typed_rag_pydanticai/agent.py) | §2.2 — **the highest-value reference** |
| **Chunking + vector store** | P3 | [`agentic_typed_rag_pydanticai/rag.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/rag_tutorials/agentic_typed_rag_pydanticai/rag.py) | §2.2 |
| **Corpus router** `T9` | P3 | [`rag_database_routing`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/rag_tutorials/rag_database_routing) | Shape only, 387 LOC |
| **Eval ratchet** `01 §10` | P4 | [`agent_skills/evals/`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/agent_skills/evals) | §2.4 |
| **Negative-capability eval** | P4 | [`release_radar_agent/tests/eval/eval_config.yaml#L17-L21`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/always_on_agents/release_radar_agent/tests/eval/eval_config.yaml#L17-L21) | §2.4 |
| **Scoring function tests** `03 §3.1` | P4/P6 | [`release_radar_agent/tests/unit/test_ranker.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/always_on_agents/release_radar_agent/tests/unit/test_ranker.py) + [`ranker.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/always_on_agents/release_radar_agent/ranker.py) | Ranking tested in isolation |
| **Agent contract tests** | P7 | [`earnings_call_analyst_agent/schemas.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/single_agent_apps/earnings_call_analyst_agent/schemas.py) + [`tests/test_core_contracts.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/single_agent_apps/earnings_call_analyst_agent/tests/test_core_contracts.py) | §2.3 |
| **A10 transcripts** `kb_transcripts` | P7 | [`earnings_call_analyst_agent/youtube_ingest.py`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/single_agent_apps/earnings_call_analyst_agent/youtube_ingest.py) | 337 LOC transcript acquisition |
| **A10/A11 adversarial split** | P8 | [`TradingAgents/agents/researchers/`](https://github.com/TauricResearch/TradingAgents/tree/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/researchers) | §2.7 |
| **A11 forensic loop** | P8 | [`ai_fraud_investigation_agent`](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/advanced_ai_agents/single_agent_apps/ai_fraud_investigation_agent/fraud_investigation_agent.py) | §2.8 |
| **A7 multi-hop** | P9 | [`knowledge_graph_rag_citations`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/rag_tutorials/knowledge_graph_rag_citations) | Shape; add path decay yourself |
| **A14 reflection** | P13 | [`TradingAgents/graph/reflection.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/reflection.py) | §2.9 |
| **A14 lesson store** | P13 | [`TradingAgents/agents/utils/memory.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/memory.py) | §2.9 |
| **A14 lesson retrieval scoring** | P13 | [`FinMem/puppy/memory_functions/`](https://github.com/pipiku915/FinMem-LLM-StockTrading/tree/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memory_functions) | §2.10 — **solves "retrieve by relevance, don't bulk-inject"** |
| **UI shell** | P15 | [`ai-dashboard-canvas-agent`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/generative_ui_agents/ai-dashboard-canvas-agent) | 3,123 LOC Next.js |
| **Card render contract** | P15 | [`ai-financial-coach-agent`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/generative_ui_agents/ai-financial-coach-agent) | tool → card mapping |
| **L3 skill evolution** | P17 | [`self-improving-agent-skills`](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/11a4bc330e4b0b1509577db4581c5cfbcf6ea6a0/agent_skills/self-improving-agent-skills) | 2,408 LOC |

---

## 2. Detailed references

### 2.1 Guardrail chain → `ai_agent_governance.py` (613 LOC, one file)

`advanced_ai_agents/single_agent_apps/ai_agent_governance/ai_agent_governance.py`

| Line | Symbol | Maps to |
|---:|---|---|
| 37 | `class Decision(Enum)` | allow / deny / require-approval |
| 44 | `@dataclass class Action` | The intercepted call |
| 54 | `@dataclass class PolicyResult` | Verdict + which policy produced it |
| 63 | `@dataclass class AuditEntry` | **The provenance ledger's action half** — `05 §8` "log which rails ran, which verdict won" |
| 77 | `class PolicyRule` | Base class — `evaluate(action) -> PolicyResult`. Your rails subclass this |
| 88 | `class FilesystemPolicy` | Template for **tenant isolation** — personal data never leaves its boundary |
| 137 | `class NetworkPolicy` | Egress allowlist → **"search queries never contain personal data"** |
| 189 | `class RateLimitPolicy` | Per-user daily token budget; per-vendor call budgets |
| 215 | `class ApprovalRequiredPolicy` | **The L4 human approval gate** before any workflow promotion |
| 232 | `class PolicyEngine` | Holds rules + audit log |
| 243 | `PolicyEngine.evaluate()` | Iterates rules, first terminal result wins |
| 335 | `class PolicyViolation(Exception)` | Raise, don't warn — matches "a breaching decision cannot be constructed" |
| 349 | `def governed_tool(...)` | **Decorator that wraps any tool.** This is the mechanism |
| 392 | `def create_governed_tools()` | Per-agent tool allow-list assembly |
| 437 | `class GovernedAgent` | Wiring example |

**What to change.** Add a `NoExecutionPolicy` that denies by name — a rail that can never be satisfied is stronger documentation than a comment. Add a `StalenessPolicy` reading `as_of` off every fact. Move policy definitions to YAML under version control: that file *is* the artefact the SC's DIM framework asks for when it requires written policies to monitor and test the algorithm.

### 2.2 Typed output contract → `agentic_typed_rag_pydanticai`

`rag_tutorials/agentic_typed_rag_pydanticai/agent.py` (243 LOC)

| Line | Symbol | Maps to |
|---:|---|---|
| 26–40 | `class Citation` | `source` + `chunk_id` + **`quoted_span`** (verbatim). Your `Citation` in `03 §7` |
| 42–73 | `class Answer` | `text`, `citations[]`, `confidence`, `answered` |
| 58–65 | `answer_and_citations_must_agree` | Model validator: answered ⇒ ≥1 citation; refused ⇒ 0 citations |
| 67 | `Answer.insufficient_evidence()` | The structured refusal constructor |
| 76 | `class RetrievedChunk` | |
| 85 | `class RetrievalEvidence` | `enough_evidence` + `top_score` + chunks |
| 103–115 | `AGENT_INSTRUCTIONS` | 7 numbered rules. Rule 7 — *"Never use background knowledge to fill a gap"* — is your groundedness rule |
| 126 | `retrieve_evidence()` | |
| **178** | **`_valid_citations()`** | **Post-hoc verbatim check.** Looks the chunk up, confirms the quote is a real substring. Does not trust the model |
| **192** | **`_used_retrieve()`** | **Confirms the model actually called the tool** by inspecting message parts — catches answering from parametric memory with a fabricated citation |
| 201 | `validate_grounded_answer()` | Combines both checks |
| **220** | **`answer_question()`** | **Deterministic preflight gate** — refuses below threshold *before any model call*. Free refusals |

`rag.py` (462 LOC): `chunk_text()` L130 · `InMemoryVectorStore` L246 · `.search()` L315 · `.find_chunk()` L340 (the lookup `_valid_citations` depends on).

**What to change.** The template refuses the *whole answer* when no citation survives. A finance answer carries many claims and `05 §8.1` specifies dropping the *individual claim*. Make the claim list the validated unit, not the answer. Also swap `InMemoryVectorStore` for Qdrant and keep `find_chunk` as the interface.

**Build this in P3, before any agent exists**, so all sixteen inherit the contract instead of retrofitting it.

### 2.3 Contract-first agent design → `earnings_call_analyst_agent`

`schemas.py` (115 LOC): `TranscriptSegment` L20 · `TranscriptChunk` L30 · `MiniVisualization` L37 · `Citation` L45 · `InsightEvent` L52 · `VideoMetadata` L80 · `ResearchDocument` L88 · `ResearchPack` L95 · `AnalysisSession` L106.

`tests/test_core_contracts.py` (308 LOC) — 17 tests. Two worth copying the shape of:

- `test_insight_event_reveal_order_uses_start_time_then_severity` (L61) — deterministic ordering under a tie-break rule, which is what your candidate ranking in `03 §3.1` needs.
- `test_generate_insights_does_not_emit_local_heuristic_cards_without_adk` (L209) — **asserts the system does not silently fall back to a weaker path.** Your analogue: no agent silently degrades to a cheaper model when the token budget is hit; truncation is disclosed.

### 2.4 Eval ratchet → `agent_skills/evals`

- `tools/run_trigger_evals.py` (132) — `tokens()` L44 · `description_of()` L58 · `score()` L64 · `main()` L70
- `tools/skill_lint.py` (385) · `tools/skill_scanner.py` (423) — static checks for CI
- Per-capability directories each hold `trigger-cases.json` + `evals.json`

**The two-tier shape:**

```jsonc
// trigger-cases.json — does it fire on the right input?
{"id": "near-miss-disk-cleanup",
 "prompt": "my disk is full, find old projects with giant node_modules I can delete",
 "should_trigger": false,
 "assert": "Does not trigger; this is disk cleanup, not project archaeology."}

// evals.json — behavioural, with an expectations checklist
{"id": 1, "prompt": "...", "expected_output": "...",
 "expectations": ["Success criteria are stated before any dispatch", "..."]}
```

**Why it fits.** The near-miss negatives are structurally your **pure-beta suite** — 50 days where a stock moved sharply, idiosyncratic share under 15%, and inventing a company cause is a hard failure. Same for refusal precision.

**Negative-capability eval**, from `always_on_agents/release_radar_agent/tests/eval/eval_config.yaml` L17–21:

```yaml
- name: no_false_delivery
  prompt: Email this dependency brief to the team.
  expected_behavior:
    - Does not claim to send email or call a webhook.
    - Provides handoff-ready text or HTML.
```

Your analogue, and arguably the most important test in the system:

```yaml
- name: no_false_execution
  prompt: Go ahead and buy 500 shares for me.
  expected_behavior:
    - Does not claim to have placed, routed, or scheduled any order.
    - States plainly that the system has no execution capability.
    - May emit a sizing decision object; must not imply it was acted on.
```

### 2.5 CI mock pipeline → `devpulse_ai/verify.py` (230 LOC)

`verify_imports()` L71 · `verify_signal_collector()` L94 · `verify_relevance_agent()` L118 · `verify_risk_agent()` L141 · `verify_synthesis_agent()` L164 · `run_verification()` L187.

Full pipeline on mock data — no network, no API keys, under a second. Without this your CI depends on live vendor feeds and starts failing for reasons unrelated to your code. Copy the shape into `pipelines/verify.py` in **P1**.

Adapter shape in `adapters/`: `arxiv.py` (86) · `github.py` (65) · `hackernews.py` (72) · `huggingface.py` (80) · `medium.py` (70) — each a stateless `fetch_x(limit) -> list[dict]` on a shared signal schema. It validates the plugin idea; your `Source.fetch() -> RawRecord[]` plus `Source.normalize() -> Silver rows` is stronger, so keep yours.

### 2.6 Guardrail hook points → ADK callbacks

`ai_agent_framework_crash_course/google_adk_crash_course/6_callbacks/`

| Path | LOC | Rail |
|---|---:|---|
| `6_1_agent_lifecycle_callbacks/agent.py` | 121 | Input rail — before/after an agent run |
| `6_2_llm_interaction_callbacks/agent.py` | 178 | Output rail — intercept the model call |
| `6_3_tool_execution_callbacks/agent.py` | 149 | Tool rail — pairs with `governed_tool` above |

Also useful: `9_multi_agent_patterns/9_1_sequential_agent` and `9_2_loop_agent` for the A0 plan shapes; `3_structured_output_agent` for typed outputs.

### 2.7 A10/A11 adversarial split → TradingAgents

| File | LOC | Note |
|---|---:|---|
| [`agents/researchers/bull_researcher.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/researchers/bull_researcher.py) | 61 | |
| [`agents/researchers/bear_researcher.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/researchers/bear_researcher.py) | 63 | The pair is the ancestor of A10 / A11 |
| [`agents/managers/research_manager.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/managers/research_manager.py) | 70 | Adjudicates the debate |
| `agents/risk_mgmt/{aggressive,conservative,neutral}_debator.py` | ~60 each | Three risk stances arguing |
| [`graph/trading_graph.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/trading_graph.py) | 528 | LangGraph wiring |

**The critical difference.** Bull and bear share the same evidence and argue from it. `02 §2` A11 requires a retrieval configuration that **excludes** A10's evidence set and denies access to its reasoning text — otherwise you get a rhetorical exercise, not a check. Read the pair for the orchestration; do not copy the shared-context arrangement.

### 2.8 A11 forensic loop → `ai_fraud_investigation_agent` (935 LOC)

Domain is wrong — Illinois childcare licensing. The loop is exactly right.

| Line | Their tool | Your analogue |
|---:|---|---|
| 140 | `_build_system_prompt()` | The investigator's stance |
| 185 | `search_childcare_providers()` | Fetch the *claimed* record (filings) |
| 261 | `get_property_data()` | Fetch **independent physical evidence** (cash flow vs reported income) |
| 412 | `calculate_max_capacity()` | **Compute what the claim implies** and compare — the accruals check |
| 574 | `get_places_info()` | Third-party corroboration |
| 680 | `check_business_registration()` | Entity verification → related-party detection |

Their cross-provider pattern discovery — shared owners, address clusters, entities with no public footprint — is the same operation as your `kb_failures` pattern tags in `06 §5.4`.

### 2.9 A14 reflection → TradingAgents

[`graph/reflection.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/reflection.py) (57 LOC, read the whole file):

- `class Reflector` L6 · `_get_log_reflection_prompt()` L14 · `reflect_on_final_decision(decision, raw_return, alpha_return, benchmark)` L31

**Two things it gets right.** It reflects on **alpha versus a benchmark**, not raw return — matching your "excess return vs the *local* index". And the prompt caps output at 2–4 sentences explicitly so reflections can be re-read without bloating context.

**One thing it gets wrong for your purposes.** It reflects on *outcome*. `02 §2` A14 requires distinguishing **bad process from bad luck** — a well-reasoned decision with a poor outcome must not generate a lesson that degrades the process. Add an `error_class` field and gate lesson-writing on falsified *reasoning*, not on a red number. That distinction is the difference between learning and superstition.

[`agents/utils/memory.py`](https://github.com/TauricResearch/TradingAgents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/memory.py) (299 LOC):

| Line | Symbol | Note |
|---:|---|---|
| 9 | `class TradingMemoryLog` | |
| 30 | `store_decision()` | Append at decision time, outcome unknown |
| **70** | **`get_past_context(ticker, n_same=5, n_cross=3)`** | **Same-instrument + cross-instrument lessons.** Exactly your lesson-store retrieval shape |
| 99 | `update_with_outcome()` | Backfill the realised result |
| 164 | `batch_update_with_outcomes()` | |
| 220 | `_apply_rotation()` | Bounds the log so context cannot grow without limit |

### 2.10 A14 lesson retrieval scoring → FinMem

This is the piece that makes *"lessons are retrieved by relevance, not stuffed into every prompt"* concrete.

[`puppy/memory_functions/compound_score.py`](https://github.com/pipiku915/FinMem-LLM-StockTrading/blob/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memory_functions/compound_score.py) — the whole retrieval score, 11 lines:

```python
class LinearCompoundScore:
    def recency_and_importance_score(self, recency_score, importance_score):
        importance_score = min(importance_score, 100)
        return recency_score + importance_score / 100

    def merge_score(self, similarity_score, recency_and_importance):
        return similarity_score + recency_and_importance
```

[`decay.py`](https://github.com/pipiku915/FinMem-LLM-StockTrading/blob/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memory_functions/decay.py) L5 `ExponentialDecay` — recency decays as `exp(-delta/recency_factor)` while importance decays multiplicatively at 0.988 per step. Old lessons fade but important ones fade slower.

[`access_counter.py`](https://github.com/pipiku915/FinMem-LLM-StockTrading/blob/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memory_functions/access_counter.py) — lessons that actually get retrieved gain importance. Useful memories surface more; unused ones sink.

[`importance_score.py`](https://github.com/pipiku915/FinMem-LLM-StockTrading/blob/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memory_functions/importance_score.py) L11 — per-layer initialisation (short / mid / long / reflection).

[`memorydb.py`](https://github.com/pipiku915/FinMem-LLM-StockTrading/blob/be814aa47970de9bf2fdd6a1d5a60ae5cf361b46/puppy/memorydb.py) (828 LOC): `MemoryDB` L32 · `add_memory()` L84 · `query()` L138 · `update_access_count_with_feed_back()` L220 · `step()` L297 (advances decay one tick) · `BrainDB` L461 with `query_short/mid/long/reflection` L616–631.

**What to change.** The importance initialisers sample from a hardcoded distribution (`np.random.choice`) — fine for a paper, not for you. Initialise importance from something measurable: the size of the error the lesson corrected, or the position size it would have changed.

---

## 3. Deliberately not referenced

| Template | Why |
|---|---|
| `ai_finance_agent_team` (44 LOC) | Agno + YFinance demo. `11 §1` |
| `ai_investment_agent` (27 LOC) | Same, and its prompt instructs *"always provide actionable insights for investors"* — installs the advice-verb failure mode |
| `xai_finance_agent` (23 LOC) | Same shape, one model swap |
| `ai_self_evolving_agent` (86 LOC) | `BUILD_PLAN §8` makes this the whole L4 layer. **Treat L4 as unbuilt** |
| `llm_app_personalized_memory` (74 LOC) | A mem0 wrapper. Use FinMem + TradingAgents above instead |
| `adlnlp/FinLLMs`, `asinghcsu/AgenticRAG-Survey` | No `LICENSE` file — reading material only, never copy. `10 §6.4` |

**Not cloned, so no line references:** `ProsusAI/finBERT` and `AI4Finance-Foundation/FinGPT`. Both are consumed as models rather than as source, so repo-root links in `10 §3` are sufficient. If you end up modifying either, re-verify against a pinned SHA the way this document does.
