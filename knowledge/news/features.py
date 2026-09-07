"""Five-dimension text features.

docs/02 A4 and docs/09 section 7. The evidence is that a single polarity score
leaves information on the table: a study across five dimensions found INTENSITY
and UNCERTAINTY carried more predictive weight than polarity, and that an LLM
combined with FinBERT beat either alone.

Two biases this module is shaped to avoid:

  1. Look-ahead. Features are EXTRACTED FROM A DOCUMENT, never predicted from it.
     The agent is never asked "will this stock go up" - extraction does not
     require the model to know the outcome, so a feature computed here can enter
     a backtest without the model having the answer in its weights.

  2. Distraction. Extraction is narrow and structured rather than "read this and
     tell me what you think", because extraneous company information is
     documented to contaminate the sentiment read.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

WORD = re.compile(r"[a-z']+", re.IGNORECASE)

# Deliberately small, auditable lexicons. The local model is the first pass;
# these make it inspectable and give the tests something deterministic.
NEGATIVE = {
    "loss",
    "losses",
    "decline",
    "declined",
    "fell",
    "weak",
    "weaker",
    "miss",
    "missed",
    "cut",
    "cuts",
    "downgrade",
    "impairment",
    "writedown",
    "default",
    "probe",
    "investigation",
    "lawsuit",
    "resign",
    "resigned",
    "warning",
    "warned",
    "shortfall",
    "delay",
    "delayed",
    "suspend",
    "suspended",
    "fraud",
    "breach",
}
POSITIVE = {
    "profit",
    "growth",
    "grew",
    "rose",
    "beat",
    "beats",
    "record",
    "upgrade",
    "expansion",
    "wins",
    "won",
    "contract",
    "approval",
    "approved",
    "dividend",
    "buyback",
    "strong",
    "stronger",
    "improved",
    "recovery",
    "surge",
}
UNCERTAIN = {
    "may",
    "might",
    "could",
    "uncertain",
    "uncertainty",
    "possible",
    "possibly",
    "potential",
    "risk",
    "risks",
    "unclear",
    "depends",
    "subject",
    "pending",
    "estimate",
    "estimated",
    "approximately",
    "expects",
    "expected",
    "if",
}
FORWARD = {
    "will",
    "guidance",
    "outlook",
    "forecast",
    "expects",
    "expected",
    "plans",
    "targets",
    "anticipates",
    "next",
    "upcoming",
    "fy26",
    "fy27",
    "2027",
    "2028",
}
INTENSE = {
    "surge",
    "surged",
    "plunge",
    "plunged",
    "collapse",
    "collapsed",
    "soar",
    "soared",
    "slump",
    "slumped",
    "record",
    "unprecedented",
    "massive",
    "sharply",
    "dramatically",
    "halted",
    "emergency",
    "crisis",
}


@dataclass(frozen=True)
class Features:
    """Five dimensions, each in [0,1] except polarity in [-1,1]."""

    relevance: float
    polarity: float
    intensity: float
    uncertainty: float
    forwardness: float
    extractor: str = "lexicon-v1"

    def as_dict(self) -> dict[str, float]:
        return {
            "relevance": self.relevance,
            "polarity": self.polarity,
            "intensity": self.intensity,
            "uncertainty": self.uncertainty,
            "forwardness": self.forwardness,
        }


class FeatureExtractor(Protocol):
    name: str

    def extract(self, text: str, entities: list[str]) -> Features: ...


class LexiconExtractor:
    """Local first pass. Zero marginal cost, deterministic, inspectable.

    docs/08 section 7: a rule and local-model filter in front of the API is what
    keeps roughly four in five articles from ever reaching a paid model.
    """

    name = "lexicon-v1"

    def extract(self, text: str, entities: list[str]) -> Features:
        words = [w.lower() for w in WORD.findall(text)]
        n = len(words) or 1
        wset = set(words)

        pos = sum(1 for w in words if w in POSITIVE)
        neg = sum(1 for w in words if w in NEGATIVE)
        polarity = (pos - neg) / max(pos + neg, 1)

        low = text.lower()
        mentions = sum(low.count(e.lower()) for e in entities if e) if entities else 0
        # One mention clears the escalation gate; each further mention adds
        # conviction. The old `mentions / 3` put a single-mention headline at
        # 0.33 - one hundredth under the 0.34 gate - so a wire story that
        # named a holding exactly once, which is how most headlines name a
        # company, could never reach the review queue.
        relevance = min(1.0, 0.5 + 0.25 * (mentions - 1)) if mentions else 0.0

        return Features(
            relevance=relevance,
            polarity=max(-1.0, min(1.0, polarity)),
            intensity=min(1.0, len(wset & INTENSE) / 3.0),
            uncertainty=min(
                1.0, sum(1 for w in words if w in UNCERTAIN) / (n * 0.08) if n else 0.0
            ),
            forwardness=min(1.0, sum(1 for w in words if w in FORWARD) / (n * 0.06) if n else 0.0),
            extractor=self.name,
        )


#: One mention of a watched name clears this; see LexiconExtractor.extract.
RELEVANCE_FLOOR = 0.34


def should_escalate(
    features: Features, entities: list[str], holdings: set[str], watchlist: set[str]
) -> bool:
    """The escalation gate. An LLM call only for articles that matter.

    docs/02 A4: local model first, LLM only for articles attached to a holding
    or a live candidate - which docs/08 section 7 sizes at roughly one article
    in five reaching a paid model.

    THREE CONDITIONS, and for a long time there were effectively one. The gate
    used to read `relevance >= 0.34 AND a watched name is in entities`, but
    relevance is a mention COUNT: any article naming the company once scores
    0.50, so the first condition was true exactly when the second was. Measured
    on the corpus at 2026-09-07, that gate passed 513 of the 791 articles linked
    to a name in the book - 65%, and 8 of 8 for Maybank, 8 of 8 for Tenaga. A
    gate that rejects nothing is not a gate; it is an LLM bill.

    The third condition is MATERIALITY: the article must say something financial
    about the company, not merely name it. Sponsorships, branch openings, golf
    tournaments and traffic reports name a bank without reporting anything about
    it. Requiring one of the three lexicon-bearing dimensions to be non-zero -
    polarity (POSITIVE or NEGATIVE fired), intensity (INTENSE), forwardness
    (FORWARD) - takes the same 791 articles to 134, or 17%, which is the
    documented design target. Per name: Maybank 8 -> 1, Tenaga 8 -> 1, Nvidia
    226 -> 68. The one Maybank story that survives is a broker note with a price
    target cut; the seven dropped are the charity cheque, the golf, the car-loan
    product launch, the meme post and the road closure.

    UNCERTAINTY is deliberately not a materiality signal even though it is a
    lexicon dimension: its words are "may", "could", "if", "expects" - ordinary
    English that fires on almost any prose, so including it re-opens the gate it
    is meant to close.

    KNOWN COST, measured rather than assumed: an article where POSITIVE and
    NEGATIVE fire exactly equally has polarity 0, and if nothing else fires it
    is dropped despite being material. That is 1 article of the 791.
    """
    touched = set(entities)
    if not (touched & holdings) and not (touched & watchlist):
        return False
    if features.relevance < RELEVANCE_FLOOR:
        return False
    return features.polarity != 0.0 or features.intensity > 0.0 or features.forwardness > 0.0


def near_duplicate_hash(text: str, shingle: int = 6) -> str:
    """Wire stories replicate across hundreds of domains.

    docs/02 A4: dedup BEFORE indexing or a single syndicated story dominates
    every top-k it is remotely relevant to.
    """
    words = [w.lower() for w in WORD.findall(text)]
    if len(words) < shingle:
        return hashlib.sha1(" ".join(words).encode()).hexdigest()[:16]
    grams = [" ".join(words[i : i + shingle]) for i in range(len(words) - shingle + 1)]
    # Min-hash over a fixed permutation: stable, order-independent, cheap.
    best = min(hashlib.md5(g.encode()).hexdigest() for g in grams)
    return best[:16]


@dataclass
class SourceReliability:
    """docs/02 section 1: repeated numeric mismatch from one domain permanently
    downweights it."""

    checks: int = 0
    mismatches: int = 0

    def record(self, matched: bool) -> None:
        self.checks += 1
        if not matched:
            self.mismatches += 1

    @property
    def score(self) -> float:
        """Laplace-smoothed agreement rate. Unknown domains start neutral."""
        return (self.checks - self.mismatches + 1) / (self.checks + 2)


@dataclass
class Article:
    doc_id: str
    title: str
    body: str
    source_domain: str
    published_at: datetime
    language: str = "en"
    countries: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    features: Features | None = None
    dup_hash: str | None = None
    #: knowledge/news/clean.quality_score, set by the adapter. None means the
    #: article predates the score (the pre-2026-09-04 corpus) - not "unknown
    #: quality", which would be a reason to drop it.
    quality: float | None = None
    #: Whether the escalation gate opened for it: relevant AND naming a held or
    #: watched instrument. Stored so a digest can say what reached the queue.
    escalated: bool = False
    #: The instrument this article was FETCHED FOR, when a per-name source went
    #: looking. Provenance, never attribution: GDELT is asked one phrase per
    #: company and answers with whatever its full-text index matched, so 457 of
    #: the 575 GDELT articles collected to 2026-09-06 named no book company at
    #: all. Recording the query is what makes that measurable per source per
    #: name; asserting the company would put "Bunny Ranch Brothel Empire Up for
    #: Sale" in Apple's evidence, which is the fetch talking, not the article.
    fetched_for: str = ""

    @property
    def text(self) -> str:
        if not self.body or self.body == self.title:
            return self.title
        return f"{self.title}. {self.body}"
