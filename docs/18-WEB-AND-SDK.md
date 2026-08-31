# 18 — The web surface and the SDK seam

Decision record for the 2026-08-31 hardening pass: what was adopted, what was
refused, and why the refusals are the important part.

---

## 1. The dependency commitment, revised honestly

The repository shipped for months on exactly two runtime dependencies, and the
docs bragged about it. That constraint bought something real — every test
offline, every CI run keyless — and those PROPERTIES are the commitment, not
the number. The pass added three runtime dependencies, each behind a seam:

| Dependency | Seam | What keeps the properties |
|---|---|---|
| `anthropic` (official SDK) | `core/llm/backends.py`, lazily imported | `EchoBackend` answers when no key; no engine or test imports the SDK |
| `fastapi` + `uvicorn` | `web/` only | Nothing outside `web/` imports them; the app binds 127.0.0.1 |

Every offline/keyless guarantee still holds and is still tested. The claim
"two dependencies" is gone from the docs because it stopped being true; the
claim "no network and no keys to run anything" remains, because it didn't.

## 2. Why the official SDK, and what was deliberately kept

The urllib backend re-derived typed errors and response parsing from raw JSON —
work the vendor ships and maintains. The SDK took that over. Three things were
NOT handed over, on purpose:

- **The retry loop stays ours** (`max_retries=0` on the SDK client): the live
  QA pass pinned retry-after honoured and capped at 60s with an injectable
  sleep, and an SDK-internal loop would make that untestable offline.
- **Refusal-as-content**: `stop_reason=refusal` becomes a ledgered
  `Completion(refused=True, …)`, mirroring every REFUSED string in the MCP
  tools. A refusal is an answer.
- **No server-side fallbacks beta.** Commitment 6 — refusal beats invention. A
  model refusal must surface as a refusal, never be silently re-routed to a
  different model that might comply.

Request shaping is a per-tier table (`tiers.REQUEST_PROFILES`): the reasoning
tier gets adaptive thinking, an effort level and streaming; the cheap tier gets
neither, because Haiku rejects both with a 400. `FINPLANET_CHEAP=1` — the
standing budget rule for live testing — resolves every Messages tier to the
cheapest model, moving model and price together so the ledger records what was
truly spent, and every surface that names a backend names the cap.

## 3. Why FastAPI on loopback, and what the web app can never do

The twelve artboards in `design/` needed a server; stdlib `http.server` would
have meant hand-rolling routing, validation and static serving — the exact
re-derivation the SDK decision just retired on the model seam. FastAPI is
confined to `web/`.

The load-bearing design choice is **parity, not a second implementation**:
every endpoint calls the SAME tool function the MCP server exposes, and the
response `text` is byte-identical by test. The screens are vanilla ES modules
with no build step; tool and model text reaches the DOM through `textContent`
only; design tokens are exported from `design/_css.txt` minus the font import
so the app renders with the network cable pulled.

What it can never do, in the same breath as everything else here: place,
route, or simulate an order. The no-execution grep scans `web/` — `.js`,
`.html` and `.css` included — on every push.

## 4. The seams that exist for a later decision

- `ChainedFeed` + the per-feed suffix maps make a third price source one class.
- `knowledge/feeds/adapter.py` remains the contract for the 32 keyless news
  sources that are registered but unbuilt — a scoping judgement, not a TODO.
- The hand-rolled MCP transport keeps its documented seam: if it is ever
  swapped for the official `mcp` SDK, the tool functions do not change.
