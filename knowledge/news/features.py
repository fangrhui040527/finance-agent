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
        mentions = sum(low.count(e.lower()) for e in entities) if entities else 0
        relevance = min(1.0, mentions / 3.0) if entities else 0.0

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


def should_escalate(
    features: Features, entities: list[str], holdings: set[str], watchlist: set[str]
) -> bool:
    """The escalation gate. An LLM call only for articles that matter.

    docs/02 A4: local model first, LLM only for articles attached to a holding
    or a live candidate.
    """
    if features.relevance < 0.34:
        return False
    touched = set(entities)
    return bool(touched & holdings) or bool(touched & watchlist)


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

    @property
    def text(self) -> str:
        return f"{self.title}. {self.body}"
