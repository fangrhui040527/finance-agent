# 17 — Knowledge graph

The multi-hop question vector search cannot answer: *the Strait of Hormuz just
closed — which of the things I own is exposed, and through what?*

`02-AGENTS-AND-RAG.md` gives that job to A7, and `07-BUILD-ORDER.md` P9 gives it
one non-negotiable rule: **every multi-hop claim ships with its traversal path
attached**, decayed per hop, so a three-hop inference is visibly weaker than a
direct link. An impact claim without a path cannot be emitted.

This document covers phase 1: the schema, the store, and the seam that lets a
graph-backed claim actually reach a user.

---

## 1. What phase 1 fixed, and why it mattered

The graph existed before this phase — 9 node kinds, 10 edge kinds, per-hop decay,
`require_path` — and it could not produce an answer. Not "produced weak answers":
could not produce one, in any configuration.

`Path.evidence()` returned `source_doc_id` **strings**. `Finding.citations`
defaulted empty and A7 never populated it. So every exposure finding reached
`verify_claim` uncited, was dropped with *"no citation verified against its
chunk"*, and when it was the only claim the answer was a refusal. The path was
computed, the evidence was held, and the function that turns a `source_doc_id`
into a `Citation` with a verifiable `quoted_span` did not exist.

`path_to_citations()` is that function. The test that proves it —
`test_a_graph_backed_exposure_claim_survives_the_output_gate` — is the first in
the repository's history to show `answered=True` on a graph-backed claim.

Four further defects were found while reading, all now fixed and tested:

| Defect | What it did |
|---|---|
| `bidirectional=True` minted the reverse edge with the **same kind** | "A supplies B" silently also asserted "B supplies A", reversing the supply chain and every exposure conclusion drawn through it |
| `EDGE_DECAY` was an unguarded dict access at traversal time | A new `EdgeKind` without a decay entry was a `KeyError` in the middle of a user's query rather than a refusal at import |
| `traverse` is not complete, and did not say so | An empty result reads as "no connection exists". It means "not found cheaply" |
| No reverse index | "who supplies X" needed a full scan |

And one found by writing the seam test, in the output gate rather than the graph:

**`verify_claim` kept a claim if ANY ONE citation verified.** Correct when
citations are redundant support for a single assertion; wrong when they are a
chain. A two-hop exposure claim survived with the first hop cited and the second
unverified — reading as evidenced while the chain was broken. `Claim` now carries
`all_citations_required`, which A7 sets. See §5.

---

## 2. What came from graphify, and what did not

[Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0,
studied at `680e3ed`) is a code-knowledge-graph builder. **No code was copied.**
Five ideas were:

| Taken | Why it fits |
|---|---|
| Edge confidence `EXTRACTED / INFERRED / AMBIGUOUS` | The binary `citable` here was the two-value version of the same idea. An edge inferred from two shared sector labels is not an edge stated in a filing |
| Provenance required on **edges**, not only nodes | `source_doc_id` existed; it is now structurally required for an EXTRACTED edge and validated before build |
| A schema gate ahead of assembly | Matches the registry ratchet's posture |
| Pure stages passing plain dicts | An extractor never imports the graph, so it is testable against its source alone |
| God-node filtering | The finance equivalents are `Malaysia`, `banking`, `USD`. A query routed through a country node connects everything to everything (phase 2) |

**One of its choices deliberately inverted.** Graphify validates an extraction,
prints a warning, and builds anyway (`build.py:875-893`), so a malformed
extractor degrades the graph quietly. `knowledge/graph/validate.py` **raises** —
same posture as `RegistryError`, where a malformed registry is a startup failure
and never a warning.

**Not taken:** NetworkX (breaks the two-dependency rule — graphify's base install
also pulls numpy, rapidfuzz and 27 pinned tree-sitter grammars), the LLM semantic
pass, community detection. Its own study notes say *"if you adapt this, use a
DiGraph"*; `_out` here was already directed and already held parallel edges.

**Two things are ours, not ported.** Graphify has no temporal validity at all —
supersession is file-granular, not claim-granular — so §4 is original. And its
`source_location` is a single line marker, never a range, so the citation seam
in §5 could not be adapted from it either.

Its most quotable line is a comment in `export.py:177` explaining why a missing
confidence defaults to the weakest value rather than a midpoint: *"a missing
score is an absence of evidence about strength, so the honest fallback is the
weakest value the rubric allows, not a midpoint that reads as a coin flip."*
`Confidence` defaults to `AMBIGUOUS` for exactly that reason.

---

## 3. Three gates, deliberately separate

Conflating these is how a graph starts laundering inference as evidence.

1. **Structure.** An edge joins two declared nodes, its kind has a decay entry,
   and its weight is in `(0, 1]` — a weight above 1 would let a hop *strengthen*
   a path, which is how a four-hop guess outranks a filing.
2. **Time.** An edge is traversable only at a date its validity interval
   contains. §4.
3. **Evidence.** An edge may back an emitted claim only if it is `EXTRACTED`
   **and** names a source document. An `INFERRED` edge may be traversed and
   shown; it may not be cited.

Gate 3 is two conditions where it used to be one. A source document is necessary
and no longer sufficient: an edge inferred from two companies sharing a sector
label can name the document the labels came from, and that document does not say
the companies are related.

---

## 4. Time

`valid_from` / `valid_to` describe a **half-open interval `[valid_from,
valid_to)`** — the closing date is the first day the relation no longer holds.
Half-open is what makes supersession clean: a contract ending the day another
begins leaves exactly one live edge on that date, not two and not zero.

`traverse(asof=...)` is **required, not defaulted**. A traversal with no date
silently answers a question about today using edges that may not have existed
when the question was asked — the look-ahead bug of
`core/market/pointintime.py`, in graph form.

`valid_from = None` means the start is unknown, and an edge with an unknown start
is **traversable at no date**. That is the point-in-time rule, not an oversight:
an edge that cannot say when it became true cannot be shown to a question asked
before it did. The validator rejects a null `valid_from` outright, so an
extractor cannot produce a silently invisible edge.

---

## 5. The citation seam

```
Path ──path_to_citations(path, lookup)──> [Citation] ──> Claim ──verify_claim──> Answer
```

The graph records *which document* supports an edge. Only the corpus knows the
*text*, so `lookup(source_doc_id) -> Citation | None` is the corpus talking.

**All-or-nothing, twice, on purpose.** `path_to_citations` refuses a path the
corpus can only partially cite, and `Claim.all_citations_required` makes the
output gate refuse the same claim for the same reason. A partially cited path is
worse than an uncited one: the claim passes verification on the hops that were
cited, while the uncited hop carries the actual inferential load. It reads as
evidence and is not.

`all_citations_required` defaults to **False** — for an ordinary claim two
sources are alternatives, and one surviving is enough, which is the
`05-RISK-AND-GUARDRAILS.md` §8.1 rule. It is True only where citations are
conjunctive, one per link.

The graph never gets to say what a document contains. `verify_claim` still
rechecks every `quoted_span` verbatim against the chunk, so a fabricated span is
dropped even when the graph vouched for the edge.

---

## 6. Storage

`knowledge/graph/store.py`, SQLite, shaped exactly like
`core/provenance/ledger.py` and for the same reason: a graph that dies with the
process cannot be reviewed, diffed against yesterday's, or asked what it looked
like last quarter.

- **WAL**, via the ledger's own `_enable_wal` — so a build in one process and a
  query in another do not lock each other out. The ordering subtlety
  (`busy_timeout` first) stays fixed in one place by reusing the helper.
- **Append-only edges.** A relationship that ends is **closed** by setting
  `valid_to`, never deleted. A trigger enforces it: the only permitted update is
  open → closed, once. Deleting an edge would make every conclusion ever drawn
  through it unauditable.
- **Idempotent inserts** keyed on `(src, dst, kind, valid_from)`, so a rebuild
  over unchanged sources produces a byte-identical database — which is a test.
  It also means a re-run cannot reopen an edge that has since been closed.
- **`tier`** marks what produced a row (`deterministic` now, `semantic` later),
  so a deterministic rebuild cannot wipe model-proposed edges. Carried before
  anything writes it, because it is expensive to retrofit.

`load(tier=...)` filters **edges only**. A node is an identity and an edge is an
assertion — which tier first observed that Maybank exists says nothing about who
may reference it, and filtering nodes too would orphan every cross-tier edge.

---

## 7. Traversal is a heuristic

`traverse` is best-first over decayed path weight, bounded by `MAX_HOPS = 4` and
`MIN_PATH_WEIGHT = 0.05`. A node is re-expanded only on a strictly better weight,
so a lighter-but-shorter route into a node is never expanded once a heavier
longer one has reached it — and the heavier one may have no hops left to spend.

`test_traversal_can_miss_a_path_that_exists_and_the_docstring_says_so`
constructs exactly that case. The bound is what keeps a dense graph queryable
(10,000 edges traverse in ~3 ms); the consequence is that **an empty result means
"not found cheaply", never "does not exist"**. No negative claim may be built
on one.

---

## 8. What is not here yet

Phase 1 is schema, store, validator and seam. **Nothing populates the graph** —
it is built only by tests, `verify.py` and `trace_run.py`. Still to come:

- **Phase 2** — five deterministic extractors (markets registry, config book,
  sector map, GDELT entities, a curated human-authored supply chain), canonical
  ids, `build()`, both god-node rules, relation precedence, `make graph`.
- **Phase 3** — `analyze` (god nodes, surprising connections, graph diff,
  orphans), a review surface for `AMBIGUOUS` edges, an MCP `explain_path` tool,
  `ask.py graph`, and a token benchmark measuring subgraph vs corpus in the MYR
  the ledger already counts.
- **Phase 4** — the codebase graph over `finance-agent` itself, using stdlib
  `ast`. A maintainer tool, deliberately last.

**Hub routing is unbounded until phase 2.** A traversal may route *through* a
`Country` or `Sector` node, which would make "Maybank and NVDA are connected"
true via `Malaysia → … → United States` and worthless. Nothing populates the
graph yet, so there is no hub to route through — but the protection lands with
the extractors, not after them.

The honest expectation for phases 1–2: a few hundred edges, most of them
classification. That is not impressive to look at, and it is the right starting
point. The graph gets interesting when filings and ownership feeds are wired,
which is separate work. Building a dense graph first by letting a model propose
edges would invert this system's one non-negotiable rule — that a claim carries
where it came from.

---

## 9. Files

| Path | Role |
|---|---|
| `knowledge/graph/entity_graph.py` | Schema, traversal, `require_path`, `path_to_citations` |
| `knowledge/graph/validate.py` | The gate nothing reaches `build` past. Raises |
| `knowledge/graph/store.py` | SQLite, WAL, append-only edges, as-of reads |
| `tests/test_graph_schema.py` | Confidence, validity, inversion, the seam, the validator |
| `tests/test_graph_store.py` | Append-only, as-of reads, determinism, tiers |
| `tests/test_feeds_graph.py` | The original P9 traversal tests |
| `stress/run.py` §10 | 10k edges, cycles, broken chains, history rewrites |
