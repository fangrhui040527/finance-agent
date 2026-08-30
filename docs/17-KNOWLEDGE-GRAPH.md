# 17 — Knowledge graph

The multi-hop question vector search cannot answer: *the Strait of Hormuz just
closed — which of the things I own is exposed, and through what?*

`02-AGENTS-AND-RAG.md` gives that job to A7, and `07-BUILD-ORDER.md` P9 gives it
one non-negotiable rule: **every multi-hop claim ships with its traversal path
attached**, decayed per hop, so a three-hop inference is visibly weaker than a
direct link. An impact claim without a path cannot be emitted.

This document covers all four phases: the schema, the store, the seam that lets a
graph-backed claim reach a user, the deterministic extractors that put something
in it, the surfaces that query and review it, and the codebase graph.

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
| God-node filtering | The finance equivalents are `Malaysia`, `banking`, `USD`. A query routed through a country node connects everything to everything. Adapted with one change — see §9 |

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
- **`close_missing(tier, keep, on)`** is the half of that promise that has teeth.
  A curated row deleted from the yaml used to stay asserted forever, and the only
  way to drop it was `--rebuild`, which deletes the whole file — taking every
  other tier with it. `make graph` now takes `--prune`, which **closes** what this
  tier stopped asserting and leaves the others alone. Closed, never deleted:
  "what did we believe in March" survives a source being corrected.

  Pruning is opt-in. Closing an edge is a claim about the world, and a build
  should not make one by accident.

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

## 8. Building it — `make graph`

```
make graph                    # -> data/graph.db, offline, no keys
python -m knowledge.graph.build --db /tmp/g.db --rebuild
```

Five extractors, all deterministic, each speaking plain dicts so none of them
imports the graph and each is testable against its own source alone:

| Extractor | Source | Emits | Confidence |
|---|---|---|---|
| `config_book` | `config.toml` holdings + watchlist | Company seed nodes, **no edges** | — |
| `sectors` | `data/sectors.yaml` | Company → SubSector → Sector | `EXTRACTED` |
| `curated` | `data/supply_chain.yaml` | supplies / customer_of / competes_with / exposed_to | `EXTRACTED` |
| `market_registry` | `markets/registry.py` + the adapters | Company `OPERATES_IN` Country, `REGULATED_BY` Regulator | `EXTRACTED` |
| `gdelt` | news articles, entity-linked | Event `AFFECTS` Company | **`INFERRED`** |

**The book contributes nodes and no edges.** Owning two companies is a fact
about you, not a relationship between them; an edge would let a traversal
connect them through your account.

**`market_registry` runs last**, over the union of every instrument the others
declared — it maps companies to their market and cannot know which companies
exist until they do. It takes that set as an argument, so it stays a pure
function of its input.

**GDELT edges are `INFERRED`, and that is the point.** `link_entities` is a
substring match: it establishes that an article *mentions* a company, never that
the event *affects* it — "Maybank was not among the banks named in the probe"
links Maybank exactly as strongly as a story about Maybank's own probe. So those
edges are traversable, displayable, and refused by `Edge.citable`. They
accumulate for the phase-3 review surface, where a person promotes one into the
curated file with a real basis or throws it away. A binary flag would have forced
a choice between discarding the news layer and letting a substring match cite
itself as evidence.

### The build is reproducible to the byte

Extractors emit sorted, `build` inserts sorted, the store's edge insert is
idempotent on `(src, dst, kind, valid_from)`, and `add_node` merges metadata and
**writes nothing when nothing changed**. Build twice, `cmp` the files, expect no
difference — enforced in CI. A graph you cannot rebuild identically is one whose
diff you cannot review, and an unreviewable diff is how a bad edge lives for a
year.

Node metadata **merges** rather than replaces. Several extractors describe the
same node from different angles — the book knows it is held, the registry knows
its MIC — and replacing wholesale makes a node's attributes depend on which
extractor happened to run last.

### Parallel edges are kept, never collapsed

Two companies can both compete and share a sub-sector. Graphify's worst reported
bug (`build.py:1268`) was alphabetical last-write-wins silently rewriting 144
specific `calls` edges into generic `references`, dropping those call sites out
of the call graph. That comes from collapsing to a simple graph; `_out` holds
parallel edges, so both survive and insertion order decides nothing. Tested in
both orders.

---

## 9. Hubs — degree, not kind

```python
HUB_MIN_DEGREE = 50
graph.hubs()      # degree >= max(HUB_MIN_DEGREE, p99_degree)
```

A traversal may terminate **at** a hub; it may not route **through** one, unless
the hub is the seed. Without this, "Maybank and NVDA are connected" is true via
`Malaysia → … → United States`, and worthless.

**The rule is degree only.** An earlier design also vetoed `Sector`, `Country`
and `Commodity` by kind. That is wrong here: the repo's canonical exposure path
is `event → SEC:shipping → your holding`, and a kind veto deletes the exact
question the graph exists to answer. What makes `Malaysia` a bad waypoint is
having four hundred neighbours, not being a Country — a shipping sector with
three members *is* the path; a financials sector with sixty is noise. Only degree
can tell those apart. `HUB_KINDS` survives as the early-warning list
`analyze.god_nodes()` will report on, never as a veto.

The p99 term catches a hub nobody predicted; the floor of 50 stops a five-node
graph declaring its busiest node a hub at degree 4 and answering nothing.

---

## 10. The curated files, read as a corpus

An extracted edge cites `curated:supply_chain#misc-pchem-marine`. Something must
hand back the text behind that id or `path_to_citations` refuses the path — the
phase-1 blocker again, one layer out. `knowledge/graph/evidence.py` is that
adapter.

**What is being quoted, and why that is honest.** The curated yaml *is* the
source document: a person wrote the relationship down and vouches for it, and
`created_by: human` in `agents/registry.yaml` is what says so. The sentence
rendered is that row formatted for reading, derived by a fixed rule with nothing
added. It is not a filing and never claims to be — the trust tier is `METHOD_KB`,
four steps below `FILINGS`, so a curated edge can never outrank a document from
the company itself.

The round trip is real: the citation's `quoted_span` is rechecked against that
text by `verify_claim`, exactly as a filing chunk would be. **A row edited after
a claim cited it fails verification**, which is the point.

That closes the loop end to end, and there is a test for exactly it:

```
checked-in yaml → extractor → validator → store → traversal
                → citation → output gate → answered=True
```

---

## 11. Looking at the graph, not querying it

Prevention lives in `entity_graph`: hubs are not routed through, uncitable edges
are not traversed. `analyze.py` is **detection** — what the graph has quietly
become, which is a different question and the one nobody asks until an answer
looks wrong. Every function is a pure read, so running an analysis can never be
what changed the answer.

```
make graph-report          # or: python ask.py graph --report
```

| | What it finds |
|---|---|
| `god_nodes` | Nodes traversal already refuses to route through — **and** `HUB_KINDS` nodes past half the threshold, which is why that list survives the move to a degree-only rule. `Malaysia` at degree 30 is on its way to connecting everything, and the moment to notice is while splitting it is still cheap |
| `orphans` | Entities nothing connects to. Not a bug — the graph saying it has never been told anything about this one, which `docs/02` §3 makes the web-search trigger |
| `review_queue` | `INFERRED` and `AMBIGUOUS` edges, weakest first. Without a queue this is not a workflow but a landfill: edges accumulate, nothing reads them, and the graph fills with material neither trusted nor discarded |
| `surprising_connections` | Company pairs the graph **composed** that no single row states |
| `graph_diff` | What one build changed. The unit of review, and the reason byte-reproducibility matters |

**`surprising_connections` carries the whole idea in two filters.** `min_hops=2`,
because a one-hop link is a row somebody typed and reporting it back to them is
noise — a surprise is something the graph composed. And no taxonomy hop
(`CLASSIFIED_IN`, `OPERATES_IN`, `REGULATED_BY`), because almost every pair in a
classification graph is "connected" through its sub-sector or country, and
reporting those buries the handful that carry information. Citable paths only: a
surprise you cannot source is a rumour, and this list exists for a person
deciding where to spend time.

---

## 12. The two surfaces

```
python ask.py graph --path "Crude oil" MISC --asof 2026-08-28
python ask.py graph --impact "crude oil" --holding MISC
python ask.py graph --report
python ask.py graph --benchmark CM:aluminium "Press Metal"
python ask.py graph --diff other.db
python ask.py graph --db data/codegraph.db --uses cost_floor_bps
```

and the MCP tool `explain_path(a, b, asof)` — the multi-hop question `docs/02`
§5 names as the whole point of P9. Both go through **one resolver**,
`EntityGraph.resolve`, because each surface grew its own and each forgot a
different `NodeKind`.

**Ambiguity is refused, never broken by enum order.** `Aluminium` is both a
sub-sector and a commodity in the shipped data; silently preferring one answers a
question the user did not ask. Both surfaces list the candidates instead.

**An empty result is phrased carefully, in both.** Traversal is a best-first
heuristic (§7), so nothing found means *not found cheaply* — never that two
entities are unconnected. A surface that says "unrelated" invites a negative
claim the graph cannot support, and the stress suite checks the wording.

---

## 13. Does the graph earn its place?

`benchmark.py` measures a path's context against handing over the whole corpus,
in tokens, because that is a claim about money and should be measurable rather
than asserted. On the shipped data an answerable question costs **~15× fewer
tokens** than the corpus.

It is allowed to come out badly, and does: a question the graph cannot reach
scores 1.0× and is reported as *not answerable*. A graph that cannot find the
answer has earned nothing on that question.

**Estimated, not counted.** Exact counts need the Anthropic `count_tokens`
endpoint, which needs a key and a network; this repo runs offline. The estimate
is `len(text) // 4` — deliberately the same heuristic `EchoBackend` already uses,
because two offline estimates that disagree are worse than one honestly
approximate. It is not a billing figure; the ledger records real usage.

---

## 14. The codebase graph

```
make codegraph        # -> data/codegraph.db, ~1,500 nodes / ~2,700 edges
python ask.py graph --db data/codegraph.db --uses cost_floor_bps
```

The same core, over `finance-agent` itself, using stdlib `ast` — not
tree-sitter, whose 27 pinned grammar packages would break the two-dependency
rule for a convenience. Modules and top-level symbols become nodes; imports and
calls become edges.

Four edge kinds, and their confidence differs because the evidence does:

| Edge | Confidence | Why |
|---|---|---|
| `imports` (`supplies`) | `EXTRACTED` | the statement names its target literally |
| `documents` | `EXTRACTED` | the markdown contains the path as a literal string |
| `tests` | `INFERRED` | a test importing a module is evidence it exercises it, not proof |
| `calls` (`exposed_to`) | `INFERRED` | a name, not a binding — see below |

`DOCUMENT` was **added to the shared vocabulary** rather than stretched from an
existing kind, because a filing describing a company is the same relation as a
page describing a module and neither domain could express it before. `TESTS` is
the one place the vocabulary is stretched for a single domain — `Module`, `Class`
and `Function` all had honest analogues in `Product` and `Technology`; "tests"
has none.

Document matching is on the **file path**, not the dotted name: prose says
`core/llm/tiers.py` and almost never `core.llm.tiers`, and matching the dotted
form would fire on ordinary sentences containing dots.

### What has nothing testing it

```
python ask.py graph --db data/codegraph.db --untested
```

A module that **defines nothing** is skipped — two thirds of the first run were
empty `__init__.py` package markers, and a list nobody can read is the same as no
list. The remainder is short enough to act on.

The signal is `INFERRED`, and **the direction of its error matters**: a module
exercised only through a helper, or by a test that never imports it, reads as
untested. It over-reports and never under-reports, which is the safe way round
for a list whose purpose is deciding where to add a test. On this repository the
four market adapters are exactly that false positive — reached through
`markets.registry`.

It also found a true one: `core/contracts/money.py` has no test importing it,
and no test file mentions `Money(` at all.

**It does not answer "which agent has no eval", and an earlier version of this
document and that module's docstring both said it did.** That question cannot
arise: `core/registry/loader.py` runs `check_suite` on every agent at load, so an
agent without a usable eval suite does not produce a report — it **refuses to
register**. A graph query would be a weaker second answer to something already
refused outright.

**Why the call edges are `INFERRED`.** A static pass reads names, not bindings.
`self.store.record(...)` is recorded against whichever `record` is defined —
dynamic dispatch, `getattr`, rebinding decorators and re-exports are all
invisible. So a call edge means "this name is referenced here and defined there",
a strong hint and not a fact, and `Edge.citable` refuses it exactly as it refuses
a substring match in the news layer. Imports are `EXTRACTED`: an import statement
names its target literally.

Three refusals rather than guesses: a name defined in two places produces **no
edge** (no edge beats several wrong ones); a name on the stop-list — `run`,
`main`, `get` — is skipped, which is graphify's god-node list in its original
form; and a file that will not parse is skipped, not guessed at.

The degree rule generalises without changes: on this repository the hubs are
`ask.py`, `mcp_server/tools.py` and the large test files — exactly the waypoints
that would otherwise connect everything to everything.

---

## 15. What is not here yet

Everything in this document is built. Two things remain, and **both need a key
or a live source**, which is why they stop here:

- The **GDELT extractor** is written and tested but is **not in the default
  build** — it needs a feed to read. Its `INFERRED` edges now have somewhere to
  go (`review_queue`), so wiring it is a config change rather than new machinery.
- A **semantic tier** — a model proposing edges — is the obvious next step. The
  `tier` column and `close_missing` exist so it cannot wipe the deterministic
  tier when it arrives, and a deterministic rebuild cannot wipe it.

One finding this work produced and did not act on: `core/contracts/money.py` has
no test. That is a gap in the repository, not in the graph.

The honest expectation: the shipped build is 47 nodes and 84 edges, most of them
classification. That is not impressive to look at, and it is the right starting
point — every edge traceable to a source, no model involved, reproducible to the
byte. The graph gets interesting when filings and ownership feeds are wired,
which is separate work. Building a dense graph first by letting a model propose
edges would invert this system's one non-negotiable rule: that a claim carries
where it came from.

**Before you trade on any of it**, the rows in `data/supply_chain.yaml` are seed
examples chosen because they are widely known, not because they were verified
against a primary source in this repository. The file says so at the top.

---

## 16. Files

| Path | Role |
|---|---|
| `knowledge/graph/entity_graph.py` | Schema, traversal, hubs, `require_path`, `path_to_citations` |
| `knowledge/graph/validate.py` | The gate nothing reaches `build` past. Raises |
| `knowledge/graph/store.py` | SQLite, WAL, append-only edges, as-of reads, tiers |
| `knowledge/graph/ids.py` | One canonical id, three producers |
| `knowledge/graph/build.py` | extract → validate → store. `make graph` |
| `knowledge/graph/evidence.py` | The curated files, read as a corpus |
| `knowledge/graph/analyze.py` | Hubs, orphans, the review queue, surprises, diff |
| `knowledge/graph/report.py` | All of the above, on one page |
| `knowledge/graph/benchmark.py` | Subgraph vs corpus tokens |
| `knowledge/graph/extractors/` | Six deterministic sources, one contract |
| `knowledge/graph/extractors/code.py` | The repository as a graph, over `ast` |
| `knowledge/graph/data/*.yaml` | Human-authored: entities, sectors, supply chain |
| `tests/test_graph_schema.py` | Confidence, validity, inversion, the seam, the validator |
| `tests/test_graph_store.py` | Append-only, as-of reads, determinism, tiers |
| `tests/test_graph_ids.py` | Idempotence, the fold-before-filter order, aliases |
| `tests/test_graph_extract.py` | Each extractor against its own source |
| `tests/test_graph_build.py` | Determinism, hubs, parallel edges, the whole loop |
| `tests/test_graph_analyze.py` | Detection, the review queue, diff, the report |
| `tests/test_graph_code.py` | What a static pass can and cannot see |
| `tests/test_graph_surfaces.py` | `ask.py graph` and `explain_path`, same refusals |
| `tests/test_feeds_graph.py` | The original P9 traversal tests |
| `stress/run.py` §10 | 10k edges, cycles, broken chains, hubs, history rewrites |
