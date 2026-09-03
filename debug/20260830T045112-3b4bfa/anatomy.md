# Anatomy of run `20260830T045112-3b4bfa`

> CONTAINS VERBATIM PROMPTS AND RESPONSES. This is a debugging artefact, not an audit record - it may hold portfolio positions, instrument ids and model output. debug/ is gitignored. Read before sharing.

**full-system** · 118 events · 63 ms wall · 4 model calls · RM 0.0082

## Organs — what fired

| Agent | Invocations | Model calls | Retrievals | Tools used | Denied | Cost MYR | ms |
|---|--:|--:|--:|--:|--:|--:|--:|
| `a0_supervisor` | 1 | 0 | 0 | 3 | 1 | 0.0000 | 0 |
| `a10_thesis` | 1 | 1 | 0 | 3 | 0 | 0.0026 | 0 |
| `a11_red_team` | 1 | 1 | 0 | 2 | 0 | 0.0026 | 0 |
| `a12_portfolio_risk` | 1 | 0 | 0 | 1 | 0 | 0.0000 | 0 |
| `a13_sizing` | 1 | 0 | 0 | 1 | 0 | 0.0000 | 0 |
| `a14_teacher` | 1 | 0 | 0 | 1 | 0 | 0.0000 | 0 |
| `a15_reflection` | 1 | 1 | 0 | 2 | 0 | 0.0026 | 0 |
| `a1_fundamentals` | 0 | 0 | 0 | 1 | 1 | 0.0000 | 0 |
| `a2_valuation` | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0 |
| `a3_price_technical` | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0 |
| `a4_news_narrative` | 0 | 1 | 0 | 2 | 1 | 0.0005 | 0 |
| `a5_catalyst_events` | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0 |
| `a6_macro_regime` | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0 |
| `a7_sector_technology` | 1 | 0 | 0 | 1 | 0 | 0.0000 | 0 |
| `a8_ownership_flow` | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0 |
| `a9_attribution` | 1 | 0 | 0 | 1 | 0 | 0.0000 | 0 |

## Circulation — what handed work to what

*No nested agent calls in this run — every agent was invoked at the top level.*

## Skeleton — event vocabulary

| Kind | Count | Means |
|---|--:|---|
| `agent` | 9 | an agent method ran |
| `allowed` | 25 | a guardrail permitted an action |
| `denied` | 3 | a guardrail refused an action |
| `engine` | 43 | a deterministic engine computed something |
| `llm_call` | 4 | a model was called; full prompt and response in prompts/ |
| `refusal` | 7 |  |
| `run_end` | 1 | the run finished |
| `run_start` | 1 | the run began |
| `span` | 7 | a named stage began |
| `span_end` | 16 | a named stage finished |
| `verification` | 2 | claims checked against their cited chunks |

## Slowest

| Stage | Kind | ms |
|---|---|--:|
| registry | span | 41.1 |
| a7_sector_technology | agent | 8.8 |
| a9_attribution | agent | 4.2 |
| a0_supervisor | agent | 1.9 |
| inference | span | 1.6 |
| markets | span | 1.4 |
| portfolio | span | 1.1 |
| a13_sizing | agent | 0.8 |
| config | span | 0.7 |
| thesis_and_red_team | span | 0.5 |

---

`trace.jsonl` holds every event, one JSON object per line.
`prompts/` holds every value too long to inline — prompts, responses, 
and any other large string, referenced from the trace by filename.
