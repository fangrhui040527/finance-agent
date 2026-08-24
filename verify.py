#!/usr/bin/env python3
"""Full P0 pipeline on mock data. No network, no API keys, under a second.

Pattern from devpulse_ai/verify.py at awesome-llm-apps 11a4bc33 (Apache-2.0),
per docs/12 section 2.5. Without this, CI ends up depending on live vendor feeds
and starts failing for reasons unrelated to the code.

    python verify.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from core.contracts.answer import Citation, Claim, TrustTier, verify_answer
from core.contracts.money import Money
from core.guardrails.chain import GuardrailChain
from core.guardrails.defaults import default_engine
from core.guardrails.policy import Action, PolicyViolation, Rail
from core.llm.client import EchoBackend, InferenceClient
from core.llm.tiers import TaskClass, Tier
from core.provenance.ledger import ProvenanceLedger

NOW = datetime.now(timezone.utc)
CHUNKS = {("1155.KL-Q2-2026", "c7"): "Net interest margin improved to 2.31% from 2.18%."}
OK, FAIL = "[OK]", "[FAIL]"
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {OK if cond else FAIL} {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        failures.append(label)


def main() -> int:
    t0 = time.time()
    print("\nFinPlanet P0 verification (mock data only)\n")

    print("1. Tier routing")
    engine = default_engine({"a4": {"llm_complete"}, "a10": {"llm_complete", "emit"}})
    ledger = ProvenanceLedger()
    client = InferenceClient(EchoBackend(), engine, ledger, daily_budget_myr=Decimal("25"))
    check("news triage -> cheap", client.complete("a4", TaskClass.NEWS_TRIAGE, "headline").tier is Tier.CHEAP)
    check("thesis -> reason", client.complete("a10", TaskClass.THESIS_SYNTHESIS, "memo").tier is Tier.REASON)

    print("\n2. Guardrail chain")
    chain = GuardrailChain(engine)
    check("all five rails covered", len(chain.rails_covered()) == 5)
    denied = False
    try:
        engine.enforce(Action("place_order", Rail.TOOL, "a10", {}))
    except PolicyViolation:
        denied = True
    check("execution denied", denied)
    blocked = False
    try:
        engine.enforce(Action("emit", Rail.OUTPUT, "a10", {"text": "you should buy now"}))
    except PolicyViolation:
        blocked = True
    check("advice language blocked", blocked)

    print("\n3. Citation verification")
    ans = verify_answer(
        [
            Claim(text="NIM improved", citations=[Citation(
                source="1155.KL-Q2-2026", chunk_id="c7",
                quoted_span="Net interest margin improved to 2.31%",
                trust=TrustTier.FILINGS, as_of=NOW)]),
            Claim(text="loan growth 9%", citations=[Citation(
                source="1155.KL-Q2-2026", chunk_id="c7",
                quoted_span="Loans grew 9 percent",
                trust=TrustTier.FILINGS, as_of=NOW)]),
        ],
        lambda s, c: CHUNKS.get((s, c)), NOW, confidence=0.62,
    )
    check("supported claim kept", len(ans.claims) == 1)
    check("fabricated claim dropped", len(ans.dropped) == 1)
    for c in ans.dropped:
        ledger.record_claim("a10", c.text, [], survived=False, dropped_reason=c.dropped_reason)

    print("\n4. Provenance ledger")
    calls = list(ledger.calls())
    check("every call logged", len(calls) == 2, f"{len(calls)} rows")
    check("cost in MYR", ledger.total_cost_myr() > 0, f"RM {ledger.total_cost_myr():.6f}")
    tamper = False
    try:
        ledger.conn.execute("DELETE FROM llm_calls")
    except Exception:
        tamper = True
    check("append-only enforced", tamper)

    print("\n5. Money contract")
    usd = Money(amount=Decimal("49"), currency="USD", fx_asof=NOW)
    myr = usd.convert("MYR", Decimal("4.15"), NOW)
    check("USD -> MYR carries fx_asof", myr.fx_asof is not None, str(myr))

    print("\n6. Attribution engine")
    import random
    from datetime import date
    from engines.attribution.decompose import Verdict, decompose
    from engines.attribution.regression import huber_fit
    rng = random.Random(11)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    ys = [1.1 * a + 0.6 * b + rng.gauss(0, 0.005) for a, b in rows]
    fit = huber_fit(rows, ys)
    w = (date(2026, 8, 1), date(2026, 8, 12))
    mkt = decompose("X", w, -0.068, -0.015, {}, -0.094, 0.0, fit)
    check("market selloff gets no company story",
          mkt.verdict in (Verdict.MARKET_DRIVEN, Verdict.NOT_SIGNIFICANT)
          and not mkt.needs_cause_hunt(), mkt.verdict.value)
    idio = decompose("X", w, -0.002, 0.001, {}, 0.072, 0.0, fit)
    check("idiosyncratic move triggers a hunt", idio.needs_cause_hunt(),
          f"{idio.unexplained_share:.0%} unexplained")

    print("\n7. Risk and sizing")
    from decimal import Decimal as DD
    from engines.risk.concentration import Limits, Position, check as ccheck, effective_number_of_bets
    from engines.sizing.caps import cost_floor_value
    from markets.registry import get as mget
    banks = [Position(f"B{i}", 0.10, "Financials", "MY", "MYR", 0.006) for i in range(10)]
    corr = [[1.0 if i == j else 0.85 for j in range(10)] for i in range(10)]
    eb = effective_number_of_bets([p.weight for p in banks], corr)
    check("10 correlated names read as ~1 bet", eb < 1.5, f"{eb:.2f} effective bets")
    check("breaches reported", len(ccheck(banks, corr, Limits())) >= 4)
    floor = cost_floor_value(mget("XKLS").fee_schedule.round_trip, "XKLS")
    check("Bursa minimum economic position", DD("3000") < floor < DD("8000"), f"RM {floor:,.0f}")

    dt = time.time() - t0
    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}  ({dt:.2f}s)\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
