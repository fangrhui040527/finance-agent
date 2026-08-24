# finance-agent

**FinPlanet — Module 5: The Analyst Mind.**
A multi-agent, multi-market equity research system that explains *why* a price moved, analyses a company the way an analyst actually does, and enforces risk limits in code rather than in prompts.

> **Not financial advice.** Candidacy bands, calibrated probabilities, attributions and sizing constraints with evidence chains. No recommendations, no execution.

## The implementation plan

Start at **[`docs/README.md`](docs/README.md)**.

| Doc | Covers |
|---|---|
| [01 System architecture](docs/01-SYSTEM-ARCHITECTURE.md) | Layers, agent org chart, request lifecycle, deployment, data model, tier routing |
| [02 Agents and their RAG](docs/02-AGENTS-AND-RAG.md) | 16 agents, 9 vector collections, per-agent retrieval configs and evals |
| [03 Why did it move?](docs/03-WHY-IT-MOVED.md) | Factor decomposition, catalyst scoring, 5-year return decomposition |
| [04 The analyst method](docs/04-ANALYST-METHOD.md) | The 12-step company workup, valuation by sector archetype, the trader's layer |
| [05 Risk and guardrails](docs/05-RISK-AND-GUARDRAILS.md) | Capital waterfall, five sizing caps, concentration, drawdown governance, guardrail chain |
| [06 Data and knowledge sources](docs/06-DATA-AND-KNOWLEDGE-SOURCES.md) | Market coverage ladder, adapter contract, ingestion, KB construction |
| [07 Build order](docs/07-BUILD-ORDER.md) | Phases, definitions of done, what to cut |
| [08 Cost breakdown](docs/08-COST-BREAKDOWN.md) | Three tiers, token math, break-even analysis, cost controls |
| [09 Research sources](docs/09-RESEARCH-SOURCES.md) | Every source behind every design decision |
| [10 Repository references](docs/10-REPOSITORY-REFERENCES.md) | Every GitHub repo used, verified licences, obligation map |
| [11 Template audit](docs/11-TEMPLATE-AUDIT.md) | What `awesome-llm-apps` actually contains, measured; what to adopt and what to drop |
| [12 Code reference map](docs/12-CODE-REFERENCE-MAP.md) | Exact file:line pointers into each upstream repo, pinned to commit SHAs |
| [13 The self-learning loop](docs/13-SELF-LEARNING-LOOP.md) | Hermes-agent studied; the assembled A14 loop and its provenance gate |

## Status

**P0 shipped.** Skeleton, inference choke point, guardrail chain, provenance ledger.
Phases P1–P19 remain — see [`docs/07-BUILD-ORDER.md`](docs/07-BUILD-ORDER.md).

```bash
make install && make test    # 52 tests
make verify                  # full pipeline on mock data, no keys, <1s
make up                      # postgres+timescale · qdrant · neo4j · redis · minio
```

### What P0 enforces

| Guarantee | Where | Test |
|---|---|---|
| Callers cannot pick a model tier — it derives from `TaskClass` | `core/llm/tiers.py` | `test_tier_routing.py` |
| No order-placement code exists anywhere in the repo | `core/guardrails/policy.py` + repo grep | `test_no_execution_anywhere.py` |
| All five rails run on every request; no bypass path | `core/guardrails/chain.py` | `test_guardrail_chain.py` |
| A claim without a verified verbatim citation is dropped individually | `core/contracts/answer.py` | `test_answer_contract.py` |
| The ledger is append-only — UPDATE and DELETE abort | `core/provenance/ledger.py` | `test_provenance.py` |
| Human-authored knowledge is never agent-editable | `core/contracts/provenance_marker.py` | `test_provenance.py` |
| Budget exhaustion raises; it never downgrades silently | `core/llm/client.py` | `test_inference_client.py` |
| Telemetry sits beside content, never inside it | `core/provenance/sidecar.py` | `test_sidecar.py` |
