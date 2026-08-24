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

## Status

Planning complete. No implementation code yet — see [`docs/07-BUILD-ORDER.md`](docs/07-BUILD-ORDER.md) for the phase sequence.
