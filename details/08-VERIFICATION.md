# 08 — Verification

Five independent checks, each answering a different question. All five run in
CI, offline, with no keys.

---

## Why five and not one

| Check | Question it answers | What it would miss alone |
|---|---|---|
| `make test` | does each unit behave? | whether the units are wired together |
| `make verify` | do the documented guarantees hold end to end? | anything adversarial |
| `make stress` | what happens at the edges and under attack? | whether the real allowlist works |
| `make trace` | does the whole system run with the **real** registry? | fine-grained correctness |
| `make mcp-check` | can an external client actually talk to it? | everything internal |

The overlap is deliberate. A single suite that answers all five questions is a
suite nobody reads the output of.

---

## 1. `make test` — 1,848 tests

76 test files. Runs in about 70 seconds.

| File | Covers |
|---|---|
| `test_agents.py`, `test_evidence_agents.py`, `test_ask_cli_agents.py` | the sixteen agents and the tool seam |
| `test_answer_contract.py` | `Claim`, `Citation`, `verify_claim`, conjunctive chains |
| `test_money.py`, `test_currency_boundary.py` | the money contract and the MYR boundary (14 tests) |
| `test_risk_sizing.py` | five caps, `binding()`, `NoPosition`, concentration |
| `test_market_foundation.py`, `test_market_aliases.py` | eleven adapters, fee schedules, lots, ticks, alias resolution |
| `test_graph_*.py` (8 files) | schema, store, ids, extraction, build, analysis, code graph, surfaces |
| `test_provenance.py`, `test_phase0_durability.py`, `test_sidecar.py` | the append-only ledger, WAL, concurrent writers |
| `test_guardrail_chain.py` | the five rails |
| `test_tier_routing.py` | `TaskClass → Tier`, `TierRoutingError` |
| `test_registry.py` | the eval ratchet |
| `test_no_execution_anywhere.py` | **the ratchet**, also run as its own CI step |
| `test_rag.py`, `test_news_events.py`, `test_feeds_graph.py`, `test_gdelt_feed.py` | retrieval, chunking, news features, feeds |
| `test_attribution.py`, `test_backtest.py`, `test_pointintime.py` | decomposition, walk-forward, as-of reads |
| `test_learning*.py` | reflection, scoring, the lesson store, the curriculum |
| `test_mcp_server.py`, `test_anthropic_backend.py`, `test_inference_client.py` | the MCP surface and the model seam |
| `test_config.py` | config loading, and the Makefile/`run.bat` parity ratchet |
| `test_trace.py`, `test_render.py` | spans and terminal output |

## 2. `make verify` — 14 sections

`verify.py`. Proves the documented guarantees on mock data. Prints `[OK]` per
check and `PASS` or a non-zero exit.

1. contracts · 2. guardrail chain · 3. citation verification · 4. provenance
ledger · 5. money contract · 6. attribution engine · 7. **risk and sizing** ·
8. news features and catalyst matching · 9. **agents: the seam and the
refusals** · 10. **graph: no path, no claim** · 11. reflection · 12. registry
ratchet · 13. teacher order · 14. surface

Sample output from §7:

```
7. Risk and sizing
  [OK] 10 correlated names read as ~1 bet - 1.16 effective bets
  [OK] breaches reported
  [OK] Bursa minimum economic position - RM 4,706
  [OK] market currency comes from the adapter
  [OK] an MYR cap cannot size a USD price
  [OK] an 8% cap never funds a 33% position
       - USD 9,360 = RM 39,312 of RM 500,000
```

That last line is the currency defect stated in the units that matter to the
holder. Before the fix it read RM 167,832.

## 3. `make stress` — 158 held, 0 findings, 2 notes

`stress/run.py`. **Adversarial, not a second happy path.** Exits with the
finding count, so CI fails on a new one.

Ten sections:

| Section | Attacks with |
|---|---|
| 1. Volume | 500 names, 6,500 bars, 2,000-node graphs |
| 2. Numbers | NaN, infinity, negatives, values outside their own range |
| 3. Boundaries | inputs sitting **exactly** on a threshold |
| 4. Concurrency | eight simultaneous writers |
| 5. Injection | prompt injection, null bytes, 200k-character bodies, SQL, path traversal |
| 6. Invariants | the properties that must hold for any input |
| 7. Live seams | a broken source must never look like a quiet one |
| 8. Registry drift | every alias reaching the same adapter and the same floor; **the currency probe** |
| 9. MCP surface | a model that argues with the tools |
| 10. Knowledge graph | hub routing, parallel edges, byte-identical rebuilds |

### The two standing notes

Not findings — known, bounded, and named so nobody rediscovers them as news:

1. **`config` accepts a relative traversing db path.** `'../../../../tmp/pwned.db'`
   is stored as given. Low risk — the operator owns the file — but the path is
   never normalised or confined to the project.
2. **Injected text is echoed in thesis output.** It is quoted *as evidence*,
   which is correct behaviour — but the model sees it.

### The currency probe

Added with the MYR boundary. Sizes an 8% slice of a MYR 500,000 book on every
registered market and checks the result back in MYR:

```
HELD  XASX: 8% cap holds in MYR  -- AUD 14,285.71 = RM 40,000.00
HELD  XNAS: 8% cap holds in MYR  -- USD  9,523.81 = RM 40,000.00
HELD  XTKS: 8% cap holds in MYR  -- JPY 1,428,571.43 = RM 40,000.00
HELD  XLON: 8% of RM 500,000 buys nothing  -- round-trip cost is 75 bps ...
```

> The probe initially reported three false findings. `to_quote` then `to_base`
> does not round-trip in the last of `Decimal`'s 28 digits, so a position
> sitting **exactly** on its cap read as a hair above it. That was the
> comparison, not the code. Fixed with one sen of tolerance — the smallest real
> overshoot is one board lot, roughly RM 40, so the probe still catches
> everything it was written for.

## 4. `make trace` — 16/16 agents, 0 errors, 11 refusals

`trace_run.py`. **The only end-to-end run that uses the real, registry-derived
allowlist.** `verify.py` and the unit tests pass hand-written allowlists with
invented agent ids, which is how a missing `llm_complete` grant survived
unnoticed.

Writes `debug/<run-id>/`:

| Artefact | Contents |
|---|---|
| `trace.jsonl` | every span, every emitted event |
| `report.html` | the run as a navigable tree |
| `anatomy.md` | what each agent did, in prose |
| `session.log` | the flat log |
| `summary.json` | counts, timings, refusals |
| `prompts/` | every prompt sent (empty offline — the stub sends none) |

Since the currency work, the trace also carries `sizing.foreign` (a USD cap and
its MYR equivalent) and `sizing.no_rate` (the refusal when no rate is given).

## 5. `make mcp-check` — SELFTEST PASS

`python -m mcp_server.server --selftest` runs the full handshake against itself:
`initialize → tools/list → tools/call`, plus a notification that correctly gets
no response. If the server cannot do that offline, no client will fare better.

---

## CI

`.github/workflows/ci.yml` — Python 3.11, ubuntu-latest, on every push and pull
request, plus `workflow_dispatch`.

> `workflow_dispatch` was added after a push produced **no run at all** — not
> queued, not slow, no run object — with no way to retry short of an empty
> commit, which is worse than the missing check it would paper over.

Steps, in order:

1. install
2. `pytest -q`
3. `verify.py` — *"No network, no keys. If this needs either, the seam has leaked."*
4. `stress/run.py`
5. **eval ratchet** — loads the registry and prints every agent with its suite
6. **knowledge graph builds twice, identically** — `cmp`
7. **codebase graph builds twice, identically** — `cmp`
8. traced full-system run
9. MCP server selftest
10. **no execution code anywhere** — run as its own step

Steps 5–10 are the ones that would not be missed if they were quietly deleted,
which is precisely why each is a named step rather than a line inside the suite.

---

## Coverage of the defect log

Every entry in `07-DEFECT-LOG.md` has a test that fails without its fix.
`tests/test_currency_boundary.py` is the clearest example — 14 tests, each of
which fails on the code as it stood, and none of which needed new machinery to
write. That is the uncomfortable part: the arithmetic was always visible. It
just never had a currency attached to disagree with.
