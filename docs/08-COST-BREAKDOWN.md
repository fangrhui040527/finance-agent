# 08 — Cost breakdown

Every figure below was checked in August 2026 and every one of them moves. Re-verify before committing to a subscription. Amounts are USD unless marked; EUR converted at ~1.09.

Three tiers, because the honest answer to "what does this cost" depends entirely on how much of the world you want covered.

---

## 1. Summary

| | **T0 — Bootstrap** | **T1 — Personal serious** | **T2 — Full-fat managed** |
|---|---|---|---|
| Markets | 1–2 (XKLS + XNAS) | T1 deep + 5 T2 analytical | T1 + T2 + T3 contextual |
| News window | 1 year | **5 years** | 5 years + bulk historical |
| Point-in-time fundamentals | DIY EDGAR + vendor free tier (S&P 500 only) | Bought for US, self-built for Bursa | Bought, full universe |
| Infra | 1 small VPS | 2 dedicated-vCPU VPS | Managed vector + graph + timeseries |
| **Data** | **$0–22** | **$199** | **$280** |
| **LLM + embeddings** | **$12–20** | **$52** | **$160** |
| **Infra** | **$15** | **$98** | **$700** |
| **Monthly total** | **≈ $27–57** | **≈ $349** | **≈ $1,140** |
| **Annual** | **≈ $320–680** | **≈ $4,190** | **≈ $13,700** |
| One-off embedding backfill | ~$10 | ~$90 | ~$390 |

---

## 2. The number that should decide the tier

Before any line item: **at what portfolio size does each tier pay for itself in alpha alone?**

The realistic long-horizon prize from a well-built cross-sectional factor model, after costs, is a low single-digit annualised excess return over the local index. Take 2% as an optimistic-but-not-absurd working figure.

```
capital required = annual cost / expected excess return
```

| Tier | Annual cost | Break-even at 2% excess | at 1% excess |
|---|---|---|---|
| T0 | ~$480 | **~$24,000** | ~$48,000 |
| T1 | ~$4,190 | **~$210,000** | ~$419,000 |
| T2 | ~$13,700 | **~$685,000** | ~$1,370,000 |

**Read this honestly.** If the portfolio is under roughly $200k, T1 cannot pay for itself out of alpha, and T2 certainly cannot. That does not make the system worthless — it means **the justification has to be the things that are not alpha**: the risk controls that stop a concentrated position from doing serious damage, the sizing engine that contributes more to outcomes than the alpha engine does, the attribution engine that stops you acting on a market move as if it were a company event, the decision journal, and the education layer.

Those are real and they are worth paying for. But they are worth paying **T0 prices** for, for most people. Start at T0. Move to T1 when the portfolio or the usage genuinely justifies it — not because the feature list is longer.

---

## 3. Data subscriptions

| Line | T0 | T1 | T2 | Note |
|---|---|---|---|---|
| Global market data (EOD + fundamentals + calendars) | Free tiers | **$99.99/mo** (all-in-one) | $99.99/mo | Annual billing drops it to ~$83.33/mo. EOD-only all-world plans start around €19.99/mo if you skip intraday |
| Point-in-time US fundamentals | DIY EDGAR (free) or vendor free tier = S&P 500 | **$49/mo** | $49/mo | ~$49/mo class buys 100M+ point-in-time, survivorship-free SEC facts with normalised concepts. **Malaysia has no equivalent — you build it** |
| Article-level news sentiment | — | **~$50/mo** | ~$50/mo | Needed because ticker-level aggregate scores cannot be traced to a headline, so they can be a feature but never evidence. **Verify current pricing** |
| ASEAN specialist feed | — | — | ~$50/mo | Only if the primary vendor's Bursa depth disappoints |
| Intraday / real-time upgrade | — | — | ~$30/mo | Not needed for a 6–36 month horizon |
| Per-ticker news + quotes | Free tier (60 calls/min) | Free tier | Free tier | Generous enough to be load-bearing |
| **GDELT 2.0** | **Free** | **Free** | **Free** | No key. 100+ languages, ~300 event categories, georeferenced, GKG entity resolution. The backbone of the 5-year corpus |
| **WorldMonitor** | Free tier | Free tier / Pro | Pro | Curated briefs, Country Instability Index, market composite |
| **SEC EDGAR** | **Free** | **Free** | **Free** | Public domain. 10 req/s with a User-Agent header |
| **FRED / World Bank / IMF** | **Free** | **Free** | **Free** | Macro series |
| Exchange announcement feeds | Free | Free | Free | Per-exchange terms; the authoritative `announced_at` |
| **Subtotal** | **$0–22** | **$199** | **$280** |

**The free backbone is genuinely substantial.** GDELT + EDGAR + FRED + exchange feeds + a free quote tier covers news breadth, US filings, macro, and corporate actions at zero cost. What money buys is: normalised global fundamentals, point-in-time `known_at` stamps, and citable article-level sentiment. Everything else in the source register is free.

---

## 4. LLM and embeddings — the token math

Model rates used (first-party API, Aug 2026). Claude hosted on Microsoft Foundry bills at these same standard API rates through the Microsoft Marketplace; other partner clouds have separate pricing.

| Tier | Model | Input $/MTok | Output $/MTok |
|---|---|---|---|
| `reason` | Claude Opus 5 | $5.00 | $25.00 |
| `balanced` | Claude Sonnet 5 | $3.00 | $15.00 |
| `cheap` | Claude Haiku 4.5 | $1.00 | $5.00 |
| `embed` | third-party embeddings | $0.02–$0.18 | — |
| `local` | FinBERT + cross-encoder | $0 marginal | — |

### 4.1 T1 workload assumptions

60-instrument watchlist · 12 holdings · ~250 news articles/day surviving the rule + FinBERT filter · 8 deep workups/month · 60 "why did it move" queries/month · 150 ad-hoc queries/month · 30 daily briefs.

### 4.2 Cheap tier — Haiku 4.5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Intent routing + classification | 200 calls | 0.20 | 0.02 | $0.30 |
| News triage, tagging, dedup adjudication | 7,500 articles | 9.00 | 1.88 | $18.38 |
| Ingest normalisation tail (category, entity disambiguation) | — | 2.00 | 0.40 | $4.00 |
| **Subtotal** | | **11.20** | **2.30** | **$22.68** |
| *with Batch API on news triage (−50%, not latency-sensitive)* | | | | **≈ $13.50** |

### 4.3 Balanced tier — Sonnet 5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Evidence agents on workups (8 × 8 agents) | 64 runs | 1.60 | 0.19 | $7.68 |
| "Why did it move" | 60 | 1.08 | 0.15 | $5.49 |
| Ad-hoc queries | 150 | 1.80 | 0.23 | $8.78 |
| Daily brief | 30 | 1.20 | 0.12 | $5.40 |
| Nightly breaker sweep + risk snapshot | 30 | 0.90 | 0.06 | $3.60 |
| **Subtotal before caching** | | **6.58** | **0.75** | **$30.95** |
| *with prompt caching on stable prefixes (~40% of input, cache reads ~10% of base)* | | | | **≈ $23.80** |

### 4.4 Reason tier — Opus 5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Thesis synthesis (A10) | 8 | 0.48 | 0.06 | $4.00 |
| Red team (A11) | 8 | 0.40 | 0.05 | $3.20 |
| Hard attribution cases (A9 escalation) | 15 | 0.60 | 0.06 | $4.50 |
| Monthly reflection + calibration deep pass | 4 | 0.32 | 0.04 | $2.60 |
| **Subtotal** | | **1.80** | **0.21** | **$14.30** |

### 4.5 Embeddings

| Item | Volume | Rate | Cost |
|---|---|---|---|
| Backfill: filings, 5y, T1 markets | ~1.2M child chunks × 300 tok ≈ 360 MTok | $0.13/MTok (quality matters here) | **$47** one-off |
| Backfill: news, 5y | ~3M articles × 600 tok ≈ 1,800 MTok | $0.02/MTok (volume dominates) | **$36** one-off |
| Backfill: craft, sector, method, failure KBs | ~10 MTok | $0.13/MTok | **$1.30** one-off |
| Ongoing | ~10 MTok/month | blended ~$0.05 | **$0.50/mo** |
| **Reranking** | every retrieval | **local cross-encoder** | **$0** |

Embeddings are close to free and reranking is free if you run it locally. **Do not pay for a hosted reranker at this scale** — a local cross-encoder on the app host delivers the same MRR improvement at zero marginal cost.

### 4.6 LLM total and the cost of getting routing wrong

| | Monthly |
|---|---|
| Cheap (batched) | $13.50 |
| Balanced (cached) | $23.80 |
| Reason | $14.30 |
| Embeddings ongoing | $0.50 |
| **Total** | **≈ $52** |
| One-off backfill | ~$85 |

**Now the same workload with everything routed to the reasoning tier:**

| | Tiered | All-Opus |
|---|---|---|
| Cheap-tier work (11.2M in / 2.3M out) | $13.50 | $113.38 |
| Balanced-tier work (6.58M in / 0.75M out) | $23.80 | $51.58 |
| Reason-tier work | $14.30 | $14.30 |
| **Total** | **$52** | **$179** |

**3.4× the cost for no additional quality** — classification and tagging do not get better on a reasoning model. This is the single highest-leverage cost control in the system, and it is why tier routing lives at the inference choke point rather than being left to each agent's discretion.

---

## 5. Infrastructure

### 5.1 T1 — self-hosted (recommended)

| Component | Spec | Monthly |
|---|---|---|
| Data host: Postgres + TimescaleDB, Qdrant, Neo4j, MinIO | 8 dedicated vCPU / 32 GB (CCX33-class) | €48.49 ≈ **$53** |
| App host: FastAPI, agent workers, scheduler, Redis, local models | 8 vCPU / 16 GB shared-class | ≈ **$30** |
| Block storage | 200 GB | ≈ **$10** |
| Offsite backups | object storage + retention | ≈ **$5** |
| **Subtotal** | | **≈ $98** |

Egress is effectively free at this provider class (20 TB/server included in EU regions), which matters because news ingestion is bandwidth-heavy.

### 5.2 T0 — single host

One 2–4 vCPU / 8 GB instance runs the whole stack for one market with a 1-year news corpus. **≈ $15/month** including storage. It will be tight, and it is entirely workable — the data volumes at T0 are small.

### 5.3 T2 — managed equivalents, for comparison

| Component | Managed cost | Self-hosted equivalent |
|---|---|---|
| Vector DB (~10M vectors) | ~$456/mo | included in the $53 data host |
| Graph DB | ~$65–200/mo | included |
| Managed timeseries/Postgres | ~$100–475/mo | included |
| App hosting | ~$80/mo | $30 |
| **Total** | **≈ $700–1,200/mo** | **≈ $98/mo** |

**Managed infrastructure costs roughly 7× the self-hosted equivalent at this scale.** For a single-tenant personal system where you are already running the ingestion pipeline, that premium buys operational convenience you probably do not need. The heuristic worth remembering: move a component to managed hosting when the *self-hosted* version starts costing you more in time than the managed version costs in money — not before.

---

## 6. One-off and hidden costs

| Item | Estimate | Note |
|---|---|---|
| Embedding backfill | $10 / $85 / $390 by tier | One-time; cache by content hash so it never repeats |
| Historical data depth | $0–500 | Some vendors charge separately for >5y history. Check before you need it |
| **Developer time** | **~420 hours** to the paper-trade gate | ≈28 weeks of phases at ~15 h/week solo. **This is the real cost of the project** and it dwarfs every subscription |
| Bursa point-in-time build | ~40–60 hours of the above | No vendor sells it. Concentrated, unglamorous, unskippable |
| Ongoing maintenance | ~4–8 h/month | Feed breakage, schema drift, eval regressions |

At any plausible hourly valuation, developer time is 90%+ of the true cost. **Optimising the $52 LLM bill while spending 40 hours on it is the wrong trade.**

---

## 7. Cost controls, ranked by impact

| Control | Saving | Where |
|---|---|---|
| **Tier routing** — classification never hits the reasoning tier | **~70% of LLM spend** | Inference choke point, not agent discretion |
| **Rule + local-model filter before any LLM call** on news | ~80% of article volume never reaches an API | A4 escalation ladder |
| **Batch API for non-latency-sensitive work** (news triage, nightly sweeps) | 50% on that slice | Overnight jobs |
| **Prompt caching on stable prefixes** — system prompts, agent instructions, method KB chunks | ~35% of input cost on repeated-prefix calls | Keep volatile content (timestamps, the question) *after* the last cache breakpoint |
| **Local reranker and local FinBERT** | 100% of what a hosted equivalent would cost | App host |
| **Embedding cache by content hash** | Never re-embed unchanged text | Ingest |
| **Context compression before long-context calls** | 20–40% on the largest calls | Before A10/A11 |
| **Redis TTL by volatility class** — quotes 60s, fundamentals 24h, briefs 6h | Fewer vendor calls, lower rate-limit pressure | Serve layer |
| **Annual billing on the primary data vendor** | ~17% | ~$83/mo vs $99.99/mo |
| **Self-host rather than manage** | ~85% of infra | §5.3 |
| **Per-user daily token budget, from day one** | Bounds the tail | Enforced at the gateway; truncation is disclosed, never silent |

---

## 8. Budget guardrails in the system

Cost is treated as a first-class engineering constraint, not an invoice discovered later.

1. **Every LLM call writes tokens and cost into the provenance ledger.** Cost per query is a fitness-function term alongside groundedness and calibration.
2. **Per-user daily token budget**, set at deploy. When the budget is hit, the supervisor truncates the plan and **says so in the response** — never silently degrades to a cheaper model, because a silently degraded answer is indistinguishable from a good one until it is wrong.
3. **Cost alerts at 60% / 85% / 100%** of the monthly budget.
4. **A monthly cost report by agent and by tier.** If A4 is consuming 40% of the LLM budget, the escalation ladder is misconfigured — that is a bug, not a bill.
5. **Data-vendor call budgets per source**, with the free-tier limits encoded so a runaway backfill cannot burn a month's quota in an afternoon.

---

## 9. Recommended starting position

**Start at T0. Spend the first three phases' worth of money on nothing.**

| Month | Spend | What you have |
|---|---|---|
| 1–3 | ~$30/mo | Free data backbone, one VPS, XKLS + XNAS, 1-year news, DIY point-in-time for the S&P 500 free tier. Attribution engine working end to end |
| 4–6 | ~$80/mo | Add the point-in-time fundamentals subscription ($49) once the backtest harness exists to consume it. Not before — it buys nothing until then |
| 7+ | ~$199–349/mo | Add the global data vendor when a second and third market genuinely matter, and the article-level sentiment feed when A4's citations start failing the output rail for lack of traceable sources |

The ordering rule mirrors the build order: **buy the data the week you have the code that uses it.** A point-in-time fundamentals subscription with no backtest harness is $49/month of unused API quota, and a five-year news corpus with no attribution engine is a large, expensive text archive.
