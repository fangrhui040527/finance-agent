"""Parent-child hierarchical chunking.

docs/06 section 5.1, from the FinTradeBench finding recorded in docs/09 section 6:
parent sections <=2,000 tokens, child retrieval chunks ~300 tokens with overlap.
Retrieve on children, generate with the parent.

docs/09 also records the reason to spend effort here rather than on model size:
across ~23,000 SEC filings, retrieval and chunking strategy affected output
quality more than raw generative horsepower, and generic chunking produced
factual errors in roughly one in seven financial queries.

Split on STRUCTURAL elements (SEC Item, note number, Bursa announcement section),
not paragraphs - that yields good chunk sizes without tuning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

PARENT_MAX_TOKENS = 2000
CHILD_TARGET_TOKENS = 300
CHILD_OVERLAP = 0.15

# SEC "Item 7.", "ITEM 1A.", note headings, and Bursa-style numbered sections.
STRUCTURAL = re.compile(
    r"^\s*(?:(?:PART\s+[IVX]+)|(?:ITEM\s+\d+[A-Z]?\.?)|(?:NOTE\s+\d+\.?)|(?:\d+\.\d*\s+[A-Z]))",
    re.MULTILINE | re.IGNORECASE,
)


def count_tokens(text: str) -> int:
    """Whitespace proxy. Swap for the model tokenizer when one is wired in."""
    return len(text.split())


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    corpus: str
    parent_id: str | None = None
    section: str | None = None
    as_of: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_parent(self) -> bool:
        return self.parent_id is None


def tables_to_markdown(text: str) -> str:
    """Convert pipe-ish or tab-separated blocks to markdown before chunking.

    docs/09: preserve section boundaries and convert embedded financial tables
    to markdown first, or a table gets split across two children and the numbers
    lose their headers.
    """
    out: list[str] = []
    for line in text.splitlines():
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t") if c.strip()]
            if len(cells) >= 2:
                out.append("| " + " | ".join(cells) + " |")
                continue
        out.append(line)
    return "\n".join(out)


def split_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) pairs on structural boundaries."""
    marks = list(STRUCTURAL.finditer(text))
    if not marks:
        return [("body", text.strip())]
    out = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = text[start:end].strip()
        heading = block.splitlines()[0].strip() if block else "body"
        out.append((heading, block))
    if marks[0].start() > 0:
        preamble = text[: marks[0].start()].strip()
        if preamble:
            out.insert(0, ("preamble", preamble))
    return out


def _window(words: list[str], size: int, overlap: float) -> list[list[str]]:
    if len(words) <= size:
        return [words]
    step = max(1, int(size * (1 - overlap)))
    out = []
    for i in range(0, len(words), step):
        piece = words[i : i + size]
        if piece:
            out.append(piece)
        if i + size >= len(words):
            break
    return out


def chunk_document(
    doc_id: str,
    text: str,
    corpus: str,
    as_of: datetime | None = None,
    metadata: dict | None = None,
    parent_max: int = PARENT_MAX_TOKENS,
    child_target: int = CHILD_TARGET_TOKENS,
) -> tuple[list[Chunk], list[Chunk]]:
    """Returns (parents, children). Children carry parent_id."""
    text = tables_to_markdown(text)
    parents: list[Chunk] = []
    children: list[Chunk] = []
    meta = metadata or {}

    for si, (heading, body) in enumerate(split_sections(text)):
        words = body.split()
        for pi, pw in enumerate(_window(words, parent_max, 0.0)):
            pid = f"{doc_id}#s{si}p{pi}"
            parent_text = " ".join(pw)
            parents.append(Chunk(pid, parent_text, corpus, None, heading, as_of, dict(meta)))
            for ci, cw in enumerate(_window(pw, child_target, CHILD_OVERLAP)):
                children.append(
                    Chunk(f"{pid}c{ci}", " ".join(cw), corpus, pid, heading, as_of, dict(meta))
                )
    return parents, children


def chunk_news(
    doc_id: str, text: str, as_of: datetime, metadata: dict | None = None
) -> list[Chunk]:
    """docs/06: whole article if under ~1k tokens. Never split a quote from its
    attribution, so short articles stay intact."""
    if count_tokens(text) < 1000:
        return [Chunk(doc_id, text.strip(), "kb_news", None, None, as_of, metadata or {})]
    parents, children = chunk_document(doc_id, text, "kb_news", as_of, metadata)
    return parents + children


def chunk_transcript(
    doc_id: str, turns: list[tuple[str, str, str]], as_of: datetime, merge_to: int = 600
) -> list[Chunk]:
    """Speaker turns merged to ~600 tokens, NEVER across speakers.

    docs/06 section 5.6: the identity of who said something is half the signal,
    and management answers in Q&A behave differently from prepared remarks.
    """
    out: list[Chunk] = []
    buf: list[str] = []
    cur_speaker: str | None = None
    cur_role: str | None = None
    idx = 0

    def flush() -> None:
        nonlocal buf, idx
        if buf:
            out.append(
                Chunk(
                    f"{doc_id}#t{idx}",
                    " ".join(buf),
                    "kb_transcripts",
                    None,
                    None,
                    as_of,
                    {"speaker": cur_speaker, "role": cur_role},
                )
            )
            idx += 1
            buf = []

    for speaker, role, said in turns:
        if speaker != cur_speaker and buf:
            flush()
        cur_speaker, cur_role = speaker, role
        buf.append(said)
        if count_tokens(" ".join(buf)) >= merge_to:
            flush()
    flush()
    return out
