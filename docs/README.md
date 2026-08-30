# FinPlanet — Module 5: The Analyst Mind

Implementation plan for a multi-agent, multi-market equity research system that explains **why** a price moved, analyses a company the way an analyst actually does, and refuses to let you put all the eggs in one basket.

> **Not financial advice.** This system produces candidacy bands, calibrated probabilities, attributions and sizing constraints with full evidence chains. It does not recommend transactions, does not execute, and is built for its author's own use.

---

## What this module adds to the existing plan

| Existing doc | Answers |
|---|---|
| `BUILD_PLAN.md` | What the system is built from |
| `DECISION_ENGINE.md` | What to buy, when, how much |
| `KNOWLEDGE_BASE.md` | What the system knows |
| **This module** | **How the system reasons about a company, and why it believes a price moved** |

Three things are genuinely new here:

1. **An attribution engine** (`03`) that splits every price move into market, sector, style, currency and idiosyncratic components *before* naming a cause — and reports what it cannot explain. No component of the reference framework does this, and it is the part of the system with the strongest evidence behind it.
2. **Sixteen agents, each with a private knowledge base** (`02`) — nine vector collections, a graph, six timeseries stores and a tenant-private SQL store, each with its own chunking, retrieval config, refresh cadence, trust tier and eval suite.
3. **Concentration enforced in the type system** (`05`) rather than in a prompt. A `SizingDecision` that breaches a cap cannot be constructed.

---

## Read in this order

| # | Document | Answers |
|---|---|---|
| **01** | [System architecture](01-SYSTEM-ARCHITECTURE.md) | Layer diagram, agent org chart, request lifecycle, deployment topology, data model, model tier routing, growth surface |
| **02** | [Agents and their private knowledge bases](02-AGENTS-AND-RAG.md) | All sixteen agents: job, corpus, store, source, chunking, retrieval, tools, output contract, guardrails, eval — plus the shared retrieval pipeline and freshness SLAs |
| **03** | [Why did it move?](03-WHY-IT-MOVED.md) | Factor decomposition, abnormal returns, catalyst scoring, the `no_identified_catalyst` verdict, five-year return decomposition, the base-rate table |
| **04** | [The analyst method, codified](04-ANALYST-METHOD.md) | The 12-step workup, reading ten years of history, capital allocation, valuation method by sector archetype, the technology lens, the trader's layer, the thesis memo |
| **05** | [Risk management and guardrails](05-RISK-AND-GUARDRAILS.md) | Capital waterfall, five sizing caps, concentration made mechanical, drawdown governance, behavioural guardrails, kill switches, the five-rail guardrail chain, model-risk policy |
| **06** | [Data and knowledge sources](06-DATA-AND-KNOWLEDGE-SOURCES.md) | Market coverage ladder, the market adapter contract, source register, ingestion architecture, how each knowledge base is built, licence boundaries |
| **07** | [Build order](07-BUILD-ORDER.md) | Phases with definitions of done, what to cut if time runs out, done-ness criteria for the whole module |
| **08** | [Cost breakdown](08-COST-BREAKDOWN.md) | Three tiers with the token math shown, break-even portfolio size, cost controls ranked by impact, recommended starting position |
| **09** | [Research sources](09-RESEARCH-SOURCES.md) | Every source behind every design decision, organised by the decision it supports |
| **10** | [Repository references](10-REPOSITORY-REFERENCES.md) | Every GitHub repo behind the plan, verified licence, what each licence requires, and what has no upstream at all |
| **11** | [Template audit](11-TEMPLATE-AUDIT.md) | `awesome-llm-apps` measured rather than assumed: what T1-T22 actually contain, what to adopt, and four templates the original selection missed |
| **12** | [Code reference map](12-CODE-REFERENCE-MAP.md) | Where to look, file and line, for each piece you build — pinned to commit SHAs so line numbers stay valid |
| **13** | [The self-learning loop](13-SELF-LEARNING-LOOP.md) | What `hermes-agent` does, which four mechanisms land here, and the one bias to invert |
| **14** | [Operations runbook](14-OPERATIONS-RUNBOOK.md) | What to do next, what to monitor, and what should make you stop |
| **15** | [MCP setup](15-MCP-SETUP.md) | Serve the system over stdio so the reasoning runs on your Claude session |
| **16** | [Tracing and anatomy](16-TRACING-AND-ANATOMY.md) | Every prompt, rail decision and dropped claim, recorded end to end |
| **17** | [Knowledge graph](17-KNOWLEDGE-GRAPH.md) | Multi-hop exposure end to end: confidence, validity, the citation seam, the deterministic build, the review surface, and the codebase graph |
| [User guide](user-guide.html) | Install, every command, and the cadence for using it — open in a browser |
| [Pipeline diagrams](pipeline.html) | Six diagrams tracing data from a wire story to a position size — open in a browser |
| [World source register](world-sources.html) | Every source knowledge can come in from, worldwide — coverage, keys, rate limits, licences — ordered by trust tier |

---

## The ten commitments

Everything in these documents reduces to these. If a design choice conflicts with one of them, the design choice is wrong.

1. **Components before narrative.** A move is decomposed statistically before any cause is named. A market-driven move never gets a company story.
2. **No execution, ever.** There is no order-placement code in the repository. Not disabled — absent.
3. **No point forecasts.** Distributions and relative rank. Never "RM 7.40 by December".
4. **No advice verbs.** Bands, not buy/sell. Enforced by an output-rail classifier.
5. **Citation or drop.** A claim without a chunk ID or a row reference is removed before the response ships.
6. **Refusal beats invention.** Refusal is a logged, non-penalised outcome. Optimise refusal *precision*, not refusal rate.
7. **Point-in-time or it's fiction.** Every fundamental carries `known_at`. Every universe includes the dead.
8. **Caps in code, not prompts.** The emergency floor is unbreachable; concentration limits raise rather than warn.
9. **Every agent owns its knowledge.** No global undifferentiated index.
10. **Base rates carry their sample size.** Always. A median from six observations is shown with the six.

---

## The honest expectation

- The realistic long-horizon prize is a **low single-digit annualised excess return** over the local index, after costs, with multi-year stretches of underperformance. That is a good outcome, not a disappointing one.
- Short-horizon hit rates of **53–56%** are the ceiling. Anything a backtest shows above ~60% is leakage until proven otherwise.
- **Most of the value is not prediction.** It is position sizing, risk control, cost discipline, and not selling at the bottom. The sizing engine will contribute more to the outcome than the alpha engine.
- If the model beats none of its three benchmarks after costs, **the correct product is an index tracker plus the planner** — and this system is built to be able to say so.
