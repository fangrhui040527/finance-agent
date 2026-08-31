"""Thresholds that fire on their own, and an append-only history of when.

`mcp_server/observability.py` answers questions when asked. This answers the
question nobody asked because nobody was watching: it evaluates a small set
of rules against the ledger and the traces, and records every state CHANGE so
a scheduled job can run it every hour and tell you only when something moved.

Design decisions worth stating, because each is a way this could have been
useless:

  * **State changes, not repetitions.** An alert that fires every hour for a
    week is noise a person learns to ignore, which is worse than silence. The
    log records `opened` and `resolved`; a rule that is still tripped writes
    nothing new.
  * **Append-only, like every other record here.** You cannot rewrite when an
    alert opened. Triggers, not convention.
  * **Silence is a rule.** "Nothing has run for N hours" is a condition worth
    alerting on: a daemon that dies quietly looks exactly like a quiet week,
    and the whole point of this module is telling those apart. Off by default
    because a personal tool is allowed to sit idle.
  * **Every alert says what to look at next.** A threshold with no next step
    is a number that makes you anxious rather than informed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import _enable_wal

WARN = "warn"
ALERT = "alert"

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_events (
    rule      TEXT NOT NULL,
    at        TEXT NOT NULL,
    state     TEXT NOT NULL,          -- opened | resolved
    severity  TEXT NOT NULL,
    title     TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS alert_events_rule_at ON alert_events(rule, at);

CREATE TRIGGER IF NOT EXISTS alert_events_no_update
BEFORE UPDATE ON alert_events
BEGIN SELECT RAISE(ABORT, 'when an alert opened is not editable'); END;
CREATE TRIGGER IF NOT EXISTS alert_events_no_delete
BEFORE DELETE ON alert_events
BEGIN SELECT RAISE(ABORT, 'alert history is append-only: a log you can prune proves nothing'); END;
"""


@dataclass(frozen=True)
class Alert:
    """One tripped rule, with the number that tripped it and the next step."""

    rule: str
    severity: str
    title: str
    detail: str
    next_step: str
    evidence: dict = field(default_factory=dict)

    def render(self) -> str:
        mark = "ALERT" if self.severity == ALERT else " warn"
        return f"[{mark}] {self.title}\n         {self.detail}\n         next: {self.next_step}"


class AlertLog:
    """Append-only history. Current state of a rule is its latest event."""

    def __init__(self, path: str | Path = "data/alerts.db") -> None:
        p = Path(path)
        if p.parent != Path("."):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p))
        self.db.row_factory = sqlite3.Row
        _enable_wal(self.db, str(p), timeout_ms=5000)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def open_rules(self) -> dict[str, sqlite3.Row]:
        """rule -> its latest event, for rules whose latest event is `opened`."""
        rows = self.db.execute("SELECT * FROM alert_events ORDER BY at, rowid").fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for r in rows:
            latest[r["rule"]] = r
        return {k: v for k, v in latest.items() if v["state"] == "opened"}

    def record(self, alert: Alert, state: str, at: datetime | None = None) -> None:
        at = at or datetime.now(UTC)
        self.db.execute(
            "INSERT INTO alert_events (rule, at, state, severity, title, detail, evidence_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                alert.rule,
                at.isoformat(),
                state,
                alert.severity,
                alert.title,
                alert.detail,
                json.dumps(alert.evidence, default=str, sort_keys=True),
            ),
        )
        self.db.commit()

    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM alert_events ORDER BY at DESC, rowid DESC LIMIT ?", (int(limit),)
        ).fetchall()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> AlertLog:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------


def evaluate(
    cfg, db: str = "", debug_root: str = "debug", now: datetime | None = None
) -> list[Alert]:
    """Every rule, against the ledger and the traces. Pure: writes nothing."""
    from core.provenance.ledger import ProvenanceLedger

    now = now or datetime.now(UTC)
    out: list[Alert] = []
    ledger_path = db or cfg.provenance_db

    with ProvenanceLedger(ledger_path) as led:
        day_ago = now - timedelta(days=1)
        spend = led.cost_since(day_ago)
        budget = Decimal(str(cfg.daily_budget_myr))
        fraction = Decimal(str(cfg.alert_spend_fraction))
        if budget > 0 and spend >= budget * fraction:
            out.append(
                Alert(
                    rule="spend_24h",
                    severity=ALERT if spend >= budget else WARN,
                    title=f"24h spend RM {spend:.4f} is {spend / budget:.0%} of the RM {budget:.2f} budget",
                    detail=f"the alert threshold is {fraction:.0%}",
                    next_step="operating_report to see which agent and model spent it; "
                    "FINPLANET_CHEAP=1 caps every tier to the cheapest model",
                    evidence={"spend_myr": str(spend), "budget_myr": str(budget)},
                )
            )

        latencies = led.latencies_between(day_ago, now)
        if latencies:
            ordered = sorted(latencies)
            p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
            limit = float(cfg.alert_p95_latency_ms)
            if limit > 0 and p95 >= limit:
                out.append(
                    Alert(
                        rule="latency_p95",
                        severity=WARN,
                        title=f"p95 latency {p95:.0f} ms over the last 24h, threshold {limit:.0f} ms",
                        detail=f"{len(latencies)} call(s) measured",
                        next_step="run_anatomy on the newest run to see which span is slow",
                        evidence={"p95_ms": p95, "calls": len(latencies)},
                    )
                )

        claims = led.claims_between(day_ago, now)
        if claims:
            dropped = [c for c in claims if not c["survived"]]
            rate = Decimal(len(dropped)) / Decimal(len(claims))
            limit_rate = Decimal(str(cfg.alert_dropped_claim_rate))
            if limit_rate > 0 and rate >= limit_rate:
                out.append(
                    Alert(
                        rule="dropped_claims",
                        severity=WARN,
                        title=f"{rate:.0%} of claims dropped for want of a citation "
                        f"({len(dropped)} of {len(claims)})",
                        detail="a dropped claim is the system declining to say what it "
                        "could not support - a rising rate means the evidence is thinning",
                        next_step="operating_report names the most recent drop reasons",
                        evidence={"dropped": len(dropped), "checked": len(claims)},
                    )
                )

        silence_hours = int(cfg.alert_silence_hours)
        if silence_hours > 0:
            recent = led.calls_between(now - timedelta(hours=silence_hours), now)
            ever = list(led.calls(limit=1))
            if ever and not recent:
                out.append(
                    Alert(
                        rule="silence",
                        severity=ALERT,
                        title=f"no model calls in {silence_hours}h, but this ledger has run before",
                        detail="a job that dies quietly looks exactly like a quiet week",
                        next_step="system_health, then check whatever schedules the run",
                        evidence={"silence_hours": silence_hours},
                    )
                )

    out.extend(_trace_rules(debug_root))
    return out


def _trace_rules(debug_root: str) -> list[Alert]:
    """Errors in the newest run, and whether the methodology moved under us."""
    from mcp_server.observability import _events, _manifest, _runs

    runs = _runs(debug_root)
    if not runs:
        return []
    out: list[Alert] = []
    newest = runs[0]

    errors = [e for e in _events(newest) if e.get("kind") == "error" or e.get("error")]
    if errors:
        first = str(errors[0].get("error", ""))[:120]
        out.append(
            Alert(
                rule="run_errors",
                severity=ALERT,
                title=f"{len(errors)} error(s) in the newest traced run {newest.name}",
                detail=f"first: {first}",
                next_step=f"run_anatomy('{newest.name}'), then debug/{newest.name}/ for the text",
                evidence={"run_id": newest.name, "errors": len(errors)},
            )
        )

    mine = _manifest(newest)
    if mine:
        prev = next((r for r in runs[1:] if _manifest(r)), None)
        if prev is not None:
            theirs = _manifest(prev)
            if theirs.get("manifest_hash") != mine.get("manifest_hash"):
                from mcp_server.observability import _diff_manifests

                changes = _diff_manifests(theirs, mine)
                out.append(
                    Alert(
                        rule="methodology_changed",
                        severity=WARN,
                        title=f"the method changed between {prev.name} and {newest.name}",
                        detail="; ".join(changes[:4]),
                        next_step="expected after a deploy; unexpected means output moved "
                        "for a reason that is not the data",
                        evidence={"from": prev.name, "to": newest.name, "changes": changes},
                    )
                )
    return out


# --------------------------------------------------------------------------
# the check a scheduler runs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    opened: list[Alert]
    still_open: list[Alert]
    resolved: list[str]
    checked_at: datetime

    @property
    def any_open(self) -> bool:
        return bool(self.opened or self.still_open)

    def render(self) -> str:
        stamp = self.checked_at.strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"MONITOR  {stamp}"]
        if self.opened:
            lines.append("")
            lines.append(f"  {len(self.opened)} NEW:")
            lines += ["  " + a.render() for a in self.opened]
        if self.still_open:
            lines.append("")
            lines.append(f"  {len(self.still_open)} still open:")
            lines += [f"    {a.title}" for a in self.still_open]
        if self.resolved:
            lines.append("")
            lines.append(f"  {len(self.resolved)} resolved: {', '.join(self.resolved)}")
        if not self.any_open and not self.resolved:
            lines.append("  nothing tripped. This covers the rules that exist, over their")
            lines.append("  own windows - it is not a statement that everything is well.")
        return "\n".join(lines)


def check(
    cfg=None,
    db: str = "",
    alerts_db: str = "data/alerts.db",
    debug_root: str = "debug",
    now: datetime | None = None,
) -> CheckResult:
    """Evaluate, diff against the last known state, and append only the changes."""
    from core.config import load as load_config

    cfg = cfg or load_config()
    now = now or datetime.now(UTC)
    firing = {a.rule: a for a in evaluate(cfg, db=db, debug_root=debug_root, now=now)}

    opened: list[Alert] = []
    still: list[Alert] = []
    resolved: list[str] = []
    with AlertLog(alerts_db) as log:
        already = log.open_rules()
        for rule, alert in firing.items():
            if rule in already:
                still.append(alert)
            else:
                log.record(alert, "opened", at=now)
                opened.append(alert)
        for rule, row in already.items():
            if rule not in firing:
                log.record(
                    Alert(
                        rule=rule,
                        severity=row["severity"],
                        title=row["title"],
                        detail="",
                        next_step="",
                    ),
                    "resolved",
                    at=now,
                )
                resolved.append(rule)
    return CheckResult(opened=opened, still_open=still, resolved=resolved, checked_at=now)
