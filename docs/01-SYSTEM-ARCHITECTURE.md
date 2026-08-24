# Module 5 — The Analyst Mind

## System architecture for a multi-agent, multi-market equity research system

> **Status:** implementation plan, v1.0 · 24 Aug 2026
> **Relationship to existing docs:** this is the module that sits between `BUILD_PLAN.md` (infrastructure), `DECISION_ENGINE.md` (what to buy, how much) and `KNOWLEDGE_BASE.md` (what the system knows). Those three answer *what the system is built from*. This one answers **how the system reasons about a company, and why it believes a price moved.**
> **Not financial advice.** Nothing here recommends a transaction. The system emits candidacy bands, calibrated probabilities, attributions and constraints — with evidence chains — and never places an order.

---

## 0. What this module has to deliver

Six requirements, stated as acceptance criteria rather than aspirations.

| # | Requirement | Acceptance criterion |
|---|---|---|
| R1 | Understand how a company is actually analysed | The system reproduces a full analyst workup — business model, segment history, earnings quality, capital allocation, balance sheet, valuation, competitive position — with every number traced to a filing line item and an `as_of` |
| R2 | Work internationally, not just one country | A new exchange is onboarded by filling one adapter contract (§6 of `06-DATA-AND-KNOWLEDGE-SOURCES.md`) and passing its eval suite. No orchestrator code changes. Accounting regime, session calendar, lot size, fee schedule and disclosure regime are per-market data, never hardcoded |
| R3 | News within a five-year window | Rolling 5-year news corpus with hot/warm/cold tiers; every article carries `published_at`, entity links and five sentiment dimensions; retrieval is recency-filtered, not recency-hinted |
| R4 | Explain **why** a price moved — statistically, not narratively | Every explanation splits the move into market / sector / style / FX / idiosyncratic components **before** naming a cause, and reports an explicit *unexplained share*. A move that is 90% index beta is never given a company story |
| R5 | Don't put all the eggs in one basket | Concentration limits are enforced in code, in the sizing engine, as a hard block — not as prompt guidance. A decision object that breaches a cap cannot be constructed |
| R6 | Every agent has its own knowledge | Each agent owns a named corpus with its own store, chunking, refresh cadence, trust tier and eval suite. No agent retrieves from a global undifferentiated index |

---

## 1. The one-paragraph description

A supervisor routes a question to a team of specialist agents. Each specialist owns a private knowledge base and a private tool set, and answers only within its competence. Their typed, cited outputs converge on three synthesis agents — one that decomposes price moves into their statistical parts, one that writes the thesis, and one whose only job is to attack it. A portfolio pair then converts the surviving thesis into exposure constraints, and a learning pair scores what actually happened and writes the lesson back into the knowledge bases. Nothing reaches the user without passing a deterministic guardrail chain, and nothing reaches a broker at all.

---

## 2. Layer diagram

```mermaid
flowchart TB
    subgraph CLIENT["CLIENT SURFACE"]
        UI["Next.js · annotated charts · cards"]
        BRIEF["Scheduled brief · email / TG"]
    end

    subgraph GATE["GATEWAY + GUARDRAIL CHAIN"]
        AUTH["authn/z · tenant scope · rate limit"]
        GIN["Input rails: PII strip · injection scan · scope check"]
        GOUT["Output rails: citation check · numeric cross-check<br/>advice-language block · staleness gate"]
    end

    subgraph ORCH["ORCHESTRATION"]
        SUP["A0 Supervisor · plan + route + budget"]
        REG["Capability registry (YAML)"]
        MEM["Shared memory: profile · constraints · decision journal"]
    end

    subgraph EVID["EVIDENCE AGENTS — each owns a private KB"]
        A1["A1 Fundamentals"]
        A2["A2 Valuation"]
        A3["A3 Price &amp; Technical"]
        A4["A4 News &amp; Narrative"]
        A5["A5 Catalyst &amp; Events"]
        A6["A6 Macro &amp; Regime"]
        A7["A7 Sector &amp; Technology"]
        A8["A8 Ownership &amp; Flow"]
    end

    subgraph SYN["SYNTHESIS"]
        A9["A9 Attribution — why it moved"]
        A10["A10 Thesis"]
        A11["A11 Red Team"]
    end

    subgraph PORT["PORTFOLIO"]
        A12["A12 Risk &amp; Portfolio"]
        A13["A13 Sizing"]
    end

    subgraph LEARN["LEARNING"]
        A14["A14 Reflection &amp; Calibration"]
        A15["A15 Teacher — KB-Craft"]
    end

    subgraph KNOW["KNOWLEDGE PLANE"]
        VEC["Qdrant — 9 named collections"]
        GRAPH["Neo4j — entity / supply-chain graph"]
        TS["TimescaleDB — prices, fundamentals(known_at), FX, macro"]
        SQL["Postgres — ledger, holdings, goals, journal"]
        OBJ["S3/MinIO — raw filings, transcripts"]
    end

    subgraph MODEL["INFERENCE"]
        TIER["Tier router: reason / balanced / cheap / embed"]
        LOCAL["Local: FinBERT · cross-encoder reranker"]
    end

    UI --> AUTH
    BRIEF --> AUTH
    AUTH --> GIN --> SUP
    SUP <--> REG
    SUP <--> MEM
    SUP --> EVID
    EVID --> SYN
    SYN --> PORT
    PORT --> GOUT
    SYN --> GOUT
    GOUT --> UI
    PORT --> A14
    A14 --> MEM
    A14 -.writes lessons.-> KNOW
    A15 --> GOUT
    EVID <--> KNOW
    SYN <--> KNOW
    PORT <--> KNOW
    EVID <--> MODEL
    SYN <--> MODEL
    MODEL --- LOCAL
```

**Read the diagram as three assertions.**
1. Evidence agents never talk to each other. They talk to their own knowledge and emit typed facts. This is what stops the classic multi-agent failure where two agents synthesise a claim that neither's evidence supports.
2. Every path to the user passes `GOUT`. There is no debug path, no "quick look" path, no admin path that skips it.
3. The learning layer writes back into the knowledge plane. That is the only loop in the diagram, and it is deliberately the slowest one.

---

## 3. Agent org chart

```mermaid
flowchart TD
    Q(["Question / scheduled trigger"]) --> A0

    A0["<b>A0 SUPERVISOR</b><br/>classifies intent · builds plan<br/>allocates token budget · picks model tier"]

    A0 -->|"company workup"| FUND
    A0 -->|"why did it move"| MOVE
    A0 -->|"screen / rank"| SCREEN
    A0 -->|"portfolio question"| PORTQ
    A0 -->|"teach me"| A15
    A0 -->|"insufficient scope"| REFUSE(["structured refusal"])

    subgraph FUND["COMPANY WORKUP"]
        direction LR
        A1["A1 Fundamentals"] --> A2["A2 Valuation"]
        A7["A7 Sector &amp; Tech"] --> A2
        A8["A8 Ownership &amp; Flow"] --> A2
    end

    subgraph MOVE["MOVE EXPLANATION"]
        direction LR
        A3["A3 Price &amp; Technical"] --> A9["A9 Attribution"]
        A5["A5 Catalyst &amp; Events"] --> A9
        A4["A4 News &amp; Narrative"] --> A9
        A6["A6 Macro &amp; Regime"] --> A9
    end

    subgraph SCREEN["CROSS-SECTIONAL SCREEN"]
        direction LR
        FACT["Factor library"] --> RANK["Ranked candidates"]
        A6 --> RANK
    end

    subgraph PORTQ["PORTFOLIO"]
        direction LR
        A12["A12 Risk &amp; Portfolio"] --> A13["A13 Sizing"]
    end

    FUND --> A10
    MOVE --> A10
    SCREEN --> A10
    A10["<b>A10 THESIS</b><br/>drivers · falsifiable breakers · horizon"]
    A10 --> A11["<b>A11 RED TEAM</b><br/>bear case · disconfirming evidence<br/>failure-case analogues"]
    A11 -->|"thesis survives"| A12
    A11 -->|"thesis broken"| REFUSE
    A13 --> DEC(["DecisionObject — typed, cited, capped"])
    DEC --> A14["<b>A14 REFLECTION</b><br/>outcome scoring · calibration · lessons"]
    A14 -.->|"lesson store"| A0
```

**The Red Team gate is not decoration.** A10 and A11 use *different* model tiers and *different* retrieval instructions: A10 retrieves supporting evidence, A11 is explicitly prompted and tooled to retrieve contradicting evidence and structurally similar failures from the failure-case library (§2.11 of `02-AGENTS-AND-RAG.md`). A thesis that A11 breaks does not proceed to sizing. Measured over time, the fraction of theses A11 kills is a health metric: if it drops near zero, A11 has been captured and needs re-grounding.

---

## 4. Mapping to the reference framework you supplied

The uploaded diagram (multi-modal multi-agent prediction framework: *Summarizer → Analyst → Prediction Agent → Reward Agent → Reflection Agent*) is the correct skeleton. Here is the mapping, and — more importantly — the three places this design deliberately departs from it.

| Reference component | Here | Change |
|---|---|---|
| LLM Summarizer Agent (news) | **A4 News & Narrative** | Extracts five features (relevance, polarity, intensity, uncertainty, forwardness) rather than a summary. Summaries lose the dimensions that carry the signal |
| LLM Analyst Agent (candlestick chart) | **A3 Price & Technical** | Reads numeric OHLCV + indicator series, not chart *images*. Vision on candlesticks adds cost and a hallucination surface for information already available numerically. Charts are rendered *for the human*, not for the model |
| Prediction Agent inputs | **A0 plan → evidence bundle** | Same idea, but each input is a typed, cited object with an `as_of`, not free text |
| Prediction Agent → `[BUY / SELL / HOLD]`, position size 1–10 | **A10 Thesis → A13 Sizing** | **No verbs.** Bands (`accumulate / hold / trim / exit / no_signal`) and a size derived from five hard caps, not a 1–10 vibe scale. The band and the size are computed by different components on purpose |
| Reward Agent → *Trade Execution Tool* | **A14 Reflection & Calibration** | **The execution tool is removed.** The system is a one-way door: it never places an order. "Reward" is computed from a paper ledger and realised prices |
| Reflection Agent → short/medium-term reflections back into prompt | **A14 → lesson store + calibration panel** | Reflections are written to a queryable store with outcome labels and retrieved by relevance, not stuffed into every prompt. Prompt-stuffed reflections silently blow the context budget and bias toward recency |

**The three departures, stated plainly:**

1. **No execution.** A model that can place orders converts a calibration bug into a financial loss with no human in between.
2. **No point predictions.** The reference emits an action and a size. This system emits a distribution, a band and a binding constraint. The honest ceiling on short-horizon directional accuracy is roughly 53–56%; a UI that shows "BUY, size 8/10" implies a precision that does not exist.
3. **Attribution before prediction.** The reference framework has no component that asks *why the price moved*. That is the component with the best evidence behind it and the lowest hallucination risk, and here it is a first-class agent (A9) that runs whether or not any forecast is requested.

---

## 5. Request lifecycle — full sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant G as Guardrail (in)
    participant S as A0 Supervisor
    participant E as Evidence agents
    participant K as Knowledge plane
    participant W as Web validator
    participant N as A9 Attribution
    participant T as A10 Thesis
    participant R as A11 Red Team
    participant P as A12/A13 Risk + Sizing
    participant O as Guardrail (out)
    participant L as Provenance ledger

    U->>G: "Why is Maybank down 6% this month, and should my position change?"
    G->>G: strip PII from any outbound query · injection scan
    G->>S: sanitised intent + tenant scope
    S->>S: plan = [attribution, fundamentals, catalyst, news, macro, portfolio]
    S->>S: budget = 180k tok · tier = balanced (reason for A11 only)
    par Evidence gathering
        S->>E: A3 price series + vol regime
        S->>E: A1 latest filing + known_at facts
        S->>E: A5 event candidates in window
        S->>E: A4 news 5-dim features, 5y corpus
        S->>E: A6 regime label + rate path
    end
    E->>K: per-agent retrieval (own collection only)
    K-->>E: chunks + rows, each with as_of
    E->>E: grade relevance · freshness · sufficiency
    alt insufficient after 2 rewrites
        E->>W: web search (verifier of last resort)
        W->>W: numeric cross-check vs Timescale · corroborate ≥2 domains · climb to primary source
        W-->>E: validated claim OR dropped claim + logged disagreement
    end
    E-->>S: typed facts (cited)
    S->>N: attribution request
    N->>N: factor regression → market/sector/style/FX/idio split
    N->>N: match catalysts to residual only · score · rank
    N-->>S: MoveExplanation{components[], candidates[], unexplained_share}
    S->>T: build thesis from evidence + attribution
    T-->>R: thesis + drivers + breakers
    R->>K: retrieve contradicting evidence + failure analogues
    R-->>S: verdict {survives | broken} + attack list
    alt thesis broken
        S->>O: structured refusal + reason
    else survives
        S->>P: exposure question
        P->>P: waterfall → 5 caps → vol target → concentration hard block
        P-->>S: SizingDecision{binding_cap, target, breakers, stops}
    end
    S->>O: candidate response
    O->>O: every claim has chunk_id? · numbers match source? · no advice verbs? · as_of within SLA?
    O->>L: append provenance rows (sources, model, tokens, cost, prompt hash)
    O-->>U: answer + evidence chain + disclaimer block
```

**Note step 22–23.** Catalysts are matched to the *residual*, never to the raw return. This is the single most important line in the whole sequence: it is the mechanical reason the system cannot tell you a company-specific story about a day when the entire index fell.

---

## 6. Physical / deployment topology

```mermaid
flowchart LR
    subgraph EDGE["Edge"]
        CF["TLS · WAF · rate limit"]
    end

    subgraph APP["Application host — 1× CCX33 class (8 vCPU / 32 GB)"]
        API["FastAPI gateway"]
        WORK["Agent workers (async pool)"]
        SCHED["Prefect scheduler"]
        RQ["Redis — cache · queue · locks"]
    end

    subgraph DATA["Data host — 1× CCX33 class + block storage"]
        PG["Postgres + TimescaleDB"]
        QD["Qdrant"]
        NEO["Neo4j Community"]
        MIN["MinIO"]
    end

    subgraph EXT["External"]
        FND["Inference endpoint (tiered)"]
        MKT["Market data: EODHD · Finnhub · iTick"]
        PIT["PIT fundamentals: valuein + self-built Bursa"]
        NEWS["GDELT 2.0 · WorldMonitor · article-level sentiment"]
        SEC["SEC EDGAR · Bursa / SGX / HKEX announcements"]
    end

    CF --> API
    API --> WORK
    SCHED --> WORK
    WORK --> RQ
    WORK --> PG & QD & NEO & MIN
    WORK --> FND
    SCHED --> MKT & PIT & NEWS & SEC
    MKT & PIT & NEWS & SEC --> MIN
    MIN --> PG
```

Two hosts, not twelve. At this data volume — end-of-day plus 15-minute bars across a few thousand instruments, a five-year news corpus — the entire stack fits comfortably on two mid-size dedicated-vCPU boxes. Managed equivalents are priced in `08-COST-BREAKDOWN.md`; the short version is that managed vector + graph + timeseries costs roughly 6–10× the self-hosted equivalent at this scale and buys operational convenience you may not need for a single-tenant personal system.

**Scale-out trigger:** move Qdrant off the data host when the news collection passes ~15M chunks or p95 retrieval latency passes 400 ms, whichever comes first. Not before.

---

## 7. Core data model

```mermaid
erDiagram
    INSTRUMENT ||--o{ PRICE_BAR : has
    INSTRUMENT ||--o{ FUNDAMENTAL_FACT : has
    INSTRUMENT ||--o{ CORPORATE_EVENT : has
    INSTRUMENT ||--o{ POSITION : "held as"
    INSTRUMENT }o--|| MARKET : "listed on"
    INSTRUMENT }o--|| SECTOR : "classified in"
    MARKET ||--o{ SESSION_CALENDAR : defines
    MARKET ||--|| FEE_SCHEDULE : has
    NEWS_ARTICLE }o--o{ INSTRUMENT : mentions
    NEWS_ARTICLE }o--|| SOURCE : from
    CORPORATE_EVENT ||--o{ EVENT_BASE_RATE : "aggregates into"
    MOVE_EXPLANATION }o--|| INSTRUMENT : explains
    MOVE_EXPLANATION ||--o{ ATTRIBUTION_COMPONENT : "splits into"
    MOVE_EXPLANATION ||--o{ CANDIDATE_CAUSE : ranks
    CANDIDATE_CAUSE }o--o| CORPORATE_EVENT : "may cite"
    CANDIDATE_CAUSE }o--o| NEWS_ARTICLE : "may cite"
    THESIS ||--o{ THESIS_BREAKER : contains
    THESIS ||--|| POSITION : justifies
    POSITION ||--o{ DECISION_JOURNAL : logs
    DECISION_JOURNAL ||--o| OUTCOME : "resolves to"
    OUTCOME ||--o{ LESSON : produces
    PORTFOLIO ||--o{ POSITION : contains
    PORTFOLIO ||--o{ RISK_SNAPSHOT : measured_by

    INSTRUMENT {
        string instrument_id PK
        string isin
        string primary_ticker
        string mic
        string currency
        int    lot_size
        date   first_listed
        date   delisted_at
        string status
    }
    FUNDAMENTAL_FACT {
        string instrument_id FK
        string concept
        date   period_end
        timestamp known_at
        numeric value
        string currency
        string accounting_std
        string source_doc_id
    }
    MOVE_EXPLANATION {
        string id PK
        string instrument_id FK
        date   window_start
        date   window_end
        numeric total_return
        numeric abnormal_return
        numeric t_stat
        numeric unexplained_share
        string regime_label
        timestamp as_of
    }
    ATTRIBUTION_COMPONENT {
        string explanation_id FK
        string component
        numeric contribution
        numeric share_of_total
    }
    CANDIDATE_CAUSE {
        string explanation_id FK
        string cause_type
        numeric score
        numeric prior
        numeric proximity
        numeric direction_agreement
        string evidence_ref
    }
```

Two fields carry more weight than the rest. `FUNDAMENTAL_FACT.known_at` is what makes every backtest honest — every historical query filters `known_at <= t`, never `period_end <= t`. `INSTRUMENT.delisted_at` being present and populated is what stops survivorship bias from deleting every catastrophic outcome from the record.

---

## 8. Position lifecycle state machine

```mermaid
stateDiagram-v2
    [*] --> Candidate: passes screen + confidence gate
    Candidate --> Rejected: red team breaks thesis
    Candidate --> Rejected: fails any hard cap
    Candidate --> Approved: thesis written, breakers set, stops set
    Rejected --> [*]

    Approved --> Tranche1: staged entry begins
    Tranche1 --> Tranche2: interval elapsed AND no breaker fired
    Tranche2 --> Tranche3: interval elapsed AND no breaker fired
    Tranche1 --> Held: entry abandoned, partial fill kept
    Tranche2 --> Held
    Tranche3 --> Held: target weight reached

    Held --> Held: monthly review, inside no-trade buffer
    Held --> Trim: weight above cap OR decile 8-9 OR thesis weakened
    Trim --> Held: back within band
    Held --> Exit: breaker fired
    Held --> Exit: stop distance hit
    Held --> Exit: time stop reached
    Trim --> Exit: breaker fired
    Exit --> Closed
    Closed --> PostMortem: A14 scores the outcome
    PostMortem --> [*]: lesson written to store

    note right of Approved
        Breakers and stops are written
        BEFORE the first tranche.
        A position without them cannot
        be constructed.
    end note
```

---

## 9. Model tier routing

One choke point, four tiers, routed by task class — never by habit.

| Tier | Used for | Agents | Rule |
|---|---|---|---|
| `reason` | Thesis synthesis, red-team attack, multi-hop graph traversal, ambiguous attribution | A10, A11, A9 (hard cases only) | Never used for anything that could be a classifier |
| `balanced` | Default agent work: fundamentals reading, valuation commentary, catalyst matching | A1, A2, A5, A6, A7, A8, A12 | The workhorse. ~70% of calls |
| `cheap` | Classification, entity tagging, routing, dedup, summarisation of routine items | A0 routing, A4 bulk news, ingest pipelines | ~80% of *volume*, ~10% of *cost* |
| `local` | Sentiment scoring, reranking, embedding | FinBERT + cross-encoder reranker on the app host | Zero marginal cost; run first, escalate only the tail |
| `embed` | Vector generation | All ingest | Cache by content hash — never re-embed unchanged text |

**Escalation, not default.** A4 processes thousands of articles a day. The pipeline is: rule filter → local FinBERT → `cheap` tier for the ones that pass relevance → `balanced` only for articles attached to a holding or a live candidate. Sending every article to a reasoning model is how a personal project generates an enterprise invoice.

---

## 10. Growth surface — how the system gets wider without a rewrite

Four layers, ordered by blast radius. Each has a different promotion gate.

```mermaid
flowchart LR
    L1["<b>L1 Registry</b><br/>new agent / tool / market<br/>= YAML entry + adapter class"]
    L2["<b>L2 Source plugins</b><br/>Source.fetch → RawRecord[]<br/>Source.normalize → Silver rows"]
    L3["<b>L3 Skill evolution</b><br/>prompts + retrieval configs<br/>optimised against evals"]
    L4["<b>L4 Workflow evolution</b><br/>orchestration graph rewriting"]

    L1 -->|"gate: eval suite exists"| SHIP1["ships on merge"]
    L2 -->|"gate: adapter conformance tests"| SHIP2["ships on merge"]
    L3 -->|"gate: measured win, no regression"| SHIP3["auto-promote"]
    L4 -->|"gate: HUMAN APPROVAL"| SHIP4["manual promote only"]
```

**The ratchet rule:** a capability ships only with an eval suite, and promotion requires no regression on existing suites. L4 — a system rewriting who calls whom — is the layer that can quietly ruin you, and it never auto-promotes.

**Fitness function**, computed nightly from the provenance ledger:

```
fitness = w1·groundedness
        + w2·citation_validity
        + w3·refusal_precision
        + w4·attribution_accuracy      ← new in this module
        + w5·forecast_calibration (Brier)
        − w6·p95_latency
        − w7·cost_per_query
```

`attribution_accuracy` is measured against a human-labelled set of ~200 historical moves with known causes (earnings dates, announced M&A, index rebalances — cases where the cause is not in dispute). It is the only new term, and it is the one that keeps A9 honest.

---

## 11. Repository layout

```
finance-agent/
├─ docs/                        ← this plan
├─ apps/
│  ├─ api/                      FastAPI gateway + guardrail chain
│  └─ web/                      Next.js · annotated charts · cards
├─ agents/
│  ├─ registry.yaml             capability registry — the growth surface
│  ├─ supervisor/               A0
│  ├─ evidence/                 A1–A8, one package each
│  ├─ synthesis/                A9 attribution · A10 thesis · A11 red team
│  ├─ portfolio/                A12 risk · A13 sizing
│  └─ learning/                 A14 reflection · A15 teacher
├─ knowledge/
│  ├─ collections/              one module per Qdrant collection
│  ├─ ingest/                   per-source adapters
│  ├─ chunking/                 per-corpus strategies
│  ├─ retrieval/                hybrid · RRF · rerank · grade
│  ├─ graph/                    Neo4j schema + traversal
│  └─ evals/                    per-corpus eval suites
├─ engines/
│  ├─ attribution/              factor model · event matching · decomposition
│  ├─ factors/                  point-in-time factor library
│  ├─ risk/                     concentration · correlation · stress · drawdown
│  ├─ sizing/                   waterfall · five caps · Kelly gate · vol target
│  └─ backtest/                 purged walk-forward · cost model · DSR
├─ markets/
│  ├─ contract.py               MarketAdapter ABC
│  ├─ xkls.py xnas.py xses.py … one per exchange
│  └─ calendars/                session + holiday data
├─ core/
│  ├─ llm/                      tier router — single inference choke point
│  ├─ guardrails/               input · retrieval · tool · output rails
│  ├─ provenance/               append-only ledger
│  ├─ contracts/                Pydantic models shared across agents
│  └─ observability/
└─ infra/                       compose · terraform · cron
```

---

## 12. Reading order for the rest of this plan

| Doc | Answers |
|---|---|
| `02-AGENTS-AND-RAG.md` | Every agent, its private knowledge base, retrieval config, tools, output contract and eval |
| `03-WHY-IT-MOVED.md` | The attribution engine — short-window statistics, catalyst matching, long-horizon return decomposition |
| `04-ANALYST-METHOD.md` | The craft, codified: how a company is actually analysed, step by step, with what each step can and cannot tell you |
| `05-RISK-AND-GUARDRAILS.md` | Risk management, concentration enforcement, drawdown governance, the full guardrail chain |
| `06-DATA-AND-KNOWLEDGE-SOURCES.md` | International coverage ladder, per-market adapter contract, every source and its refresh cadence |
| `07-BUILD-ORDER.md` | Phases, deliverables, definition of done, what to cut if time runs out |
| `08-COST-BREAKDOWN.md` | Full cost model at three tiers, with the token math shown |
| `09-RESEARCH-SOURCES.md` | Every source behind the design decisions above |
