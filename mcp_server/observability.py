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

#: The verdicts the attribution engine can reach. Anything else in a trace's
#: `verdict` field is prose, and grouping by sentence reports noise as signal.
_VERDICTS = frozenset(
    {"not_significant", "market_driven", "no_identified_catalyst", "attribution_unavailable"}
)


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

    from core.llm.tiers import selection_note

    note = selection_note()
    if note:
        lines += ["", f"  selection in force: {note}"]
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


# --------------------------------------------------------------------------
# 6. is the ANALYSIS any good
# --------------------------------------------------------------------------


def quality_report(days: int = 30, db: str = "", root: str = DEBUG_ROOT) -> str:
    """Analytical quality: calibration, verdicts, claim survival, red team, evals.

    Not "did it run" - "was it right, and did it know how sure it was". Every
    number here refuses to exist without its sample size, because a hit rate
    from six calls is a number about six calls.
    """
    from core.config import load as load_config

    cfg = load_config()
    now = datetime.now(UTC)
    since = now - timedelta(days=max(1, days))
    lines = [f"QUALITY  last {days} day(s)", ""]

    # -- calibration: the only honest score, and it cannot be back-filled
    from agents.learning.store import DEFAULT_PATH, LearningStore

    try:
        with LearningStore(DEFAULT_PATH) as store:
            pairs = store.calibration_pairs()
            counts = store.counts()
    except Exception as e:  # a missing store is a fact, not a crash
        pairs, counts = [], {"error": str(e)}

    lines.append("  forecast calibration")
    need = int(cfg.min_graded_for_calibration)
    if len(pairs) < need:
        lines.append(f"    CANNOT SCORE: {len(pairs)} graded of the {need} this system requires.")
        lines.append("    Below that a calibration table measures luck. It is a clock, not a task.")
    else:
        brier = sum((c - (1.0 if hit else 0.0)) ** 2 for c, hit in pairs) / len(pairs)
        hits = sum(1 for _, hit in pairs if hit) / len(pairs)
        stated = sum(c for c, _ in pairs) / len(pairs)
        lines.append(f"    Brier {brier:.3f} over {len(pairs)} graded (lower is better)")
        lines.append(
            f"    stated {stated:.0%} against realised {hits:.0%} - "
            + ("overconfident" if stated > hits else "underconfident" if stated < hits else "level")
        )
    if isinstance(counts, dict) and "pending" in counts:
        lines.append(f"    {counts.get('pending', 0)} pending, {counts.get('graded', 0)} graded")

    # -- claim survival: the system declining to say what it cannot support
    with _ledger(db) as led:
        claims = led.claims_between(since, now)
    lines += ["", "  citation survival"]
    if claims:
        kept = [c for c in claims if c["survived"]]
        lines.append(
            f"    {len(kept)} of {len(claims)} claims survived verification "
            f"({len(kept) / len(claims):.0%})"
        )
        reasons: dict[str, int] = {}
        for c in claims:
            if not c["survived"]:
                reasons[str(c["dropped_reason"])] = reasons.get(str(c["dropped_reason"]), 0) + 1
        for reason, n in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"      {n:>3}x  {reason}")
    else:
        lines.append("    no claims verified in this window")

    # -- what the analysis actually concluded
    verdicts: dict[str, int] = {}
    unstructured = [0]
    refusal_reasons: dict[str, int] = {}
    challenges = 0
    runs = _runs(root)
    for run in runs[:20]:
        for e in _events(run):
            data = e.get("data") or {}
            code = data.get("verdict_code")
            if code and str(code) in _VERDICTS:
                verdicts[str(code)] = verdicts.get(str(code), 0) + 1
            elif data.get("verdict"):
                # A `verdict` key holding prose, from a run written before the
                # code was emitted alongside it. Counted as unstructured rather
                # than grouped by sentence, which would report noise as signal.
                unstructured[0] += 1
            if e.get("kind") == "refusal":
                r = str(data.get("reason", e.get("name", "?")))[:60]
                refusal_reasons[r] = refusal_reasons.get(r, 0) + 1
            challenges += int(data.get("n_challenges", 0) or 0)

    lines += ["", f"  verdicts across the last {min(len(runs), 20)} traced run(s)"]
    if verdicts:
        for v, n in sorted(verdicts.items(), key=lambda x: -x[1]):
            lines.append(f"    {n:>4}  {v}")
        market = verdicts.get("market_driven", 0) + verdicts.get("not_significant", 0)
        total = sum(verdicts.values())
        if total:
            lines.append(
                f"    {market / total:.0%} of moves needed no company story - "
                "the decomposition doing its job"
            )
    else:
        lines.append("    none recorded")
    if unstructured[0]:
        lines.append(
            f"    {unstructured[0]} event(s) carried a verdict as prose rather than a "
            "code, from runs written before verdict_code existed - not counted"
        )
    if challenges:
        lines.append(f"    {challenges} red-team challenge(s) raised against theses")

    # -- the eval ratchet: what it can and cannot promise
    lines += ["", "  eval suites (the registration ratchet)"]
    lines += _eval_suite_health()
    return "\n".join(lines)


def _eval_suite_health() -> list[str]:
    """Suite presence and shape. NOT pass rates: running a suite needs a
    per-agent runner the registry does not carry, and claiming a pass rate we
    did not measure would be the exact dishonesty this file exists against."""
    from core.registry.loader import MIN_EVAL_CASES, MIN_NEGATIVE_CASES, load

    try:
        reg = load("agents/registry.yaml")
    except Exception as e:
        return [f"    registry unreadable: {e}"]

    import yaml

    thin = []
    total_cases = total_negatives = 0
    for spec in sorted(reg.agents.values(), key=lambda s: s.id):
        p = Path(spec.eval_suite)
        if not p.is_file():
            thin.append(f"{spec.id}: suite missing at {spec.eval_suite}")
            continue
        try:
            suite = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            thin.append(f"{spec.id}: unreadable ({e})")
            continue
        cases = suite.get("cases") or []
        negatives = [
            c
            for c in cases
            if c.get("negative") is True
            or c.get("expect")
            in (
                "refuse",
                "no_lesson",
                "not_significant",
                "market_driven",
                "no_position",
                "no_identified_catalyst",
                "no_view",
            )
        ]
        total_cases += len(cases)
        total_negatives += len(negatives)
        if len(cases) < MIN_EVAL_CASES or len(negatives) < MIN_NEGATIVE_CASES:
            thin.append(f"{spec.id}: {len(cases)} cases, {len(negatives)} negative")
    out = [
        f"    {len(reg.agents)} agent(s), {total_cases} case(s), {total_negatives} negative",
        f"    every agent registered with a suite of at least {MIN_EVAL_CASES} cases "
        f"and {MIN_NEGATIVE_CASES} negatives, or it does not register at all",
    ]
    out += [f"    THIN: {t}" for t in thin]
    out.append(
        "    This is suite SHAPE, not pass rate: running a suite needs a per-agent "
        "runner, and a pass rate nobody measured would be worse than none."
    )
    return out


# --------------------------------------------------------------------------
# 7. am I paying for what I am getting
# --------------------------------------------------------------------------


def _why_nothing_cached(calls) -> list[str]:
    """A zero hit rate has two causes and they need opposite fixes.

    The report used to name only one of them - "the prefix is changing between
    calls" - and say it unconditionally. On this system's own ledger that was
    wrong and expensively so: the four recorded calls carry 183, 392, 392 and
    438 input tokens, every one of them shorter than the shortest published
    minimum cacheable prefix, so nothing could have been cached whatever the
    prefix did. Someone reading that line would have gone looking for a varying
    timestamp that does not exist.

    The size test comes first because it is the one that can be settled from
    the ledger alone. Prefix instability is only worth naming once the prompts
    are long enough for caching to have been possible at all.
    """
    from core.llm.tiers import CACHE_MIN_CEILING, CACHE_MIN_FLOOR

    biggest = max((int(r["input_tokens"] or 0) for r in calls), default=0)
    if biggest < CACHE_MIN_FLOOR:
        return [
            f"NOTHING is being cached, and nothing could be: the largest single "
            f"prompt was {biggest:,} tokens, under the {CACHE_MIN_FLOOR:,}-token floor "
            f"below which no model caches at all.",
            "the cache_control marker is accepted and silently does nothing at this "
            "size - this is not a bug to hunt, it is a prompt too short to be worth "
            "caching. Longer system prompts, or nothing.",
        ]
    if biggest < CACHE_MIN_CEILING:
        return [
            f"NOTHING is being cached. The largest single prompt was {biggest:,} "
            f"tokens, inside the {CACHE_MIN_FLOOR:,}-{CACHE_MIN_CEILING:,} band where the "
            f"minimum is model-dependent.",
            "check this model's minimum cacheable length before looking for an "
            "unstable prefix - a prompt under it is not cached and says nothing.",
        ]
    return [
        f"NOTHING is being cached. At {biggest:,} tokens the prompts clear every "
        f"published minimum, so the size explanation is ruled out.",
        "the system prompt carries a cache_control block, so a zero hit rate "
        "across repeated calls means the prefix is changing between them - a "
        "timestamp or a varying tool set.",
    ]


def efficiency_report(days: int = 7, db: str = "") -> str:
    """Cost per answer, cache effectiveness, tier discipline, wasted spend."""
    now = datetime.now(UTC)
    since = now - timedelta(days=max(1, days))
    with _ledger(db) as led:
        calls = led.calls_between(since, now)
        spend = led.cost_since(since)

    if not calls:
        return (
            f"No model calls in the last {days} day(s), so there is nothing to be "
            f"efficient or wasteful about."
        )

    fresh = sum(int(r["input_tokens"] or 0) for r in calls)
    reads = sum(int(r["cached_tokens"] or 0) for r in calls)
    writes = sum(int(r["cache_write_tokens"] or 0) for r in calls)
    out_tokens = sum(int(r["output_tokens"] or 0) for r in calls)

    cols = {d[0] for d in [(c,) for c in calls[0].keys()]}
    wasted = [
        r
        for r in calls
        if "stop_reason" in cols
        and str(r["stop_reason"] or "") in ("refusal", "truncated", "declined")
    ]
    wasted_cost = sum((Decimal(str(r["cost_myr"])) for r in wasted), Decimal(0))

    lines = [
        f"EFFICIENCY  last {days} day(s)",
        "",
        f"  {len(calls)} call(s), RM {spend:.4f}",
        f"  cost per call        RM {spend / len(calls):.5f}",
    ]
    if out_tokens:
        lines.append(f"  cost per 1k output   RM {spend / Decimal(out_tokens) * 1000:.5f}")

    lines += ["", "  cache"]
    total_in = fresh + reads
    if total_in:
        lines.append(
            f"    hit rate {reads / total_in:.0%}  ({reads:,} read of {total_in:,} input tokens)"
        )
        lines.append(f"    {writes:,} written at 1.25x; reads bill at 0.1x")
        if reads == 0:
            lines += [f"    {ln}" for ln in _why_nothing_cached(calls)]
    else:
        lines.append("    no input tokens recorded")

    lines += ["", "  tier discipline"]
    by_tier: dict[str, list] = {}
    for r in calls:
        by_tier.setdefault(str(r["tier"]), []).append(r)
    for tier, rows in sorted(by_tier.items(), key=lambda x: -len(x[1])):
        cost = sum((Decimal(str(r["cost_myr"])) for r in rows), Decimal(0))
        share = cost / spend if spend else 0
        lines.append(f"    {tier:<10} {len(rows):>4} call(s)  RM {cost:.4f}  {share:.0%} of spend")
    from core.llm.tiers import selection_note

    note = selection_note()
    if note:
        lines.append(f"    selection in force: {note}")

    lines += ["", "  waste"]
    if wasted:
        lines.append(
            f"    {len(wasted)} call(s) billed RM {wasted_cost:.4f} and returned nothing usable "
            f"(refused or truncated)"
        )
        lines.append(
            "    a truncation is a max_tokens that should have been larger, and it is paid for either way"
        )
    elif "stop_reason" not in cols:
        lines.append(
            "    stop_reason is not recorded on these rows (written before that column existed)"
        )
    else:
        lines.append("    none: every billed call returned usable text")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 8. can this be changed safely
# --------------------------------------------------------------------------


def maintainability_report(db: str = "data/codegraph.db") -> str:
    """Test and doc coverage per module, from the codebase graph.

    The graph is built offline from the repository itself (`make codegraph`),
    so this is a fact about the code rather than a claim about it. A module
    with no test edge is not necessarily untested - a test that exercises it
    without importing it by name leaves no edge - which the report says rather
    than implying a number it cannot support.
    """
    import sqlite3

    p = Path(db)
    if not p.is_file():
        return (
            f"No codebase graph at {db}. Build it with `make codegraph` "
            f"(offline, no keys). Nothing to report is not the same as nothing wrong."
        )
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        modules = {
            r["node_id"]: r
            for r in conn.execute(
                "SELECT node_id, label, metadata_json FROM nodes WHERE kind = 'Product'"
            )
        }
        tested = {r["dst"] for r in conn.execute("SELECT dst FROM edges WHERE kind = 'tests'")}
        documented = {
            r["dst"] for r in conn.execute("SELECT dst FROM edges WHERE kind = 'documents'")
        }
        symbols = conn.execute("SELECT count(*) FROM nodes WHERE kind = 'Technology'").fetchone()[0]
    finally:
        conn.close()

    if not modules:
        return f"The graph at {db} holds no modules. Rebuild it with `make codegraph`."

    # A package root is an __init__.py with no code of its own; counting it as
    # an untested module inflates the gap with something there is nothing to
    # test. Separated rather than silently dropped.
    def is_package(row) -> bool:
        import json as _json

        try:
            path = str(_json.loads(row["metadata_json"] or "{}").get("path", ""))
        except Exception:
            return False
        return path.replace("\\", "/").endswith("__init__.py")

    packages = {nid for nid, row in modules.items() if is_package(row)}
    real = {nid: row for nid, row in modules.items() if nid not in packages}
    untested = sorted(m["label"] for nid, m in real.items() if nid not in tested)
    n = len(real)
    lines = [
        "MAINTAINABILITY  from the codebase graph",
        "",
        f"  {n} module(s) with code, {len(packages)} package root(s), "
        f"{symbols} top-level symbol(s)",
        f"  test edges     {len(real.keys() & tested)} of {n} ({len(real.keys() & tested) / n:.0%})",
        f"  doc edges      {len(real.keys() & documented)} of {n} "
        f"({len(real.keys() & documented) / n:.0%})",
        "",
        "  A test edge means a test file IMPORTS the module by name. A module without",
        "  one may still be covered indirectly - this counts edges, not coverage, and",
        "  says so rather than reporting a number it did not measure.",
    ]
    if untested:
        lines += ["", f"  modules with no test edge ({len(untested)})"]
        for label in untested[:15]:
            lines.append(f"    {label}")
        if len(untested) > 15:
            lines.append(f"    ... and {len(untested) - 15} more")

    # dependency surface, from the manifest the traces record
    from core.provenance.manifest import current

    try:
        versions = current().package_versions
        lines += ["", "  runtime dependencies"]
        for pkg, ver in sorted(versions.items()):
            lines.append(f"    {pkg:<12} {ver}")
    except Exception as e:
        lines += ["", f"  dependency versions unavailable: {e}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 9. how is the model behaving
# --------------------------------------------------------------------------


def reasoning_report(runs: int = 20, db: str = "", root: str = DEBUG_ROOT) -> str:
    """The model's own conduct, and what the user got back.

    Two questions a chat surface cannot answer about itself: what did the
    guardrails have to stop, and how often did a question end in an answer
    rather than a refusal? Both are recorded; neither was readable.
    """
    now = datetime.now(UTC)
    lines = ["REASONING AND CONDUCT", ""]

    # -- how turns ended, from the durable record
    with _ledger(db) as led:
        calls = led.calls_between(now - timedelta(days=30), now)
    lines.append("  how model turns ended (last 30 days)")
    if calls:
        cols = set(calls[0].keys())
        if "stop_reason" in cols:
            stops: dict[str, int] = {}
            for r in calls:
                stops[str(r["stop_reason"] or "unrecorded")] = (
                    stops.get(str(r["stop_reason"] or "unrecorded"), 0) + 1
                )
            for reason, n in sorted(stops.items(), key=lambda x: -x[1]):
                note = ""
                if reason == "refusal":
                    note = "  - the model declined; surfaced as content, never re-routed"
                elif reason in ("truncated", "max_tokens"):
                    note = "  - hit max_tokens; the answer was paid for and discarded"
                lines.append(f"    {n:>4}  {reason}{note}")
        else:
            lines.append("    stop_reason not recorded on these rows")
    else:
        lines.append("    no model calls in the window")

    # -- what the rails had to stop
    rules: dict[str, int] = {}
    denied_actions: dict[str, int] = {}
    refusals: dict[str, int] = {}
    answered = refused = 0
    unsupported_hits = 0
    found = _runs(root)[:runs]
    for run in found:
        for e in _events(run):
            data = e.get("data") or {}
            kind = e.get("kind")
            if kind == "denied":
                rule = str(data.get("rule", "?"))
                rules[rule] = rules.get(rule, 0) + 1
                act = str(e.get("name", data.get("action", "?")))
                denied_actions[act] = denied_actions.get(act, 0) + 1
            elif kind == "refusal":
                r = str(data.get("reason", e.get("name", "?")))[:70]
                refusals[r] = refusals.get(r, 0) + 1
                refused += 1
            if data.get("answered"):
                answered += 1
            if data.get("unsupported_numbers"):
                unsupported_hits += 1

    lines += ["", f"  guardrail activity across {len(found)} traced run(s)"]
    if rules:
        for rule, n in sorted(rules.items(), key=lambda x: -x[1]):
            lines.append(f"    {n:>4}  {rule}")
        lines.append("    actions stopped: " + ", ".join(sorted(denied_actions)))
        lines.append(
            "    A denial is the chain working. A rising injection_scan count means "
            "hostile text is reaching the input rail, which is worth knowing."
        )
    else:
        lines.append("    nothing was denied")

    lines += ["", "  what the user got back"]
    total = answered + refused
    if total:
        lines.append(
            f"    {answered} answered, {refused} refused ({refused / total:.0%} refusal rate)"
        )
        lines.append(
            "    Refusal RATE is not a quality score - the design optimises refusal "
            "PRECISION. A rate near zero is as suspicious as one near one."
        )
    else:
        lines.append("    no answered/refused outcomes recorded in these runs")
    if refusals:
        lines.append("    most common reasons:")
        for reason, n in sorted(refusals.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"      {n:>3}x  {reason}")

    lines += ["", "  numeric faithfulness"]
    if unsupported_hits:
        lines.append(
            f"    {unsupported_hits} narrative(s) contained a number the engines did not supply"
        )
        lines.append("    run `ask.py thesis --narrate` to see the list for a fresh one")
    else:
        lines.append(
            "    no narrative in these runs carried an unsupported number - or none was "
            "checked. The check runs on `ask.py thesis --narrate`."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 10. all of it, in one view
# --------------------------------------------------------------------------


def _paper_figures(cfg, db: str = "") -> dict | None:
    """What the paper ledger holds, or None when no book is open."""
    from engines.paper.pricing import weekdays_between
    from engines.paper.store import CONTROL, DECIDED, PaperStore

    path = db or cfg.paper.database
    store = PaperStore.open_existing(path)
    if store is None:
        return None
    with store:
        if not store.has_books():
            return None
        opened = store.opened_on()
        marks = store.marks(DECIDED)
        ctl = store.marks(CONTROL)
        cost = store.cost_to_date(DECIDED)
        pending_stops = [t for t in store.pending_targets(DECIDED) if t.reason == "stop"]
        latest = marks[-1] if marks else None
        initial = store.initial_cash(DECIDED)
        today = datetime.now(UTC).date()
        need = min(21, max(1, weekdays_between(opened or today, today))) if opened else 21
        rets = [
            float(b.equity_usd / a.equity_usd - 1)
            for a, b in zip(marks, marks[1:])
            if a.equity_usd > 0
        ]
        return {
            "path": path,
            "opened": opened,
            "marks": len(marks),
            "need": need,
            "initial": initial,
            "latest": latest,
            "control_latest": ctl[-1] if ctl else None,
            "returns": rets,
            "cost": cost,
            "pending_stops": len(pending_stops),
        }


def paper_report(days: int = 30, db: str = "") -> str:
    """The paper book against its control (docs/22): equity path, return, max
    drawdown, cost drag, the paper predictions' hit rate, halt and stop state.
    CANNOT SCORE until enough sessions are marked - a book three days old has
    a balance, not a track record."""
    from agents.learning.store import LearningStore
    from core.config import load as load_config
    from engines.backtest.metrics import drawdown_profile

    cfg = load_config()
    fig = _paper_figures(cfg, db)
    lines = ["PAPER BOOK", ""]
    if fig is None:
        lines.append("  NO BOOK: nothing has been opened (`ask.py paper init`). CANNOT SCORE.")
        return "\n".join(lines)
    latest, ctl = fig["latest"], fig["control_latest"]
    lines.append(
        f"  opened {fig['opened']} with USD {fig['initial']:,.2f}; {fig['marks']} session(s) marked"
    )
    if latest is None:
        lines.append("  not yet marked. CANNOT SCORE.")
        return "\n".join(lines)
    ret = latest.equity_usd / fig["initial"] - 1 if fig["initial"] > 0 else Decimal(0)
    lines.append(
        f"  equity USD {latest.equity_usd:,.2f} ({ret:+.2%}) at the {latest.day} mark; "
        f"drawdown {latest.drawdown:.2%}{'  HALTED' if latest.halted else ''}; "
        f"{fig['pending_stops']} stop(s) pending"
    )
    if ctl is not None and fig["initial"] > 0:
        cret = ctl.equity_usd / fig["initial"] - 1
        lines.append(
            f"  control USD {ctl.equity_usd:,.2f} ({cret:+.2%}); decided minus control "
            f"{(ret - cret) * 100:+.2f} pp"
        )
    cost = fig["cost"]
    lines.append(
        f"  cost drag USD {cost.total:.2f} ({cost.pct_of_initial:.2%} of opening cash): fees "
        f"{cost.fees_usd:.2f}, fx spread {cost.fx_spread_usd:.2f}, slippage {cost.slippage_usd:.2f}"
    )
    if fig["marks"] < fig["need"]:
        lines.append(
            f"  CANNOT SCORE: {fig['marks']} of {fig['need']} sessions marked. A balance is not a "
            "track record; the return above is a figure, not a verdict."
        )
    else:
        dd, under = drawdown_profile(fig["returns"])
        lines.append(f"  max drawdown {dd:.2%}, longest underwater run {under} session(s)")
    try:
        with LearningStore(cfg.database) as learning:
            rows = learning.db.execute(
                "SELECT o.correct FROM predictions p JOIN outcomes o USING (prediction_id) "
                "WHERE p.agent = 'paper' AND p.direction != 0"
            ).fetchall()
            pending = learning.db.execute(
                "SELECT COUNT(*) FROM predictions p LEFT JOIN outcomes o USING (prediction_id) "
                "WHERE p.agent = 'paper' AND o.prediction_id IS NULL"
            ).fetchone()[0]
    except Exception:
        rows, pending = [], 0
    if rows:
        hit = sum(1 for r in rows if r[0]) / len(rows)
        lines.append(
            f"  predictions: {len(rows)} graded, hit rate {hit:.0%} vs the control; {pending} pending"
        )
    else:
        lines.append(f"  predictions: none graded yet; {pending} pending their horizon")
    lines.append("")
    lines.append(
        "  The book is a calibration instrument. Read the journal in knowledge/paper/ for what "
        "it learned; nothing here is a recommendation."
    )
    return "\n".join(lines)


def scorecard(db: str = "", root: str = DEBUG_ROOT) -> str:
    """Every dimension, one line each - and an explicit CANNOT SCORE where the
    evidence does not exist yet. A dashboard that shows green for a thing it
    never measured is the most expensive kind of comfort."""
    from core.config import load as load_config
    from core.doctor import FAIL, OK, WARN, run_checks

    cfg = load_config()
    now = datetime.now(UTC)
    day = now - timedelta(days=1)
    month = now - timedelta(days=30)
    rows: list[tuple[str, str, str]] = []  # dimension, verdict, evidence

    checks = run_checks(offline=True)
    blocking = [c for c in checks if c.status == FAIL and c.critical]
    degraded = [c for c in checks if c.status == WARN]
    rows.append(
        (
            "robustness",
            "BLOCKED" if blocking else ("degraded" if degraded else "ok"),
            f"{sum(1 for c in checks if c.status == OK)}/{len(checks)} preflight checks pass"
            + (f"; {', '.join(c.name for c in degraded)} degraded" if degraded else ""),
        )
    )

    with _ledger(db) as led:
        calls = led.calls_between(month, now)
        spend_day = led.cost_since(day)
        lat = led.latencies_between(month, now)
        claims = led.claims_between(month, now)

    if lat:
        p95 = sorted(lat)[min(len(lat) - 1, int(0.95 * len(lat)))]
        rows.append(
            (
                "performance",
                "ok" if p95 < float(cfg.alert_p95_latency_ms) else "slow",
                f"p95 {p95:.0f} ms over {len(lat)} call(s)",
            )
        )
    else:
        rows.append(("performance", "CANNOT SCORE", "no timed model calls recorded"))

    budget = Decimal(str(cfg.daily_budget_myr))
    if calls:
        rows.append(
            (
                "efficiency",
                "ok" if budget == 0 or spend_day < budget else "over budget",
                f"RM {spend_day:.4f} in 24h of RM {budget:.2f}; {len(calls)} call(s) in 30d",
            )
        )
    else:
        rows.append(("efficiency", "CANNOT SCORE", "nothing has run in 30 days"))

    from agents.learning.store import DEFAULT_PATH, LearningStore

    try:
        with LearningStore(DEFAULT_PATH) as store:
            pairs = store.calibration_pairs()
    except Exception:
        pairs = []
    need = int(cfg.min_graded_for_calibration)
    if len(pairs) >= need:
        brier = sum((c - (1.0 if h else 0.0)) ** 2 for c, h in pairs) / len(pairs)
        rows.append(
            ("quality", "ok" if brier < 0.25 else "poor", f"Brier {brier:.3f} on {len(pairs)}")
        )
    elif claims:
        kept = sum(1 for c in claims if c["survived"])
        rows.append(
            (
                "quality",
                "partial",
                f"{kept}/{len(claims)} claims survived; calibration needs {need - len(pairs)} more graded",
            )
        )
    else:
        rows.append(
            (
                "quality",
                "CANNOT SCORE",
                f"no verified claims, and calibration needs {need - len(pairs)} more graded call(s)",
            )
        )

    graph = Path("data/codegraph.db")
    if graph.is_file():
        import sqlite3

        conn = sqlite3.connect(str(graph))
        try:
            mods = {r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE kind='Product'")}
            tested = {r[0] for r in conn.execute("SELECT dst FROM edges WHERE kind='tests'")}
        finally:
            conn.close()
        share = len(mods & tested) / len(mods) if mods else 0
        rows.append(
            (
                "maintainability",
                "ok" if share >= 0.4 else "thin",
                f"{len(mods & tested)}/{len(mods)} modules have a test edge ({share:.0%})",
            )
        )
    else:
        rows.append(("maintainability", "CANNOT SCORE", "no codebase graph - run `make codegraph`"))

    answered = refused = 0
    for run in _runs(root)[:20]:
        for e in _events(run):
            data = e.get("data") or {}
            if data.get("answered"):
                answered += 1
            if e.get("kind") == "refusal":
                refused += 1
    if answered + refused:
        rows.append(
            (
                "usability",
                "ok",
                f"{answered} answered / {refused} refused across recent runs",
            )
        )
    else:
        rows.append(("usability", "CANNOT SCORE", "no traced runs with an outcome"))

    if calls and "stop_reason" in set(calls[0].keys()):
        bad = sum(
            1 for r in calls if str(r["stop_reason"] or "") in ("refusal", "truncated", "declined")
        )
        rows.append(
            (
                "reasoning",
                "ok" if bad == 0 else "check",
                f"{bad} of {len(calls)} turn(s) ended without usable text",
            )
        )
    else:
        rows.append(("reasoning", "CANNOT SCORE", "no model turns with a recorded stop reason"))

    try:
        fig = _paper_figures(cfg)
    except Exception:
        fig = None
    if fig is None:
        rows.append(("paper book", "CANNOT SCORE", "no book opened - `ask.py paper init`"))
    elif fig["latest"] is None or fig["marks"] < fig["need"]:
        rows.append(
            (
                "paper book",
                "CANNOT SCORE",
                f"{fig['marks']} of {fig['need']} sessions marked since {fig['opened']}",
            )
        )
    else:
        latest, ctl = fig["latest"], fig["control_latest"]
        ret = latest.equity_usd / fig["initial"] - 1
        cret = (ctl.equity_usd / fig["initial"] - 1) if ctl is not None else Decimal(0)
        # Equal is level, not behind: an all-cash book at +0.00% against a
        # control at +0.00% has neither won nor lost, and calling it behind
        # would report a verdict the arithmetic never reached.
        rows.append(
            (
                "paper book",
                "ahead" if ret > cret else ("level" if ret == cret else "behind"),
                f"{ret:+.2%} vs control {cret:+.2%} over {fig['marks']} sessions; drawdown "
                f"{latest.drawdown:.2%}{'; HALTED' if latest.halted else ''}",
            )
        )

    width = max(len(r[0]) for r in rows)
    lines = ["SCORECARD", ""]
    for dim, verdict, evidence in rows:
        lines.append(f"  {dim:<{width}}  {verdict:<13} {evidence}")
    unscored = [r[0] for r in rows if r[1] == "CANNOT SCORE"]
    lines.append("")
    if unscored:
        lines.append(f"  {len(unscored)} dimension(s) cannot be scored yet: {', '.join(unscored)}.")
        lines.append(
            "  That is a statement about the evidence, not about the system. A "
            "dashboard showing green for something it never measured is the most "
            "expensive kind of comfort."
        )
    else:
        lines.append("  Every dimension had enough evidence to score.")
    return "\n".join(lines)
