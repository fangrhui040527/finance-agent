"""Adversarial stress harness. Its job is to break things, not to pass.

The 473-test suite checks that the system does what it is supposed to. This
checks what it does when it is abused: volume it was not sized for, numbers that
are not numbers, inputs sitting exactly on a threshold, several writers at once,
and text that is trying to talk to the model rather than inform it.

A finding here is not automatically a bug. Three outcomes:

  HELD      it refused, degraded, or absorbed the abuse correctly
  FINDING   real defect - wrong answer, crash where a refusal belongs, or a
            limit that did not bind
  NOTE      works, but the behaviour is worth writing down

The exit code is the FINDING count, so CI can gate on it.
"""

from __future__ import annotations

import math
import random
import sys
import time
import traceback
from dataclasses import dataclass, field


@dataclass
class Result:
    held: int = 0
    findings: list[tuple[str, str]] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)
    timings: list[tuple[str, float]] = field(default_factory=list)


R = Result()
_section = ""


def section(name: str) -> None:
    global _section
    _section = name
    print(f"\n{name}")
    print("-" * len(name))


def held(label: str, detail: str = "") -> None:
    R.held += 1
    print(f"  HELD     {label}" + (f"  -- {detail}" if detail else ""))


def finding(label: str, detail: str) -> None:
    R.findings.append((f"{_section}: {label}", detail))
    print(f"  FINDING  {label}\n           {detail}")


def note(label: str, detail: str) -> None:
    R.notes.append((f"{_section}: {label}", detail))
    print(f"  NOTE     {label}  -- {detail}")


def timed(label: str, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    R.timings.append((f"{_section}: {label}", dt))
    return out, dt


def expect_raises(label: str, exc, fn, *, why: str) -> None:
    """The input SHOULD be refused. Silent acceptance is the finding."""
    try:
        result = fn()
    except exc:
        held(label)
        return
    except Exception as e:                       # wrong exception type is a note
        note(label, f"refused, but with {type(e).__name__} not {exc.__name__}")
        return
    finding(label, f"{why} Accepted silently, returned {result!r:.120}")


def expect_no_crash(label: str, fn, *, why: str = ""):
    try:
        return fn()
    except Exception as e:
        finding(label, f"crashed with {type(e).__name__}: {e}. {why}".strip())
        return None


def report() -> int:
    print("\n" + "=" * 68)
    print(f"HELD {R.held}   FINDINGS {len(R.findings)}   NOTES {len(R.notes)}")
    if R.timings:
        slowest = sorted(R.timings, key=lambda kv: -kv[1])[:5]
        print("\nslowest operations")
        for label, dt in slowest:
            print(f"  {dt * 1000:>8.1f} ms  {label}")
    if R.findings:
        print("\nFINDINGS")
        for label, detail in R.findings:
            print(f"  - {label}\n      {detail}")
    if R.notes:
        print("\nNOTES")
        for label, detail in R.notes:
            print(f"  - {label}: {detail}")
    print("=" * 68)
    return len(R.findings)
