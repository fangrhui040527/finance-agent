"""Force every tier onto the cheapest model, for the duration of a QA session.

Why this exists, and why it is confined to `qa/`.

The product's design rule is that a caller may never choose a tier: `route()`
derives one from a `TaskClass`, and `MODEL_IDS` maps that tier to a model. That
rule is correct and is not being relaxed here. What this module changes is the
*table*, not the routing - every tier is pointed at Haiku so a QA run that
exercises the reason-tier code path bills at the cheap-tier rate.

Two consequences a reader should know before trusting a live QA result:

1. **Answer quality is not under test when this is active.** A thesis produced by
   Haiku through the reason-tier path proves the seam works; it says nothing
   about whether Opus would have written a better thesis. Quality assertions in
   `qa/phase2/test_p2_geval.py` are graded relative to the model that produced
   them, and the report records which model that was.

2. **Cost assertions still use the real per-tier prices.** `PRICING_USD` is left
   alone deliberately, so a test that reconciles the ledger against the live
   usage payload will disagree with the real invoice by exactly the tier price
   ratio. `qa/_support/cost.py` prices its own meter off the model that actually
   answered, and the reconciliation test asserts against that, not against the
   ledger, so the discrepancy is measured rather than hidden.

Use it as a context manager so the patch cannot leak into another test:

    with cheap_models():
        ...
"""

from __future__ import annotations

import contextlib
import os

#: The cheapest first-party model, and the one this QA session is pinned to.
CHEAP_MODEL = "claude-haiku-4-5"

#: What the API resolves CHEAP_MODEL to. Asserted in phase 2 so an alias that
#: silently starts pointing at a pricier snapshot is caught rather than paid for.
CHEAP_MODEL_RESOLVED = "claude-haiku-4-5-20251001"


@contextlib.contextmanager
def cheap_models(model: str = CHEAP_MODEL):
    """Point every tier at one model, then restore the real table."""
    from core.llm import tiers

    original = dict(tiers.MODEL_IDS)
    for tier in list(tiers.MODEL_IDS):
        if tier in (tiers.Tier.EMBED, tiers.Tier.LOCAL):
            continue  # not Messages API models; leave them alone
        tiers.MODEL_IDS[tier] = model
    try:
        yield model
    finally:
        tiers.MODEL_IDS.clear()
        tiers.MODEL_IDS.update(original)


def api_key() -> str | None:
    """The key, from the environment or from a local `.env`, or None.

    `.env` is read directly rather than through a dependency: the runtime dep
    set is deliberate (pydantic/pyyaml, the anthropic SDK, fastapi/uvicorn -
    see the docs' dependency commitment) and the QA suite does not grow it by
    one more for four lines of parsing.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    from pathlib import Path

    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def redact(text: str, key: str | None = None) -> str:
    """Replace an API key with a marker.

    Used on anything a QA run writes to disk. A key that reaches an artefact
    directory has left the operator's control, and the artefacts here are meant
    to be readable and shareable.
    """
    key = key or api_key()
    if not key:
        return text
    return text.replace(key, "sk-ant-<REDACTED>")
