"""The MCP surface, and the invariant that makes it safe to point a model at it.

The whole reason this server can be handed to a model: the model writes the
narrative, the engines decide the numbers. These tests hold that line - a caller
that argues, retries with softer inputs, or asks for something the caps forbid
gets a refusal, every time, from code rather than from a prompt.
"""

import io
import json
import random
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.market.cache import PriceCache
from core.market.feed import ChainedFeed, PriceFeed
from mcp_server import tools as T
from mcp_server.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Server,
)
from mcp_server.server import S, selftest


def call(name, **args):
    return S.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )


def text(resp) -> str:
    assert "error" not in resp, resp.get("error")
    return resp["result"]["content"][0]["text"]


def err(resp) -> dict:
    assert "error" in resp, f"expected an error, got {resp}"
    return resp["error"]


# --- protocol ---------------------------------------------------------------
def test_selftest_passes():
    assert selftest() == 0


def test_initialize_advertises_tools_and_instructions():
    r = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]
    assert r["protocolVersion"] == "2024-11-05"
    assert r["capabilities"]["tools"] is not None
    assert "REFUSAL IS AN ANSWER" in r["instructions"].upper()


def test_every_tool_lists_a_schema_and_a_description():
    tools = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert len(tools) >= 10
    for t in tools:
        assert t["description"].strip()
        assert t["inputSchema"]["type"] == "object"
        for req in t["inputSchema"].get("required", []):
            assert req in t["inputSchema"]["properties"], f"{t['name']}: {req} not described"


def test_a_notification_gets_no_response():
    """Answering a notification is a protocol violation some clients treat as fatal."""
    assert S.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_and_unknown_tool_are_distinct_errors():
    assert (
        err(S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "nope"}))["code"] == METHOD_NOT_FOUND
    )
    assert err(call("no_such_tool"))["code"] == INVALID_PARAMS


def test_missing_required_argument_is_refused_before_the_tool_runs():
    e = err(call("size_position", instrument="MYX:1155"))
    assert e["code"] == INVALID_PARAMS
    assert "missing required" in e["message"]


def test_an_unexpected_argument_is_the_callers_error_not_a_crash():
    assert err(call("market_info", nonsense=1))["code"] == INVALID_PARAMS


def test_a_tool_that_raises_returns_an_error_not_a_dead_server():
    s = Server(name="t", version="0")

    @s.tool("boom", "raises", {"type": "object", "properties": {}})
    def _boom():
        raise RuntimeError("kaboom")

    e = err(
        s.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        )
    )
    assert e["code"] == INTERNAL_ERROR and "kaboom" in e["message"]


def test_malformed_json_does_not_kill_the_loop():
    out = io.StringIO()
    S.serve(io.StringIO('not json\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == PARSE_ERROR
    assert lines[1]["id"] == 9, "the server kept serving after a bad line"


def test_a_non_object_request_is_rejected():
    out = io.StringIO()
    S.serve(io.StringIO("[1,2,3]\n"), out)
    assert json.loads(out.getvalue())["error"]["code"] != 0


# --- the line: the model cannot argue past the engines ----------------------
def test_a_position_below_the_cost_floor_is_refused_not_shrunk():
    out = text(
        call(
            "size_position",
            instrument="MYX:1155",
            portfolio_value=5000,
            price=6.20,
            stop_price=5.60,
            adv_20d=900000,
        )
    )
    assert "NO POSITION" in out
    assert "4,705" in out or "4,706" in out


def test_the_single_name_cap_cannot_be_raised_past_its_bound():
    """Limits.__post_init__ refuses above 15%. The tool must surface that, not crash."""
    out = text(
        call(
            "check_portfolio_risk",
            positions=[
                {"instrument": "MYX:1155", "weight": 0.5, "sector": "bank", "country": "MY"}
            ],
            single_name_limit=0.99,
        )
    )
    assert "REFUSED" in out
    assert "cannot be raised" in out


def test_a_stop_above_the_entry_is_refused_with_a_reason():
    out = text(
        call(
            "size_position",
            instrument="MYX:1155",
            portfolio_value=200000,
            price=6.20,
            stop_price=6.50,
            adv_20d=900000,
        )
    )
    assert "REFUSED" in out and "not a stop" in out


def test_sizing_an_unadaptered_market_refuses_rather_than_approximating():
    """A flat-bps stand-in has no fixed minimum, and the minimum is what makes
    small positions uneconomic. Guessing here would produce a fundable position
    that cannot pay its own spread."""
    out = text(
        call(
            "size_position",
            instrument="XFRA:BMW",
            portfolio_value=200000,
            price=6.20,
            stop_price=5.60,
            adv_20d=900000,
        )
    )
    assert "REFUSED" in out


def test_a_thesis_with_an_unfalsifiable_breaker_is_refused():
    e = err(
        call(
            "compose_thesis",
            instrument="MYX:1155",
            breakers=[{"statement": "it goes up", "query": "", "store": "kb_filings"}],
        )
    )
    assert "cannot be checked" in e["message"]


def test_fewer_than_two_breakers_forfeits_the_stance():
    out = text(
        call(
            "compose_thesis",
            instrument="MYX:1155",
            stance="accumulate",
            breakers=[{"statement": "NIM < 2%", "query": "nim < 0.02", "store": "kb_filings"}],
        )
    )
    assert "actionable: NO" in out
    assert "stance reached: no_view" in out


def test_the_red_team_is_never_silent():
    out = text(
        call(
            "compose_thesis",
            instrument="MYX:1155",
            stance="accumulate",
            evidence=[{"agent": "a1_fundamentals", "text": "CASA 24%"}],
            breakers=[
                {"statement": "a", "query": "q", "store": "s"},
                {"statement": "b", "query": "q", "store": "s"},
            ],
        )
    )
    assert "RED TEAM" in out
    assert "(silent" not in out


def test_a_prediction_cannot_be_logged_without_a_thesis(tmp_path):
    e = err(
        call(
            "log_prediction",
            instrument="MYX:1155",
            direction=1,
            horizon_days=63,
            confidence=0.6,
            thesis="   ",
            db=str(tmp_path / "p.db"),
        )
    )
    assert "no thesis" in e["message"]


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
def test_impossible_confidence_is_refused(bad, tmp_path):
    e = err(
        call(
            "log_prediction",
            instrument="MYX:1155",
            direction=1,
            horizon_days=63,
            confidence=bad,
            thesis="x",
            db=str(tmp_path / "p.db"),
        )
    )
    assert "confidence" in e["message"]


def test_a_direction_that_is_not_a_direction_is_refused(tmp_path):
    e = err(
        call(
            "log_prediction",
            instrument="MYX:1155",
            direction=0,
            horizon_days=63,
            confidence=0.6,
            thesis="x",
            db=str(tmp_path / "p.db"),
        )
    )
    assert "+1 or -1" in e["message"]


def test_calibration_cannot_be_invented_before_a_record_exists(tmp_path):
    out = text(call("calibration_status", db=str(tmp_path / "empty.db")))
    assert "cannot be back-filled" in out


def test_a_concept_cannot_be_taught_before_its_prerequisites():
    out = text(call("explain_concept", concept="kelly"))
    assert "has to come first" in out


def test_mastery_of_a_concept_that_does_not_exist_is_refused():
    assert (
        "not in the curriculum"
        in err(call("explain_concept", concept="kelly", mastered=["probability"]))["message"]
    )


# --- decomposition before explanation ---------------------------------------
def test_a_market_wide_fall_is_attributed_to_the_market():
    out = text(
        call(
            "why_did_it_move",
            instrument="MYX:1155",
            instrument_return=-0.09,
            market_return=-0.08,
            sector_return=-0.02,
        )
    )
    assert "market_driven" in out
    assert "unexplained" in out


def test_one_measured_leg_against_one_typed_leg_is_refused():
    """It is a subtraction dressed as a decomposition."""
    e = err(call("why_did_it_move", instrument="XNAS:NVDA", instrument_return=0.07))
    assert "not a decomposition" in e["message"]


def test_supplied_returns_are_labelled_as_supplied():
    out = text(
        call("why_did_it_move", instrument="MYX:1155", instrument_return=-0.09, market_return=-0.08)
    )
    assert "as SUPPLIED by the caller, not measured" in out


# --- measured legs: the same sessions, and this instrument's own sigma ------
# Two defects, both reproduced against the cached bars. Each leg of a measured
# decomposition was the last N bars of ITS OWN series, so on 2026-08-31 - a
# Bursa holiday Yahoo carried for the shares and skipped for ^KLSE - Maybank's
# flat carried day was paired with the index's move over the previous session.
# And the measured legs were decomposed against the synthetic fit built for
# typed returns, whose 0.4% residual sigma flagged Genting's ordinary -1.55% as
# significant when the nightly pack, estimating on real sessions, read 0.60
# sigma. Nothing here touches the network: a scripted feed goes through the
# real parser, the real cache and the real tool.
class _Scripted(PriceFeed):
    """Answers from a dict of id -> CSV body, through the base class's parser and cache."""

    name = "scripted"

    def __init__(self, bodies, cache=None):
        self.bodies, self.cache = dict(bodies), cache

    def symbol_for(self, instrument_id):
        return instrument_id

    def _fetch_csv(self, symbol):
        return self.bodies[symbol]


def _sessions(last: date, n: int) -> list[date]:
    days, d = [], last
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def _csv(closes: dict[date, float]) -> str:
    rows, prev = ["date,open,high,low,close,volume"], None
    for day, c in sorted(closes.items()):
        o = c if prev is None else prev
        rows.append(f"{day},{o},{max(o, c) * 1.001},{min(o, c) * 0.999},{c},1000")
        prev = c
    return "\n".join(rows) + "\n"


def _two_symbol_world(seed=1):
    """140 shared sessions to Friday 2026-08-28, then a carried Monday 08-31 bar
    for the share only - the Merdeka shape, on a beta-1.2 name."""
    rng = random.Random(seed)
    mkt, share, pm, ps = {}, {}, 1600.0, 10.0
    for d in _sessions(date(2026, 8, 28), 140):
        m = rng.gauss(0, 0.008)
        pm *= 1 + m
        ps *= 1 + 1.2 * m + rng.gauss(0, 0.01)
        mkt[d], share[d] = pm, ps
    share[date(2026, 8, 31)] = ps
    return mkt, share


def test_measured_legs_are_paired_on_the_sessions_both_printed(monkeypatch):
    mkt, share = _two_symbol_world()
    feed = ChainedFeed([_Scripted({"MYX:1155": _csv(share), "MYX:^KLSE": _csv(mkt)})])
    d0, d1 = date(2026, 8, 27), date(2026, 8, 28)

    legs = T.measured_legs(feed, "MYX:1155", "MYX:^KLSE", 1, date(2026, 8, 31))
    assert (legs.first, legs.last) == (d0, d1), "the carried 08-31 is not a common session"
    assert legs.instrument_return == pytest.approx(share[d1] / share[d0] - 1)
    assert legs.market_return == pytest.approx(mkt[d1] / mkt[d0] - 1)
    assert legs.fit is not None and legs.fit.n == 120
    assert legs.fit.coefficients[1] == pytest.approx(1.2, abs=0.15)
    assert f"to {d0}" in legs.estimation, "the window is not in its own estimation"

    monkeypatch.setattr(T, "_feed", lambda: feed)
    out = text(
        call("why_did_it_move", instrument="MYX:1155", market_proxy="MYX:^KLSE", as_at="2026-08-31")
    )
    assert f"{d0} to {d1}" in out
    assert f"MYX:^KLSE {mkt[d1] / mkt[d0] - 1:+.2%}" in out, "the matching session's market leg"
    assert "MEASURED" in out and "sessions the legs share" in out


def test_measured_betas_are_not_called_synthetic_and_typed_ones_are(monkeypatch):
    mkt, share = _two_symbol_world()
    feed = ChainedFeed([_Scripted({"MYX:1155": _csv(share), "MYX:^KLSE": _csv(mkt)})])
    monkeypatch.setattr(T, "_feed", lambda: feed)
    measured = text(
        call("why_did_it_move", instrument="MYX:1155", market_proxy="MYX:^KLSE", as_at="2026-08-31")
    )
    assert "SYNTHETIC" not in measured
    assert "Betas are stated" not in measured
    assert "betas from 120 sessions" in measured

    typed = text(
        call("why_did_it_move", instrument="MYX:1155", instrument_return=-0.09, market_return=-0.08)
    )
    assert "SYNTHETIC betas (stated 1.10/0.50); sigma is not this instrument's" in typed
    assert "Betas are stated" in typed and "not this instrument's" in typed


def test_too_few_common_sessions_is_unavailable_not_a_borrowed_sigma(monkeypatch):
    """Thirty shared sessions measure a return and cannot estimate a beta. The
    old code answered with the synthetic fit's sigma and a verdict."""
    mkt, share = _two_symbol_world()
    keep = sorted(mkt)[-30:]
    feed = ChainedFeed(
        [
            _Scripted(
                {
                    "MYX:1155": _csv({d: share[d] for d in keep}),
                    "MYX:^KLSE": _csv({d: mkt[d] for d in keep}),
                }
            )
        ]
    )
    monkeypatch.setattr(T, "_feed", lambda: feed)
    out = text(
        call("why_did_it_move", instrument="MYX:1155", market_proxy="MYX:^KLSE", as_at="2026-08-31")
    )
    assert "attribution unavailable" in out
    assert "betas not estimated" in out and "120 needed" in out
    assert "SYNTHETIC" not in out and "no_identified_catalyst" not in out


def test_two_measured_calls_then_a_price_read_share_one_feed_and_one_cache(tmp_path, monkeypatch):
    """The threadpool that fetched the two legs built the process-wide feed and
    its sqlite connection inside a worker thread, and the next call from the
    main thread found a connection it was not allowed to use. Sequential now;
    the lock is for the web app, which reads one cache from a threadpool."""
    mkt, share = _two_symbol_world()
    bodies = {"MYX:1155": _csv(share), "MYX:^KLSE": _csv(mkt)}
    cache = PriceCache(tmp_path / "p.db", today=lambda: "2026-08-31")
    fetched: list[str] = []

    class Counting(_Scripted):
        def _fetch_csv(self, symbol):
            fetched.append(symbol)
            return super()._fetch_csv(symbol)

    feed = ChainedFeed([Counting(bodies, cache=cache)])
    monkeypatch.setattr(T, "_feed", lambda: feed)
    first = T.why_did_it_move(instrument="MYX:1155", market_proxy="MYX:^KLSE", as_at="2026-08-31")
    second = T.why_did_it_move(instrument="MYX:1155", market_proxy="MYX:^KLSE", as_at="2026-08-31")
    prices = T.get_prices(instrument="MYX:1155", bars=2)
    assert "MEASURED" in first and "MEASURED" in second
    assert "2026-08-31" in prices
    assert sorted(fetched) == ["MYX:1155", "MYX:^KLSE"], "the cache answered every repeat"

    errors: list[BaseException] = []

    def hammer(symbol: str) -> None:
        try:
            for _ in range(200):
                assert cache.get("scripted", symbol) == bodies[symbol]
                cache.put("scripted", symbol, bodies[symbol])
        except BaseException as e:  # noqa: BLE001 - anything a thread raised is the finding
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(s,)) for s in bodies for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_a_factor_model_on_too_little_history_is_refused():
    csv = "\n".join("0.01,0.008,0.004" for _ in range(10))
    assert "REFUSED" in text(call("fit_factor_model", returns_csv=csv))
    assert "noise" in text(call("fit_factor_model", returns_csv=csv))


def test_a_factor_model_on_enough_history_reports_betas():
    import random

    rng = random.Random(3)
    rows = []
    for _ in range(300):
        m, s = rng.gauss(0, 0.01), rng.gauss(0, 0.008)
        rows.append(f"{1.2 * m + 0.4 * s + rng.gauss(0, 0.002)},{m},{s}")
    out = text(call("fit_factor_model", returns_csv="\n".join(rows)))
    assert "beta_market" in out and "beta_sector" in out


# --- data absence is stated, never inferred ---------------------------------
def test_missing_price_data_says_no_data_rather_than_returning_a_quiet_market(monkeypatch):
    from core.market.feed import PriceFeedError

    class Dead:
        def fetch(self, *a, **kw):
            raise PriceFeedError("source unreachable")

    monkeypatch.setattr(T, "_feed", lambda: Dead())
    assert "NO DATA" in text(call("get_prices", instrument="XNAS:NVDA"))


def test_an_unmapped_market_is_reported_not_guessed(monkeypatch):
    out = text(call("get_prices", instrument="XFRA:BMW"))
    assert "NO DATA" in out


def test_a_bad_as_at_date_is_refused():
    assert (
        "YYYY-MM-DD"
        in err(call("get_prices", instrument="XNAS:NVDA", as_at="last tuesday"))["message"]
    )


# --- every answer carries its disclaimer ------------------------------------
@pytest.mark.parametrize(
    "name,args",
    [
        (
            "why_did_it_move",
            {"instrument": "MYX:1155", "instrument_return": -0.09, "market_return": -0.08},
        ),
        (
            "check_portfolio_risk",
            {
                "positions": [
                    {"instrument": "MYX:1155", "weight": 0.2, "sector": "bank", "country": "MY"}
                ]
            },
        ),
        (
            "size_position",
            {
                "instrument": "MYX:1155",
                "portfolio_value": 200000,
                "price": 6.20,
                "stop_price": 5.60,
                "adv_20d": 900000,
            },
        ),
    ],
)
def test_analysis_tools_never_read_as_a_recommendation(name, args):
    assert "Not financial advice" in text(call(name, **args))


# --- inputs that are not quantities -----------------------------------------
# Every case here produced a REAL POSITION before the guards existed. None of
# them crashed; each returned a number that looked like an answer.


@pytest.mark.parametrize(
    "field,value",
    [
        ("portfolio_value", float("nan")),
        ("portfolio_value", float("inf")),
        ("price", float("nan")),
        ("stop_price", float("inf")),
        ("adv_20d", float("nan")),
    ],
)
def test_a_non_finite_input_is_refused_before_it_reaches_a_cap(field, value):
    args = dict(
        instrument="MYX:1155", portfolio_value=200000, price=6.20, stop_price=5.60, adv_20d=900000
    )
    args[field] = value
    assert "finite" in err(call("size_position", **args))["message"]


def test_a_negative_portfolio_cannot_invert_the_concentration_cap():
    """The serious one. A negative portfolio makes concentration_cap negative,
    and a negative cap WINS binding() - the same inversion docs/05 3.5 records
    for liquidity_cap, arriving through a new door. The engine fix stopped a cap
    from computing negative; nothing stopped a caller supplying a negative
    portfolio to compute it from."""
    e = err(
        call(
            "size_position",
            instrument="MYX:1155",
            portfolio_value=-200000,
            price=6.20,
            stop_price=5.60,
            adv_20d=900000,
        )
    )
    assert "must be positive" in e["message"]
    assert "inverts the caps" in e["message"]


@pytest.mark.parametrize(
    "field",
    [
        "portfolio_value",
        "price",
        "stop_price",
        "adv_20d",
        "risk_per_trade",
        "single_name_limit",
        "participation",
    ],
)
def test_every_quantity_argument_rejects_zero(field):
    """Zero ADV yielded a liquidity cap of 0 and reported '0 units' as a sizing
    outcome rather than as an untradeable instrument."""
    args = dict(
        instrument="MYX:1155", portfolio_value=200000, price=6.20, stop_price=5.60, adv_20d=900000
    )
    args[field] = 0
    assert "positive" in err(call("size_position", **args))["message"]


def test_a_non_finite_return_cannot_reach_a_verdict():
    """docs/05 3.5: a NaN return once reached a verdict as `nan% unexplained`."""
    e = err(
        call(
            "why_did_it_move",
            instrument="MYX:1155",
            instrument_return=float("nan"),
            market_return=-0.08,
        )
    )
    assert "must be finite" in e["message"]


def test_a_non_finite_weight_is_refused():
    e = err(
        call(
            "check_portfolio_risk",
            positions=[
                {
                    "instrument": "MYX:1155",
                    "weight": float("inf"),
                    "sector": "bank",
                    "country": "MY",
                }
            ],
        )
    )
    assert "finite" in e["message"]


def test_a_negative_weight_is_refused_as_out_of_scope_not_absorbed():
    e = err(
        call(
            "check_portfolio_risk",
            positions=[
                {"instrument": "MYX:1155", "weight": -0.2, "sector": "bank", "country": "MY"}
            ],
        )
    )
    assert "negative weight is a short" in e["message"]


def test_deeply_nested_arguments_do_not_hang_up_the_server():
    """json.loads raises RecursionError, not JSONDecodeError. An uncaught one
    ends the session: one line from a client kills the server."""
    nested = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
        '{"name":"market_info","arguments":{"market":' + "[" * 5000 + "]" * 5000 + "}}}"
    )
    out = io.StringIO()
    S.serve(io.StringIO(nested + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'), out)
    answered = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    assert any(a.get("id") == 2 for a in answered), "the server died on one bad line"


def test_the_cost_floor_does_not_soften_under_repeated_asking():
    """The realistic abuse: a model that wants a position and keeps asking."""
    for pv in (5000, 4000, 3000, 2000, 1000, 500, 100):
        out = text(
            call(
                "size_position",
                instrument="MYX:1155",
                portfolio_value=pv,
                price=6.20,
                stop_price=5.60,
                adv_20d=900000,
            )
        )
        assert "NO POSITION" in out or "REFUSED" in out, f"yielded at {pv}"


def test_no_tool_on_the_surface_is_execution_shaped():
    banned = [
        t
        for t in S.tools
        if any(w in t.lower() for w in ("order", "buy", "sell", "execute", "trade", "broker"))
    ]
    assert not banned


def test_a_valid_prediction_is_actually_logged(tmp_path):
    """The success path, not just the refusals. log_prediction built a
    Prediction with field names the class never had (horizon_days, thesis,
    made_on) - every valid call crashed with TypeError while all the
    validation-refusal tests kept passing around it."""
    out = T.log_prediction(
        instrument="MYX:1155",
        direction=1,
        horizon_days=63,
        confidence=0.6,
        thesis="NIM stabilises above 2.25%",
        db=str(tmp_path / "p.db"),
    )
    assert "logged MYX:1155-" in out
    assert "gradeable on or after" in out

    from agents.learning.store import LearningStore

    with LearningStore(tmp_path / "p.db") as store:
        pending = store.pending()
    assert len(pending) == 1
    p = pending[0]
    assert p.statement == "NIM stabilises above 2.25%"
    assert p.horizon.value == "63d"
    assert p.agent == "mcp"


def test_a_horizon_outside_the_ladder_is_refused_not_rounded(tmp_path):
    e = err(
        call(
            "log_prediction",
            instrument="MYX:1155",
            direction=1,
            horizon_days=64,
            confidence=0.6,
            thesis="x",
            db=str(tmp_path / "p.db"),
        )
    )
    assert "horizon_days must be one of" in e["message"]


# --- the working directory an MCP client hands us ---------------------------------


def test_the_server_anchors_itself_to_its_own_repository(tmp_path, monkeypatch):
    """A client launches this as a subprocess and it inherits the client's cwd;
    neither `claude mcp add` nor .mcp.json has a field to correct that. Every
    path here is relative, so from the wrong directory the server would load
    default settings and open a new empty ledger - and answer normally."""
    from mcp_server.server import ROOT, _anchor_to_the_repository

    assert (ROOT / "config.toml").is_file(), "ROOT must be the repo, not the package"

    monkeypatch.delenv("FINPLANET_NO_CHDIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _anchor_to_the_repository()
    assert Path.cwd().resolve() == ROOT


def test_the_anchor_can_be_declined(tmp_path, monkeypatch):
    """A caller that has deliberately arranged its own layout keeps it."""
    from mcp_server.server import _anchor_to_the_repository

    monkeypatch.setenv("FINPLANET_NO_CHDIR", "1")
    monkeypatch.chdir(tmp_path)
    _anchor_to_the_repository()
    assert Path.cwd().resolve() == tmp_path.resolve()


# --- news: the feed that was missing from the surface -----------------------
# The tool exists because MCP could reach prices but not news, so a model asked
# "why did it move" could decompose the move and never look for a catalyst. The
# tests below hold the distinction the whole ingest contract is built on: a
# BROKEN source and a QUIET window are different answers, and neither is silence.
def test_a_broken_news_source_is_reported_not_raised(monkeypatch):
    from knowledge.feeds.adapter import FeedError

    class Dead:
        name = "gdelt"

        def fetch(self, since, limit=20):
            raise FeedError("gdelt unreachable: timed out")

    monkeypatch.setattr(T, "_news_adapter", lambda source, query: Dead())
    out = text(call("pull_news", source="gdelt"))
    assert "NO NEWS" in out
    assert "timed out" in out


def test_a_quiet_window_is_reported_as_one(monkeypatch):
    from knowledge.feeds.adapter import IngestStats

    class Quiet:
        name = "gdelt"

        def fetch(self, since, limit=20):
            return []

        def normalize(self, records, entity_index=None, holdings=None, watchlist=None):
            return [], IngestStats()

    monkeypatch.setattr(T, "_news_adapter", lambda source, query: Quiet())
    out = text(call("pull_news", source="gdelt"))
    assert "quiet window" in out
    assert "NO NEWS" not in out


def test_an_unknown_source_names_the_ones_that_exist():
    message = err(call("pull_news", source="bloomberg"))["message"]
    assert "gdelt" in message


def test_nothing_escalates_with_an_empty_watchlist(monkeypatch):
    """The gate that made the news half look like a quiet day either way."""
    from knowledge.feeds.adapter import IngestStats

    class Feed:
        name = "gdelt"

        def fetch(self, since, limit=20):
            return [object()]

        def normalize(self, records, entity_index=None, holdings=None, watchlist=None):
            assert holdings == set() and watchlist == set()
            return [], IngestStats(fetched=1, kept=1, unlinked=1)

    monkeypatch.setattr(T, "_news_adapter", lambda source, query: Feed())
    monkeypatch.setattr(T, "_holdings_and_watchlist", lambda: (set(), set()))
    out = text(call("pull_news", source="gdelt"))
    assert "escalated 0" in out
    assert "holdings" in out and "watchlist" in out


def test_hours_below_one_is_refused():
    assert "hours" in err(call("pull_news", hours=0))["message"]
