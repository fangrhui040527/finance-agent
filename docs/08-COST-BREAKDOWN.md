# 08 — Cost breakdown

Every figure was checked in August 2026 and every one of them moves. Re-verify before committing to a subscription.

**Currency.** Costs are shown in **Malaysian Ringgit (RM)**, because that is what leaves your account. Vendors bill in USD, so the USD figure is kept alongside — that is the number on the invoice, and the RM number is what the card actually charges.

| | Rate | Note |
|---|---|---|
| Mid-market USD/MYR | **~4.04** | 24 August 2026 |
| **Planning rate used here** | **RM 4.15 / USD** | Mid-market plus ~2.5% for the foreign-transaction markup a Malaysian card adds on USD charges |

Using the mid-market rate to budget a USD subscription understates it every month. The markup varies by issuer — check your own card's foreign-transaction fee and adjust; some Malaysian multi-currency accounts bring it close to mid-market and shave roughly 2–3% off every line below.

---

## 1. Summary

| | **T0 — Bootstrap** | **T1 — Personal serious** | **T2 — Full-fat managed** |
|---|---|---|---|
| Markets | 1–2 (XKLS + XNAS) | T1 deep + 5 T2 analytical | T1 + T2 + T3 contextual |
| News window | 1 year | **5 years** | 5 years + bulk historical |
| Point-in-time fundamentals | DIY EDGAR + vendor free tier (S&P 500 only) | Bought for US, self-built for Bursa | Bought, full universe |
| Infra | 1 small VPS | 2 dedicated-vCPU VPS | Managed vector + graph + timeseries |
| **Data** | **RM 0–91** | **RM 826** | **RM 1,162** |
| **LLM + embeddings** | **RM 50–83** | **RM 183** | **RM 664** |
| **Infra** | **RM 62** | **RM 407** | **RM 2,905** |
| **Monthly total** | **≈ RM 112–237** | **≈ RM 1,415** | **≈ RM 4,731** |
| **Annual** | **≈ RM 1,340–2,840** | **≈ RM 17,000** | **≈ RM 56,800** |
| One-off embedding backfill | ~RM 42 | ~RM 374 | ~RM 1,619 |
| *(monthly in USD)* | *$27–57* | *$341* | *$1,140* |

---

## 2. The number that should decide the tier

Before any line item: **at what portfolio size does each tier pay for itself in alpha alone?**

The realistic long-horizon prize from a well-built cross-sectional factor model, after costs, is a low single-digit annualised excess return over the local index. Take 2% as an optimistic-but-not-absurd working figure.

```
capital required = annual cost / expected excess return
```

| Tier | Annual cost | Break-even at 2% excess | at 1% excess |
|---|---|---|---|
| T0 | ~RM 2,840 | **~RM 142,000** | ~RM 284,000 |
| T1 | ~RM 17,400 | **~RM 870,000** | ~RM 1.74m |
| T2 | ~RM 56,800 | **~RM 2.84m** | ~RM 5.68m |

**Read this honestly.** If the portfolio is under roughly **RM 870,000**, tier 1 cannot pay for itself out of alpha, and tier 2 certainly cannot. That does not make the system worthless — it means **the justification has to be the things that are not alpha**: the risk controls that stop a concentrated position from doing serious damage, the sizing engine that contributes more to outcomes than the alpha engine does, the attribution engine that stops you acting on a market move as if it were a company event, the decision journal, and the education layer.

Those are real and worth paying for. But they are worth paying **T0 prices** for, for most people. Start at T0. Move to T1 when the portfolio or the usage genuinely justifies it — not because the feature list is longer.

**A second FX point, and it cuts the other way.** Your costs are USD-denominated and your capital is largely MYR. A 10% MYR depreciation raises this entire bill by 10% in ringgit terms while your Bursa holdings are unchanged. That is a small, real, and entirely unhedged currency exposure sitting on the cost side of the project — worth noting given `03 §2.4` treats FX as a first-class attribution component on the return side.

---

## 3. Data subscriptions

| Line | T0 | T1 | T2 | Note |
|---|---|---|---|---|
| Global market data (EOD + fundamentals + calendars) | Free tiers | **RM 415** *($99.99)* | RM 415 | Annual billing drops it to ~RM 346/mo *($83.33)*. EOD-only all-world plans start around RM 90/mo *(€19.99)* if you skip intraday |
| Point-in-time US fundamentals | DIY EDGAR (free) or vendor free tier = S&P 500 | **RM 203** *($49)* | RM 203 | Buys 100M+ point-in-time, survivorship-free SEC facts with normalised concepts. **Malaysia has no equivalent — you build it** |
| Article-level news sentiment | — | **~RM 208** *(~$50)* | ~RM 208 | Needed because ticker-level aggregate scores cannot be traced to a headline, so they can be a feature but never evidence. **Verify current pricing** |
| ASEAN specialist feed | — | — | ~RM 208 *(~$50)* | Only if the primary vendor's Bursa depth disappoints |
| Intraday / real-time upgrade | — | — | ~RM 125 *(~$30)* | Not needed for a 6–36 month horizon |
| Per-ticker news + quotes | Free tier | Free tier | Free tier | 60 calls/min — generous enough to be load-bearing |
| **GDELT 2.0** | **Free** | **Free** | **Free** | No key. 100+ languages, ~300 event categories, georeferenced, GKG entity resolution. The backbone of the 5-year corpus |
| **WorldMonitor** | Free tier | Free tier / Pro | Pro | Curated briefs, Country Instability Index, market composite |
| **SEC EDGAR** | **Free** | **Free** | **Free** | Public domain. 10 req/s with a User-Agent header |
| **FRED / World Bank / IMF** | **Free** | **Free** | **Free** | Macro series |
| Exchange announcement feeds | Free | Free | Free | The authoritative `announced_at` |
| **Subtotal** | **RM 0–91** | **RM 826** | **RM 1,162** |

**The free backbone is genuinely substantial.** GDELT + EDGAR + FRED + exchange feeds + a free quote tier covers news breadth, US filings, macro and corporate actions at zero cost. What money buys is: normalised global fundamentals, point-in-time `known_at` stamps, and citable article-level sentiment. Everything else in the source register is free.

---

## 4. LLM and embeddings — the token math

Model rates (first-party API, Aug 2026), converted at RM 4.15/USD:

| Tier | Model | Input RM/MTok | Output RM/MTok | *(USD)* |
|---|---|---|---|---|
| `reason` | Claude Opus 5 | RM 20.75 | RM 103.75 | *$5 / $25* |
| `balanced` | Claude Sonnet 5 | RM 8.30 | RM 41.50 | *$2 / $10* |
| `cheap` | Claude Haiku 4.5 | RM 4.15 | RM 20.75 | *$1 / $5* |
| `embed` | third-party embeddings | RM 0.08–0.75 | — | *$0.02–0.18* |
| `local` | FinBERT + cross-encoder | RM 0 marginal | — | — |

### 4.1 T1 workload assumptions

60-instrument watchlist · 12 holdings · ~250 news articles/day surviving the rule + FinBERT filter · 8 deep workups/month · 60 "why did it move" queries/month · 150 ad-hoc queries/month · 30 daily briefs.

### 4.2 Cheap tier — Haiku 4.5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Intent routing + classification | 200 calls | 0.20 | 0.02 | RM 1.25 |
| News triage, tagging, dedup adjudication | 7,500 articles | 9.00 | 1.88 | RM 76.28 |
| Ingest normalisation tail | — | 2.00 | 0.40 | RM 16.60 |
| **Subtotal** | | **11.20** | **2.30** | **RM 94** *($22.68)* |
| *with Batch API on news triage (−50%)* | | | | **≈ RM 56** *($13.50)* |

### 4.3 Balanced tier — Sonnet 5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Evidence agents on workups (8 × 8 agents) | 64 runs | 1.60 | 0.19 | RM 21.16 |
| "Why did it move" | 60 | 1.08 | 0.15 | RM 15.19 |
| Ad-hoc queries | 150 | 1.80 | 0.23 | RM 24.48 |
| Daily brief | 30 | 1.20 | 0.12 | RM 14.94 |
| Nightly breaker sweep + risk snapshot | 30 | 0.90 | 0.06 | RM 9.96 |
| **Subtotal before caching** | | **6.58** | **0.75** | **RM 86** *($20.66)* |
| *with prompt caching on stable prefixes* | | | | **≈ RM 66** *($15.98)* |

### 4.4 Reason tier — Opus 5

| Task | Volume/month | In (MTok) | Out (MTok) | Cost |
|---|---|---|---|---|
| Thesis synthesis (A10) | 8 | 0.48 | 0.06 | RM 16.60 |
| Red team (A11) | 8 | 0.40 | 0.05 | RM 13.28 |
| Hard attribution cases (A9 escalation) | 15 | 0.60 | 0.06 | RM 18.68 |
| Monthly reflection + calibration deep pass | 4 | 0.32 | 0.04 | RM 10.79 |
| **Subtotal** | | **1.80** | **0.21** | **RM 59** *($14.30)* |

### 4.5 Embeddings

| Item | Volume | Cost |
|---|---|---|
| Backfill: filings, 5y, T1 markets | ~360 MTok @ RM 0.54/MTok | **RM 195** one-off *($47)* |
| Backfill: news, 5y | ~1,800 MTok @ RM 0.08/MTok | **RM 149** one-off *($36)* |
| Backfill: craft, sector, method, failure KBs | ~10 MTok | **RM 5** one-off |
| Ongoing | ~10 MTok/month | **RM 2/mo** |
| **Reranking** | every retrieval | **RM 0** — local cross-encoder |

Embeddings are close to free and reranking is free if run locally. **Do not pay for a hosted reranker at this scale.**

### 4.6 LLM total, and the cost of getting routing wrong

| | Monthly |
|---|---|
| Cheap (batched) | RM 56 |
| Balanced (cached) | RM 66 |
| Reason | RM 59 |
| Embeddings ongoing | RM 2 |
| **Total** | **≈ RM 183** *($44)* |
| One-off backfill | ~RM 353 *($85)* |

**The same workload with everything routed to the reasoning tier:**

| | Tiered | All-Opus |
|---|---|---|
| Cheap-tier work (11.2M in / 2.3M out) | RM 56 | RM 470 |
| Balanced-tier work (6.58M in / 0.75M out) | RM 66 | RM 214 |
| Reason-tier work | RM 59 | RM 59 |
| **Total** | **RM 183** | **RM 743** |

**4.1× the cost for no additional quality** — classification and tagging do not get better on a reasoning model. This is the single highest-leverage cost control in the system, and it is why tier routing lives at the inference choke point rather than being left to each agent's discretion. **RM 560/month, or RM 6,720/year, is the price of getting it wrong.**

---

## 5. Infrastructure

### 5.1 T1 — self-hosted (recommended)

| Component | Spec | Monthly |
|---|---|---|
| Data host: Postgres + TimescaleDB, Qdrant, Neo4j, MinIO | 8 dedicated vCPU / 32 GB (CCX33-class) | **RM 220** *(€48.49)* |
| App host: FastAPI, agent workers, scheduler, Redis, local models | 8 vCPU / 16 GB shared-class | **RM 125** *(~$30)* |
| Block storage | 200 GB | **RM 42** *($10)* |
| Offsite backups | object storage + retention | **RM 21** *($5)* |
| **Subtotal** | | **≈ RM 407** *($98)* |

Egress is effectively free at this provider class (20 TB/server included in EU regions), which matters because news ingestion is bandwidth-heavy.

**A latency note for a Malaysian operator.** EU-region hosting adds roughly 150–250 ms round-trip from Malaysia. Irrelevant for a system whose fastest loop is a 15-minute news poll and whose horizon is 6–36 months. Singapore-region equivalents cost meaningfully more for the same specs; take the latency.

### 5.2 T0 — single host

One 2–4 vCPU / 8 GB instance runs the whole stack for one market with a 1-year news corpus. **≈ RM 62/month** *($15)* including storage. Tight, and entirely workable — the data volumes at T0 are small.

### 5.3 T2 — managed equivalents, for comparison

| Component | Managed | Self-hosted equivalent |
|---|---|---|
| Vector DB (~10M vectors) | ~RM 1,892/mo *($456)* | included in the RM 220 data host |
| Graph DB | ~RM 270–830/mo | included |
| Managed timeseries/Postgres | ~RM 415–1,971/mo | included |
| App hosting | ~RM 332/mo | RM 125 |
| **Total** | **≈ RM 2,900–4,980/mo** | **≈ RM 407/mo** |

**Managed infrastructure costs roughly 7× the self-hosted equivalent at this scale.** The heuristic: move a component to managed hosting when the *self-hosted* version starts costing you more in time than the managed version costs in money — not before.

---

## 6. One-off and hidden costs

| Item | Estimate | Note |
|---|---|---|
| Embedding backfill | RM 42 / RM 374 / RM 1,619 by tier | One-time; cache by content hash so it never repeats |
| Historical data depth | RM 0–2,075 | Some vendors charge separately for >5y history |
| **Developer time** | **~420 hours** to the paper-trade gate | ≈28 weeks of phases at ~15 h/week solo |
| Bursa point-in-time build | ~40–60 hours of the above | No vendor sells it. Concentrated, unglamorous, unskippable |
| Ongoing maintenance | ~4–8 h/month | Feed breakage, schema drift, eval regressions |

**Put a number on the time and the ranking becomes obvious.** At a notional RM 100/hour, 420 hours is **RM 42,000** — more than two years of T1 subscriptions, and roughly fifteen times the entire first year at T0. Developer time is 90%+ of the true cost of this project at every tier.

**Optimising a RM 183 monthly LLM bill by spending 40 hours on it costs RM 4,000 to save maybe RM 500 a year.** Do the tier routing, which is a day's work and saves RM 6,720/year, and then stop.

---

## 7. Cost controls, ranked by impact

| Control | Saving | Where |
|---|---|---|
| **Tier routing** — classification never hits the reasoning tier | **~75% of LLM spend · RM 6,720/yr** | Inference choke point |
| **Rule + local-model filter before any LLM call** on news | ~80% of article volume never reaches an API | A4 escalation ladder |
| **Self-host rather than manage** | ~85% of infra · **RM 30,000/yr at T2 scale** | §5.3 |
| **Batch API for non-latency-sensitive work** | 50% on that slice | Overnight jobs |
| **Prompt caching on stable prefixes** | ~35% of input cost on repeated-prefix calls | Keep volatile content after the last cache breakpoint |
| **Local reranker and local FinBERT** | 100% of what a hosted equivalent would cost | App host |
| **Embedding cache by content hash** | Never re-embed unchanged text | Ingest |
| **Context compression before long-context calls** | 20–40% on the largest calls | Before A10/A11 |
| **Redis TTL by volatility class** | Fewer vendor calls, lower rate-limit pressure | Serve layer |
| **Annual billing on the primary data vendor** | ~17% · RM 830/yr | RM 346 vs RM 415/mo |
| **A multi-currency card or account** | ~2–3% on every USD line | Removes most of the 4.04 → 4.15 gap |
| **Per-user daily token budget, from day one** | Bounds the tail | Enforced at the gateway; truncation disclosed, never silent |

---

## 8. Budget guardrails in the system

1. **Every LLM call writes tokens and cost into the provenance ledger.** Cost per query is a fitness-function term alongside groundedness and calibration.
2. **Per-user daily token budget**, set at deploy. When hit, the supervisor truncates the plan and **says so** — never silently degrades to a cheaper model, because a silently degraded answer is indistinguishable from a good one until it is wrong.
3. **Cost alerts at 60% / 85% / 100%** of the monthly budget.
4. **A monthly cost report by agent and by tier.** If A4 is consuming 40% of the LLM budget, the escalation ladder is misconfigured — a bug, not a bill.
5. **Data-vendor call budgets per source**, with free-tier limits encoded so a runaway backfill cannot burn a month's quota in an afternoon.
6. **Store cost in USD and RM with the `fx_asof` rate**, per the `(amount, ccy, fx_asof)` rule in `06 §4.1`. The project's own cost ledger follows the same discipline as its market data — otherwise a year of cost history silently rewrites itself when the rate moves.

---

## 9. Recommended starting position

**Start at T0. Spend the first three phases' worth of money on nothing.**

| Month | Spend | What you have |
|---|---|---|
| 1–3 | ~RM 125/mo | Free data backbone, one VPS, XKLS + XNAS, 1-year news, DIY point-in-time for the S&P 500 free tier. Attribution engine working end to end |
| 4–6 | ~RM 330/mo | Add the point-in-time fundamentals subscription (RM 203) once the backtest harness exists to consume it. Not before — it buys nothing until then |
| 7+ | ~RM 830–1,450/mo | Add the global data vendor when a second and third market genuinely matter, and article-level sentiment when A4's citations start failing the output rail for lack of traceable sources |

The ordering rule mirrors the build order: **buy the data the week you have the code that uses it.** A point-in-time subscription with no backtest harness is RM 203/month of unused API quota, and a five-year news corpus with no attribution engine is a large, expensive text archive.

---

## Cache pricing correction (2026-08-31)

The Messages API reports `input_tokens` as the UNCACHED remainder; cache reads
and writes arrive in fields of their own. The original `cost_usd` subtracted
reads from `input_tokens` a second time - invisible while nothing was cached,
and a negative bill on the first day something was. The formula is now:

    cost = input*rate + reads*0.1*rate + writes*1.25*rate + output*out_rate

with every term clamped at zero, pinned by tests, and verified live against a
metered QA run (ledger == meter under FINPLANET_CHEAP=1).
