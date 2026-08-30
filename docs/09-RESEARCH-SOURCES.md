# 09 — Research sources

Web research conducted 24 August 2026, organised by the design decision each source supports. Prices, free-tier limits and API surfaces move — re-verify anything load-bearing before you commit to it.

Sources carried forward from the earlier `RESEARCH.md` are marked †.

---

## 1. Why prices move — the evidence behind the attribution engine

Supports: `03-WHY-IT-MOVED.md` in its entirety.

| Source | What it establishes |
|---|---|
| [Event studies — methodology reference](https://bookdown.org/mike/data_analysis/sec-event-studies.html) | The classical form: abnormal returns as deviations from a market/factor model prediction. Causal interpretation requires a precisely known, unanticipated event date with no overlapping news — conditions that frequently do not hold |
| [Which News Moves Stock Prices? A Textual Analysis (NBER w18725)](https://www.nber.org/system/files/working_papers/w18725/w18725.pdf) | Textual analysis linking specific news categories to price movement |
| [Chan — Stock Price Reaction to News and No-News (Yale)](http://www.econ.yale.edu/~shiller/behfin/2001-05-11/chan.pdf) · [journal version](https://www.sciencedirect.com/science/article/abs/pii/S0304405X03001466) | **The finding that makes `no_identified_catalyst` a feature:** moves accompanied by identifiable news drift; large moves with no news tend to reverse. Distinguishing the two is the point |
| [Post-Earnings-Announcement Effect](https://quantpedia.com/strategies/post-earnings-announcement-effect) · [PEAD review](https://www.sciencedirect.com/science/article/pii/S2214635020303750) | Drift direction and magnitude relate directly to surprise size; attention amplifies it. Basis for the surprise-bucketed base-rate table |
| [Market bias in M&A, dividend and repurchase events (Heliyon)](https://www.cell.com/heliyon/fulltext/S2405-8440(24)05431-8) | Differential reaction by event type — strongest and longest to buybacks, weakest to stock dividends, negative for acquirers and positive for targets. **Also: information leaks ~1 day before official announcement**, which is why the event window starts before the announcement date |
| [FTSE Russell corporate actions and events guide](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/policy-documents/corporate-actions-and-events-guide.pdf) | Event taxonomy and index-treatment reference for the A5 catalogue |

---

## 2. Return decomposition — the long-horizon "why"

Supports: `03-WHY-IT-MOVED.md` §4, `04-ANALYST-METHOD.md` §3.

| Source | What it establishes |
|---|---|
| [Robeco — Decomposing equity returns: earnings growth vs multiple expansion](https://www.robeco.com/en-int/insights/2025/02/decomposing-equity-returns-earnings-growth-versus-multiple-expansion) | `Return = dividend yield + earnings growth ± ΔP/E`. Dividends and earnings growth dominate long-run returns; multiples mean-revert. Regional evidence 2015–2024: US benefited from both the highest earnings growth *and* significant re-rating; Europe and Japan from neither |
| [Federal Reserve — A Stock Return Decomposition Using Observables](https://www.federalreserve.gov/econres/feds/files/2022014r1pap.pdf) | Formal decomposition methodology |
| [TSR decomposition framework](https://umbrex.com/resources/frameworks/strategy-frameworks/tsr-decomposition/) | Earnings growth / multiple change / cash yield, with FX, leverage and M&A adjustments — the structure used in the driver tree |
| [State Street — Deconstructing equity returns](https://www.ssga.com/us/en/institutional/insights/systematic-active-monthly-september-2025) | Rate-cycle conditioning of the decomposition |
| [AQR — Driving with the Rear-View Mirror](https://www.aqr.com/-/media/AQR/Documents/Insights/White-Papers/Driving-with-the-Rear-View-Mirror.pdf) | Why extrapolating a re-rating-driven past return is a forecasting error |

---

## 3. Company analysis — the craft

Supports: `04-ANALYST-METHOD.md`.

| Source | What it establishes |
|---|---|
| [Fundamental analysis checklist](https://www.winvesta.in/blog/investors/building-your-fundamental-analysis-checklist-for-stock-picking) | Tiered checklist structure: must-haves (understandable, honest management, positive FCF, Debt/EBITDA < 4×, margin of safety) vs should-haves (moat, ROIC > 15%, Z-score > 2.99) |
| [Fundamental analysis step-by-step](https://ryanoconnellfinance.com/fundamental-analysis/) | The seven-step workup that the 12-step pipeline extends |
| [Best financial ratios for stock analysis](https://moatscope.com/blog/best-financial-ratios-for-stock-analysis) | **ROIC sustained above ~15% for 5+ years as the strongest quantitative moat signal.** Earnings-quality cross-check: net income rising while operating cash flow is flat or falling is the most reliable warning sign |
| [Fundamental analysis, technical analysis and momentum: review and integration (RQFA 2026)](https://link.springer.com/article/10.1007/s11156-026-01540-7) | Momentum life-cycle theory; profits concentrated in the first months after a trend forms, consistent with underreaction |

---

## 4. What actually works — momentum, trend, and their decay

Supports: `04-ANALYST-METHOD.md` §6.1.

| Source | What it establishes |
|---|---|
| [Time-series momentum: the academic evidence](https://www.pfolio.io/academy/time-series-momentum) | 12-month lookback / 1-month hold produced positive significant returns across nearly every asset class, independent of cross-sectional momentum; strongly positive in 2008 |
| [A Century of Evidence on Trend-Following Investing](https://fairmodel.econ.yale.edu/ec439/hurst.pdf) | Consistent positive returns across every ten-year sub-period back to 1880 across 67 markets |
| [Are trend-following and TSMOM results robust? (Alpha Architect)](https://alphaarchitect.com/are-trend-following-and-time-series-momentum-research-results-robust/) | **The counterweight:** evidence is weak for large cross-sections; fast trend following has declined materially |
| [Revisiting the Structure of Trend Premia](https://arxiv.org/pdf/2510.23150) | Diversification across trend signals often hides redundancy |

---

## 5. Multi-agent LLM systems for finance

Supports: `01-SYSTEM-ARCHITECTURE.md` §3–§4, `02-AGENTS-AND-RAG.md`.

| Source | What it establishes |
|---|---|
| [TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv 2412.20138)](https://arxiv.org/abs/2412.20138) · [repo](https://github.com/tauricresearch/tradingagents) · [site](https://tradingagents-ai.github.io/) | The firm-simulation pattern: fundamental / sentiment / technical analysts, bull and bear researchers, trader, risk manager. Persistent decision memory with realised-return reflection injected into later prompts. **The bull/bear debate structure is the direct ancestor of the A10/A11 split** |
| [FinMem: Layered Memory and Character Design (arXiv 2311.13743)](https://arxiv.org/abs/2311.13743) · [repo](https://github.com/pipiku915/finmem-llm-stocktrading) | Profiling / layered memory / decision modules; working memory plus layered long-term memory with adjustable cognitive span |
| [FinVision: multi-agent framework for market prediction](https://arxiv.org/pdf/2411.08899) | Multi-modal agent composition |
| [FinAgent: multimodal foundation agent for financial trading (arXiv 2402.18485)](https://arxiv.org/pdf/2402.18485) | Tool-augmented, diversified, generalist agent design |
| [ContestTrade: internal contest mechanism](https://arxiv.org/pdf/2508.00554) | Competitive rather than cooperative multi-agent selection |
| [FinSphere: real-time stock analysis agent](https://arxiv.org/pdf/2501.12399) | Instruction-tuned LLM + domain tools |

**Where this design departs from all of them:** none has an attribution component, and all of them terminate in an action. See `01-SYSTEM-ARCHITECTURE.md` §4.

---

## 6. Agentic RAG and per-agent knowledge

Supports: `02-AGENTS-AND-RAG.md` §3.

| Source | What it establishes |
|---|---|
| [Agentic RAG: a survey (arXiv 2501.09136)](https://arxiv.org/abs/2501.09136) · [companion repo](https://github.com/asinghcsu/AgenticRAG-Survey) | Retrieval as a multi-step decision process; planner / executor / critic decomposition; specialised verification agents to check intermediate outputs before proceeding |
| [Memory in the Age of AI Agents (arXiv 2512.13564)](https://arxiv.org/pdf/2512.13564) | Short-term working context vs long-term persisted memory across vector, relational and graph stores |
| [MARAG-R1: multi-tool agentic retrieval](https://arxiv.org/pdf/2510.27569) | Learned routing across multiple retrieval tools rather than one index |
| [Agentic RAG systems for enterprise-scale retrieval](https://toloka.ai/blog/agentic-rag-systems-for-enterprise-scale-information-retrieval/) | Per-agent tool assignment: SQL agent, semantic agent, web agent, executing in parallel |
| † [Snowflake — impact of retrieval and chunking on finance RAG](https://www.snowflake.com/en/engineering-blog/impact-retrieval-chunking-finance-rag/) | Over ~23,000 SEC filings: **retrieval and chunking strategy affect output quality more than raw generative power**, even with long-context models available |
| † [FinTradeBench (arXiv 2603.19225)](https://arxiv.org/pdf/2603.19225) | Parent–child hierarchical indexing: parents ≤2,000 tokens, children ~300; retrieve on children, generate with parents; convert tables to markdown before chunking |
| † [Chunking by structural element (arXiv 2402.05131)](https://arxiv.org/html/2402.05131v2) | Splitting on SEC Items yields good chunk size without tuning |
| † [RAG pipelines for financial intelligence](https://fintechstudios.com/blog/rag-pipelines-financial-intelligence-best-practices) | Generic chunking produced factual errors in ~14.7% of financial queries in one study |
| † [RAG architecture 2026](https://futureagi.com/blog/rag-architecture-llm-2025/) | BM25 + dense fused with RRF beats either alone; cross-encoder rerank adds ~5–15 MRR on hard sets |

---

## 7. LLM text features and the biases specific to them

Supports: `02-AGENTS-AND-RAG.md` A4, `05-RISK-AND-GUARDRAILS.md` §9.

| Source | What it establishes |
|---|---|
| † Glasserman & Lin — LLM news-sentiment biases ([Frontiers in AI](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1608365/full), [arXiv](https://arxiv.org/pdf/2512.23847)) | **Look-ahead bias** — the model has the outcome in its weights — and the **distraction effect** — extraneous company information skews the sentiment read |
| [Mitigating Look-Ahead Bias in Financial Backtesting with LLMs (arXiv 2605.24564)](https://arxiv.org/pdf/2605.24564) | Methods for constraining LLM knowledge to the evaluation window |
| † Five sentiment dimensions in commodity futures ([arXiv 2603.11408](https://arxiv.org/pdf/2603.11408)) | Relevance, polarity, intensity, uncertainty, forwardness. **Intensity and uncertainty carry more predictive weight than polarity**; LLM + FinBERT beats either alone |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) · [PIXIU (arXiv 2306.05443)](https://arxiv.org/pdf/2306.05443) · [Pre-trained LLMs for financial sentiment](https://arxiv.org/html/2401.05215v1) | Open-source financial NLP models deployable on consumer hardware — the basis for the local-first escalation ladder |
| [Spurious Predictability in Financial ML (arXiv 2604.15531)](https://arxiv.org/pdf/2604.15531) | How apparent predictability arises from methodology rather than signal |

---

## 8. Risk management and position sizing

Supports: `05-RISK-AND-GUARDRAILS.md`.

| Source | What it establishes |
|---|---|
| [18 position-sizing strategies](https://www.quantifiedstrategies.com/position-sizing-strategies/) | Sizing method taxonomy including maximum-drawdown-based sizing |
| [Trading risk management: position sizing, drawdowns, capital protection](https://www.quantvps.com/blog/trading-risk-management) | **Portfolio heat** — total risk across open positions — commonly capped at 4–8% of equity, with many professionals at 5% |
| [Maximum drawdown guide](https://tradefundrr.com/understanding-maximum-drawdown/) | Conservative portfolios 10–15% max drawdown, moderate 15–25%, aggressive 25–40%+. Basis for the drawdown-governance tiers |
| [Managing drawdowns](https://groww.in/blog/manage-drawdowns-like-a-hedge-fund-manager) | Apparent diversification that is really one concentrated exposure — the argument for correlation clusters over sector labels |
| † [Kelly criterion](https://ryanoconnellfinance.com/kelly-criterion/) · † [Optimal position sizing](https://mbrenndoerfer.com/writing/optimal-position-sizing-kelly-criterion-leverage) | **Half-Kelly cuts volatility roughly in half while giving up ~25% of expected growth.** Kelly fraction rises with Sharpe, falls with volatility; Kelly-optimal excess log-growth = Sharpe²/2. **~50–100 logged trades before Kelly inputs mean anything**; recalculate quarterly; a claimed 30% edge means the model is broken |

---

## 9. Backtesting honesty

Supports: `05-RISK-AND-GUARDRAILS.md` §9, `07-BUILD-ORDER.md` P12.

| Source | What it establishes |
|---|---|
| [Bailey & López de Prado — The Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [overview](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio) | Corrects for selection bias under multiple testing, sample length and non-normality. **The probability of selecting an overfit strategy grows rapidly with the number of trials** |
| [Statistical Overfitting and Backtest Performance](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf) | The mechanics of backtest overfitting |
| [Implementation Risk in Portfolio Backtesting (arXiv 2603.20319)](https://arxiv.org/pdf/2603.20319) | A previously unquantified error source in backtest-to-live translation |
| † [Lookahead bias in fundamental backtests](https://tradevodata.com/blog/lookahead-bias-fundamental-backtests) | US 10-K deadlines of 60/75/90 days by filer class; one audit found **11% of ticker-months** used numbers not yet public under a naive join |

---

## 10. Behavioural finance

Supports: `05-RISK-AND-GUARDRAILS.md` §6.

| Source | What it establishes |
|---|---|
| [Overconfidence and the disposition effect (MDPI)](https://www.mdpi.com/2227-7072/11/2/78) · [ScienceDirect](https://www.sciencedirect.com/science/article/am/pii/S2214635018300418) | The interaction between the two, and their market effects |
| [Behavioural biases that impact investing (W&M)](https://online.mason.wm.edu/blog/behavioral-biases-that-can-impact-investing-decisions) | **Least-active traders returned 18.5% annually vs 11.4% for the most active** — the empirical case for turnover caps |
| [Investor psychology and behavioural biases](https://www.toptal.com/management-consultants/financial-analysts/investor-psychology-behavioral-biases) | Loss aversion → disposition effect; a loss registers roughly twice as strongly as an equivalent gain |
| [Systematic review and meta-analysis of behavioural biases (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12576316/) | Coverage and effect-size summary across the literature |
| [Incorporating cognitive biases into RL for financial decisions (arXiv 2601.08247)](https://arxiv.org/pdf/2601.08247) | Modelling bias explicitly rather than assuming it away |

---

## 11. International markets and accounting comparability

Supports: `06-DATA-AND-KNOWLEDGE-SOURCES.md` §2.

| Source | What it establishes |
|---|---|
| [GAAP vs IFRS (HBS Online)](https://online.hbs.edu/blog/post/gaap-vs-ifrs) | IFRS principles-based, used in 140+ countries; US GAAP rules-based |
| [EY — US GAAP vs IFRS (Jan 2026)](https://www.ey.com/content/dam/ey-unified-site/ey-com/en-us/technical/accountinglink/documents/ey-ifrs29540-261us-01-21-2026.pdf) · [WSP cheat sheet](https://www.wallstreetprep.com/knowledge/us-gaap-vs-ifrs-differences-similarities-examples-pdf-cheat-sheet/) | Specific differences reaching ratios: impairment reversals permitted under IFRS except goodwill and prohibited under GAAP; development-cost capitalisation; inverted balance-sheet ordering |
| [US capital markets and international standards (CRS R44089)](https://www.everycrsreport.com/reports/R44089.html) | Why reconciliation is required for cross-border comparability |
| [Bursa Malaysia — transaction costs, fees and charges](https://www.bursamalaysia.com/trade/post_trade/transaction_costs_fees_charges) · [broker fee calculator](https://calculatormalaysia.com/investment/bursa-brokerage-fee-calculator-malaysia/) · [SC fees effective Jan 2026 (Bernama)](https://www.bernama.com/en/news.php?id=2509180) | 2026 Bursa mandatory legs: clearing fee 0.03% (capped RM 1,000), stamp duty RM 1 per RM 1,000 = 0.1% (capped RM 1,000, through end-2026), each on both sides; brokerage typically 0.1% with a minimum. New SC regulatory fees apply to the exchange operator, not per cash-equity trade |
| † [SC licensing and registration guidelines](https://www.sc.com.my/regulation/guidelines/licensing-and-registration) | CMSL under CMSA 2007; Digital Investment Management framework; **Technical Note No. 1/2022 on digital investment advice** |

---

## 12. Data sources

Supports: `06-DATA-AND-KNOWLEDGE-SOURCES.md` §3.

| Source | What it establishes |
|---|---|
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free, public domain, no key. 10 req/s with a User-Agent header. `data.sec.gov/submissions/CIK##########.json` for filing history; `companyfacts` for XBRL |
| [GDELT Project](https://gdeltproject.org/) · [data access](https://gdeltproject.org/data.html) | Free, no key, 15-minute cadence, 100+ languages, ~300 event categories back to 1979, georeferenced; GKG resolves persons, organisations, locations, themes and tone |
| [25 free algo-trading data sources (2026)](https://slmaj.com/blog/25-free-algo-trading-data-sources) · [free stock market APIs tested](https://thenextgennexus.com/2026/05/15/10-best-free-stock-market-apis-2026/) | Current state of free tiers; FRED for macro |
| [EODHD pricing](https://eodhd.com/pricing) · [KLSE coverage](https://eodhd.com/exchange/KLSE) | All-in-one $99.99/mo or $999.90/yr; fundamentals-only $59.99/mo; EOD all-world from €19.99/mo. KLSE listings under MIC `XKLS` |
| † [Best financial data APIs 2026](https://nb-data.com/p/best-financial-data-apis-in-2026) | Provider landscape and free-tier comparison |

---

## 13. Guardrails and compliance for LLM financial systems

Supports: `05-RISK-AND-GUARDRAILS.md` §8.

| Source | What it establishes |
|---|---|
| [LLM guardrails for fintech: compliance, hallucination prevention, audit trails](https://www.getmaxim.ai/articles/llm-guardrails-for-fintech-compliance-hallucination-prevention-and-audit-trails/) | Named risks: unlicensed-advice claims, hallucinated tickers or rates, PII exfiltration. Countermeasures: factuality checks against authoritative price feeds, regulated-advice disclaimer checks, strict PII rails. **Log per request: which rails ran and in what order, which models scored, which verdict won** |
| [Ultimate guide to LLM guardrails (2026)](https://futureagi.com/blog/ultimate-guide-llm-guardrails-2026/) · [AI guardrails guide](https://www.openlayer.com/blog/ai-guardrails-llm-guide) | Four control categories: content safety, security (injection, jailbreak), data protection, compliance. Runtime placement between application and model |
| [Type-Checked Compliance: Deterministic Guardrails for Agentic Financial Systems (arXiv 2604.01483)](https://arxiv.org/pdf/2604.01483) | **The argument for enforcing constraints in the type system rather than in prompts** — the basis for making a cap-breaching `SizingDecision` unconstructable |
| [Guardrails platforms for financial services](https://www.getmaxim.ai/articles/top-5-guardrails-platforms-for-financial-services/) | April 2026 interagency guidance extending model-risk-management expectations to generative and agentic systems |

---

## 14. Cost references

Supports: `08-COST-BREAKDOWN.md`. **The most volatile section — re-verify everything here.**

| Source | What it establishes |
|---|---|
| Claude model pricing (first-party API, Aug 2026) | Opus 5 $5/$25 · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5 per MTok. Batch API 50% off; prompt caching discounts cache reads. Claude on Microsoft Foundry bills at standard API rates |
| [Text embedding model pricing 2026](https://tokenmix.ai/blog/text-embedding-models-comparison) · [OpenAI embedding pricing](https://tokenmix.ai/blog/openai-embedding-pricing) · [embedding model comparison](https://reintech.io/blog/embedding-models-comparison-2026-openai-cohere-voyage-bge) | $0.006–$0.18 per MTok depending on provider and quality tier |
| [Hetzner pricing calculator](https://costgoat.com/pricing/hetzner) · [June 2026 price adjustment](https://www.hetzner.com/pressroom/standardization-and-price-adjustment-of-our-server-products/) | CPX22 €7.99/mo · CCX33 (8 dedicated vCPU / 32 GB) €48.49/mo · 20 TB egress included in EU regions |
| [Hetzner vs AWS 2026](https://gartsolutions.com/hetzner-vs-aws/) · [AWS vs DO vs Hetzner](https://www.forasoft.com/blog/article/aws-vs-digitalocean-vs-hetzner-1302) | Roughly 3–5× cheaper for equivalent compute |
| [Qdrant Cloud pricing 2026](https://ranksquire.com/2026/04/19/qdrant-cloud-pricing-2026/) · [vector DB pricing comparison](https://ranksquire.com/2026/03/04/vector-database-pricing-comparison-2026/) | Free tier available; ~$114/mo for 1M vectors at 1536d with quantization; ~$456/mo at 10M vectors |
| [Neo4j pricing](https://neo4j.com/pricing/) | Free tier suitable for small projects with node and relationship limits |
| [Managed PostgreSQL comparison 2026](https://selfhost.dev/blog/managed-postgresql-comparison-2026/) · [RDS vs self-hosted](https://selfhost.dev/blog/aws-rds-vs-self-hosted-postgresql-cost-comparison/) | $0 to ~$475/mo range; self-hosted ~$80/mo vs RDS ~$130–260/mo for equivalent specs |

---

## 15. Carried forward from `RESEARCH.md`

Still load-bearing, not re-verified in this pass:

- Azure AI Foundry: build against the GA `/openai/v1` surface; the AI Inference beta SDK and the Assistants API both retired 26 Aug 2026, replaced by the Foundry Agent Service Responses API. Deployments act as aliases — pass the deployment name, not the model name. Keyless auth via Entra ID preferred. → [SDK overview](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/sdk-overview) · [endpoints](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints)
- OpenBB as the data-access abstraction (100+ providers, one interface, FastAPI + MCP servers) — **AGPL-3.0** → [repo](https://github.com/OpenBB-finance/OpenBB)
- TimescaleDB as the timeseries store; QuestDB wins on ingest but has limited JOIN support, ClickHouse wins at billions of rows which this system does not have → [comparison](https://selfhost.dev/blog/best-time-series-database/)
- TradingView Lightweight Charts (Apache-2.0) is the chart library that may be used for personal projects; Advanced Charts and Trading Platform explicitly are not → [free charting libraries](https://www.tradingview.com/free-charting-libraries/)
- Backtest framework roles: VectorBT to triage, Zipline-Reloaded or PyBroker for cross-sectional factor work, NautilusTrader only if fills become the binding question; Backtrader is frozen and should not start a new 2026 project
- Alpha decay is a documented phenomenon with its own literature (Pénasse, *Management Science* 2022), not folklore

---

## 16. Standing caveat

Every empirical claim in these documents is conditional on the sample, the market and the regime it was measured in. The system reports base rates **with their sample sizes** and **broken down by regime** for exactly this reason. A relationship that held in one regime and one market is a hypothesis about the next one, not a fact about it.
