"""P3: the vectors, and the measurement that judges them.

Search was the one part of this system whose quality nobody had a number for.
These tests guard the number and the machinery that produces it - not a target
score, which drifts as the corpus grows, but the STRUCTURAL claims that must
hold whatever the corpus looks like on the day.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge.chunking.parent_child import Chunk
from knowledge.retrieval.embedding import (
    DistributionalEmbedder,
    EmbeddingError,
    HashingEmbedder,
    _VectorCache,
    default_embedder,
    signature,
)
from knowledge.retrieval.evaluate import GOLD, Case, LegScore, evaluate, load_gold
from knowledge.retrieval.hybrid import DOC_NONZEROS, Collection

NOW = datetime.now(UTC)

#: A corpus small enough to read and large enough to have word company in it.
#: Two clusters that share no vocabulary with each other, so a backend that has
#: learned anything keeps them apart.
CHIPS = [
    "nvidia sells accelerators to hyperscalers amazon google microsoft",
    "hyperscalers amazon google microsoft buy accelerators for datacentres",
    "datacentre demand from amazon and google lifts accelerator orders",
    "accelerator orders from microsoft and google reach a record",
]
PALM = [
    "palm oil futures rose on tighter malaysian stockpiles",
    "malaysian palm stockpiles fell and futures rose again",
    "stockpiles of palm oil in malaysia tightened through the quarter",
    "futures for malaysian palm oil gained on lower stockpiles",
]


def chunks(texts: list[str], corpus: str = "kb_news") -> list[Chunk]:
    return [Chunk(f"d{i}", t, corpus, None, None, NOW, {}) for i, t in enumerate(texts)]


# --- signatures ---------------------------------------------------------------


def test_a_signature_is_stable_for_the_same_term():
    assert signature("maybank") == signature("maybank")
    assert signature("maybank") != signature("tenaga")


def test_a_signature_is_pinned_across_platforms():
    """A stored vector means nothing if the signature moves under it.

    Pinned rather than merely deterministic-within-a-run: the whole point of a
    hash-derived signature is that a vector built on one machine is comparable
    to a query embedded on another, and a Python or platform change that
    silently altered the digest would break that without failing anything.
    """
    assert signature("nvidia", dimensions=64, nonzeros=4) == {27: -1.0, 47: 1.0, 25: -1.0, 61: -1.0}


def test_a_signature_marks_at_most_the_requested_dimensions():
    sig = signature("hyperscaler", dimensions=32, nonzeros=8)
    assert len(sig) <= 8
    assert all(0 <= d < 32 for d in sig)
    assert all(v in (1.0, -1.0) for v in sig.values())


# --- the distributional backend -----------------------------------------------


def test_fitting_puts_words_that_keep_company_together():
    """The whole claim, in one assertion.

    'accelerators' and 'hyperscalers' never mean the same thing, but they are
    used beside each other, and that is what this backend learns. 'palm' is used
    beside neither. If the first pair is not closer than the second, nothing
    downstream is doing semantics.
    """
    e = DistributionalEmbedder().fit(CHIPS + PALM)
    from knowledge.retrieval.hybrid import cosine

    near = cosine(e.embed("accelerators"), e.embed("hyperscalers"))
    far = cosine(e.embed("accelerators"), e.embed("palm"))
    assert near > far


def test_a_question_in_different_words_still_reaches_the_right_cluster():
    """The question names neither cluster's headline words and still lands in
    the right one: 'datacentres' and 'record' never appear in the same sentence
    in this corpus, but both keep company with the chip cluster and neither
    keeps company with palm oil."""
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS + PALM))
    top = col.dense("datacentres record", 3)
    assert top, "a question in known words must reach something"
    assert all(hit.chunk_id in {f"d{i}" for i in range(4)} for hit, _ in top)


def test_a_question_in_words_the_corpus_has_never_seen_reaches_nothing():
    """Not a gap - the honest answer. Every term is unknown, so the vector has
    nothing in it, and returning the arbitrary nearest document to an empty
    vector would be inventing a result. BM25 answers the same way."""
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS + PALM))
    assert col.dense("zzzalpha zzzbeta zzzgamma", 3) == []


def test_the_same_corpus_gives_the_same_vectors_every_time():
    """Float addition is not associative; iteration order is therefore part of
    the contract, not an implementation detail."""
    a = DistributionalEmbedder().fit(CHIPS + PALM)
    b = DistributionalEmbedder().fit(CHIPS + PALM)
    assert a.embed("accelerator orders") == b.embed("accelerator orders")


def test_words_the_corpus_has_never_seen_are_dropped():
    """An unknown term can only contribute noise: no document contains it, so
    not one of its marks can match. It used to arrive at the LARGEST weight
    there is, which is how one unknown word decided a whole ranking."""
    e = DistributionalEmbedder().fit(CHIPS)
    assert e.embed("hyperscalers") == e.embed("hyperscalers zzzunknownzzz")


def test_an_unfitted_backend_still_answers():
    e = DistributionalEmbedder()
    assert not e.fitted
    assert len(e.embed("anything at all")) == e.dimensions
    assert "NOT fitted" in e.describe()


def test_a_fitted_backend_says_so():
    e = DistributionalEmbedder().fit(CHIPS + PALM)
    assert "fitted on" in e.describe() and "centred" in e.describe()


def test_fitting_on_nothing_does_not_raise():
    assert DistributionalEmbedder().fit([]).fitted


# --- the collection's side of the seam ----------------------------------------


def test_the_dense_index_is_built_lazily_and_rebuilt_when_a_document_arrives():
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS))
    assert col._vectors is None, "nothing should be embedded before a dense query"
    col.dense("accelerators", 1)
    assert col._vectors is not None
    col.add(Chunk("late", "a new headline about accelerators", "kb_news", None, None, NOW, {}))
    assert col._vectors is None, "a new document changes what the old ones mean"


def test_stored_vectors_are_pruned():
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS + PALM))
    col.dense("accelerators", 1)
    assert col._vectors is not None
    assert all(len(v) <= DOC_NONZEROS for _, v in col._vectors)


def test_a_backend_without_fit_is_never_asked_for_one():
    """`fit` is deliberately outside the Protocol: a three-line test embedder
    stays three lines."""

    class Three:
        dimensions = 4

        def embed(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0, 0.0]

    col = Collection("kb_news", Three())
    col.add_all(chunks(CHIPS))
    assert col.dense("anything", 2)


def test_the_hashing_backend_is_kept_and_still_works():
    e = HashingEmbedder()
    assert e.embed("margin compression") == e.embed("margin compression")
    assert len(e.embed("margin compression")) == 256


# --- the API backend ----------------------------------------------------------


def test_the_api_backend_refuses_to_exist_without_a_key(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    from knowledge.retrieval.embedding import ApiEmbedder

    with pytest.raises(EmbeddingError, match="EMBEDDING_API_KEY"):
        ApiEmbedder()


def test_the_default_backend_stays_keyless(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    assert isinstance(default_embedder(), DistributionalEmbedder)


def test_the_vector_cache_round_trips(tmp_path: Path):
    cache = _VectorCache(str(tmp_path / "vectors.db"))
    assert cache.get("m1", "hello") is None
    cache.put("m1", "hello", [0.5, -0.5])
    assert cache.get("m1", "hello") == [0.5, -0.5]
    assert cache.get("m2", "hello") is None, "a different model must not read these vectors"


# --- the measurement ----------------------------------------------------------


def test_a_leg_score_counts_ranks_the_way_a_reader_would():
    s = LegScore("test")
    s.observe([1])
    s.observe([4, 9])
    s.observe([])
    assert s.cases == 3
    assert s.hit_at_1 == 1 and s.hit_at_5 == 2 and s.hit_at_10 == 2
    assert s.mrr == pytest.approx((1.0 + 0.25) / 3)


def test_evaluating_a_corpus_scores_every_leg():
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS + PALM))
    cases = [Case("cloud companies buying chips", ("d0",), "semantic")]
    report = evaluate(col, cases)
    assert report.cases == 1
    assert set(report.legs) == {"bm25", "dense", "fused", "reranked"}
    assert "dense lift" in report.summary()


def test_a_label_that_left_the_corpus_is_named_not_silently_missed():
    col = Collection("kb_news", DistributionalEmbedder())
    col.add_all(chunks(CHIPS))
    report = evaluate(col, [Case("anything", ("gone",), "semantic", titles=("An old headline",))])
    assert report.missing_labels and "An old headline" in report.missing_labels[0]


# --- the shipped gold set -----------------------------------------------------


def test_the_gold_set_loads_and_is_not_trivial():
    cases = load_gold()
    assert len(cases) >= 20
    assert all(c.relevant for c in cases), "a question with no labels scores nothing"
    kinds = {c.kind for c in cases}
    assert kinds == {"semantic", "lexical"}


def test_the_gold_set_has_both_families_in_useful_numbers():
    cases = load_gold()
    assert sum(c.kind == "semantic" for c in cases) >= 12
    assert sum(c.kind == "lexical" for c in cases) >= 5


@pytest.mark.skipif(not Path("data/corpus.db").exists(), reason="no corpus in this checkout")
def test_the_vector_leg_earns_its_place():
    """The claim the old embedder failed, stated so it cannot silently fail again.

    Deliberately structural rather than a target score. Absolute recall moves as
    the corpus grows - more articles means more competition for the same ten
    slots - and a test pinned to a number would go red for a reason that is not
    a regression. These two hold whatever the corpus looks like: the vector leg
    must find something exact-token search missed, and fusing the two must not
    be worse than the better half.
    """
    from knowledge.retrieval.evaluate import report_for

    report = report_for(gold_path=GOLD)
    assert report.dense_lift > 0, (
        "the vector leg found nothing BM25 missed - it is a second lexical "
        "search, which is what the hashing projection was"
    )
    assert report.legs["fused"].recall_at_10 >= report.legs["bm25"].recall_at_10
    assert report.legs["reranked"].recall_at_10 >= report.legs["bm25"].recall_at_10
