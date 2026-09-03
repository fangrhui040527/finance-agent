# `details/` — the system as actually built

This folder is the **as-built record**. It describes the code that exists, the
rules it enforces, the defects found while building it, and what is still
missing.

It is deliberately separate from `docs/`:

| Folder | What it is | Written |
|---|---|---|
| `docs/` | the **design specification** — what the system should be, and why | before and alongside the code |
| `details/` | the **as-built reference** — what the code does, file by file, rule by rule | from the code, after it worked |

Where the two disagree, `details/` is the one that was checked against a running
`python -m pytest`. Every number in this folder was produced by running
something, not recalled.

---

## The system in one paragraph

**FinPlanet Module 5, "The Analyst Mind"** is a multi-agent equity research
system for a single Malaysian retail investor. Sixteen agents produce typed
findings; a synthesis layer composes them into a thesis and then argues against
it; a portfolio layer turns a stance into lots or into a refusal. Every claim
carries where it came from, and a claim whose citation cannot be verified
verbatim is dropped rather than softened. It places no orders — there is no
execution code anywhere in the repository, and a test enforces that.

## Measured state

Run on the commit that added this folder.

| | |
|---|---|
| Python modules | 169 files, 29,286 lines |
| Tests | **1,073 passing** |
| `verify.py` | PASS — 14 sections |
| `stress/run.py` | **161 held, 0 findings**, 2 notes |
| MCP selftest | PASS |
| `trace_run.py` | 16/16 agents, 0 errors, 10 refusals |
| Knowledge graph | 47 nodes, 84 edges, 84/84 citable, 0 hubs |
| Registered agents | 16 |
| Registered markets | 11 (8 currencies) |
| Knowledge stores | 20 |
| Runtime dependencies | **2** (`pydantic`, `pyyaml`) |
| Network required | **none** |
| API keys required | **none** |

## Contents

| File | What it covers |
|---|---|
| [`01-SYSTEM-MAP.md`](01-SYSTEM-MAP.md) | the layers, what flows between them, where each boundary is enforced |
| [`02-MODULE-REFERENCE.md`](02-MODULE-REFERENCE.md) | every package and module, what it owns |
| [`03-INVARIANTS.md`](03-INVARIANTS.md) | every rule the code refuses to break, and where it is enforced |
| [`04-AGENTS.md`](04-AGENTS.md) | the 16 agents, their tools, knowledge and tiers; the registry ratchet |
| [`05-MARKETS-AND-MONEY.md`](05-MARKETS-AND-MONEY.md) | 11 market adapters, fee schedules, and the MYR unit-of-account boundary |
| [`06-KNOWLEDGE-GRAPH.md`](06-KNOWLEDGE-GRAPH.md) | the entity graph: schema, three gates, extractors, reproducible build |
| [`07-DEFECT-LOG.md`](07-DEFECT-LOG.md) | every silent-wrong-answer defect found and fixed, with the magnitude of each |
| [`08-VERIFICATION.md`](08-VERIFICATION.md) | the five independent checks and what each one is for |
| [`09-RUNBOOK.md`](09-RUNBOOK.md) | every command, what it prints, what it needs |
| [`10-STATUS-AND-GAPS.md`](10-STATUS-AND-GAPS.md) | what is built, what is not, and what each gap is blocked on |

## The one idea

Every other decision in this repository follows from one:

> **A wrong number that survives every downstream check is worse than a crash.**

A crash is loud, local and fixed in an afternoon. A finite, plausible,
correctly-typed wrong number is acted on. Most of the machinery here —
the typed contracts, the append-only ledger, the verbatim citation check,
the declared currency, the graph's three gates — exists to convert the
second failure mode into the first.

`07-DEFECT-LOG.md` is the evidence that this was not a theoretical concern.
Every entry in it was a number that looked right.
