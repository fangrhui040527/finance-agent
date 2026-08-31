"""P0 DoD: a claim without a verified citation is dropped, not hedged."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.contracts.answer import (
    Answer,
    Citation,
    Claim,
    TrustTier,
    verify_answer,
    verify_claim,
)

NOW = datetime.now(UTC)
CHUNKS = {("10-K", "c1"): "Revenue rose 12% to RM 4.2 billion in the period."}


def lookup(source, chunk_id):
    return CHUNKS.get((source, chunk_id))


def cite(span, source="10-K", chunk_id="c1"):
    return Citation(
        source=source,
        chunk_id=chunk_id,
        quoted_span=span,
        trust=TrustTier.FILINGS,
        as_of=NOW,
    )


def test_verbatim_citation_survives():
    c = verify_claim(Claim(text="up 12%", citations=[cite("Revenue rose 12%")]), lookup)
    assert c.supported


def test_fabricated_quote_is_dropped():
    c = verify_claim(Claim(text="up 40%", citations=[cite("Revenue rose 40%")]), lookup)
    assert not c.supported and "no citation verified" in c.dropped_reason


def test_unknown_chunk_is_dropped():
    c = verify_claim(
        Claim(text="x", citations=[cite("Revenue rose 12%", chunk_id="ghost")]), lookup
    )
    assert not c.supported


def test_trivially_short_quote_is_rejected():
    c = verify_claim(Claim(text="x", citations=[cite("12%")]), lookup)
    assert not c.supported


def test_bad_claim_dropped_individually_good_one_kept():
    """The change docs/12 section 2.2 specifies: per-claim, not whole-answer."""
    ans = verify_answer(
        [
            Claim(text="up 12%", citations=[cite("Revenue rose 12%")]),
            Claim(text="margin doubled", citations=[cite("Margin doubled")]),
        ],
        lookup,
        NOW,
        confidence=0.7,
    )
    assert ans.answered
    assert len(ans.claims) == 1 and len(ans.dropped) == 1


def test_all_claims_bad_becomes_a_refusal():
    ans = verify_answer([Claim(text="x", citations=[cite("nonsense")])], lookup, NOW, 0.7)
    assert ans.answered is False and ans.claims == []


def test_answered_with_no_claims_is_unconstructable():
    with pytest.raises(ValidationError):
        Answer(claims=[], dropped=[], confidence=0.9, answered=True, as_of=NOW)


def test_refusal_carrying_claims_is_unconstructable():
    with pytest.raises(ValidationError):
        Answer(
            claims=[Claim(text="x", citations=[cite("Revenue rose 12%")])],
            dropped=[],
            confidence=0.1,
            answered=False,
            as_of=NOW,
        )


def test_refusal_helper_is_valid():
    assert Answer.refusal("insufficient evidence", NOW).answered is False


def test_trust_order_puts_web_last():
    assert TrustTier.LEDGER < TrustTier.FILINGS < TrustTier.CURATED_NEWS < TrustTier.WEB_SEARCH
