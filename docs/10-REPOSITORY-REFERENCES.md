# 10 — Repository references

Every GitHub repository behind this plan, with its verified licence and what that licence actually requires of you.

**Provenance, stated honestly.** No repository source was cloned or read while writing this plan. The references below come from three places: repos named in the project's own earlier docs (`BUILD_PLAN.md`, `RESEARCH.md`), repos found through web research for this module, and canonical tooling projects named in the stack. Every licence and repo name in the tables below was verified against the repository page on **24 August 2026**; licences change, so re-check before you depend on one.

---

## 1. Foundations — carried in from the earlier plan

These three came from `BUILD_PLAN.md` and `RESEARCH.md`. They are the load-bearing external dependencies.

| Repository | Role | Licence |
|---|---|---|
| [Shubhamsaboo/awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) | Source of templates T1–T22. "100+ AI Agents, Agent Skills and RAG Apps" | **Apache-2.0** |
| [koala73/worldmonitor](https://github.com/koala73/worldmonitor) | Curated news layer, Country Instability Index, market composite, MCP server | **AGPL-3.0-only** |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | "Open Data Platform for analysts, quants and AI agents" — 100+ providers behind one interface. Effectively the L2 data-plugin layer, already written | **AGPL-3.0** |

---

## 2. Multi-agent LLM finance — the design ancestors

Research prototypes with companion code. Read for architecture, not for reuse.

| Repository | What it contributed to this design | Licence |
|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | "Multi-Agents LLM Financial Trading Framework." Its **bull/bear researcher debate is the direct ancestor of the A10 Thesis / A11 Red Team split**; its persistent decision-memory-with-realised-return-reflection informed A14 | **Apache-2.0** |
| [pipiku915/FinMem-LLM-StockTrading](https://github.com/pipiku915/FinMem-LLM-StockTrading) | Profiling / layered memory / decision modules. Shaped the working-memory vs long-term-lesson-store separation | **MIT** |
| [adlnlp/FinLLMs](https://github.com/adlnlp/FinLLMs) | Benchmarks and datasets accompanying the *Large Language Models in Finance* survey | **No licence stated** — see §6.4 |
| [asinghcsu/AgenticRAG-Survey](https://github.com/asinghcsu/AgenticRAG-Survey) | Companion to the agentic RAG survey. Planner / executor / critic decomposition and verification agents | **No licence stated** — see §6.4 |

**What none of them have:** an attribution engine. TradingAgents and FinMem both terminate in an action; neither asks *why* the price moved. `03-WHY-IT-MOVED.md` is built from the academic literature, not from any of this code.

---

## 3. Financial NLP — the local-first tier

| Repository | Role | Licence |
|---|---|---|
| [ProsusAI/finBERT](https://github.com/ProsusAI/finBERT) | "Financial Sentiment Analysis with BERT." The local model that runs **before** any paid API call in A4. Roughly four in five articles never reach a hosted model because of it | **Apache-2.0** |
| [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | Open-source financial LLMs, LoRA-finetuned, deployable on consumer hardware. The escalation-ladder alternative if FinBERT proves too coarse | **MIT** (repo notes codes are shared for academic purposes) |

---

## 4. Backtest frameworks — three jobs, three tools

Named in `RESEARCH.md` §6 and reused in `07-BUILD-ORDER.md` P12. **Two of these carry a Commons Clause restriction that the earlier docs did not flag.**

| Repository | Job in this project | Licence |
|---|---|---|
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Phase-1 triage — "does this even have alpha?" Fastest loop in Python. Never draw a conclusion from it | ⚠️ **Apache-2.0 with Commons Clause** ("fair-code") |
| [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | The cross-sectional factor work. Its Pipeline API is the only one designed for ranking a universe | **Apache-2.0** |
| [edtechre/pybroker](https://github.com/edtechre/pybroker) | Alternative to Zipline if the ML model becomes the centre of gravity — walk-forward and bootstrapped metrics pre-wired | ⚠️ **Apache-2.0 with Commons Clause** |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | "Production-grade Rust-native trading engine." Only if fills ever become the binding question (Bursa small caps) | **LGPL-3.0-only** |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Managed end-to-end alternative if you would rather rent the infrastructure than build it. Ecosystem lock-in | **Apache-2.0** |
| [mementum/backtrader](https://github.com/mementum/backtrader) | **Listed as a do-not-use.** Mature with a huge tutorial corpus, but development is effectively frozen — do not start a 2026 project on it | **GPL-3.0** |

---

## 5. Infrastructure

| Repository | Role | Licence |
|---|---|---|
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | The nine vector collections in `02-AGENTS-AND-RAG.md` | **Apache-2.0** |
| [timescale/timescaledb](https://github.com/timescale/timescaledb) | Prices, `known_at` fundamentals, FX, macro, factor returns. A Postgres extension, so one dialect across ledger and market data | **Dual: Apache-2.0 / Timescale License (TSL)** — some features are TSL-only |
| [neo4j/neo4j](https://github.com/neo4j/neo4j) | Entity and supply-chain graph for A7's multi-hop traversal | **GPL-3.0** (Community Edition; Enterprise is commercial) |
| [tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) | The annotated price chart in `03-WHY-IT-MOVED.md` §5 | **Apache-2.0 — with an attribution requirement.** See §6.3 |

---

## 6. What the licences actually require

### 6.1 If this stays a personal tool

You run it for your own money, you never expose it to another user, you never sell it.

**Then essentially none of the obligations below bite.** AGPL's network clause triggers on *providing the software to users over a network*. The Commons Clause triggers on *selling*. GPL and LGPL trigger on *distribution*. Running software privately is not any of those things.

The one exception is §6.3 — the attribution requirement applies to any page you publish, including one only you look at, if it is served over a network.

### 6.2 If it ever becomes a product

Then the licence conversation is unavoidable, and it should happen **before** the codebase entangles these dependencies.

| Licence | Repos | What it requires of a product |
|---|---|---|
| **AGPL-3.0** | worldmonitor, OpenBB | Consuming a **hosted API over the network is normal API use** and triggers nothing. **Self-hosting a modified instance and exposing it to users triggers source-availability obligations** — your users must be able to get the corresponding source. Keep any fork in a **separate repository** so the boundary is unambiguous |
| **Commons Clause** (on top of Apache-2.0) | vectorbt, pybroker | You may use it freely, including commercially — but **you may not sell a product or service whose value derives substantially from this software**. A backtest engine inside a larger product is usually fine; a hosted backtesting service is the case the clause exists to prevent. This is *not* an OSI-approved open-source licence, and "Apache-2.0" alone in a dependency audit is a misreading |
| **GPL-3.0** | backtrader, neo4j (Community) | Distributing a work derived from it means distributing that work under GPL-3.0. For Neo4j specifically, running it as a **separate service you talk to over Bolt** is the ordinary arrangement and is not linking; embedding the database in your binary is a different question. Enterprise Edition is a commercial licence |
| **LGPL-3.0** | nautilus_trader | **Linking is permitted** without your code becoming LGPL, provided users can replace the library. Modifying nautilus itself is what triggers copyleft on those modifications |
| **Timescale (TSL)** | timescaledb | The Apache-2.0 core is unrestricted. **TSL-licensed features may not be offered as a database-as-a-service to third parties.** Irrelevant for a personal tool; decisive if you ever host for others |
| **Apache-2.0 / MIT** | awesome-llm-apps, TradingAgents, FinMem, FinGPT, finBERT, zipline-reloaded, Lean, qdrant, lightweight-charts | Keep the notices. Fork, ship, sell |

This is a summary of what the licence texts say, not legal advice. Get advice before commercialising anything that touches the AGPL or Commons Clause rows.

### 6.3 The attribution requirement — a real build task

`tradingview/lightweight-charts` is Apache-2.0, **but its terms additionally require** that you add the attribution notice from the `NOTICE` file and a link to `https://www.tradingview.com/` on the page of your site or app that users see. The library ships an `attributionLogo` chart option that satisfies this by rendering the link on the chart itself.

**Action:** enable `attributionLogo` when the annotated chart is built in P15. It is one option flag, and forgetting it is a licence breach on the most visible screen in the product.

Note also the separation the earlier research flagged: TradingView's *Advanced Charts* and *Trading Platform* libraries are explicitly **not** offered for personal, hobby, study or testing use. Lightweight Charts is the one you can actually use.

### 6.4 Repositories with no stated licence

`adlnlp/FinLLMs` and `asinghcsu/AgenticRAG-Survey` publish no `LICENSE` file. Under default copyright, **no licence means all rights reserved** — you may read them, and you may not copy code or content out of them.

**Treat both as reading material only.** Both are survey companions, so this costs nothing: the value is in the papers they index, which carry their own individual licences and are cited by DOI in `09-RESEARCH-SOURCES.md`.

---

## 7. What has no upstream at all

Worth stating plainly, because it is where the build effort actually goes. None of the repositories above contains:

| Component | Where it is specified | Why nothing exists to fork |
|---|---|---|
| **Attribution engine** | `03-WHY-IT-MOVED.md` | Every multi-agent finance framework surveyed terminates in an action. None decomposes a move before naming a cause |
| **`event_base_rates`** | `03-WHY-IT-MOVED.md` §6 | Historical abnormal-return distributions by `event_type × market × cap_band × surprise × regime`. No vendor sells this and no repo contains it |
| **`kb_failures`** | `06-DATA-AND-KNOWLEDGE-SOURCES.md` §5.4 | A blowup library retrieved by *structural pattern* rather than by company or sector |
| **Bursa point-in-time store** | `06` §3.2 | No vendor offers `known_at` for Bursa. Built from announcement feeds |
| **Sizing engine with five caps** | `05-RISK-AND-GUARDRAILS.md` §3 | Position-sizing code exists everywhere; a waterfall joined to a personal planner with caps enforced in the type system does not |
| **Cross-market factor library** | `04-ANALYST-METHOD.md` | Zipline's Pipeline gives you the machinery, not the factors, and not the accounting normalisation across regimes |

Roughly two-thirds of that work is data plumbing rather than modelling. Budget accordingly.

---

## 8. Verification log

Checked against the repository page on 24 August 2026. Exact casing matters for two of them — `TauricResearch/TradingAgents` and `pipiku915/FinMem-LLM-StockTrading` are commonly miswritten in lowercase.

| Repository | Name verified | Licence verified |
|---|---|---|
| Shubhamsaboo/awesome-llm-apps | ✓ | ✓ Apache-2.0 |
| koala73/worldmonitor | ✓ | ✓ AGPL-3.0-only |
| OpenBB-finance/OpenBB | ✓ | ✓ AGPLv3 |
| TauricResearch/TradingAgents | ✓ | ✓ Apache-2.0 |
| pipiku915/FinMem-LLM-StockTrading | ✓ | ✓ MIT |
| adlnlp/FinLLMs | ✓ | ✓ none stated |
| asinghcsu/AgenticRAG-Survey | ✓ | ✓ none stated |
| ProsusAI/finBERT | ✓ | ✓ Apache-2.0 |
| AI4Finance-Foundation/FinGPT | ✓ | ✓ MIT |
| polakowo/vectorbt | ✓ | ✓ Apache-2.0 + Commons Clause |
| stefan-jansen/zipline-reloaded | ✓ | ✓ Apache-2.0 |
| edtechre/pybroker | ✓ | ✓ Apache-2.0 + Commons Clause |
| nautechsystems/nautilus_trader | ✓ | ✓ LGPL-3.0-only |
| QuantConnect/Lean | ✓ | ✓ Apache-2.0 |
| mementum/backtrader | ✓ | ✓ GPL-3.0 |
| qdrant/qdrant | ✓ | ✓ Apache-2.0 |
| timescale/timescaledb | ✓ | ✓ Apache-2.0 / TSL dual |
| neo4j/neo4j | ✓ | ✓ GPL-3.0 Community |
| tradingview/lightweight-charts | ✓ | ✓ Apache-2.0 + attribution |
