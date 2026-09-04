"""The routine's pages become kb_lessons - the one store an agent may write."""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge.retrieval.index import build_router, lessons_collection

NOW = datetime(2026, 9, 6, tzinfo=UTC)

PAGE = """# Feedback 2026-09-04

## Names

### Maybank (MYX:1155)

**Moved** -2.10% vs market -0.80%; unexplained share 58%.

**Why.** Guidance on net interest margin was cut - evidence `google_news:abc`.
  Why that? Deposit competition from digital banks - evidence `bursa_announcements:1155:33921`.

## Lessons proposed

- A Bursa bank's margin guidance moves the stock more than the print itself.
"""


def test_dated_pages_are_chunked_and_dated_and_the_template_is_not(tmp_path):
    (tmp_path / "2026-09-04.md").write_text(PAGE, encoding="utf-8")
    (tmp_path / "TEMPLATE.md").write_text("# Feedback YYYY-MM-DD\n{placeholder}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# contract", encoding="utf-8")
    (tmp_path / "2026-09-04.pack.md").write_text("# pack", encoding="utf-8")
    col = lessons_collection(tmp_path)
    assert len(col) >= 1
    hits = col.search("margin guidance digital banks", 5, now=NOW)
    assert hits and hits[0].chunk.as_of == datetime(2026, 9, 4, tzinfo=UTC)
    assert (
        hits[0].chunk.metadata["licence"] == "own" and hits[0].chunk.metadata["day"] == "2026-09-04"
    )
    assert all("placeholder" not in h.chunk.text for h in hits)


def test_an_absent_folder_gives_an_empty_store(tmp_path):
    assert len(lessons_collection(tmp_path / "nowhere")) == 0


def test_the_reflection_agent_owns_the_lessons_and_the_news_agent_does_not(registry, tmp_path):
    import pytest

    from knowledge.retrieval.pipeline import CollectionScopeError

    (tmp_path / "2026-09-04.md").write_text(PAGE, encoding="utf-8")
    router = build_router(registry, None, now=NOW, feedback_dir=tmp_path)
    assert len(router.get("a15_reflection", "kb_lessons")) >= 1
    with pytest.raises(CollectionScopeError):
        router.get("a4_news_narrative", "kb_lessons")
