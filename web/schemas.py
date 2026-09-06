"""The one response envelope, and the request bodies the POST endpoints take.

Every endpoint answers the same shape:

    {"ok": bool, "text": str, "data": ..., "refusal": {"reason": str} | null,
     "disclaimer": str}

`text` is BYTE-IDENTICAL to what the MCP tool returns for the same inputs -
the web app and the model read the same words, which is what makes the parity
testable. `refusal` is first-class: a refusal is HTTP 200 with ok=true and a
reason, because "no, and here is why" is a successful answer everywhere in
this system. `data` carries structure only where it comes straight off an
engine or store; nothing in it is computed twice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

REFUSAL_PREFIXES = ("REFUSED", "NO DATA", "NO POSITION")


class Refusal(BaseModel):
    reason: str


class Envelope(BaseModel):
    ok: bool = True
    text: str = ""
    data: Any = None
    refusal: Refusal | None = None
    disclaimer: str = ""


def envelope(text: str, data: Any = None, disclaimer: str = "") -> Envelope:
    """Wrap a tool's text, promoting a REFUSED/NO DATA opening into `refusal`."""
    stripped = text.strip()
    refusal = None
    for prefix in REFUSAL_PREFIXES:
        if stripped.startswith(prefix):
            first_line = stripped.splitlines()[0]
            refusal = Refusal(reason=first_line)
            break
    return Envelope(ok=True, text=text, data=data, refusal=refusal, disclaimer=disclaimer)


# --- request bodies -------------------------------------------------------------


class WhyBody(BaseModel):
    instrument: str
    instrument_return: float | None = None
    market_return: float | None = None
    sector_return: float | None = None
    fx_return: float = 0.0
    market_proxy: str = ""
    bars: int = Field(default=1, ge=1, le=250)
    as_at: str = ""
    beta_market: float = 1.1
    beta_sector: float = 0.5
    currency: str = "MYR"


class FactorModelBody(BaseModel):
    returns_csv: str


class EvidenceItem(BaseModel):
    agent: str
    text: str


class BreakerItem(BaseModel):
    statement: str
    query: str
    store: str


class ThesisBody(BaseModel):
    instrument: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    breakers: list[BreakerItem] = Field(default_factory=list)
    stance: str = "hold"
    horizon_months: int = Field(default=12, ge=1, le=120)
    narrate: bool = False
    derive_valuation: bool = False
    as_at: str = ""
    archetype: str = ""


class PositionItem(BaseModel):
    instrument: str
    weight: float
    sector: str
    country: str
    currency: str | None = None
    risk_to_stop: float = 0.0


class RiskBody(BaseModel):
    positions: list[PositionItem] = Field(default_factory=list)
    base_currency: str = "MYR"
    single_name_limit: float | None = None
    equity: float | None = None
    peak_equity: float | None = None


class SizingBody(BaseModel):
    instrument: str
    portfolio_value: float
    price: float
    stop_price: float
    adv_20d: float
    risk_per_trade: float = 0.0075
    single_name_limit: float = 0.08
    participation: float = 0.05
    win_rate: float | None = None
    payoff: float | None = None
    n_trades: int = 0
    fx_myr_per_unit: float | None = None


class PlanBody(BaseModel):
    question: str
    instruments: list[str] = Field(default_factory=list)
    budget_myr: float | None = None


class ExplainBody(BaseModel):
    concept: str = ""
    mastered: list[str] = Field(default_factory=list)


class PredictionBody(BaseModel):
    instrument: str
    direction: int
    horizon_days: int
    confidence: float
    thesis: str


class GradeBody(BaseModel):
    realised_return: float
    benchmark_return: float
    note: str = ""
    today: str = ""


class HypothesisBody(BaseModel):
    title: str
    thesis: str


class AllocateBody(BaseModel):
    names: list[str] = Field(default_factory=list)
    portfolio_value: float | None = None
    fetch: bool = False
    as_at: str = ""
    single_name_limit: float = 0.08
    risk_per_trade: float = 0.0075
    participation: float = 0.05


class RebalanceBody(BaseModel):
    names: list[str] = Field(default_factory=list)
    portfolio_value: float | None = None
    from_plan: bool = False
    fetch: bool = True
    as_at: str = ""
    single_name_limit: float = 0.08
    risk_per_trade: float = 0.0075
