# Security

## What this system can and cannot do

- **No order placement.** There is no broker client, no execution verb, no
  simulated fill anywhere in the repository. `tests/test_no_execution_anywhere.py`
  greps every source file on every CI run and fails if one appears. This is a
  structural guarantee, not a setting.
- **The model never decides a number.** Every position size, cap, and risk
  figure is computed by tested engines. A language model is handed the result
  and writes prose; it is never handed a lever.
- **Local only.** The web server binds `127.0.0.1`. The MCP server speaks over
  stdio. Nothing listens on a network interface.

## Secrets

- `.env` holds credentials and is gitignored. `.env.example` documents keys
  with placeholder values only.
- **Rotate a key the moment it may have been seen** - pasted into a chat,
  captured in a trace, echoed in a terminal. Anthropic keys are revoked from the
  Console; the old key stops working immediately.
- Traces under `debug/` contain **verbatim prompts and responses** and may
  include portfolio positions. They are gitignored and pruned automatically
  (`run trace-prune`), but treat the directory as sensitive.
- The provenance ledger stores a prompt **hash**, never the prompt.

## Spend

- Every model call passes one budget check (`InferenceClient`) against a
  24-hour window; exceeding it raises rather than downgrading silently.
- Set `FINPLANET_CHEAP=1` to force every tier to the cheapest model for
  development. Live QA runs are metered and capped in USD.

## Reporting

This is a single-user research tool. If you find a way for it to place an
order, leak a key, or let the model choose a number, open an issue describing
the path - that is a design failure, and the fix belongs in the guardrail chain,
not in a prompt.
