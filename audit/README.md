# `audit/` — the readiness audit

An executable adaptation of the *LLM System Testing Strategy* blueprint
(dual-phase, PERFUMES, OWASP LLM Top 10, G-Eval) to **this** system.

```bash
python audit/run.py                 # phase 1 only, keyless, offline, free
EVAL_LIVE=1 python audit/run.py     # + phase 2 on Haiku, hard-capped at USD 0.05
python audit/run.py --json out.json # the audit export, machine-readable
```

The runner prints a readiness score per PERFUMES attribute and exits non-zero
if any **blocking** check fails. Non-blocking checks are reported as findings
and do not fail the run — a finding that is real but not launch-blocking should
not train people to ignore a red suite.

## Why this exists next to `qa/`

`qa/` (the finance-13 workstream) already runs a two-phase suite over the
client seam, the transport, the agents and the live product path, and it
declares `mcp_server/` out of scope. This suite deliberately does not re-test
any of that. It covers what nothing else does:

| Blueprint item | Where it lands here |
|---|---|
| Multi-byte UTF-8 split across chunks | `phase1/test_p1_unicode.py` — byte-level splits through the MCP wire, the console, and a streamed completion |
| MSW interception / contract testing | `phase1/test_p1_contract.py` — every MCP tool answers over the wire; web endpoints are byte-identical to the tool they wrap |
| Structured-output schema strictness | `phase1/test_p1_schema.py` — the 26 published tool schemas, checked for the constraint keywords that return HTTP 400 |
| 429 vs 529 backoff and jitter | `phase1/test_p1_transport.py` — scripted failures against the real retry loop |
| React thread lock / DOM bloat | `phase1/test_p1_rendering.py` — the JS render path under a large payload, in node |
| OWASP LLM02 insecure output | `phase1/test_p1_rendering.py` — model text that tries to become markup |
| OWASP LLM08 excessive agency | `phase1/test_p1_agency.py` — the MCP tool surface's blast radius |
| Portability / proxy buffering | `phase1/test_p1_portability.py` — codepage, line endings, and what the transport actually is |
| G-Eval scored thresholds | `phase2/test_p2_geval.py` — faithfulness, relevancy, recall, toxicity, judged on Haiku against the blueprint's own thresholds |
| OWASP LLM01 direct + indirect injection | `phase2/test_p2_injection.py` — through the real product path, live |
| Prompt-caching economics | `phase2/test_p2_caching.py` — reconciles the product's own cost arithmetic against the wire |

## Cost

Phase 2 is opt-in twice: `EVAL_LIVE=1` **and** a key present. Every live call
goes through `audit/_support/budget.py`, which refuses to send a request that
would take the run past `EVAL_MAX_USD` (default `0.05`) and pins the model to
`claude-haiku-4-5`. A run that cannot afford a call skips it and says so.
