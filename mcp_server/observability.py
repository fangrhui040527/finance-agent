"""The tools that let a model watch this system run, and spot what broke.

Everything here reads what the system already records - the provenance ledger,
the trace bundles under `debug/`, the preflight checks - and reports it as
text. Nothing here computes an analytical number, and nothing here can change
state: these are read-only windows onto the machine's own behaviour.

One boundary is deliberate and load-bearing. Traces hold VERBATIM prompts and
responses, and whatever portfolio positions were passed in. These tools report
WHAT failed, WHERE, and HOW OFTEN - error text, event names, run ids, counts,
timings. They never return a prompt blob. An operator who wants the exact text
opens `debug/<run_id>/` themselves, which is a decision a person makes rather
than one a tool makes for them.

The other property worth stating: **absent evidence is reported as absent**.
An empty ledger is "nothing has run yet", never a clean bill of health, and a
window with no errors says how much it looked at. A monitor that cannot tell
silence from success is worse than no monitor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

DEBUG_ROOT = "debug"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _ledger(db: str = ""):
    from core.config import load as load_config
    from core.provenance.ledger import ProvenanceLedger

    return ProvenanceLedger(db or load_config().provenance_db)


def _runs(root: str = DEBUG_ROOT) -> list[Path]:
    p = Path(root)
    if not p.is_dir():
        return []
    return sorted((d for d in p.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)


def _summary(run: Path) -> dict:
    f = run / "summary.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_unreadable": True}


def _events(run: Path, limit: int = 20_000) -> list[dict]:
    f = run / "trace.jsonl"
    if not f.is_file():
        return []
    out: list[dict] = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[:limit]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


# --------------------------------------------------------------------------
# 1. is this installation able to work at all
# --------------------------------------------------------------------------


def system_health(offline: bool = True) -> str:
    """Preflight: what this installation can actually do, and what each gap costs.

    `offline=true` (the default) skips the two network probes, so the answer is
    instant and free. Pass false to also test whether the price and news
    sources are reachable from this machine.
    """
    from core.doctor import FAIL, WARN, render, run_checks

    results = run_checks(offline=offline)
    body = render(results)
    blocking = [r for r in results if r.status == FAIL and r.critical]
    degraded = [r for r in results if r.status == WARN]
    verdict = "\n\n"
    if blocking:
        verdict += "BLOCKED: " + "; ".join(f"{r.name} ({r.impact})" for r in blocking)
    elif degraded:
        verdict += "USABLE, DEGRADED: " + "; ".join(
            f"{r.name} affects {r.impact}" for r in degraded
        )
    else:
        verdict += "Every check passed."
    return body + verdict


# --------------------------------------------------------------------------
# 2. what has it been doing, and what did it cost
# --------------------------------------------------------------------------


def operating_report(days: int = 7, db: str = "") -> str:
    """Cost, latency, model mix and citation health over a window.

    The performance view: how many model calls, at what price, on which
    models, how slow, how close to the budget, and how many claims were
    DROPPED for want of a citation - the last being the quality signal that
    matters most, because a dropped claim is the system refusing to say
    something it could not support.
    """
    if days < 1:
        from mcp_server.protocol import ToolError

        raise ToolError(f"days must be at least 1, got {days}")

    from core.config import load as load_config

    cfg = load_config()
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    with _ledger(db) as led:
        calls = led.calls_between(since, now)
        spend = led.cost_since(since)
        by_tier = led.cost_by_tier_myr()
        by_agent = led.cost_by_agent_myr(since=since)
        latencies = led.latencies_between(since, now)
        claims = led.claims_between(since, now)

    if not calls:
        return (
            f"No model calls in the last {days} day(s).\n"
            f"  The ledger holds {len(list(_ledger(db).calls(limit=1)))} row(s) in total.\n"
            f"  Nothing has run - which is a fact about usage, not a clean bill of health."
        )

    models: dict[str, int] = {}
    tiers: dict[str, int] = {}
    tokens_in = tokens_out = cache_read = cache_write = 0
    for row in calls:
        models[row["model_id"]] = models.get(row["model_id"], 0) + 1
        tiers[row["tier"]] = tiers.get(row["tier"], 0) + 1
        tokens_in += int(row["input_tokens"] or 0)
        tokens_out += int(row["output_tokens"] or 0)
        cache_read += int(row["cached_tokens"] or 0)
        cache_write += int(row["cache_write_tokens"] or 0)

    dropped = [c for c in claims if not c["survived"]]
    budget = Decimal(str(cfg.daily_budget_myr))
    day_spend = Decimal(0)
    with _ledger(db) as led:
        day_spend = led.cost_since(now - timedelta(days=1))

    lines = [
        f"OPERATING REPORT  last {days} day(s)",
        "",
        f"  model calls        {len(calls)}",
        f"  spend              RM {spend:.4f}",
        f"  last 24h           RM {day_spend:.4f} of RM {budget:.2f} budget "
        f"({(day_spend / budget * 100) if budget else 0:.1f}%)",
        f"  tokens             {tokens_in:,} in / {tokens_out:,} out",
        f"  cache              {cache_read:,} read, {cache_write:,} written",
    ]
    if latencies:
        lines += [
            f"  latency            p50 {_percentile(latencies, 0.5):.0f} ms, "
            f"p95 {_percentile(latencies, 0.95):.0f} ms, max {max(latencies):.0f} ms",
        ]
    lines += ["", "  models actually called"]
    for model, n in sorted(models.items(), key=lambda x: -x[1]):
        lines.append(f"    {model:<28} {n:>4} call(s)")
    lines += ["", "  tier mix"]
    for tier, n in sorted(tiers.items(), key=lambda x: -x[1]):
        lines.append(
            f"    {tier:<10} {n:>4}   lifetime cost RM {by_tier.get(tier, Decimal(0)):.4f}"
        )
    if by_agent:
        lines += ["", "  spend by agent (this window)"]
        for agent, cost in sorted(by_agent.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"    {agent:<24} RM {cost:.4f}")

    lines += ["", "  citation health"]
    if claims:
        rate = len(dropped) / len(claims)
        lines.append(
            f"    {len(claims)} claim(s) checked, {len(dropped)} dropped ({rate:.0%}) "
            f"for want of a verifiable citation"
        )
        for c in dropped[:5]:
            lines.append(f"      - {c['dropped_reason'] or 'no reason recorded'}")
    else:
        lines.append("    no claims recorded in this window (the emit path was not exercised)")

    from core.llm.tiers import cheap_capped

    if cheap_capped():
        lines += ["", "  FINPLANET_CHEAP=1 is in force: every tier resolves to the cheapest model."]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 3. what broke
# --------------------------------------------------------------------------


def recent_failures(runs: int = 10, db: str = "", root: str = DEBUG_ROOT) -> str:
    """Errors, guardrail denials and dropped claims across recent traced runs.

    Reports WHAT failed and WHERE - error text, the event that raised it, and
    the run id to open. Never returns prompt text: traces hold verbatim
    prompts and positions, and handing those back is a decision for the
    operator, not for a tool.
    """
    if runs < 1:
        from mcp_server.protocol import ToolError

        raise ToolError(f"runs must be at least 1, got {runs}")

    found = _runs(root)[:runs]
    if not found:
        return (
            f"No traced runs under {root}/. Run `python trace_run.py` to produce one.\n"
            "  Nothing to inspect is not the same as nothing wrong."
        )

    lines = [f"FAILURES across the {len(found)} most recent traced run(s)", ""]
    total_errors = total_denials = 0
    for run in found:
        summary = _summary(run)
        events = _events(run)
        errors = [e for e in events if e.get("kind") == "error" or e.get("error")]
        denials = [e for e in events if e.get("kind") == "denied"]
        refusals = [e for e in events if e.get("kind") == "refusal"]
        total_errors += len(errors)
        total_denials += len(denials)

        head = (
            f"  {run.name}   {summary.get('events', len(events))} events, "
            f"{summary.get('wall_ms', 0):.0f} ms"
        )
        lines.append(head)
        if errors:
            for e in errors[:5]:
                lines.append(f"      ERROR  {e.get('name', '?')}: {str(e.get('error'))[:160]}")
        if denials:
            names = sorted({str(e.get("name", "?")) for e in denials})
            lines.append(f"      denied {len(denials)}: {', '.join(names[:6])}")
        if refusals:
            lines.append(f"      refusals {len(refusals)} (an answer, not a fault)")
        if not errors and not denials:
            lines.append("      clean")

    with _ledger(db) as led:
        since = datetime.now(UTC) - timedelta(days=30)
        dropped = [c for c in led.claims_between(since, datetime.now(UTC)) if not c["survived"]]

    lines += ["", f"  totals: {total_errors} error(s), {total_denials} guardrail denial(s)"]
    lines.append(
        f"  dropped claims in the last 30 days: {len(dropped)}"
        + (f" - most recent: {dropped[-1]['dropped_reason']}" if dropped else "")
    )
    if total_errors == 0:
        lines.append(
            "  No errors in what was inspected. That covers these runs only - "
            "an untraced run cannot be reported on."
        )
    lines.append(
        "\n  Verbatim prompts and responses are NOT returned here. "
        "Open debug/<run_id>/ to read them."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 4. one run, in detail - and did the method change?
# --------------------------------------------------------------------------


def run_anatomy(run_id: str = "", root: str = DEBUG_ROOT) -> str:
    """One traced run: where the time went, what was denied, and whether the
    METHODOLOGY changed since the run before it.

    That last part is the question a slow or surprising run actually raises:
    did the system change, or did the world? The manifest hashes the system
    prompts, the registry, the tool surface and package versions - never the
    run id or the time - so an equal hash means an equal method.
    """
    found = _runs(root)
    if not found:
        return f"No traced runs under {root}/. Run `python trace_run.py` to produce one."

    if run_id:
        safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
        match = [r for r in found if r.name == safe]
        if not match:
            from mcp_server.protocol import ToolError

            raise ToolError(f"no run {run_id!r}. Recent: {', '.join(r.name for r in found[:5])}")
        run = match[0]
    else:
        run = found[0]

    summary = _summary(run)
    events = _events(run)
    lines = [
        f"RUN {run.name}   {summary.get('label', '?')}",
        f"  started   {summary.get('started_at', '?')}",
        f"  wall      {summary.get('wall_ms', 0):.0f} ms over {summary.get('events', len(events))} events",
    ]
    llm = summary.get("llm") or {}
    if llm:
        lines.append(
            f"  model     {llm.get('calls', 0)} call(s), RM {llm.get('cost_myr', 0)}, "
            f"{llm.get('input_tokens', 0)} in / {llm.get('output_tokens', 0)} out"
        )
    by_kind = summary.get("by_kind") or {}
    if by_kind:
        lines.append("  events    " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())))
    if summary.get("agents_seen"):
        lines.append(
            f"  agents    {len(summary['agents_seen'])}: {', '.join(summary['agents_seen'])}"
        )

    slowest = summary.get("slowest") or []
    if slowest:
        lines += ["", "  slowest"]
        for s in slowest[:5]:
            lines.append(
                f"    {s.get('ms', 0):>8.1f} ms  {s.get('kind', '?')}  {s.get('name', '?')}"
            )

    errors = [e for e in events if e.get("kind") == "error" or e.get("error")]
    if errors:
        lines += ["", "  errors"]
        for e in errors[:8]:
            lines.append(f"    {e.get('name', '?')}: {str(e.get('error'))[:160]}")
    denied = [e for e in events if e.get("kind") == "denied"]
    if denied:
        lines += ["", "  guardrail denials (the chain working)"]
        for e in denied[:8]:
            meta = e.get("data") or {}
            lines.append(f"    {e.get('name', '?')}  {str(meta.get('reason', ''))[:100]}")

    # methodology diff against the previous run
    lines += ["", "  methodology"]
    mine = _manifest(run)
    if not mine:
        lines.append("    no manifest.json in this run (written from trace_run.py onward)")
    else:
        lines.append(f"    hash {mine.get('manifest_hash', '?')}")
        older = [r for r in found if r.name < run.name]
        prev = next((r for r in older if _manifest(r)), None)
        if prev is None:
            lines.append("    no earlier run carries one, so nothing to compare against")
        else:
            theirs = _manifest(prev)
            if theirs.get("manifest_hash") == mine.get("manifest_hash"):
                lines.append(
                    f"    unchanged since {prev.name} - a difference in this run's "
                    f"output came from the DATA, not the method"
                )
            else:
                lines.append(f"    CHANGED since {prev.name}:")
                for item in _diff_manifests(theirs, mine):
                    lines.append(f"      - {item}")
    return "\n".join(lines)


def _manifest(run: Path) -> dict:
    f = run / "manifest.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _diff_manifests(old: dict, new: dict) -> list[str]:
    from core.provenance.manifest import RunManifest

    def build(d: dict) -> RunManifest:
        return RunManifest(
            system_prompt_hashes=d.get("system_prompt_hashes", {}),
            registry_hash=d.get("registry_hash", ""),
            tools_hash=d.get("tools_hash", ""),
            package_versions=d.get("package_versions", {}),
        )

    # old.diff(new), not the reverse: RunManifest.diff renders "self -> other",
    # so calling it the other way round reports every upgrade as a downgrade.
    return build(old).diff(build(new)) or ["hash differs but no field-level change was identified"]


# --------------------------------------------------------------------------
# 5. what tripped while nobody was asking
# --------------------------------------------------------------------------


def open_alerts(history: int = 10, alerts_db: str = "data/alerts.db") -> str:
    """Monitor rules currently tripped, and when things opened and cleared.

    The other four tools answer when asked. This one reports what a scheduled
    `ask.py watch` found while nobody was looking - and the history, because
    "this has been open for three days" is a different fact from "this just
    started".
    """
    from core.monitor import AlertLog

    with AlertLog(alerts_db) as log:
        open_now = log.open_rules()
        rows = log.history(limit=max(1, history))

    if not open_now and not rows:
        return (
            "No alert history. Nothing has run `ask.py watch` against this store yet - "
            "which means no rule has been evaluated, not that no rule would fire."
        )

    lines = []
    if open_now:
        lines.append(f"{len(open_now)} OPEN")
        for rule, r in sorted(open_now.items()):
            lines.append(f"  [{r['severity']}] {rule}: {r['title']}")
            lines.append(f"      open since {r['at'][:19]}")
            if r["detail"]:
                lines.append(f"      {r['detail']}")
    else:
        lines.append("Nothing open right now.")
    if rows:
        lines += ["", "history (newest first)"]
        for r in rows:
            lines.append(f"  {r['at'][:19]}  {r['state']:<8} {r['rule']:<22} {r['title'][:60]}")
    return "\n".join(lines)
