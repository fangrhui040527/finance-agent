#!/usr/bin/env python3
"""Ask the system a question.

docs/14 section 3 month 1 says to run the system read-only against questions you
already know the answer to. That instruction needed a way to ask, which did not
exist: the agents were classes with no entrypoint.

    python ask.py plan "why did maybank fall today" --instrument MYX:1155
    python ask.py why MYX:1155 --move -0.090 --market -0.080 --sector -0.020
    python ask.py why XNAS:NVDA --move 0.072 --market 0.004 --sector 0.002 --history nvda.csv

The returns are arguments rather than a live feed on purpose. The feed seam is
unwired (`GdeltFeed._fetch_raw` raises), so anything that pretended to fetch
prices here would be inventing them. Supply them, or point --history at a CSV of
`instrument_return,market_return,sector_return` rows for the estimation window.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from agents.base import AgentContext
from agents.supervisor import A0Supervisor
from agents.synthesis.agents import A9Attribution
from core.guardrails.defaults import default_engine
from core.registry.loader import load as load_registry
from engines.attribution.decompose import MIN_OBSERVATIONS
from engines.attribution.regression import huber_fit
from knowledge.retrieval.pipeline import Router
from ui.render import decomposition_bars, refusal_card

REGISTRY = "agents/registry.yaml"


def context() -> AgentContext:
    """The allowlist comes from the registry, never a hand-written dict."""
    reg = load_registry(REGISTRY)
    return AgentContext(router=Router({}), engine=default_engine(reg.allowlist()),
                        now=datetime.now(timezone.utc))


def _fit_from_csv(path: str):
    rows, y = [], []
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if not r or r[0].lstrip().startswith("#"):
                continue
            try:
                inst, mkt, sec = float(r[0]), float(r[1]), float(r[2])
            except (ValueError, IndexError):
                continue                      # header row
            y.append(inst)
            rows.append([mkt, sec])
    # Same guard the engine applies in estimate(). Calling huber_fit directly
    # would slip past MIN_OBSERVATIONS and fit betas to noise.
    if len(y) < MIN_OBSERVATIONS:
        print(f"note: {len(y)} usable rows in {path}, below the {MIN_OBSERVATIONS} the "
              "engine requires. Reporting attribution_unavailable rather than guessing.",
              file=sys.stderr)
        return None
    try:
        return huber_fit(rows, y)
    except ValueError as e:
        # Collinear or degenerate history. A refusal, not a crash.
        print(f"note: {path} cannot support a factor model ({e}).", file=sys.stderr)
        return None


def _fit_synthetic(beta_mkt: float, beta_sec: float, seed: int = 7):
    """A stated-beta stand-in for a real estimation window.

    This is NOT an estimate of anything. It exists so the pipeline can be
    exercised on a known answer, which is what month 1 asks for. Every output
    built on it is labelled accordingly.
    """
    rng = random.Random(seed)
    rows = [[rng.gauss(0, 0.01), rng.gauss(0, 0.008)] for _ in range(250)]
    y = [beta_mkt * a + beta_sec * b + rng.gauss(0, 0.004) for a, b in rows]
    return huber_fit(rows, y)


def cmd_plan(a) -> int:
    a0 = A0Supervisor(context())
    plan = a0.plan(a.question,
                   budget_myr=Decimal(str(a.budget)) if a.budget else None,
                   instrument_ids=tuple(a.instrument or ()))
    if not plan.allowed:
        print(refusal_card(plan.refusal.reason, plan.refusal.what_would_help))
        return 2                                     # a refusal is not an error
    print(f"intent    {plan.intent.value}")
    print(f"cost      about RM {plan.estimated_cost.amount:.2f}")
    print(f"agents    {len(plan.agents)}")
    for name in plan.agents:
        print(f"            {name}")
    for note in plan.notes:
        print(f"note      {note}")
    return 0


def cmd_why(a) -> int:
    fit = _fit_from_csv(a.history) if a.history else _fit_synthetic(a.beta_market, a.beta_sector)
    end = date.fromisoformat(a.on) if a.on else date.today()
    window = (end - timedelta(days=a.days), end)

    a9 = A9Attribution(context())
    findings = a9.run(
        a.instrument, window,
        realised_local=a.move, event_market=a.market, event_sector=a.sector,
        event_styles={}, fx_return=a.fx, fit=fit, base_currency=a.currency,
    )
    head = findings[0]

    # Rebuild the explanation for the renderer rather than re-deriving numbers.
    from engines.attribution.decompose import decompose
    exp = decompose(a.instrument, window, a.market, a.sector, {}, a.move, a.fx,
                    fit, base_currency=a.currency)
    print(decomposition_bars(exp))
    print()
    print(head.text)
    for c in head.caveats:
        print(f"  caveat: {c}")
    if not a.history:
        print("\n  betas are stated, not estimated: pass --history with 120+ rows of")
        print("  instrument,market,sector returns for a real estimation window.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ask", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="what would the system do with this question")
    pl.add_argument("question")
    pl.add_argument("--instrument", action="append", help="repeatable")
    pl.add_argument("--budget", type=float, help="cap in MYR; too small is refused, not cheapened")
    pl.set_defaults(fn=cmd_plan)

    wy = sub.add_parser("why", help="decompose a move before naming a cause")
    wy.add_argument("instrument")
    wy.add_argument("--move", type=float, required=True, help="realised local return, e.g. -0.09")
    wy.add_argument("--market", type=float, required=True, help="index return over the same window")
    wy.add_argument("--sector", type=float, default=0.0)
    wy.add_argument("--fx", type=float, default=0.0, help="base-currency leg")
    wy.add_argument("--currency", default="MYR")
    wy.add_argument("--days", type=int, default=1, help="window length in calendar days")
    wy.add_argument("--on", help="window end date (YYYY-MM-DD), default today")
    wy.add_argument("--history", help="CSV of instrument,market,sector returns")
    wy.add_argument("--beta-market", type=float, default=1.1, help="used only without --history")
    wy.add_argument("--beta-sector", type=float, default=0.5, help="used only without --history")
    wy.set_defaults(fn=cmd_why)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
