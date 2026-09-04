"""Every cap refuses with its message; the phase calendar; the fundable set."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engines.paper.rules import (
    FULL,
    OBSERVE,
    PRE,
    RAMP,
    Fundable,
    fundables,
    min_names,
    names_rule,
    phase_for,
    validate_targets,
)
from engines.paper.settings import PaperSettings
from engines.paper.store import BookState

S = PaperSettings(start_date=date(2026, 3, 2))


def test_the_phase_calendar_turns_on_the_agreed_weeks():
    assert phase_for(date(2026, 3, 1), S).name == PRE
    assert (
        phase_for(date(2026, 3, 2), S).name == OBSERVE and phase_for(date(2026, 3, 15), S).week == 2
    )
    assert phase_for(date(2026, 3, 16), S).name == RAMP and phase_for(
        date(2026, 3, 16), S
    ).max_invested == Decimal("0.40")
    assert phase_for(date(2026, 4, 12), S).name == RAMP  # week 6
    full = phase_for(date(2026, 4, 13), S)  # week 7
    assert full.name == FULL and full.max_invested == Decimal("0.80") and not full.review_due
    assert phase_for(date(2026, 6, 1), S).review_due  # week 14


def test_the_agreed_names_rule():
    assert min_names(S, Decimal(0)) == 0
    assert min_names(S, Decimal("0.10")) == 1
    assert min_names(S, Decimal("0.37")) == 2
    assert min_names(S, Decimal("0.40")) == 2
    assert min_names(S, Decimal("0.41")) == 4
    assert "at least 4 names" in names_rule(S, Decimal("0.6"))
    assert "ceil(invested" in names_rule(S, Decimal("0.3"))


def _fund(iid, lot_usd, ccy="MYR", lot=100, equity=Decimal(1000)):
    lot_usd = Decimal(lot_usd)
    return Fundable(
        iid,
        ccy,
        lot,
        lot_usd / lot * 4,
        date(2026, 3, 13),
        lot_usd,
        lot_usd / equity,
        int((Decimal("0.25") * equity) // lot_usd),
        Decimal("0.012"),
    )


FUNDS = [
    _fund("MYX:1155", 262),
    _fund("MYX:5347", 340),
    _fund("MYX:5183", 104),
    _fund("MYX:5225", 200),
    _fund("MYX:8869", 199),
    _fund("MYX:3182", 51),
    _fund("XNAS:NVDA", 224, "USD", 1),
    _fund("XNAS:AAPL", 325, "USD", 1),
    _fund("XNAS:MSFT", 497, "USD", 1),
]
WATCH = tuple(f.instrument_id for f in FUNDS)
EMPTY = BookState("decided", Decimal(1000), (), Decimal(0), Decimal(1000))


def _validate(weights, **kw):
    args = dict(
        state=EMPTY,
        current={},
        equity=Decimal(1000),
        fundable=FUNDS,
        phase=phase_for(date(2026, 3, 16), S),
        settings=S,
        halted=False,
        pending_stops=set(),
        turnover_used=Decimal(0),
        watchlist=WATCH,
    )
    args.update(kw)
    return {
        r.code: r.message
        for r in validate_targets({k: Decimal(str(v)) for k, v in weights.items()}, **args)
    }


def test_a_clean_ramp_book_passes():
    assert _validate({"MYX:5183": 0.11, "MYX:8869": 0.20, "MYX:3182": 0.06}) == {}


def test_each_cap_refuses_with_its_own_message():
    assert "start" in _validate({}, phase=phase_for(date(2026, 3, 1), S))
    assert "universe" in _validate({"XNAS:TSLA": 0.1})
    assert "negative" in _validate({"MYX:5183": -0.1})
    assert "per_name" in _validate({"XNAS:NVDA": 0.26})
    r = _validate({"MYX:5183": 0.11, "MYX:8869": 0.20, "MYX:3182": 0.06, "MYX:5225": 0.2})
    assert "invested" in r and "40%" in r["invested"]
    # 63% invested with three names, most of it already held: the names rule
    # fires (four are needed above 40%) and turnover does not (one raise).
    r = _validate(
        {"MYX:5183": 0.21, "MYX:8869": 0.21, "MYX:5225": 0.21},
        current={
            "MYX:5183": Decimal("0.21"),
            "MYX:8869": Decimal("0.21"),
            "MYX:5225": Decimal("0.10"),
        },
        phase=phase_for(date(2026, 4, 13), S),
    )
    assert "names" in r and "at least 4 names" in r["names"] and "turnover" not in r
    assert (
        "lot" in _validate({"XNAS:MSFT": 0.25})
        and "not fundable" in _validate({"XNAS:MSFT": 0.25})["lot"]
    )
    assert "below one lot" in _validate({"MYX:5183": 0.05})["lot"]
    assert "halt" in _validate({"MYX:5183": 0.11}, halted=True)
    assert "halt" not in _validate(
        {"MYX:5183": 0.0}, halted=True, current={"MYX:5183": Decimal("0.11")}
    )
    assert "stop" in _validate({"MYX:5183": 0.11}, pending_stops={"MYX:5183"})
    r = _validate({"MYX:5183": 0.11, "MYX:8869": 0.20}, turnover_used=Decimal(400))
    assert "turnover" in r and "USD 400.00" in r["turnover"]
    # a pure reduction is never blocked by turnover
    r = _validate(
        {"MYX:5183": 0.0}, current={"MYX:5183": Decimal("0.11")}, turnover_used=Decimal(490)
    )
    assert "turnover" not in r


def test_observe_targets_are_validated_against_the_ramp_ceiling_not_zero():
    obs = phase_for(date(2026, 3, 3), S)
    assert _validate({"MYX:5183": 0.11, "MYX:8869": 0.20}, phase=obs) == {}
    assert "invested" in _validate({"MYX:5183": 0.11, "MYX:8869": 0.20, "MYX:5225": 0.2}, phase=obs)


def test_fundables_split_the_watchlist_at_usd_1000(paper_env):
    q = paper_env.fx.asof(date(2026, 3, 13))
    funds = fundables(paper_env.feed, paper_env.cfg, Decimal(1000), q, date(2026, 3, 13), S)
    by = {f.instrument_id: f for f in funds}
    assert [f.instrument_id for f in funds if f.fundable] == [
        "MYX:5183",
        "MYX:5225",
        "MYX:8869",
        "MYX:3182",
        "XNAS:NVDA",
    ]
    assert not by["MYX:1155"].fundable and not by["XNAS:MSFT"].fundable
    assert by["XNAS:NVDA"].max_lots == 1 and by["MYX:3182"].max_lots >= 4
    assert by["MYX:5183"].lot == 100 and by["XNAS:NVDA"].lot == 1
    assert all(f.round_trip > 0 for f in funds)
    assert "not fundable" in by["XNAS:MSFT"].row()
