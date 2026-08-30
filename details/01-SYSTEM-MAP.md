# 01 — System map

How the layers fit together, what crosses each boundary, and where the boundary
is actually enforced rather than merely described.

---

## The seven layers

```
                    ask.py  ·  mcp_server/  ·  ui/render.py
                              surfaces
                                  │
  ┌───────────────────────────────┴────────────────────────────────┐
  │  a0_supervisor          plan → budget → route → refuse          │  orchestration
  └───────────────────────────────┬────────────────────────────────┘
                                  │  Plan (allowed | Refusal)
  ┌───────────────────────────────┴────────────────────────────────┐
  │  a1..a8   evidence agents — own knowledge, emit typed Findings   │  evidence
  │           never talk to each other                              │
  └───────────────────────────────┬────────────────────────────────┘
                                  │  list[Finding]
  ┌───────────────────────────────┴────────────────────────────────┐
  │  a9 attribution · a10 thesis · a11 red team                     │  synthesis
  └───────────────────────────────┬────────────────────────────────┘
                                  │  Answer (Claims + Citations)
  ┌───────────────────────────────┴────────────────────────────────┐
  │  a12 portfolio risk · a13 sizing                                │  portfolio
  └───────────────────────────────┬────────────────────────────────┘
                                  │  SizingDecision | NoPosition
  ┌───────────────────────────────┴────────────────────────────────┐
  │  a14 teacher · a15 reflection                                   │  learning
  └─────────────────────────────────────────────────────────────────┘

  cross-cutting, every layer:
    core/guardrails/   5 rails, PolicyEngine.enforce raises
    core/provenance/   append-only ledger, SQLite triggers
    core/trace/        span tree, prompts, rail decisions
    core/llm/          TaskClass → Tier routing, cost accounting
```

## What crosses each boundary

The boundaries are typed. A layer cannot hand the next one an untyped bag of
text, which is the usual way a multi-agent system loses provenance.

| Boundary | Type | Defined in |
|---|---|---|
| surface → orchestration | `str` question + optional instrument ids | `agents/supervisor.py` |
| orchestration → evidence | `Plan` — agents to run, budget, or a `Refusal` | `agents/supervisor.py` |
| evidence → synthesis | `list[Finding]` — agent id, kind, text, numbers, caveats | `agents/base.py` |
| synthesis → surface | `Answer` — `Claim`s, each with `Citation`s | `core/contracts/answer.py` |
| synthesis → portfolio | `Stance` + `Breaker`s | `agents/synthesis/agents.py` |
| portfolio → surface | `SizingDecision` or `NoPosition` | `engines/sizing/decision.py` |
| any → any money | `Money(amount, currency, fx_asof)` | `core/contracts/money.py` |

**`Finding` is the load-bearing one.** An evidence agent may not return prose.
It returns a finding with `numbers: dict[str, float]` and `caveats: list[str]`
alongside its text, so the synthesis layer can compose *and* the trace can show
what each agent actually contributed. Agents never call each other — a1 cannot
ask a4 for anything. Everything meets at synthesis.

## The refusal path is a first-class outcome

Three places return a refusal rather than an answer, and all three are normal:

1. **`a0_supervisor.plan()`** returns `Plan(allowed=False, refusal=...)` for a
   question the system will not answer — an order, a point price forecast, a
   question whose minimum honest plan costs more than the budget.
2. **`verify_claim()`** drops a claim whose citation does not verify verbatim.
   An `Answer` with every claim dropped has `answered=False`.
3. **`size()`** raises `NoPosition` — not an error. At a given capital level,
   lot granularity or the cost floor may mean the correct size is zero.

`trace_run.py` counts 10 refusals in an end-to-end run. That number going *down*
without a reason is a regression, not an improvement.

## Where each boundary is actually enforced

A rule described in a docstring is a suggestion. These are the mechanisms:

| Rule | Enforced by | Failure mode if removed |
|---|---|---|
| an agent may only use its registered tools | `Agent._guard_tool` against the registry-derived allowlist | any agent could place an order |
| a tier is never chosen by a caller | `TaskClass → Tier` in `core/llm/tiers.py`; passing a tier raises `TierRoutingError` | cost drifts silently to the expensive tier |
| every model call is recorded | `core/provenance/ledger.py`, SQLite `RAISE(ABORT)` triggers on UPDATE and DELETE | a month of cost history rewrites itself |
| a claim carries a verifiable quote | `verify_claim` does verbatim substring matching, `MIN_QUOTE_CHARS = 8` | a plausible paraphrase becomes a citation |
| no execution code exists | `tests/test_no_execution_anywhere.py` greps every file | the boundary erodes one helper at a time |
| nothing registers without an eval suite | `core/registry/loader.py` refuses at load | an agent ships with no negative cases |
| money knows its currency | `Money` + `CapSet.currency` + `CurrencyMismatch` | see `07-DEFECT-LOG.md` §4 |
| a graph claim has a live, sourced path | three gates in `knowledge/graph/entity_graph.py` | "connected via Malaysia" reads as a finding |

Each of these has at least one test whose only job is to fail if the mechanism
is removed. Those are the **ratchets** — see `03-INVARIANTS.md`.

## Data flow for one real question

`python ask.py why MYX:1155 --move -0.090 --market -0.080 --sector -0.020`

1. **a0** classifies intent as `WHY_IT_MOVED`, picks the documented playbook,
   estimates cost against the per-question budget, returns an allowed `Plan`.
2. **a9** decomposes the −9.0% move: market −8.0%, sector −2.0%, and what is
   left is idiosyncratic. It computes an `unexplained_share`.
3. If the unexplained share is small, the verdict is **not** "here is the
   cause" — it is `no_identified_catalyst`. The system says a routine dividend
   cannot explain a 7% move rather than proposing the nearest headline.
4. **a5** offers candidate causes from the event window, each with a base rate.
5. Every number reaching the surface is labelled with which agent produced it
   and whether it was stated by the user or measured from a feed.

The step that matters is 3. The most common failure of a "why did it move"
system is fluent causal attribution to whatever news happened to be nearby.
`engines/attribution/decompose.py` is built so that the honest answer — *most of
this was the market, and the rest is unexplained* — is the default, not a
fallback.
