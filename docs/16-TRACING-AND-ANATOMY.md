# Tracing: every bone, every organ

    make trace                       # full system run, offline, no keys
    python trace_run.py --live MYX:1155   # also hit the real price feed

Writes `debug/<run_id>/`. **Gitignored, and deliberately so** — a trace holds
verbatim prompts, model responses, and whatever portfolio positions were passed
in.

## Why this is separate from the provenance ledger

They have opposite jobs, so they have opposite trade-offs:

| | Provenance ledger | Trace |
|---|---|---|
| Lifetime | permanent | until the bug is found |
| Prompts | `prompt_hash` only | verbatim |
| Size | small, bounded | large |
| Mutability | append-only, trigger-enforced | rewritten each run |
| In git | the path is configured | never |

The ledger answers *"what did this cost and who claimed what"*, forever. The
trace answers *"what exactly happened this one time"*. Making the ledger store
prompts would break the first job to serve the second.

They cross-reference: every `llm_call` event carries the `prompt_hash` the ledger
recorded, and every run carries a `run_id` the ledger also stores.

## What lands in a run directory

| File | Answers |
|---|---|
| `session.log` | "walk me through the run" — chronological, indented by call depth |
| `anatomy.md` | "what are the moving parts and which fired" — aggregated per agent, plus a call graph |
| `report.html` | "let me dig" — the same, navigable, prompts inline, theme-aware |
| `trace.jsonl` | machine-readable, one event per line, flushed as it happens |
| `prompts/` | every value too long to inline, whole |
| `summary.json` | counts, timings, cost, errors |

`trace.jsonl` is flushed per event on purpose: **a trace you only get on clean
exit is useless for the failures worth tracing.** All three reports render from a
torn file, so a hard kill still produces them.

## The event vocabulary

| Kind | Means |
|---|---|
| `llm_call` | a model was called — full prompt, system, response, tokens, tier, cost, latency |
| `allowed` / `denied` | a guardrail decided — which rail, which rule, why |
| `retrieval` | a store was queried — corpus, hits, grade, relevance |
| `verification` | claims checked against cited chunks — **what was dropped and why** |
| `agent` | an agent method ran — inputs, outputs, timing |
| `engine` | a deterministic computation with its inputs and result |
| `refusal` | the system declined, with the reason |
| `feed` | an external source was polled |
| `error` | an exception escaped a span |

Two are worth watching in particular:

- **`verification`** — the mechanical citation check is the only real output gate
  in the system. What it drops is the most informative line in any trace.
- **`allowed`**, not just `denied` — "which rail let this through" is as much a
  debugging question as "what blocked it", and a log of only refusals cannot
  answer it.

## Cost when off

`is_tracing()` is one attribute lookup against `None`, and every emit site is
guarded by it. A tracer that costs something when disabled is one people turn
off and then cannot turn on when they need it.

## What a clean run looks like

```
events       105 in ~110 ms
model calls  4  (RM 0.0082, EchoBackend)
agents seen  16 of 16 registered
refusals     8
errors       0
```

Sixteen of sixteen is asserted by `tests/test_trace.py` — a traced run that
misses an agent silently under-reports what the system contains.

Eight refusals is not a fault. `docs/14` §3: **the refusals are the product.** A
run with none means a guardrail stopped firing.

## What the first traced run found

`InferenceClient.complete()` enforces an `llm_complete` tool action, and **no
agent in `agents/registry.yaml` declared it.** So the registry-derived allowlist
— the one `ask.py` and the MCP server both build — denied every model call from
every registered agent.

It survived because `verify.py` and every test pass *hand-written* allowlists
with invented ids (`{"a4": {"llm_complete"}}`) rather than the real registry. The
seam was exercised; the grant never was. Nothing failed, because nothing had yet
tried to reason with a real key.

`llm_complete` is now granted to exactly four agents — a4 triages prose, a10
writes the thesis, a11 argues against it, a15 judges lessons. The other twelve
are denied, and `a0_supervisor` is denied deliberately: routing here is
deterministic, and a trace showing A0 reasoning would mean that changed.

## Reading a trace when something looks wrong

1. `summary.json` → errors and refusal count first.
2. `anatomy.md` → did the agent you expected actually fire? A zero row means it
   was never invoked, which is different from invoked-and-found-nothing.
3. `session.log` → find the `verification` events. Claims dropped there are
   claims the citation check could not support.
4. `report.html` → open the `llm_call` you care about and read the prompt whole.

`docs/14` §6 is the companion: in a system whose job is to express uncertainty,
**a bug does not look like a crash, it looks like a slightly-too-humble answer,
forever.** The trace is how you tell the difference between honest uncertainty
and a gate that quietly stopped firing.

---

## Added in the 2026-08-31 hardening pass

- Every run directory now carries `manifest.json` - a methodology hash over
  the system prompts, the registry, the tool surface and package versions
  (never run_id or time). Equal hash = equal methodology; `RunManifest.diff`
  names what changed between two runs.
- Retention: `Tracer.prune` keeps the newest 20 runs within 14 days and runs
  on every start. The in-memory event list is bounded (the FILE is complete).
- `FINPLANET_TRACE_SYNC=0` batches flushes for long unattended runs; the
  default remains flush-per-event so a crash still leaves everything on disk.
- Each `llm_call` event records the API `request_id` alongside tokens, cache
  reads/writes and cost.
