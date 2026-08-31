"""Typed, cited answers.

Adapted from rag_tutorials/agentic_typed_rag_pydanticai at awesome-llm-apps
11a4bc33 (Apache-2.0), per docs/12 section 2.2.

The one change docs/12 specifies: that template refuses the WHOLE answer when no
citation survives. A finance answer carries many claims and docs/05 section 8.1
requires dropping the individual claim. So the Claim is the validated unit here,
not the Answer.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_QUOTE_CHARS = 8


class TrustTier(int, Enum):
    """docs/02 section 1. Lower value = higher trust. A web result may never
    overwrite a filing or a price series."""

    LEDGER = 0
    MARKET_SERIES = 1
    FILINGS = 2
    METHOD_KB = 3
    TRANSCRIPTS = 4
    CURATED_NEWS = 5
    WEB_SEARCH = 6


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    chunk_id: str
    quoted_span: str
    trust: TrustTier
    as_of: datetime

    @field_validator("source", "chunk_id", "quoted_span")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("citation fields must not be blank")
        return v


class Claim(BaseModel):
    """One assertion. Survives only if at least one citation verifies."""

    text: str
    citations: list[Citation] = Field(default_factory=list)
    dropped_reason: str | None = None
    all_citations_required: bool = False
    """Are the citations redundant support, or a chain?

    Default False: two sources for one assertion are alternatives, and one
    surviving is enough - which is the docs/05 8.1 rule.

    True for a claim whose citations are CONJUNCTIVE, each supporting a
    different link. A multi-hop graph exposure is the case: 'Alpha is exposed
    via port closure -> shipping -> Alpha' needs the document behind every hop.
    Under the default rule that claim survives with the first hop cited and the
    second unverified, and then reads as evidenced when the chain is broken.
    """

    @property
    def supported(self) -> bool:
        return bool(self.citations) and self.dropped_reason is None


class Answer(BaseModel):
    """Claims that survived, plus an explicit record of what was dropped."""

    claims: list[Claim]
    dropped: list[Claim] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    answered: bool
    as_of: datetime

    @model_validator(mode="after")
    def answered_implies_support(self) -> Answer:
        if self.answered and not self.claims:
            raise ValueError("an answered response requires at least one supported claim")
        if not self.answered and self.claims:
            raise ValueError("a refusal must carry no supported claims")
        if any(not c.supported for c in self.claims):
            raise ValueError("every surviving claim must be supported")
        return self

    @classmethod
    def refusal(cls, reason: str, as_of: datetime, confidence: float = 0.0) -> Answer:
        """A refusal is a valid, logged, non-penalised outcome (docs/05 8.1)."""
        return cls(
            claims=[],
            dropped=[Claim(text=reason, dropped_reason=reason)],
            confidence=confidence,
            answered=False,
            as_of=as_of,
        )


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def verify_claim(claim: Claim, chunk_lookup) -> Claim:
    """Post-hoc verbatim check. Never trusts the model's citation.

    chunk_lookup(source, chunk_id) -> chunk text or None.
    """
    good: list[Citation] = []
    for c in claim.citations:
        chunk = chunk_lookup(c.source, c.chunk_id)
        if chunk is None:
            continue
        q = _norm(c.quoted_span)
        if len(q) >= MIN_QUOTE_CHARS and q in _norm(chunk):
            good.append(c)
    if not good:
        return claim.model_copy(
            update={"citations": [], "dropped_reason": "no citation verified against its chunk"}
        )
    if claim.all_citations_required and len(good) < len(claim.citations):
        # A chain is not partially true. Keeping the verified links would emit a
        # conclusion that only holds if the unverified one does.
        return claim.model_copy(
            update={
                "citations": [],
                "dropped_reason": (
                    f"{len(claim.citations) - len(good)} of {len(claim.citations)} citations "
                    "failed and this claim needs every one: each supports a different link "
                    "in its chain"
                ),
            }
        )
    return claim.model_copy(update={"citations": good, "dropped_reason": None})


def verify_answer(claims: list[Claim], chunk_lookup, as_of: datetime, confidence: float) -> Answer:
    """Drop unsupported claims individually; refuse only if none survive."""
    checked = [verify_claim(c, chunk_lookup) for c in claims]
    kept = [c for c in checked if c.supported]
    dropped = [c for c in checked if not c.supported]
    if not kept:
        return Answer(claims=[], dropped=dropped, confidence=0.0, answered=False, as_of=as_of)
    return Answer(claims=kept, dropped=dropped, confidence=confidence, answered=True, as_of=as_of)
