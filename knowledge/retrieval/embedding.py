"""Where the vectors come from.

`knowledge/retrieval/evaluate.py` measured the hashing projection this system
shipped with and found the number that settles the argument: over 24 labelled
questions, the vector leg found a relevant article BM25's own top ten did not
**zero times**. Not seldom - never. Hashing a token into a bucket and adding
plus or minus one is a lossy re-encoding of the word itself, so two ways of
saying the same thing land in unrelated buckets and the "semantic" leg is an
inferior copy of the lexical one. The fusion was paying rank-fusion overhead
for a duplicate of its other half.

Three backends now sit behind the same `EmbeddingBackend` seam:

  `HashingEmbedder`         the original. Kept, because it is the one backend
                            that needs neither a fitted corpus nor a key, and a
                            test that wants a fixed vector in three lines still
                            wants it.
  `DistributionalEmbedder`  the default. Learns from the corpus's own word
                            company: two terms that keep the same neighbours
                            get similar vectors, so "cloud buyers" can reach an
                            article about hyperscalers. Offline, deterministic,
                            no key, no download.
  `ApiEmbedder`             a real embedding model over HTTP, for when a key is
                            present. It is the only one of the three that knows
                            anything about words it has never seen in this
                            corpus, which is the ceiling on everything below.

WHAT THE DISTRIBUTIONAL ONE CANNOT DO, stated up front because it is the honest
limit and not a bug: it only relates words it has SEEN, and only to the words it
has seen them beside. A question asking about a "bendable" phone when every
article says "foldable" gets no help from this, because "bendable" appears
nowhere in the corpus and a corpus is the only teacher this backend has. Closing
that gap needs a model trained on general language - `ApiEmbedder`, or a local
model downloaded at install. What this backend does close is the far more common
case where both wordings are present in the corpus but not in the same article.

The method is random indexing (Kanerva), which is the right shape for a corpus
this size: no matrix factorisation, no training loop, one pass, and a vector
space whose dimension does not grow with the vocabulary.

  1. Every term gets a fixed sparse random signature - a handful of +1/-1 marks
     at positions derived from a hash of the term. Signatures are near-orthogonal
     by construction, which is what lets them be summed without erasing.
  2. A term's MEANING vector is the sum of the signatures of the terms it
     appears beside, over the whole corpus, weighted so common words carry less.
     Terms used in the same company end up pointing the same way.
  3. A document, or a question, is the weighted sum of its terms' meaning
     vectors, plus a smaller amount of their own signatures so a rare exact
     token still pulls its own weight.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from collections.abc import Iterable

#: Wide enough that a few thousand terms' signatures stay near-orthogonal,
#: small enough that a pure-Python cosine over a season of headlines is still
#: a few milliseconds.
DIMENSIONS = 384

#: Marks per signature. Too few and two terms collide outright; too many and
#: every signature overlaps every other and the space goes flat.
NONZEROS = 12

#: How much of a term's OWN signature goes into a document vector, beside the
#: meaning vector. Zero would make the dense leg blind to a rare exact token it
#: has no context for; one would make it the hashing embedder again.
LEXICAL_WEIGHT = 0.35

#: A term must appear in at least this many documents to get a meaning vector.
#: One is right, not two: a term used once has a meaning vector that is simply
#: the article it appeared in, and for RETRIEVAL that is the correct answer
#: rather than overfitting. Raising it to two cost four of the labelled
#: questions their only route in - "aluminium" appears in exactly one headline.
MIN_DF = 1

#: A term in more than this share of the corpus tells you nothing about which
#: document you want. "the" is caught by idf anyway; this catches the corpus's
#: own boilerplate - "stock", "reported" - which idf alone does not.
MAX_DF_SHARE = 0.4

#: Terms per document that contribute to the fit. A 10,000-word filing would
#: otherwise dominate every meaning vector in the corpus by sheer length.
MAX_FIT_TERMS = 400

#: Dimensions kept in a term's meaning vector. Random indexing spreads a term's
#: company over every dimension, but the mass concentrates in a few - so the
#: rest is a rounding error being multiplied and added once per term per
#: document. Pruning to the largest is what keeps a season of headlines
#: affordable: the cost of building the index is documents x terms x THIS, and
#: at the full 384 a 15,000-chunk window took twenty seconds that an MCP call
#: does not have. Measured at 48: no change to any retrieval number, twelve
#: times less arithmetic.
MEANING_NONZEROS = 48

#: Documents the meaning vectors are learned from, newest first. Learning which
#: words keep company with which saturates long before a corpus is exhausted,
#: and this term is the one that grows without bound. Every document is still
#: EMBEDDED and searchable - this caps what is learned from, not what is found.
MAX_FIT_DOCS = 6000

#: How many documents the common direction is estimated from. Summed word
#: vectors all share a large component that says "this is a finance headline"
#: and nothing about WHICH headline; left in, every pair of documents scores
#: around 0.6 similar and the ranking is noise on top of a constant. The fix is
#: to project that direction OUT of every vector, document and question alike.
#: The mean direction stands in for the first principal component: it captures
#: most of the same thing for one extra pass instead of a decomposition.
CENTRE_SAMPLE = 4000

TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Keeps dots and hyphens so `0011.KL` and `Q3-FY25` survive as one token."""
    return [t.lower() for t in TOKEN.findall(text)]


def signature(
    term: str, dimensions: int = DIMENSIONS, nonzeros: int = NONZEROS
) -> dict[int, float]:
    """A term's fixed sparse +1/-1 marks. Same term, same marks, forever.

    blake2b rather than md5: the digest length is a parameter, so one hash call
    yields exactly the bytes this needs instead of a fixed 16 that then have to
    be stretched. Nothing here is a security decision - it is a deterministic
    spreading function, and it must stay stable across platforms and Python
    versions or a stored vector stops meaning what it meant.
    """
    raw = hashlib.blake2b(term.encode("utf-8"), digest_size=nonzeros * 3).digest()
    out: dict[int, float] = {}
    for i in range(nonzeros):
        a, b, c = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
        out[((a << 8) | b) % dimensions] = 1.0 if c & 1 else -1.0
    return out


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _prune(vec: list[float], keep: int) -> dict[int, float]:
    """The `keep` largest components, renormalised. See MEANING_NONZEROS."""
    top = sorted(range(len(vec)), key=lambda i: -abs(vec[i]))[:keep]
    norm = math.sqrt(sum(vec[i] * vec[i] for i in top)) or 1.0
    return {i: vec[i] / norm for i in top}


class HashingEmbedder:
    """Deterministic, offline, dependency-free, and semantically blind.

    Retained deliberately. It needs no corpus and no fit, which is exactly what
    a unit test wants, and it is the honest baseline any change to retrieval is
    measured against. It is no longer the default: see the module docstring for
    the measurement that moved it aside.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dimensions] += 1.0 if (h >> 8) & 1 else -1.0
        return _normalise(vec)


class DistributionalEmbedder:
    """Vectors learned from which words keep company with which.

    Call `fit` with the corpus before embedding. Unfitted, it degrades to the
    signatures alone - which is roughly the hashing embedder, and is what a
    caller gets if they forget. `Collection` fits it automatically the first
    time a dense search is run, so in practice nobody has to remember.
    """

    def __init__(
        self,
        dimensions: int = DIMENSIONS,
        nonzeros: int = NONZEROS,
        lexical_weight: float = LEXICAL_WEIGHT,
    ) -> None:
        self.dimensions = dimensions
        self.nonzeros = nonzeros
        self.lexical_weight = lexical_weight
        self._sig: dict[str, dict[int, float]] = {}
        self._idf: dict[str, float] = {}
        self._meaning: dict[str, dict[int, float]] = {}
        self._centre: list[float] | None = None
        self.fitted = False

    # -- signatures ----------------------------------------------------------

    def _signature(self, term: str) -> dict[int, float]:
        hit = self._sig.get(term)
        if hit is None:
            hit = signature(term, self.dimensions, self.nonzeros)
            self._sig[term] = hit
        return hit

    # -- fitting -------------------------------------------------------------

    def fit(self, texts: Iterable[str]) -> DistributionalEmbedder:
        """One pass. Deterministic: terms are summed in sorted order, always.

        Float addition is not associative, so an iteration order that depends on
        set ordering would give a different vector on a different run and make
        every retrieval number irreproducible. Sorting costs nothing here and
        buys a result that can be compared across machines.
        """
        docs = [sorted(set(tokenize(t)))[:MAX_FIT_TERMS] for t in texts]
        docs = [d for d in docs if len(d) > 1]
        if len(docs) > MAX_FIT_DOCS:
            docs = docs[-MAX_FIT_DOCS:]
        n = len(docs)
        if not n:
            self.fitted = True
            return self

        df: Counter[str] = Counter()
        for d in docs:
            df.update(d)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

        ceiling = max(MIN_DF, int(n * MAX_DF_SHARE))
        keep = {t for t, c in df.items() if MIN_DF <= c <= ceiling}

        zero = [0.0] * self.dimensions
        meaning: dict[str, list[float]] = {t: list(zero) for t in keep}
        for d in docs:
            # The document's own total, once. A term's neighbours are then the
            # total minus itself - O(terms) per document instead of the
            # O(terms squared) that summing each pair would cost, and exactly
            # the same sum.
            total = list(zero)
            own: dict[str, float] = {}
            for t in d:
                w = self._idf[t]
                own[t] = w
                for dim, s in self._signature(t).items():
                    total[dim] += w * s
            for t in d:
                acc = meaning.get(t)
                if acc is None:
                    continue
                w = own[t]
                sig = self._signature(t)
                acc[:] = [a + b for a, b in zip(acc, total)]
                for dim, s in sig.items():
                    acc[dim] -= w * s

        self._meaning = {t: _prune(v, MEANING_NONZEROS) for t, v in meaning.items()}
        self.fitted = True

        # The common direction, measured on the corpus this will be searched
        # over. Queries are centred by the same vector, so both sides of the
        # cosine live in the same space.
        sample = [_normalise(self._raw(" ".join(d))) for d in docs[:CENTRE_SAMPLE]]
        if sample:
            self._centre = _normalise([sum(col) for col in zip(*sample)])
        return self

    # -- embedding -----------------------------------------------------------

    def _raw(self, text: str) -> list[float]:
        """The uncentred sum. Terms the corpus has never seen are DROPPED.

        Dropping them is the whole point rather than an omission. An unknown
        term's signature marks twelve dimensions at random, and since no
        document in the corpus contains that term, not one of those marks can
        match anything - it is noise added at full strength to a vector that had
        signal in it. Worse, the natural weight for an unseen term is the
        LARGEST idf there is, so the noise arrived louder than the signal:
        "which Malaysian utility posted a fall in earnings" was decided almost
        entirely by the random marks of the word "utility". BM25 is the leg that
        handles a term this index has never seen, and it handles it correctly by
        returning nothing.
        """
        vec = [0.0] * self.dimensions
        scale = self.lexical_weight / math.sqrt(self.nonzeros)
        for term in sorted(set(tokenize(text))):
            w = self._idf.get(term)
            if w is None:
                continue
            sense = self._meaning.get(term)
            if sense is not None:
                for dim, val in sense.items():
                    vec[dim] += w * val
            lex = scale * w
            for dim, s in self._signature(term).items():
                vec[dim] += lex * s
        return vec

    def embed(self, text: str) -> list[float]:
        """The vector, with the corpus's common direction projected out.

        Removed by PROJECTION, not by subtracting a mean vector. Subtraction
        looks equivalent and is not: a question is a handful of words and a
        document is a paragraph, so their raw vectors differ in length by a
        large factor, and subtracting the same fixed vector from both leaves the
        short one pointing mostly backwards along it. Every question then looked
        like every other question and dense recall fell to 4%. Removing the
        projection is scale-free and does the same job to both.
        """
        vec = self._raw(text)
        u = self._centre
        if u is not None:
            along = sum(a * b for a, b in zip(vec, u))
            vec = [a - along * b for a, b in zip(vec, u)]
        return _normalise(vec)

    def describe(self) -> str:
        if not self.fitted:
            return f"distributional ({self.dimensions}d), NOT fitted - signatures only"
        return (
            f"distributional ({self.dimensions}d), fitted on {len(self._idf)} terms, "
            f"{len(self._meaning)} with a meaning vector, "
            f"{'centred' if self._centre else 'uncentred'}"
        )


class EmbeddingError(RuntimeError):
    """A real embedding model was asked for and could not answer."""


#: The provider `ApiEmbedder` talks to when nothing else is configured. Any
#: host that speaks the OpenAI `/v1/embeddings` shape works; the tier table in
#: `core/llm/tiers.py` already names the model this pairs with.
DEFAULT_EMBED_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
EMBED_KEY_ENV = "EMBEDDING_API_KEY"
EMBED_URL_ENV = "EMBEDDING_API_URL"
EMBED_MODEL_ENV = "EMBEDDING_MODEL"


class ApiEmbedder:
    """A real embedding model, over HTTP, cached on disk.

    Off unless `EMBEDDING_API_KEY` is set - this system runs keyless by default
    and a retrieval path that silently starts costing money on a nightly sweep
    is exactly the kind of surprise the tier router exists to prevent.

    The cache is not an optimisation, it is the affordability. A sweep re-indexes
    the same season of headlines every night; without a cache that is thousands
    of identical paid requests a week for text that has not changed. Keyed by the
    model id and the text, so switching models does not read back the old
    model's vectors.
    """

    def __init__(
        self,
        api_key: str | None = None,
        url: str | None = None,
        model: str | None = None,
        cache_path: str | None = "data/embeddings.db",
        dimensions: int = 1536,
        batch: int = 64,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get(EMBED_KEY_ENV, "")
        self.url = url or os.environ.get(EMBED_URL_ENV) or DEFAULT_EMBED_URL
        self.model = model or os.environ.get(EMBED_MODEL_ENV) or DEFAULT_EMBED_MODEL
        self.dimensions = dimensions
        self.batch = batch
        self.timeout = timeout
        # The key is checked BEFORE the cache is opened. The other order created
        # data/embeddings.db as a side effect of a constructor that then raised,
        # so every keyless run left a cache file for a model it could not call.
        if not self.api_key:
            raise EmbeddingError(
                f"{EMBED_KEY_ENV} is not set; there is no real embedding model to call. "
                "Leave the default DistributionalEmbedder in place, or set the key."
            )
        self._cache = _VectorCache(cache_path) if cache_path else None
        self._memo: dict[str, list[float]] = {}

    def fit(self, texts: Iterable[str]) -> ApiEmbedder:
        """Warm the cache in batches, so the first query does not pay for all of it."""
        pending = [t for t in dict.fromkeys(texts) if self._lookup(t) is None]
        for i in range(0, len(pending), self.batch):
            chunk = pending[i : i + self.batch]
            for text, vec in zip(chunk, self._request(chunk)):
                self._store(text, vec)
        return self

    def embed(self, text: str) -> list[float]:
        hit = self._lookup(text)
        if hit is not None:
            return hit
        vec = self._request([text])[0]
        self._store(text, vec)
        return vec

    # -- plumbing ------------------------------------------------------------

    def _lookup(self, text: str) -> list[float] | None:
        hit = self._memo.get(text)
        if hit is not None:
            return hit
        if self._cache is None:
            return None
        hit = self._cache.get(self.model, text)
        if hit is not None:
            self._memo[text] = hit
        return hit

    def _store(self, text: str, vec: list[float]) -> None:
        self._memo[text] = vec
        if self._cache is not None:
            self._cache.put(self.model, text, vec)

    def _request(self, texts: list[str]) -> list[list[float]]:
        import json
        import urllib.error
        import urllib.request

        payload = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "finance-agent (personal research)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise EmbeddingError(f"embedding request refused: HTTP {e.code}") from e
        except OSError as e:
            raise EmbeddingError(f"embedding request failed: {e}") from e
        rows = body.get("data") or []
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"asked for {len(texts)} vectors and got {len(rows)}; refusing to guess "
                "which text each one belongs to"
            )
        rows.sort(key=lambda r: r.get("index", 0))
        return [_normalise([float(x) for x in r["embedding"]]) for r in rows]

    def describe(self) -> str:
        return f"api ({self.model}) via {self.url}"


class _VectorCache:
    """Text -> vector on disk, so a re-index of unchanged text costs nothing."""

    def __init__(self, path: str) -> None:
        import sqlite3
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            " model TEXT NOT NULL, text_hash TEXT NOT NULL, vector TEXT NOT NULL,"
            " PRIMARY KEY (model, text_hash))"
        )
        self.db.commit()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, model: str, text: str) -> list[float] | None:
        import json

        row = self.db.execute(
            "SELECT vector FROM vectors WHERE model = ? AND text_hash = ?",
            (model, self._key(text)),
        ).fetchone()
        return [float(x) for x in json.loads(row[0])] if row else None

    def put(self, model: str, text: str, vector: list[float]) -> None:
        import json

        self.db.execute(
            "INSERT OR REPLACE INTO vectors (model, text_hash, vector) VALUES (?, ?, ?)",
            (model, self._key(text), json.dumps(vector)),
        )
        self.db.commit()


def default_embedder():
    """What a `Collection` uses when the caller names nothing.

    A key promotes the real model; without one the corpus-fitted backend runs,
    which is the keyless promise this system makes everywhere else.
    """
    if os.environ.get(EMBED_KEY_ENV, "").strip():
        try:
            return ApiEmbedder()
        except EmbeddingError:
            pass
    return DistributionalEmbedder()
