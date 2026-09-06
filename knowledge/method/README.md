# knowledge/method - the five human-written knowledge stores, as notes

`agents/registry.yaml` names five stores that a person writes and no agent may
write: `kb_craft` (the curriculum), `kb_method_valuation`, `kb_method_technical`,
`kb_method_risk` and `kb_failures` (the blowup library). Until 2026-09-06 every
one of them was registered empty. The notes in these folders fill them;
`knowledge/retrieval/method.py` loads them into the shared router on every
surface, and `ask.py doctor` prints how many chunks each store holds.

## The contract, per note

One note per file, `<collection>/<slug>.md`:

```
---
title: A sentence that says what the note claims
as_of: 2026-09-06            # written or last reviewed; the chunk's date
licence: own                 # always: this system's own writing, quotable in full
concepts: [cash_flow]        # kb_craft only: the curriculum keys it teaches
archetypes: [bank]           # kb_method_valuation only: where the method applies
patterns: [receivables_run]  # kb_failures only: tags from method.PATTERN_TAGS
case: {name: ..., country: MY, year: 2007, outcome: ...}   # kb_failures only
base_rate: {n: 0, unvalidated: true}   # kb_method_technical: measured or not
refs:
  - {title: ..., url: ..., licence: attributed}   # open | attributed | link_only
---
1. First section
...
5. References
```

Rules the tests enforce (`tests/test_method_notes.py`):

* `licence: own` on every note; every reference carries `open`, `attributed`
  or `link_only`. A `link_only` reference is a title and a URL; its body is
  never reproduced (docs/06 section 7).
* ASCII only. Windows opens text as cp1252 unless told otherwise, and a note
  that renders on Linux and raises on Windows is a failure this CI has had.
* At least three numbered sections (`1. Title`). The chunker splits on them,
  so a citation points at one section and the verifier finds the quoted span.
* Every note body passes the OUTPUT rail: no advice language. Ranges and
  mechanisms, never verbs. This is personal research; in Malaysia, issuing
  research to the public needs a CMSRL from the Securities Commission.
* Every one of the teacher's 30 concept keys appears in some `kb_craft` note;
  every `kb_failures` tag is in the pinned vocabulary; every valuation note
  names its archetypes.

## Who reads what

| Store | Reader | How |
|---|---|---|
| `kb_craft` | A14 teacher | retrieves the note for a concept and cites it; `Concept.sources` points at the file |
| `kb_method_valuation` | A2 valuation | retrieves the method note for the archetype; the cost-of-capital table's rows are citable chunks too |
| `kb_failures` | A11 red team | retrieves on the structural PATTERN a thesis's flags imply and returns analogues, never predictions |
| `kb_method_technical`, `kb_method_risk` | `ask.py method`, MCP `method_note` | read on request; the technical notes carry a `base_rate` field that starts unvalidated |

## `data/cost_of_capital.yaml`

Damodaran's published equity risk premiums, country premiums, industry betas
and synthetic rating spreads, transcribed with their update date. Free to use
with attribution. A `null` is "not transcribed yet" and is never read as
zero; the table flags itself stale after `stale_after_days`.
