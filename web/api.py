"""Every endpoint. One truth: the MCP tool functions and the stores.

`text` comes from the SAME functions the MCP server exposes, so the words a
screen shows and the words the model reads cannot drift (the parity tests in
tests/test_web_api.py assert byte equality). `data` is added only where it
comes straight off an engine, store or registry - never computed a second
time from the text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mcp_server import tools as T
from mcp_server.protocol import ToolError
from web import schemas as S

router = APIRouter()

DISCLAIMER = T.DISCLAIMER.strip()


def _run(fn, *args, data: Any = None, **kwargs) -> S.Envelope:
    """Call a tool function; its refusal strings become the refusal field, its
    ToolError (bad arguments) becomes a 422 - the same split the wire makes."""
    try:
        text = fn(*args, **kwargs)
    except ToolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return S.envelope(text, data=data, disclaimer=DISCLAIMER)


# --- system --------------------------------------------------------------------


@router.get("/health")
def health() -> S.Envelope:
    return S.Envelope(text="ok", data={"status": "ok"})


@router.get("/backend")
def backend() -> S.Envelope:
    from core.llm.backends import backend_from_env, backend_name, effort_reaches, models_by_tier
    from core.llm.tiers import (
        cheap_capped,
        effective_tier,
        pinned_tier,
        profile_for,
        selected_effort,
    )

    b, reason = backend_from_env()
    effort = selected_effort()
    pin = pinned_tier()
    # From the backend, not MODEL_IDS: a free provider answers with its own
    # lineup, and a screen that showed Claude ids for it would be showing the
    # design, not the run.
    models = models_by_tier(b)
    return S.Envelope(
        text=reason,
        data={
            "backend": type(b).__name__,
            "is_stub": type(b).__name__ == "EchoBackend",
            "cheap_capped": cheap_capped(),
            "pinned_model": models[pin] if pin is not None else None,
            "effort": effort.value if effort is not None else None,
            # What each tier will ACTUALLY call and how hard it will think.
            "tiers": {
                tier.value: {
                    "model": models[effective_tier(tier)],
                    "backend": backend_name(b, effective_tier(tier)),
                    "effort": (
                        profile_for(effective_tier(tier)).effort
                        if effort_reaches(b, effective_tier(tier))
                        else None
                    ),
                    "thinking_budget": (
                        profile_for(effective_tier(tier)).thinking_budget
                        if effort_reaches(b, effective_tier(tier))
                        else None
                    ),
                    "max_tokens": profile_for(effective_tier(tier)).max_tokens,
                }
                for tier in models
            },
        },
    )


@router.get("/config")
def config() -> S.Envelope:
    from core.config import HARD_BOUNDS, load

    cfg = load()
    bounds = [
        {"field": key, "low": str(lo), "high": str(hi), "why": why}
        for key, lo, hi, why in HARD_BOUNDS
    ]
    return S.Envelope(
        text=cfg.describe(),
        data={
            "source": cfg.source,
            "base_currency": cfg.base_currency,
            "markets": list(cfg.markets),
            "risk_per_trade": str(cfg.risk_per_trade),
            "daily_budget_myr": str(cfg.daily_budget_myr),
            "daemon_budget_myr": str(cfg.daemon_budget_myr),
            "single_name": str(cfg.limits.single_name),
            "min_effective_bets": str(cfg.limits.min_effective_bets),
            "portfolio_heat": str(cfg.limits.portfolio_heat),
            "holdings": list(cfg.holdings),
            "watchlist": list(cfg.watchlist),
            "hard_bounds": bounds,
        },
    )


@router.get("/doctor")
def doctor(offline: bool = True) -> S.Envelope:
    from core.doctor import render, run_checks

    results = run_checks(offline=offline)
    return S.Envelope(
        text=render(results),
        data=[
            {
                "name": r.name,
                "status": r.status,
                "message": r.message,
                "impact": r.impact,
                "critical": r.critical,
            }
            for r in results
        ],
    )


@router.get("/capital")
def capital() -> S.Envelope:
    """The waterfall, or an explicit NO PLAN. Never a zero that looks like an answer."""
    from core.config import load as load_config

    cfg = load_config()
    plan = cfg.capital
    env = _run(T.investable_capital)
    env.data = {
        "stated": plan.stated,
        "liquid_assets": str(plan.liquid_assets),
        "essential_monthly_spend": str(plan.essential_monthly_spend),
        "planned_monthly_contribution": str(plan.planned_monthly_contribution),
        "emergency_months": cfg.emergency_months,
        "debt_hurdle": str(cfg.debt_hurdle),
        "goals": [
            {"name": g.name, "amount": str(g.amount), "months_away": g.months_away}
            for g in plan.goals
        ],
        "liabilities": [
            {"name": x.name, "balance": str(x.balance), "annual_rate": str(x.annual_rate)}
            for x in plan.liabilities
        ],
        "book": [
            {
                "id": h.id,
                "units": str(h.units) if h.units is not None else None,
                "avg_cost": str(h.avg_cost) if h.avg_cost is not None else None,
            }
            for h in cfg.book
        ],
    }
    return env


# --- markets -------------------------------------------------------------------


@router.get("/markets")
def markets() -> S.Envelope:
    from decimal import Decimal

    from core.config import load as load_cfg
    from engines.sizing.caps import cost_floor_bps, cost_floor_value
    from markets.brokers import cost_at, prices_venue
    from markets.registry import get as market_get
    from markets.registry import supported

    #: The share price the broker figures below are quoted at. A broker leg
    #: charged PER SHARE makes the minimum position a function of price, so
    #: there is no single number to report here - this listing has no
    #: instrument and therefore no price of its own. Quoting one silently
    #: would be inventing the input; naming it lets a reader check it.
    REFERENCE_PRICE = Decimal(100)

    broker = load_cfg().broker
    rows = []
    for mic in supported():
        a = market_get(mic)
        row = {
            "mic": mic,
            "country": a.country,
            "currency": a.currency,
            "tier": a.tier,
            "index": a.local_index,
            "settlement_days": a.settlement_days,
            # The VENUE's own terms - what this exchange charges everyone.
            "cost_floor_bps": str(cost_floor_bps(mic)),
            "minimum_economic_position": str(cost_floor_value(a.fee_schedule.round_trip, mic)),
        }
        # ...and the account's, where the broker sets its own on this venue.
        # Absent rather than duplicated when it does not: a broker figure equal
        # to the venue figure reads as confirmation, when it only means nobody
        # modelled this venue for this broker.
        if prices_venue(broker, mic):
            row["broker"] = broker
            row["broker_cost_floor_bps"] = str(cost_floor_bps(mic, broker))
            row["broker_minimum_economic_position"] = str(
                cost_floor_value(cost_at(mic, broker, REFERENCE_PRICE), mic, broker)
            )
            row["broker_minimum_quoted_at_price"] = str(REFERENCE_PRICE)
        rows.append(row)
    return _run(T.market_info, data=rows)


@router.get("/markets/{mic}")
def market(mic: str) -> S.Envelope:
    return _run(T.market_info, mic)


# --- prices --------------------------------------------------------------------


@router.get("/prices/{market}/{code}")
def prices(market: str, code: str, bars: int = 60, as_at: str = "") -> S.Envelope:
    instrument = f"{market}:{code}"
    text = None
    data = None
    try:
        end = T._parse_date(as_at) if as_at else None
    except ToolError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    from core.market.feed import PriceFeedError

    try:
        series = T._feed().fetch(instrument, end=end)
        window = series.raw()[-max(1, bars) :]
        data = {
            "instrument": instrument,
            "bars": [
                {
                    "day": b.day.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in window
            ],
            "adv_20d": series.adv(20),
            "atr_20d": series.atr(20),
        }
    except PriceFeedError:
        data = None  # the text path below reports it in the standard words
    text = T.get_prices(instrument, bars=bars, as_at=as_at)
    return S.envelope(text, data=data, disclaimer=DISCLAIMER)


# --- attribution ---------------------------------------------------------------


@router.post("/why")
def why(body: S.WhyBody) -> S.Envelope:
    return _run(T.why_did_it_move, **body.model_dump())


@router.post("/factor-model")
def factor_model(body: S.FactorModelBody) -> S.Envelope:
    return _run(T.fit_factor_model, body.returns_csv)


# --- thesis --------------------------------------------------------------------


@router.post("/thesis")
def thesis(body: S.ThesisBody) -> S.Envelope:
    env = _run(
        T.compose_thesis,
        body.instrument,
        evidence=[e.model_dump() for e in body.evidence],
        breakers=[b.model_dump() for b in body.breakers],
        stance=body.stance,
        horizon_months=body.horizon_months,
    )
    if body.narrate and env.refusal is None:
        env.data = {"narrative": _narrative(body)}
    return env


def _narrative(body: S.ThesisBody) -> dict:
    """Model prose over the engine's thesis - labelled, railed, refusal-aware."""
    from decimal import Decimal

    from agents.base import Finding
    from agents.synthesis.agents import A10Thesis, A11RedTeam, Breaker, Stance
    from agents.synthesis.narrate import narrate_thesis
    from core.config import load as load_config
    from core.guardrails.policy import Action, PolicyViolation, Rail
    from core.llm.backends import backend_from_env
    from core.llm.client import InferenceClient
    from core.provenance.ledger import ProvenanceLedger

    ctx = T.context()
    a10 = A10Thesis(ctx)
    a10.run(
        body.instrument,
        [Finding(e.agent, "supplied", e.text) for e in body.evidence],
        horizon_months=body.horizon_months,
        proposed_stance=Stance(body.stance),
        breakers=[Breaker(b.statement, b.query, b.store) for b in body.breakers],
    )
    th = a10.last
    challenges = A11RedTeam(ctx).run(th)
    backend_obj, reason = backend_from_env()
    cfg = load_config()
    client = InferenceClient(
        backend_obj,
        ctx.engine,
        ProvenanceLedger(cfg.provenance_db),
        daily_budget_myr=Decimal(str(cfg.daily_budget_myr)),
    )
    done = narrate_thesis(client, th, challenges)
    if done.refused:
        return {
            "refused": True,
            "reason": done.refusal_reason,
            "backend": type(backend_obj).__name__,
        }
    try:
        ctx.engine.enforce(
            Action(
                name="narrate", rail=Rail.OUTPUT, agent="a10_thesis", payload={"text": done.text}
            )
        )
    except PolicyViolation as e:
        return {"blocked": True, "reason": str(e), "backend": type(backend_obj).__name__}
    return {"text": done.text, "backend": type(backend_obj).__name__, "reason": reason}


@router.post("/allocate")
def allocate(body: S.AllocateBody) -> S.Envelope:
    return _run(T.allocate_capital, **body.model_dump())


@router.post("/rebalance")
def rebalance(body: S.RebalanceBody) -> S.Envelope:
    return _run(T.rebalance_book, **body.model_dump())


# --- portfolio -----------------------------------------------------------------


@router.post("/portfolio/risk")
def portfolio_risk(body: S.RiskBody) -> S.Envelope:
    return _run(
        T.check_portfolio_risk,
        positions=[p.model_dump() for p in body.positions],
        base_currency=body.base_currency,
        single_name_limit=body.single_name_limit,
        equity=body.equity,
        peak_equity=body.peak_equity,
    )


@router.post("/sizing")
def sizing(body: S.SizingBody) -> S.Envelope:
    return _run(T.size_position, **body.model_dump())


# --- planning and learning ------------------------------------------------------


@router.post("/plan")
def plan(body: S.PlanBody) -> S.Envelope:
    return _run(
        T.plan_question,
        body.question,
        instruments=body.instruments,
        budget_myr=body.budget_myr,
    )


@router.get("/learn/concepts")
def learn_concepts() -> S.Envelope:
    return _run(T.explain_concept)


@router.post("/learn/explain")
def learn_explain(body: S.ExplainBody) -> S.Envelope:
    return _run(T.explain_concept, body.concept, mastered=body.mastered)


# --- the forward record ---------------------------------------------------------


@router.get("/predictions")
def predictions() -> S.Envelope:
    from agents.learning.store import DEFAULT_PATH, LearningStore

    with LearningStore(DEFAULT_PATH) as store:
        pending = store.pending()
        graded = store.graded(limit=200)
        counts = store.counts()
    return S.Envelope(
        text=f"{counts['pending']} pending, {counts['graded']} graded",
        data={
            "counts": counts,
            "pending": [
                {
                    "prediction_id": p.prediction_id,
                    "instrument_id": p.instrument_id,
                    "direction": p.direction,
                    "confidence": p.confidence,
                    "horizon": p.horizon.value,
                    "statement": p.statement,
                    "grade_on": p.grade_on.isoformat(),
                }
                for p in pending
            ],
            "graded": [
                {
                    "prediction_id": o.prediction_id,
                    "graded_on": o.graded_on.isoformat(),
                    "realised_return": o.realised_return,
                    "benchmark_return": o.benchmark_return,
                    "correct": o.correct,
                    "note": o.note,
                }
                for o in graded
            ],
        },
    )


@router.post("/predictions")
def log_prediction(body: S.PredictionBody) -> S.Envelope:
    return _run(T.log_prediction, **body.model_dump())


@router.get("/calibration")
def calibration() -> S.Envelope:
    return _run(T.calibration_status)


@router.get("/hypotheses")
def hypotheses() -> S.Envelope:
    from agents.learning.hypotheses import HypothesisStore
    from agents.learning.store import DEFAULT_PATH

    with HypothesisStore(DEFAULT_PATH) as store:
        views = store.all()
    return S.Envelope(
        text=f"{len(views)} hypothesis(es)",
        data=[
            {
                "hypothesis_id": v.hypothesis_id,
                "title": v.title,
                "thesis": v.thesis,
                "status": v.status,
                "status_note": v.status_note,
                "predictions": list(v.prediction_ids),
                "history": [
                    {"at": at.isoformat(), "status": st, "note": note} for at, st, note in v.history
                ],
            }
            for v in views
        ],
    )


@router.post("/hypotheses")
def log_hypothesis(body: S.HypothesisBody) -> S.Envelope:
    return _run(T.log_hypothesis, title=body.title, thesis=body.thesis)


@router.get("/alerts")
def alerts(history: int = 20) -> S.Envelope:
    from core.monitor import AlertLog

    with AlertLog() as log:
        open_now = log.open_rules()
        rows = log.history(limit=history)
    return S.Envelope(
        text=f"{len(open_now)} open, {len(rows)} event(s) in history",
        data={
            "open": [
                {
                    "rule": rule,
                    "severity": r["severity"],
                    "title": r["title"],
                    "detail": r["detail"],
                    "since": r["at"],
                }
                for rule, r in sorted(open_now.items())
            ],
            "history": [
                {
                    "at": r["at"],
                    "state": r["state"],
                    "rule": r["rule"],
                    "title": r["title"],
                    "severity": r["severity"],
                }
                for r in rows
            ],
        },
    )


# --- traces --------------------------------------------------------------------


@router.get("/trace/runs")
def trace_runs() -> S.Envelope:
    import json
    from pathlib import Path

    root = Path("debug")
    runs = []
    if root.is_dir():
        for p in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)[:50]:
            summary = {}
            sfile = p / "summary.json"
            if sfile.exists():
                try:
                    summary = json.loads(sfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    summary = {"error": "summary unreadable"}
            runs.append({"run_id": p.name, "summary": summary})
    return S.Envelope(text=f"{len(runs)} trace run(s)", data=runs)


@router.get("/trace/runs/{run_id}")
def trace_run(run_id: str) -> S.Envelope:
    import json
    from pathlib import Path

    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    p = Path("debug") / safe
    if safe != run_id or not p.is_dir():
        raise HTTPException(status_code=404, detail=f"no trace run {run_id!r}")
    events = []
    trace = p / "trace.jsonl"
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines()[:5000]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    manifest = {}
    mfile = p / "manifest.json"
    if mfile.exists():
        try:
            manifest = json.loads(mfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {"error": "manifest unreadable"}
    return S.Envelope(
        text=f"{len(events)} event(s) in {safe}",
        data={"run_id": safe, "events": events, "manifest": manifest},
    )


@router.get("/trace/runs/{run_id}/blob/{name}")
def trace_blob(run_id: str, name: str) -> S.Envelope:
    """One externalised prompt/response blob, verbatim. Names are sanitised the
    same way the tracer writes them, so traversal cannot compose a path."""
    from pathlib import Path

    safe_run = "".join(c for c in run_id if c.isalnum() or c in "-_")
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_.")
    p = Path("debug") / safe_run / "prompts" / safe_name
    if safe_run != run_id or safe_name != name or not p.is_file() or p.suffix != ".txt":
        raise HTTPException(status_code=404, detail=f"no blob {name!r} in {run_id!r}")
    return S.Envelope(
        text=p.read_text(encoding="utf-8"), data={"run_id": safe_run, "name": safe_name}
    )


# --- world and graph ------------------------------------------------------------


@router.get("/world")
def world() -> S.Envelope:
    import os

    enabled = ["gdelt"]  # config.toml [sources]; gdelt is the one wired live
    return S.Envelope(
        text="1 live feed (gdelt); price chain stooq -> yahoo",
        data={
            "feeds": [
                {
                    "name": "gdelt",
                    "kind": "news",
                    "keyless": True,
                    "enabled": True,
                    "endpoint_override": bool(os.environ.get("GDELT_DOC_API", "")),
                },
                {"name": "stooq", "kind": "prices", "keyless": True, "enabled": True},
                {"name": "yahoo", "kind": "prices", "keyless": True, "enabled": True},
            ],
            "enabled": enabled,
        },
    )


@router.get("/graph/path")
def graph_path(a: str, b: str, asof: str = "") -> S.Envelope:
    return _run(T.explain_path, a, b, asof=asof)


# --- agents --------------------------------------------------------------------


@router.get("/agents")
def agents() -> S.Envelope:
    from core.registry.loader import load as load_registry

    reg = load_registry(T.REGISTRY)
    rows = [
        {
            "id": spec.id,
            "layer": spec.layer,
            "tools": list(spec.tools),
            "knowledge": list(spec.knowledge),
            "tier_hint": spec.tier_hint,
            "eval_suite": spec.eval_suite,
        }
        for spec in sorted(reg.agents.values(), key=lambda s: s.id)
    ]
    return S.Envelope(text=f"{len(rows)} agents registered", data=rows)


# --- the daily brief ------------------------------------------------------------


@router.get("/brief")
def brief() -> S.Envelope:
    from datetime import UTC, date, datetime, timedelta

    from agents.learning.store import DEFAULT_PATH, LearningStore
    from core.config import load as load_config
    from core.provenance.ledger import ProvenanceLedger

    cfg = load_config()
    with LearningStore(DEFAULT_PATH) as store:
        due = [p for p in store.pending() if p.grade_on <= date.today()]
    ledger = ProvenanceLedger(cfg.provenance_db)
    spent = ledger.cost_since(datetime.now(UTC) - timedelta(days=1))
    ledger.close()
    lines = []
    if due:
        lines.append(f"{len(due)} prediction(s) due for grading")
    lines.append(f"model spend last 24h: RM {spent:.4f} of RM {cfg.daily_budget_myr:.2f}")
    if not due:
        lines.append("Nothing needs a decision today.")
    return S.Envelope(
        text="\n".join(lines),
        data={
            "due": [p.prediction_id for p in due],
            "spend_24h_myr": str(spent),
            "daily_budget_myr": str(cfg.daily_budget_myr),
            "watchlist": list(cfg.watchlist),
            "holdings": list(cfg.holdings),
        },
    )
