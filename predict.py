#!/usr/bin/env python3
"""The prediction log, as a command.

docs/14 section 2: the outcome clock is the one part of this system that cannot
be compressed by working harder, and it only works if logging a view is easier
than not logging it. A REPL snippet is not easier than not logging it.

    python predict.py log MYX:1155 +1 63d 0.62 "NIM stabilises above 2.25%"
    python predict.py due
    python predict.py grade 2026-08-25-myx1155-a1b2 --return 0.031 --benchmark 0.012
    python predict.py status

Everything lands in data/learning.db and survives restarts. Nothing is ever
edited or deleted - the triggers refuse.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, timedelta

from agents.learning.reflection import Horizon, Prediction, calibrate
from agents.learning.store import DEFAULT_PATH, LearningStore

SESSIONS_PER_WEEK = 5


def _grade_date(made: datetime, horizon: Horizon) -> date:
    """Sessions -> calendar days, roughly. Weekends are not trading days."""
    weeks = horizon.sessions / SESSIONS_PER_WEEK
    return (made + timedelta(days=weeks * 7)).date()


def _new_id(instrument: str, made: datetime, statement: str) -> str:
    slug = instrument.replace(":", "").lower()
    h = hashlib.sha256(f"{instrument}{made.isoformat()}{statement}".encode()).hexdigest()[:4]
    return f"{made:%Y-%m-%d}-{slug}-{h}"


def cmd_log(a) -> int:
    made = datetime.now(UTC)
    horizon = Horizon(a.horizon)
    grade_on = date.fromisoformat(a.grade_on) if a.grade_on else _grade_date(made, horizon)

    p = Prediction(
        prediction_id=a.id or _new_id(a.instrument, made, a.statement),
        instrument_id=a.instrument,
        agent=a.agent,
        made_at=made,
        horizon=horizon,
        statement=a.statement,
        direction=a.direction,
        confidence=a.confidence,
        grade_on=grade_on,
    )
    with LearningStore(a.db) as s:
        s.record(p)
        n = s.counts()
    arrow = {1: "up", -1: "down", 0: "no directional view"}[p.direction]
    print(f"logged {p.prediction_id}")
    print(f"  {p.instrument_id} {arrow} over {horizon.value}, stated {p.confidence:.0%}")
    print(f"  grades on {grade_on} - not before, and the code enforces that")
    print(f"  {n['pending']} pending, {n['graded']} graded")
    if n["graded"] < 30:
        print(f"  {30 - n['graded']} more graded calls before calibration means anything")
    return 0


def cmd_due(a) -> int:
    today = date.fromisoformat(a.today) if a.today else date.today()
    with LearningStore(a.db) as s:
        due = [p for p in s.pending() if p.grade_on <= today]
        upcoming = [p for p in s.pending() if p.grade_on > today]
    if not due:
        print(
            f"nothing due as at {today}."
            + (f" Next grades {min(p.grade_on for p in upcoming)}." if upcoming else "")
        )
        return 0
    print(f"{len(due)} due for grading as at {today}:\n")
    for p in due:
        overdue = (today - p.grade_on).days
        flag = f"  [{overdue}d overdue]" if overdue > 0 else ""
        print(f"  {p.prediction_id}{flag}")
        print(f"    {p.instrument_id}  {p.statement}")
        print(f"    stated {p.confidence:.0%} on {p.made_at:%Y-%m-%d} over {p.horizon.value}\n")
    return 0


def cmd_grade(a) -> int:
    today = date.fromisoformat(a.today) if a.today else date.today()
    with LearningStore(a.db) as s:
        queue = s.load_queue()
        try:
            o = queue.grade(a.prediction_id, today, a.realised, a.benchmark, a.note or "")
        except (KeyError, ValueError) as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        s.record_outcome(o)
        n = s.counts()
        pairs = s.calibration_pairs()

    print(f"graded {o.prediction_id}: {'correct' if o.correct else 'wrong'}")
    print(
        f"  realised {o.realised_return:+.2%} vs benchmark {o.benchmark_return:+.2%}"
        f"  (excess {o.excess:+.2%})"
    )
    print(f"  {n['graded']} graded, {n['pending']} still pending")
    if len(pairs) >= 10:
        c = calibrate(pairs)
        print(f"  Brier {c.brier:.3f} over {c.n}")
        for stated, realised, k in c.overconfident_bands():
            print(f"  overconfident: stated {stated:.0%}, realised {realised:.0%} over {k} calls")
    return 0


def cmd_status(a) -> int:
    with LearningStore(a.db) as s:
        n = s.counts()
        pairs = s.calibration_pairs()
        pending = s.pending()
    today = date.today()
    overdue = [p for p in pending if p.grade_on < today]

    print(f"prediction log  {a.db}")
    print(f"  logged   {n['logged']}")
    print(f"  graded   {n['graded']}")
    print(f"  pending  {n['pending']}" + (f"  ({len(overdue)} overdue)" if overdue else ""))
    print(f"  lessons  {n['lessons']} active")

    if len(pairs) < 30:
        print(
            f"\n{30 - len(pairs)} more graded calls before the calibration table "
            "measures skill rather than luck."
        )
        return 0

    c = calibrate(pairs)
    print(f"\ncalibration over {c.n} graded calls   Brier {c.brier:.3f}")
    print(f"  {'stated':>8} {'realised':>9} {'n':>5}")
    for stated, realised, k in c.buckets:
        gap = stated - realised
        mark = "  <- overconfident" if (k >= 5 and gap > 0.10) else ""
        print(f"  {stated:>7.0%} {realised:>9.0%} {k:>5}{mark}")
    return 0


def cmd_hypothesis(a) -> int:
    """The registry above the predictions: ideas, their lives, their cohorts."""
    from agents.learning.hypotheses import STATUSES, HypothesisStore

    with HypothesisStore(a.db) as store:
        if a.hcmd == "new":
            hid = store.create(a.title, a.thesis)
            print(f"created {hid}  (status: exploring)")
            print("  link predictions with:  predict hypothesis link " + hid + " <prediction_id>")
            return 0
        if a.hcmd == "status":
            store.transition(a.hypothesis_id, a.to, note=a.note or "")
            v = store.get(a.hypothesis_id)
            print(
                f"{v.hypothesis_id} -> {v.status}"
                + (f"  ({v.status_note})" if v.status_note else "")
            )
            return 0
        if a.hcmd == "link":
            store.link(a.hypothesis_id, a.prediction_id)
            v = store.get(a.hypothesis_id)
            print(f"{v.hypothesis_id} now carries {len(v.prediction_ids)} prediction(s)")
            return 0
        # list
        views = store.all(status=a.only)
        if not views:
            scope = f" with status {a.only}" if a.only else ""
            print(f"no hypotheses{scope}. An idea worth money is worth a row here first.")
            return 0
        for v in views:
            print(f"{v.hypothesis_id}  [{v.status:<10}] {v.title}")
            print(f"    {v.thesis}")
            if v.prediction_ids:
                print(f"    predictions: {', '.join(v.prediction_ids)}")
            if v.status_note and v.status in ("validated", "rejected"):
                print(f"    verdict note: {v.status_note}")
        print(f"\n{len(views)} hypothesis(es); statuses: {', '.join(STATUSES)}")
        return 0


def cmd_reflect(a) -> int:
    """Grade a cohort BY THE IDEA that links it, through A15's deterministic
    gates - and, only when a real backend answers, a structured second opinion
    the gates are free to ignore."""
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from agents.base import AgentContext
    from agents.learning.hypotheses import HypothesisStore
    from agents.learning.reflection import A15Reflection, LessonStore, OutcomeQueue
    from core.guardrails.defaults import default_engine
    from core.registry.loader import load as _load_registry
    from knowledge.retrieval.pipeline import Router

    with HypothesisStore(a.db) as hstore:
        try:
            view = hstore.get(a.hypothesis_id)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 2
    with LearningStore(a.db) as store:
        wanted = set(view.prediction_ids)
        outcomes = [o for o in store.graded() if o.prediction_id in wanted]
        instruments = store.instruments_for(list(wanted))

    print(f"{view.hypothesis_id}  [{view.status}]  {view.title}")
    print(f"  {view.thesis}")
    print(f"  linked {len(view.prediction_ids)}, graded {len(outcomes)}")
    if not outcomes:
        print("  nothing graded yet; the clock cannot be argued with.")
        return 0

    engine = default_engine(_load_registry("agents/registry.yaml").allowlist())
    ctx = AgentContext(router=Router({}), engine=engine, now=_dt.now(_UTC))
    a15 = A15Reflection(ctx, OutcomeQueue(), LessonStore())
    for f in a15.propose(view.title, outcomes, date.today(), instruments or None):
        print(f"  [{f.kind}] {f.text}")
        for c in f.caveats:
            print(f"      caveat: {c}")

    if a.second_opinion:
        from decimal import Decimal as _D

        from core.config import load as _load_config
        from core.llm.backends import backend_from_env
        from core.llm.client import EchoBackend, InferenceClient
        from core.provenance.ledger import ProvenanceLedger

        backend, reason = backend_from_env()
        if isinstance(backend, EchoBackend):
            print(f"  (no second opinion: {reason})")
            return 0
        cfg = _load_config()
        client = InferenceClient(
            backend,
            engine,
            ProvenanceLedger(cfg.provenance_db),
            daily_budget_myr=_D(str(cfg.daily_budget_myr)),
        )
        parsed, done = a15.second_opinion(client, view.title, outcomes)
        if done.refused:
            print(f"  model refused: {done.refusal_reason}")
        elif parsed is not None:
            root = parsed.root
            if root.kind == "no_lesson":
                print(f"  model second opinion: NO LESSON - {root.reason}")
            else:
                print(f"  model second opinion (ADVISORY, gates still decide): {root.text}")
    return 0


def main(argv=None) -> int:
    from core.env import load as _load_dotenv
    from core.logging import configure as _configure_logging

    _load_dotenv()
    _configure_logging()
    ap = argparse.ArgumentParser(
        prog="predict", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=str(DEFAULT_PATH), help="prediction log (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="log a view before you find out")
    lg.add_argument("instrument")
    lg.add_argument(
        "direction", type=int, choices=[1, 0, -1], help="+1 up, -1 down, 0 no directional view"
    )
    lg.add_argument("horizon", choices=[h.value for h in Horizon])
    lg.add_argument("confidence", type=float, help="0-1, your honest number")
    lg.add_argument("statement", help="what has to be true, in one sentence")
    lg.add_argument("--agent", default="human", help="who made the call")
    lg.add_argument("--id", help="override the generated id")
    lg.add_argument("--grade-on", help="override the computed grading date (YYYY-MM-DD)")
    lg.set_defaults(fn=cmd_log)

    du = sub.add_parser("due", help="what needs grading")
    du.add_argument("--today", help="override today (YYYY-MM-DD)")
    du.set_defaults(fn=cmd_due)

    gr = sub.add_parser("grade", help="score a call that has reached its horizon")
    gr.add_argument("prediction_id")
    gr.add_argument("--return", dest="realised", type=float, required=True)
    gr.add_argument(
        "--benchmark",
        type=float,
        required=True,
        help="being up 6%% when the index rose 8%% is being wrong",
    )
    gr.add_argument("--note", help="what you learned, if anything")
    gr.add_argument("--today", help="override today (YYYY-MM-DD)")
    gr.set_defaults(fn=cmd_grade)

    st = sub.add_parser("status", help="the calibration table")
    st.set_defaults(fn=cmd_status)

    hy = sub.add_parser("hypothesis", help="the idea above the predictions")
    hsub = hy.add_subparsers(dest="hcmd", required=True)
    hn = hsub.add_parser("new", help="register an idea, append-only")
    hn.add_argument("title")
    hn.add_argument("thesis", help="the falsifiable claim, one sentence")
    hs = hsub.add_parser("status", help="record a transition (a new event, never an edit)")
    hs.add_argument("hypothesis_id")
    hs.add_argument("to", choices=["exploring", "testing", "validated", "rejected", "monitoring"])
    hs.add_argument("--note", help="required for validated/rejected: the why")
    hl = hsub.add_parser("link", help="tie a logged prediction to the idea it tests")
    hl.add_argument("hypothesis_id")
    hl.add_argument("prediction_id")
    hls = hsub.add_parser("list", help="every idea and where it stands")
    hls.add_argument("--only", help="filter by status")
    for p_ in (hn, hs, hl, hls):
        p_.set_defaults(fn=cmd_hypothesis)

    rf = sub.add_parser("reflect", help="grade a cohort by the hypothesis that links it")
    rf.add_argument("hypothesis_id")
    rf.add_argument(
        "--second-opinion",
        action="store_true",
        help="ask the model too (advisory; needs a real backend, spends money)",
    )
    rf.set_defaults(fn=cmd_reflect)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
