"""The readiness audit, as one runnable command.

    python audit/run.py                  # keyless, offline, free
    EVAL_LIVE=1 python audit/run.py      # + the live phase, Haiku, capped
    python audit/run.py --json out.json  # the audit export

Exit code is 0 unless a BLOCKING check failed. A non-blocking failure is a
finding: real, reported, and deliberately not a red build - a suite that goes
red for things nobody intends to fix this week is a suite people stop reading.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The audit must never be the thing that spends money by accident, and must not
# read a developer's .env into the keyless phase.
os.environ.setdefault("FINPLANET_NO_DOTENV", "1")
os.environ.setdefault("FINPLANET_CHEAP", "1")

from audit._support import budget  # noqa: E402
from audit._support.scorecard import Check, Report  # noqa: E402
from audit.phase1 import (  # noqa: E402
    agency,
    contract,
    portability,
    rendering,
    schema,
    transport,
    unicode,
)
from audit.phase2 import caching, geval, injection, thinking  # noqa: E402

PHASE1 = (unicode, schema, transport, contract, agency, rendering, portability)
PHASE2 = (caching, thinking, geval, injection)


def run(live: bool) -> Report:
    report = Report()
    for module in PHASE1 + PHASE2:
        for fn in module.CHECKS:
            started = time.time()
            try:
                check = fn()
            except Exception as e:  # noqa: BLE001
                # A check that crashes is a failed check, not a failed run: the
                # remaining checks still have something to say.
                check = Check(
                    getattr(fn, "__name__", "?")[:6],
                    "Reliability",
                    1,
                    fn.__name__,
                    "",
                ).failed(f"the check itself raised {type(e).__name__}: {e}")
            elapsed = time.time() - started
            if elapsed > 5 and check.evidence:
                check.evidence += f"  ({elapsed:.1f}s)"
            report.add(check)
    report.budget = budget.METER.summary()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="finance-agent readiness audit")
    ap.add_argument("--json", metavar="PATH", help="write the audit export here")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="treat findings as blocking too",
    )
    args = ap.parse_args(argv)

    live = budget.live_enabled()
    if not live:
        why = (
            "EVAL_LIVE is not 1"
            if os.environ.get("EVAL_LIVE") != "1"
            else "ANTHROPIC_API_KEY is not set"
        )
        print(f"  phase 2 will skip: {why}\n", file=sys.stderr)
    else:
        print(
            f"  phase 2 live on {budget.CHEAP_MODEL}, ceiling USD {budget.max_usd()}\n",
            file=sys.stderr,
        )

    report = run(live)
    print(report.render())

    if args.json:
        Path(args.json).write_text(report.to_json(), encoding="utf-8")
        print(f"\n  audit exported to {args.json}")

    blocking = report.blocking_failures
    if args.strict:
        blocking = report.failures
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
