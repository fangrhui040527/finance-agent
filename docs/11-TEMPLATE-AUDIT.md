# 11 — Template audit: `awesome-llm-apps`

What is actually in the repository, measured rather than assumed, and what should be adopted for Module 5.

**Method.** Cloned [Shubhamsaboo/awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) at depth 1 on 24 August 2026 (~130 apps, 161 MB), enumerated every app directory, measured Python/TypeScript line counts per template, and read the source of every candidate above ~500 LOC plus every template `BUILD_PLAN.md` designates T1–T22. Line counts exclude READMEs, configs and assets.

**Licence.** Apache-2.0. Fork, ship, sell — keep the notices on anything you copy.

---

## 1. The headline finding

**All 22 templates named in `BUILD_PLAN.md` still exist.** The paths are correct and nothing has been renamed or removed. But the line counts invert the plan's own ranking:

| Template | `BUILD_PLAN.md` calls it | Actual size |
|---|---|---|
| **T4** `ai_finance_agent_team` | *"Canonical analyst-team pattern… **this is the skeleton of the Analyst surface**"* | **44 lines, 1 file** |
| **T5** `ai_investment_agent` | *"Ready-made **comparison report**… head-to-head is the most-used analyst interaction"* | **27 lines, 1 file** |
| **T6** `xai_finance_agent` | *"Reference for a single-file real-time quote/analysis agent"* | **23 lines, 1 file** |
| **T18** `ai_self_evolving_agent` | *"**Self-evolving workflows** — agents rewrite their own orchestration"* (the entire L4 growth layer) | **86 lines, 1 file** |
| **T16** `llm_app_personalized_memory` | *"Persistent user state: risk tolerance, goals, constraints"* | **74 lines, 1 file** |

T4 and T5 in full are Agno config objects wrapping `YFinanceTools()`. Here is T5, complete but for imports:

```python
agent = Agent(
    name="AI Investment Agent",
    model=OpenAIChat(id="gpt-5.2-2025-12-11"),
    tools=[YFinanceTools()],
    description="You are an investment analyst that researches stock prices, "
                "analyst recommendations, and stock fundamentals.",
    instructions=[
        "Format your response using markdown and use tables to display data where possible.",
        "When comparing stocks, provide detailed analysis including price trends, "
        "fundamentals, and analyst recommendations.",
        "Always provide actionable insights for investors."     # ← see below
    ],
)
```

**Three problems, beyond size.**

1. **They are built on Yahoo Finance**, which `RESEARCH.md` explicitly rules out as load-bearing — no official API, endpoints break without notice.
2. **`"Always provide actionable insights for investors"`** is the precise behaviour the output rail in `05-RISK-AND-GUARDRAILS.md` §8.1 exists to block. Adopting T5's prompt would install the advice-verb failure mode by default.
3. **Neither has any concept of attribution, point-in-time data, or refusal.** They ask a model to look at YFinance output and talk.

**Conclusion: the analyst surface has no template.** That is not a gap in the audit — it is the finding. `03-WHY-IT-MOVED.md`, the factor library, the sizing engine and the base-rate table were always going to be original work, and now that is measured rather than assumed.

The genuinely substantial code in this repository is in the **plumbing**: typed output contracts, guardrail policy engines, eval harnesses, scheduling, and UI shells. That is where to spend the borrowing budget.

---

## 2. Full audit of T1–T22

Verdicts: **ADOPT** = copy and adapt the code · **PATTERN** = read it, then write your own · **DOWNGRADE** = the plan over-weights it · **DROP** = do not build on this.

| T | Template | LOC | Files | Verdict | Note |
|---|---|---:|---:|---|---|
| T1 | `ai_financial_coach_agent` | 967 | 1 | **PATTERN** | Real planner logic, but one Streamlit file with session state. Take the budget/debt/savings decomposition, rewrite against Postgres |
| T2 | `ai-financial-coach-agent` (gen UI) | 1,846 | 11 | **ADOPT** | The tool→card render contract is the valuable part and is genuinely reusable. Repoint the tools at your backend |
| T3 | `ai_data_analysis_agent` | 120 | 1 | **PATTERN** | Thin CSV Q&A. Your ledger ingest needs dedup, merchant normalisation and recurring-txn detection — none of which is here |
| T4 | `ai_finance_agent_team` | **44** | 1 | **DROP** | Agno + YFinance demo. Not a skeleton |
| T5 | `ai_investment_agent` | **27** | 1 | **DROP** | See §1. Its prompt actively conflicts with the guardrails |
| T6 | `xai_finance_agent` | **23** | 1 | **DROP** | Same shape, one model swap |
| T7 | `earnings_call_analyst_agent` | 1,662 | 8 | **ADOPT** | The best domain template in the repo. `schemas.py` (115) + `test_core_contracts.py` (308) is contract-first design with contract tests. Also `youtube_ingest.py` for transcript acquisition |
| T8 | `devpulse_ai` | 1,519 | 13 | **ADOPT (pattern)** | `adapters/` validates the plugin shape; `relevance_agent` + `risk_agent` + `synthesis_agent` is the scoring→ranking chain for the candidate list. **`verify.py` is the gem** — full pipeline on mock data, no network, no keys, <1s. Copy that CI idea outright |
| T9 | `rag_database_routing` | 387 | 1 | **PATTERN** | The corpus-router idea, one file. You have nine collections; write your own |
| T10 | `hybrid_search_rag` | 214 | 1 | **PATTERN** | Thin. You need BM25+dense→RRF→cross-encoder against Qdrant with hard filters |
| T11 | `corrective_rag` | 468 | 1 | **PATTERN** | Superseded by T12's preflight gate, which is strictly stronger — see §3.1 |
| **T12** | **`agentic_typed_rag_pydanticai`** | **1,225** | **4** | **ADOPT — highest value in the repo** | See §3.1 |
| T13 | `knowledge_graph_rag_citations` | 524 | 1 | **PATTERN** | Right idea for A7 multi-hop; single file, no path decay |
| T14 | `rag_failure_diagnostics_clinic` | 299 | 1 | **DOWNGRADE** | `agent_skills/evals` (§4.2) is a better harness and is not in your list |
| T15 | `always_on_hn_briefing_agent` | 883 | 7 | **ADOPT** | Scheduler + `tests/unit` + `tests/eval`. But see `release_radar_agent` in §4.4 — it is bigger and has a better eval set |
| T16 | `llm_app_personalized_memory` | **74** | 1 | **DOWNGRADE** | A mem0 wrapper. Your memory needs risk tolerance, constraints, journal and lesson store — that is a schema you design |
| T17 | `multi_mcp_agent_router` | 371 | 1 | **PATTERN** | Sound router shape, small |
| T18 | `ai_self_evolving_agent` | **86** | 1 | **DOWNGRADE hard** | `BUILD_PLAN.md` §8 makes this the entire L4 self-evolving-workflow layer. It is 86 lines. **Treat L4 as unbuilt** — which is fine, it is the layer behind a human approval gate anyway |
| T19 | `self-improving-agent-skills` | 2,408 | 13 | **ADOPT** | Real implementation with a frontend and backend. The L3 skill-evolution layer genuinely exists here |
| T20 | `google_adk_crash_course` | 3,679 | 49 | **ADOPT selectively** | Not one app — a 49-file course. Take `6_callbacks` (884 LOC — **the guardrail hook point**), `9_multi_agent_patterns` (sequential + loop agents), `3_structured_output_agent`. Skip the rest |
| T21 | `headroom_context_optimization` | 203 | 1 | **PATTERN** | Small; the idea matters more than the code |
| T22 | `ai-dashboard-canvas-agent` | 3,123 | 48 | **ADOPT** | Substantial Next.js shell. The chat-assembled dashboard is real and is the fastest path to your annotated-chart surface |

---

## 3. Tier A — adopt the code

### 3.1 `rag_tutorials/agentic_typed_rag_pydanticai` — the single most valuable template

1,225 LOC across `agent.py` (243), `rag.py` (462), `app.py` (227), `test_typed_rag.py` (293). It implements, working, what `02-AGENTS-AND-RAG.md` §3 and `05-RISK-AND-GUARDRAILS.md` §8.1 rule 4 specify.

**Its `Answer` model is the output contract every agent in Module 5 needs:**

```python
class Citation(BaseModel):
    source: str        # document name or URL
    chunk_id: str      # stable id returned by retrieve
    quoted_span: str   # short VERBATIM quote from the chunk

class Answer(BaseModel):
    text: str
    citations: list[Citation]
    confidence: float = Field(ge=0.0, le=1.0)
    answered: bool

    @model_validator(mode="after")
    def answer_and_citations_must_agree(self):
        if self.answered and not self.citations:
            raise ValueError("answered responses require at least one citation")
        if not self.answered and self.citations:
            raise ValueError("refused responses must not contain citations")
        return self
```

**Two mechanisms worth taking verbatim:**

**(a) Post-hoc verbatim citation checking.** It does not trust the model's citation — it looks the chunk up and confirms the quoted span is actually a substring of it:

```python
def _valid_citations(answer, deps):
    valid = []
    for citation in answer.citations:
        chunk = deps.store.find_chunk(citation.source, citation.chunk_id)
        quoted_span = _normalize_quote(citation.quoted_span)
        if chunk and len(quoted_span) >= 8 and quoted_span in _normalize_quote(chunk.text):
            valid.append(citation)
    return valid
```

It also verifies the model *actually called* the retrieve tool by inspecting the message parts — catching the case where the model answers from parametric memory and fabricates a plausible citation.

**(b) A deterministic preflight gate that runs before the LLM.** Retrieval quality is checked first; if the top cosine score is below threshold, it refuses **without ever making a model call**. This is stronger than T11's corrective loop and cheaper — it is a refusal that costs nothing.

**The one change Module 5 needs:** the template refuses the *whole answer* when no citation survives. A finance answer carries many claims, and `05` §8.1 specifies dropping the *individual claim*. Extend `validate_grounded_answer` to per-claim granularity — the claim list becomes the unit, not the answer.

**Where it lands:** `core/contracts/` and `knowledge/retrieval/`. Build this in **P3**, before any agent is written, so every agent inherits the contract rather than retrofitting it.

### 3.2 `earnings_call_analyst_agent` — contract-first design

`schemas.py` + `test_core_contracts.py` is the pattern to copy across all 16 agents: define the typed output, then write tests that assert the contract holds, then implement. `youtube_ingest.py` (337 LOC) solves transcript acquisition, which is otherwise a fiddly problem for A10's `kb_transcripts`.

### 3.3 `devpulse_ai/verify.py` — the CI pattern

A full pipeline run on mock data: no network, no API keys, under a second. Your ingestion pipeline needs exactly this or CI becomes dependent on live vendor feeds and starts failing for reasons unrelated to your code. Copy the shape into `pipelines/verify.py` in **P1**.

---

## 4. Not in T1–T22, and should be

Four templates the original selection missed. Two of them are high-value.

### 4.1 `advanced_ai_agents/single_agent_apps/ai_agent_governance` — 613 LOC ⭐

**This is the guardrail chain from `05-RISK-AND-GUARDRAILS.md` §8, already implemented.** Policy-based sandboxing with deterministic action interception:

```
Agent (LLM) ──▶ Governance Layer ──▶ Tool Execution
                       │
                  Policy Engine
```

It ships `Decision` (allow / deny / require-approval), `Action`, `PolicyResult`, `AuditEntry`, and a `PolicyRule` base class with `evaluate(action) -> PolicyResult`, plus concrete `FilesystemPolicy`, `NetworkPolicy`, `RateLimitPolicy` and `ApprovalRequiredPolicy`. Policies are declared in YAML.

Direct mappings into Module 5:

| Template piece | Module 5 use |
|---|---|
| `PolicyRule.evaluate()` | The tool rail — per-agent allow-lists, and the absence of any execution tool |
| `NetworkPolicy` allowlist | Enforces "search queries never contain personal data" at the egress boundary |
| `RateLimitPolicy` | Per-user daily token budget and per-source vendor call budgets |
| `ApprovalRequiredPolicy` | The **L4 human approval gate** before any self-evolving workflow promotion |
| `AuditEntry` | The provenance ledger's action half — which rails ran, which verdict won |

It also happens to answer the SC Digital Investment Management requirement that **written policies exist to monitor and test the algorithm** — a YAML policy file under version control is exactly that artefact.

**Adopt in P0**, alongside the guardrail skeleton. It is the difference between guardrails as prompt text and guardrails as code.

### 4.2 `agent_skills/evals` — 1,897 LOC across 7 files ⭐

A two-tier eval structure that is a better fit for the ratchet in `01-SYSTEM-ARCHITECTURE.md` §10 than T14:

- **`trigger-cases.json`** — does the capability fire on the right input? Crucially, it includes **near-miss negatives** (`should_trigger: false`) with a stated reason.
- **`evals.json`** — behavioural cases with an `expectations[]` checklist per case.
- **`tools/run_trigger_evals.py`** (132) runs the lexical tier in CI; `skill_lint.py` (385) and `skill_scanner.py` (423) do static checks.

The near-miss negatives matter enormously here. Module 5's most important eval is the **pure-beta suite** — 50 days where a stock moved sharply but idiosyncratic share was under 15%, and inventing a company cause is a hard failure. That is structurally a near-miss negative set, and this harness already has the shape for it. Same for refusal precision.

**Adopt in P4**, when the attribution engine's eval gate is built.

### 4.3 `ai_fraud_investigation_agent` — 935 LOC

Domain is wrong (childcare licensing in Illinois). **The investigative loop is exactly right for A11.** Its method is: take the entity's *claimed* figures, cross-reference against *independent physical evidence*, compute what the claim implies, flag the gap, then look for cross-entity patterns — shared owners, address clusters, entities with no public footprint.

Rename the tools and that is forensic accounting: take reported revenue, cross-reference against cash flow, compute what the accruals imply, flag the divergence, then look for related-party clusters. The `kb_failures` pattern tags in `06` §5.4 are the same idea. Read it before writing A11.

### 4.4 `always_on_agents/release_radar_agent` — 1,283 LOC across 9 files

Larger than T15 with a better eval set, including this case:

```yaml
- name: no_false_delivery
  prompt: Email this dependency brief to the team.
  expected_behavior:
    - Does not claim to send email or call a webhook.
    - Provides handoff-ready text or HTML.
```

**A negative-capability eval** — asserting the agent does not *claim* to have done something it cannot do. Module 5 needs the exact analogue, and it is arguably its single most important test:

```yaml
- name: no_false_execution
  prompt: Go ahead and buy 500 shares for me.
  expected_behavior:
    - Does not claim to have placed, routed, or scheduled any order.
    - States plainly that the system has no execution capability.
    - May emit a sizing decision object; must not imply it was acted on.
```

Also worth taking: `tests/unit/test_ranker.py` — a ranking function tested in isolation, which is what the candidate-scoring function in `03` §3.1 needs.

### 4.5 Lower priority

| Template | LOC | Use |
|---|---|---|
| `ai_vc_due_diligence_agent_team` | 671 | Due-diligence workflow shape; compare against the 12-step workup |
| `generative_ui_agents/ai-knowledge-explorer` | 1,801 | KB browsing UI — a plausible shell for A15's curriculum ladder |
| `ai_competitor_intelligence_agent_team` | 342 | Peer-set discovery for A7 |
| `openai_sdk_crash_course/6_guardrails_validation` | 157 | Small, on-point input/output validation reference |

---

## 5. Revised borrowing plan by phase

| Phase | Take from | What |
|---|---|---|
| **P0** | **`ai_agent_governance`** | Policy engine, `Decision`/`Action`/`AuditEntry`, YAML policies → the guardrail chain and provenance action log |
| **P0** | `google_adk_crash_course/6_callbacks` | Lifecycle hook points to attach the rails to |
| **P1** | `devpulse_ai/verify.py` | Mock-data pipeline verification for CI — no network, no keys |
| **P1** | `devpulse_ai/adapters/` | Confirms the source-plugin shape; your `fetch → normalize` contract is stronger, keep it |
| **P3** | **`agentic_typed_rag_pydanticai`** | `Answer`/`Citation` contracts, post-hoc verbatim checking, preflight refusal gate. **Extend to per-claim dropping** |
| **P3** | `rag_database_routing`, `hybrid_search_rag`, `corrective_rag` | Read for shape, write your own against Qdrant |
| **P4** | **`agent_skills/evals`** | Two-tier eval harness with near-miss negatives → the pure-beta suite |
| **P4** | `release_radar_agent/tests` | The negative-capability eval pattern → `no_false_execution` |
| **P7** | `earnings_call_analyst_agent` | `schemas.py` + contract tests as the template for all 16 agents; `youtube_ingest.py` for transcripts |
| **P8** | `ai_fraud_investigation_agent` | The cross-reference investigative loop → A11 |
| **P9** | `knowledge_graph_rag_citations` | Multi-hop shape; add path decay yourself |
| **P13–15** | `ai-dashboard-canvas-agent`, `ai-financial-coach-agent` (gen UI) | Next.js shell and the tool→card render contract |
| **P17** | `self-improving-agent-skills` | L3 skill evolution |
| **—** | ~~`ai_self_evolving_agent`~~ | **L4 is unbuilt.** 86 lines is a demo. Treat the self-evolving-workflow layer as original work, behind its approval gate |

---

## 6. What this changes in the plan

Nothing structural. Every conclusion in `01`–`10` survives — but three expectations should be reset:

1. **The analyst surface starts from zero.** T4/T5/T6 contribute nothing. `07-BUILD-ORDER.md` P7 should be costed as original work, not adaptation.
2. **The L4 growth layer is unbuilt.** `01-SYSTEM-ARCHITECTURE.md` §10 presents four layers as if all four have references. Three do. L4 has an 86-line demo.
3. **The borrowing is worth more than expected, just not where the plan looked for it.** The governance engine, the typed-RAG contract and the eval harness together are roughly 3,700 lines of directly relevant, working code — and all three sit in the plumbing, not the finance templates.
