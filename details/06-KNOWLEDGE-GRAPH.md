# 06 — The knowledge graph

`knowledge/graph/` — 14 modules including 7 extractors. Built offline from
checked-in sources, reproducible to the byte.

Current build: **47 nodes, 84 edges, 84/84 citable, 0 hubs.**

---

## Why it exists

To answer "what is Maybank's exposure to a Red Sea shipping disruption" you need
a path — `EV:redsea → SEC:shipping → CO:MISC` — and you need every hop on that
path to be a fact you can cite. A graph that returns a path it cannot source is
worse than no graph, because "connected via Malaysia" reads like a finding.

## Schema

### Node kinds (10)

`Company`, `Sector`, `SubSector`, `Country`, `Product`, `Technology`,
`Commodity`, `Regulator`, `Event`, `Document`.

`Document` was added for the code graph and turned out to be immediately useful
to the finance one: *a document describes a company* is a relationship the
vocabulary could not previously express.

### Edge kinds (12)

`supplies`, `customer_of`, `competes_with`, `owns`, `operates_in`, `exposed_to`,
`substitutes`, `regulated_by`, `classified_in`, `affects`, `documents`, `tests`.

`tests` is the one place the shared vocabulary is stretched for a single domain.
`Module`, `Class` and `Function` all had honest analogues in `Product` and
`Technology`; "a test constrains a module" has none.

### Inverses

```python
EDGE_INVERSE = {SUPPLIES ↔ CUSTOMER_OF, COMPETES_WITH ↔ self,
                SUBSTITUTES ↔ self, EXPOSED_TO ↔ AFFECTS}
```

**`bidirectional=True` used to mint the reverse edge with the same kind**, so
"A supplies B" produced "B supplies A". The inverse map is what makes a
symmetric relation symmetric and an asymmetric one correct.

### Confidence

```python
EXTRACTED  # a source says this
INFERRED  # we derived it
AMBIGUOUS  # default
```

Defaults to `AMBIGUOUS` — the weakest — so an extractor that forgets to set it
produces something traversable and displayable that **cannot back a claim**.

---

## The three gates

An edge is `citable` only if all three hold:

| Gate | Rule | Why |
|---|---|---|
| **structure** | `weight ∈ (0, 1]` | a zero-weight edge is not a relationship |
| **time** | `valid_from ≤ asof < valid_to` | half-open, so a relation that ended today is not live today |
| **evidence** | `confidence is EXTRACTED` **and** `sourced` | an inferred link may be shown, never cited |

```python
@property
def citable(self) -> bool:
    return self.sourced and self.confidence is Confidence.EXTRACTED
```

`path_to_citations(path, lookup)` is **all-or-nothing**. A path yields citations
only if every hop is citable — there is no partially cited path, because a chain
with one unsourced link is not three-quarters true.

> **This was the phase-1 blocker.** Before `path_to_citations` existed, the
> graph could not emit any claim in any configuration. It could traverse, and
> everything it found died at the citation check.

## Traversal

```python
traverse(start, *, asof, max_hops=..., ...)
```

`asof` is **required**, not defaulted. A traversal without a time is a traversal
that silently includes relationships that ended years ago.

### Hub protection

```python
HUB_MIN_DEGREE = 50
def hubs(self) -> frozenset[str]     # degree >= max(HUB_MIN_DEGREE, p99_degree)
```

Traversal may terminate **at** a hub; it may not route **through** one, unless
the hub is the seed.

Without it, "Maybank and NVDA are connected" is true via
`Malaysia → … → United States` and worthless.

**The rule is degree-only, deliberately.** An earlier design also vetoed routing
through `Sector`, `Country` and `Commodity` by kind — but that kills the exact
path the graph exists to answer. `EV:redsea → SEC:shipping → CO:MISC` routes
through a sector, and six existing tests do the same. What makes `Malaysia` a
bad waypoint is having 400 neighbours, not being a `Country`; a shipping sector
with three members is a real exposure link. `HUB_KINDS` survives as an
early-warning list in `analyze.god_nodes()`, never as a traversal veto.

The `p99` term catches a hub nobody predicted. The floor of 50 stops a sparse
graph declaring its busiest node a hub at degree 4. The current build has **no**
hubs, which is what a 47-node graph should say.

### Parallel edges

`_out: dict[str, list[Edge]]` holds **parallel edges**, so a `SUPPLIES` and a
`CLASSIFIED_IN` between the same pair coexist and neither is lost. A stress
check inserts both in each order and asserts both survive — the guard against
someone "fixing" it into a dict later.

---

## Identity

`knowledge/graph/ids.py`. Three things mint ids — the extractors, the store, and
whatever asks the traversal a question — and if any two disagree the graph
quietly grows a second Maybank. Nothing crashes; the supply chain forks, and
half the exposure paths lead to a node no query ever names.

```
node_id(NodeKind.COMPANY, "Maybank")  →  "CO:XKLS:1155"
```

Three guarantees, all test-enforced:

| Guarantee | Meaning |
|---|---|
| idempotent | `node_id(k, node_id(k, x)) == node_id(k, x)` |
| word characters | everything after the prefix matches `[\w:]+` |
| caseless-stable | spelling, spacing and punctuation do not change the answer |

**The ordering trap.** NFKC normalisation and casefold are iterated **to
convergence before** the non-word filter, because they do not commute: casefold
can expand one character into a base plus a combining mark, which NFKC then
recomposes, which can casefold differently again. Filtering first would delete
the combining mark and change which company you are talking about.

Companies resolve through `markets.registry.resolve_mic`, so `MYX:1155` and
`XKLS:1155` land on one id — reuse, not a second alias table, because that
resolver exists precisely because the two drifting apart sized every Bursa
position against the wrong cost floor.

---

## The extractors

`extract() -> {"nodes": [...], "edges": [...]}` — plain dicts, so an extractor
never imports the graph and is testable against its source alone. Everything
goes through `validate.parse`, which raises.

| Extractor | Source | Emits | Confidence |
|---|---|---|---|
| `market_registry` | `registry.supported()` + adapter fields | `OPERATES_IN`, `REGULATED_BY` | `EXTRACTED` |
| `config_book` | `core.config.load()` holdings + watchlist | Company seeds, no edges | — |
| `sectors` | `data/sectors.yaml` | `CLASSIFIED_IN` | `EXTRACTED` |
| `curated` | `data/supply_chain.yaml` | `SUPPLIES`, `CUSTOMER_OF`, `COMPETES_WITH` | `EXTRACTED`, each row with its own doc and `valid_from` |
| `gdelt` | offline fixture feed | Event `AFFECTS` Company | **`INFERRED`** |
| `code` | the repository itself | `DOCUMENTS`, `TESTS`, module structure | `EXTRACTED` |

### GDELT edges are `INFERRED`, and that is the honest call

`link_entities` is a substring match over surface forms. It establishes that an
article *mentions* a company, never that the event *affects* it. So those edges
are traversable and displayable and **cannot back a claim** — which is exactly
what the three-way confidence split was added for. They accumulate for the
review surface, where a human promotes one into the curated file.

### Build order

Seeds and curated files first, `market_registry` last over the union.
`market_registry` takes its instrument set as an argument rather than reading
the book itself, so it stays a pure function of its input.

**A fresh checkout with empty `holdings`/`watchlist` still builds a graph.** The
curated files declare their own companies; the book only says which ones you
care about.

---

## The build

```
extract(each) → validate.parse (raises) → dedupe → GraphStore
```

- Every node and edge carries `tier="deterministic"`. Re-extraction replaces
  **only its own tier**, so a model tier arriving later is not wiped — and a
  deterministic rebuild after that is not wiped by it either.
- Pruning uses `close_missing(tier, keep, on)`, which **closes** rather than
  deletes. History survives.
- Extractors emit sorted, `build` inserts sorted, and the store's insert is
  idempotent on `(src, dst, kind, valid_from)`.

**Building twice gives a byte-identical database.** CI builds into two paths and
runs `cmp`, for both the finance graph and the code graph. A build that is not
reproducible cannot have its diff reviewed, and an unreviewable diff is how a
bad edge lives for a year.

```
$ make graph
knowledge graph
  config_book          0 nodes      0 edges
  curated             14 nodes     18 edges
  markets             21 nodes     34 edges
  sectors             40 nodes     32 edges
  TOTAL               47 nodes     84 edges
  citable             84 of 84 edges can back a claim
  hubs             none - no node is well connected enough to be a
                   meaningless waypoint yet
```

## The code graph

`make codegraph` runs the same machinery over the repository itself — modules,
classes, functions, tests, docs. It is a maintainer's tool, but it parses every
file, so a build failure is a syntax error somewhere, and the reproducibility
guarantee has to hold for it too. Both are CI steps.

## The honest caveat

This produces a few hundred edges, most of them classification, and it does not
look impressive. That is the right starting point: every edge traceable to a
source, no model involved, reproducible to the byte.

The graph gets interesting when filings and ownership feeds are wired, which is
separate work. Building a dense graph first by letting a model propose edges
would invert this system's one non-negotiable rule — that a claim carries where
it came from.

**Not yet verified:** the rows in `data/supply_chain.yaml` and the Bursa stock
codes in `data/entities.yaml` were written by hand. Each carries a
`source_doc_id`; nothing checks that the document says what the row claims.
