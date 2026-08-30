# 03 — Invariants

Every rule this system refuses to break, where it is enforced, and what test
fails if the enforcement is removed.

A rule stated in a docstring is a suggestion. Each entry below names the
**mechanism**, because that is the part that survives a refactor by someone who
never read the docstring.

---

## The ratchets

A *ratchet* is a test whose only job is to fail when a guarantee is quietly
removed. Six of them.

### 1. No execution code, anywhere

`tests/test_no_execution_anywhere.py`

Greps every file in the repository for order-placement vocabulary. A file that
must contain the words — the registry's `FORBIDDEN_TOOLS`, the supervisor's
refusal path, the design artboard that renders a *denied* `place_order` — is
listed in `ALLOWED_PATHS`, and the test **also asserts each allowed path still
contains the pattern**. So the allowlist cannot be padded with files that do not
need to be on it.

> This is the invariant that makes the whole system safe to run unattended. It
> is checked as its own CI step, separately from the suite, so it cannot be
> lost in a sea of dots.

### 2. Nothing registers without an eval suite

`core/registry/loader.py`, exercised by `tests/test_registry.py` and a dedicated
CI step.

Refuses **at load time** if an agent has no `eval_suite`, or a suite has no
negative cases. A capability that cannot be shown to fail correctly is not
registered, so it cannot be called.

### 3. The provenance ledger is append-only

`core/provenance/ledger.py` — four SQLite triggers:

```sql
BEGIN SELECT RAISE(ABORT, 'provenance ledger is append-only'); END;
```

on UPDATE and DELETE. Not a convention, not an ORM setting: the database itself
refuses. `tests/test_provenance.py` tries to update a row and asserts the abort.

### 4. A tier is never chosen by a caller

`core/llm/tiers.py` — `route(TaskClass) -> Tier`. There is no code path that
accepts a `Tier` from a caller; attempting one raises `TierRoutingError`.
`tests/test_tier_routing.py`.

### 5. The knowledge graph builds reproducibly

CI builds the graph twice into two paths and runs `cmp`. Byte-identical or the
build fails. Done for both the finance graph and the code graph.

A build that is not reproducible cannot have its diff reviewed, and an
unreviewable diff is how a bad edge lives for a year.

### 6. Configuration cannot silently diverge from the tooling

`tests/test_config.py` checks that every `Makefile` target also exists in
`run.bat`, so a Windows user is not quietly running a smaller set of checks.

---

## Contract invariants

### Money knows its currency

| Rule | Where |
|---|---|
| every monetary field is `(amount, currency, fx_asof)` | `core/contracts/money.py` |
| `currency` is three **letters**, not merely three characters | `Money.upper` validator |
| conversion at a non-positive rate raises | `Money.convert` |
| same-currency conversion is an identity that **ignores** the rate | `Money.convert` |
| a `CapSet` names the one currency all five caps are in | `engines/sizing/caps.py` |
| crossing currencies without an explicit dated rate raises `CurrencyMismatch` | `to_base` / `to_quote` |
| a market's currency is read off its adapter, never passed in | `markets.registry.market_currency` |

`tests/test_money.py`, `tests/test_currency_boundary.py` (14 tests).

### A claim carries a verifiable quote

| Rule | Where |
|---|---|
| citation matching is **verbatim substring**, not semantic | `verify_claim` |
| a quote under `MIN_QUOTE_CHARS = 8` cannot support a claim | `core/contracts/answer.py` |
| a claim whose citations fail is **dropped**, with a `dropped_reason` | `verify_claim` |
| a conjunctive chain requires **all** citations, not any | `Claim.all_citations_required` |
| an `Answer` with every claim dropped is `answered=False` | `Answer` |

The `all_citations_required` flag exists because the default — keep the claim if
*any* citation verified — is right for redundant support and wrong for a chain.
"A supplies B, and B is exposed to C, therefore A is exposed to C" is not
three-quarters true when one link fails.

### The graph's three gates

A graph edge may back a claim only if it passes all three:

| Gate | Rule |
|---|---|
| structure | `weight ∈ (0, 1]` |
| time | `valid_from ≤ asof < valid_to` — half-open, so an edge that ended today is not live today |
| evidence | `confidence is EXTRACTED` **and** `sourced` |

`Edge.citable` is the conjunction. `path_to_citations` is **all-or-nothing**:
a path yields citations only if every hop is citable. There is no partially
cited path.

`Confidence` defaults to `AMBIGUOUS` — the weakest value — so an extractor that
forgets to set it produces something traversable and displayable that cannot
back a claim.

### Risk limits have bounds that config cannot loosen

`engines/risk/concentration.py`:

```python
if self.single_name > 0.15:
    raise ValueError("single-name cap cannot be raised above 15%")
if self.min_effective_bets < 3.0:
    raise ValueError("effective bets cannot be set below 3")
```

`config.toml` sets *defaults*. The bounds are in code and are not file-settable.
A risk limit you can loosen by editing a config file is not a risk limit; it
gets loosened on exactly the day it would have saved you.

### Numbers stay inside their own range

Found by stress testing, each now refused at the source:

| Function | Was | Now |
|---|---|---|
| `hhi` | weights `[-0.5, 1.5]` returned 2.5, outside `[0, 1]` | negative and non-finite weights refused |
| `effective_number_of_bets` | a correlation of 2.0 gave 0.67 bets from 2 positions | matrix validated, result clamped to `[1, n]` |
| `liquidity_cap` | negative ADV passed through, and a negative cap **always wins** `min()` | refused at the source |
| `decompose` | a NaN return produced `unexplained_share = nan` rendered as "nan% unexplained" | returns `attribution_unavailable` naming the field |

The common thread: none of them crashed. Three of the four would have shown a
*more alarming* reading than the truth.

---

## Behavioural invariants

| Rule | Consequence if broken |
|---|---|
| evidence agents never call each other | findings stop being independent; one agent's error propagates as corroboration |
| an agent may only use its registered tools | the allowlist stops being a control |
| `llm_complete` is granted to exactly **four** agents (a4, a10, a11, a15) | an allowlist naming every capability for every agent is not an allowlist |
| a disabled feed says so rather than returning `[]` | "no news" and "the feed is broken" become indistinguishable |
| `NoPosition` is an outcome, not an error | the interface trains its user to expect a position every time |
| a refusal names what would help | the user cannot tell a limitation from a bug |
| `created_by: human` knowledge is read-only to every agent, forever | the system edits its own evidence |

---

## What is deliberately *not* enforced

Honesty about the boundary matters as much as the boundary.

- **Curated graph data is not verified against primary sources.**
  `knowledge/graph/data/supply_chain.yaml` rows and the Bursa stock codes in
  `entities.yaml` were written by hand. Each row carries a `source_doc_id`, but
  nothing checks that the document says what the row claims. This is human work
  and is listed in `10-STATUS-AND-GAPS.md`.
- **FX rates are supplied per call.** `FxStore` takes explicit dated rates and
  no feed populates it. The rate comes from whoever calls `--fx`.
- **The paper-trade gate is time-blocked.** docs/05 requires 3–6 months of
  forward-tested, calibration-checked results before a signal moves money. No
  amount of code shortens that.
