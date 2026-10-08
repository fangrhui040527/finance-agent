"""A filtered search is filled from every scored document, and punctuation is not part of a word.

Both tests fail on the code before 2026-10-08: the freshness and entity
filters ran over a BM25 pool cut to four times `limit` across the whole
120-day index, and the tokenizer glued a full stop or a hyphen suffix onto the
word before it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from knowledge.retrieval.embedding import tokenize
from knowledge.retrieval.hybrid import Chunk, Collection

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_fresh_linked_chunks_are_found_however_many_older_ones_outscore_them():
    col = Collection("kb_news")
    for i in range(30):  # old stories that say IHH three times each
        col.add(
            Chunk(
                f"old{i}",
                f"IHH IHH IHH hospital story {i}",
                "kb_news",
                as_of=NOW - timedelta(days=40 + i),
                metadata={"instruments": ["MYX:5225"]},
            )
        )
    for i in range(5):  # this week's, saying it once
        col.add(
            Chunk(
                f"new{i}",
                f"IHH raises its Fortis stake, report {i}, with a long body about beds and capacity",
                "kb_news",
                as_of=NOW - timedelta(days=i),
                metadata={"instruments": ["MYX:5225"]},
            )
        )
    hits = col.search("IHH", limit=6, max_age=timedelta(days=7), now=NOW, entity="MYX:5225")
    assert sorted(h.chunk.chunk_id for h in hits) == [f"new{i}" for i in range(5)]


def test_a_full_stop_or_a_hyphen_suffix_is_not_part_of_the_word():
    toks = tokenize("Analysts raised targets on Nvidia. An Nvidia-backed startup.")
    assert "nvidia" in toks and "nvidia." not in toks
    assert {"nvidia-backed", "backed"} <= set(toks)
    assert "startup" in toks


def test_internal_dots_and_hyphens_still_hold_a_token_together():
    toks = tokenize("0011.KL rose; Q3-FY25 guidance; U.S. Inc.")
    assert {"0011.kl", "q3-fy25", "u.s", "inc"} <= set(toks)


def test_a_sentence_final_name_is_reachable_by_search():
    col = Collection("kb_news")
    col.add(Chunk("a", "Analysts raised their targets on Nvidia.", "kb_news", as_of=NOW))
    col.add(Chunk("b", "Apple shipped a phone", "kb_news", as_of=NOW))
    assert [h.chunk.chunk_id for h in col.search("nvidia", limit=3, now=NOW)] == ["a"]
