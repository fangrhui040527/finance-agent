"""The audit's result model: a check, its PERFUMES attribute, and its weight.

The blueprint's checklist produces a readiness percentage. A percentage is only
honest if it distinguishes between a check that must pass before anyone uses
this and one that records a real but survivable weakness - otherwise the number
is arithmetic over incomparable things, and the first amber item trains people
to ignore the colour.

So each check declares whether it BLOCKS. The runner exits non-zero on a failed
blocking check and reports the rest as findings, with the score computed over
both and the two counts printed separately.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

#: The eight PERFUMES attributes, in the blueprint's order.
ATTRS = (
    "Portability",
    "Efficiency",
    "Reliability",
    "Functionality",
    "Usability",
    "Maintainability",
    "Extensibility",
    "Security",
)

PASS, FAIL, SKIP, NA = "pass", "fail", "skip", "n/a"


@dataclass
class Check:
    id: str
    attr: str
    phase: int
    title: str
    #: What the blueprint asks for, in this system's terms.
    asks: str
    blocking: bool = True
    status: str = SKIP
    detail: str = ""
    evidence: str = ""

    def ok(self) -> Check:
        self.status = PASS
        return self

    def failed(self, detail: str) -> Check:
        self.status = FAIL
        self.detail = detail
        return self

    def skipped(self, why: str) -> Check:
        self.status = SKIP
        self.detail = why
        return self

    def not_applicable(self, why: str) -> Check:
        """A check that cannot apply to this architecture, with the reason.

        Marking it n/a is not the same as passing it, and the report says so:
        an architecture that grows an SSE endpoint later must revisit these.
        """
        self.status = NA
        self.detail = why
        return self


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    budget: dict = field(default_factory=dict)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    # --- counting ------------------------------------------------------------

    @property
    def scored(self) -> list[Check]:
        """n/a and skipped checks are excluded from the denominator.

        Counting a skipped live test as a failure makes the offline run look
        broken; counting it as a pass makes an unrun test look verified. It is
        neither, so it is not scored - and the header says how many there were.
        """
        return [c for c in self.checks if c.status in (PASS, FAIL)]

    @property
    def passed(self) -> list[Check]:
        return [c for c in self.checks if c.status == PASS]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.failures if c.blocking]

    @property
    def readiness(self) -> float:
        scored = self.scored
        return 100.0 * len(self.passed) / len(scored) if scored else 0.0

    def by_attr(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for attr in ATTRS:
            rows = [c for c in self.scored if c.attr == attr]
            out[attr] = (len([c for c in rows if c.status == PASS]), len(rows))
        return out

    # --- output --------------------------------------------------------------

    def render(self) -> str:
        w = 78
        out = [
            "=" * w,
            "  FINANCE-AGENT READINESS AUDIT",
            f"  dual-phase - PERFUMES - OWASP LLM Top 10 - G-Eval     {self.started}",
            "=" * w,
            "",
        ]
        for phase in (1, 2):
            rows = [c for c in self.checks if c.phase == phase]
            if not rows:
                continue
            label = "KEYLESS - structural integrity" if phase == 1 else "LIVE - Haiku, capped"
            out += [f"  PHASE {phase}: {label}", ""]
            for c in rows:
                mark = {PASS: "[ ok ]", FAIL: "[FAIL]", SKIP: "[skip]", NA: "[ na ]"}[c.status]
                flag = "" if c.blocking else "  (non-blocking)"
                out.append(f"  {mark} {c.id:<6} {c.attr:<15} {c.title}{flag}")
                if c.detail:
                    for line in _wrap(c.detail, w - 18):
                        out.append(f"         {line}")
                if c.evidence and c.status == PASS:
                    out.append(f"         {c.evidence}")
            out.append("")

        out += ["-" * w, "  PERFUMES", ""]
        for attr, (ok, total) in self.by_attr().items():
            if not total:
                out.append(f"    {attr:<16} - no scored check")
                continue
            bar = "#" * int(round(12 * ok / total)) + "." * (12 - int(round(12 * ok / total)))
            out.append(f"    {attr:<16} {bar}  {ok}/{total}")

        skipped = [c for c in self.checks if c.status == SKIP]
        na = [c for c in self.checks if c.status == NA]
        out += [
            "",
            "-" * w,
            f"  READINESS {self.readiness:5.1f}%   "
            f"{len(self.passed)}/{len(self.scored)} scored checks passed",
            f"  {len(self.blocking_failures)} blocking failure(s), "
            f"{len(self.failures) - len(self.blocking_failures)} finding(s), "
            f"{len(skipped)} skipped, {len(na)} not applicable",
        ]
        if self.budget:
            out.append(
                f"  live spend USD {self.budget.get('spent_usd', '0')} of "
                f"{self.budget.get('ceiling_usd', '0')} ceiling on "
                f"{', '.join(self.budget.get('models') or ['-'])}"
            )
        if self.blocking_failures:
            out += ["", "  BLOCKING:"]
            out += [f"    {c.id} {c.title}" for c in self.blocking_failures]
        findings = [c for c in self.failures if not c.blocking]
        if findings:
            out += ["", "  FINDINGS (real, not launch-blocking):"]
            out += [f"    {c.id} {c.title}" for c in findings]
        out += ["=" * w]
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps(
            {
                "started": self.started,
                "readiness_pct": round(self.readiness, 1),
                "scored": len(self.scored),
                "passed": len(self.passed),
                "blocking_failures": [c.id for c in self.blocking_failures],
                "findings": [c.id for c in self.failures if not c.blocking],
                "perfumes": {k: {"passed": v[0], "of": v[1]} for k, v in self.by_attr().items()},
                "budget": self.budget,
                "checks": [asdict(c) for c in self.checks],
            },
            indent=2,
        )


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        if len(cur) + len(word) + 1 > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines
