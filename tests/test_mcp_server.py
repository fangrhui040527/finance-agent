"""The MCP surface, and the invariant that makes it safe to point a model at it.

The whole reason this server can be handed to a model: the model writes the
narrative, the engines decide the numbers. These tests hold that line - a caller
that argues, retries with softer inputs, or asks for something the caps forbid
gets a refusal, every time, from code rather than from a prompt.
"""
import io
import json

import pytest

from mcp_server import tools as T
from mcp_server.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Server,
    ToolError,
)
from mcp_server.server import S, selftest


def call(name, **args):
    return S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})


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
    assert err(S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "nope"}))["code"] == METHOD_NOT_FOUND
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

    e = err(s.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "boom", "arguments": {}}}))
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
    out = text(call("size_position", instrument="MYX:1155", portfolio_value=5000,
                    price=6.20, stop_price=5.60, adv_20d=900000))
    assert "NO POSITION" in out
    assert "4,705" in out or "4,706" in out


def test_the_single_name_cap_cannot_be_raised_past_its_bound():
    """Limits.__post_init__ refuses above 15%. The tool must surface that, not crash."""
    out = text(call("check_portfolio_risk",
                    positions=[{"instrument": "MYX:1155", "weight": 0.5,
                                "sector": "bank", "country": "MY"}],
                    single_name_limit=0.99))
    assert "REFUSED" in out
    assert "cannot be raised" in out


def test_a_stop_above_the_entry_is_refused_with_a_reason():
    out = text(call("size_position", instrument="MYX:1155", portfolio_value=200000,
                    price=6.20, stop_price=6.50, adv_20d=900000))
    assert "REFUSED" in out and "not a stop" in out


def test_sizing_an_unadaptered_market_refuses_rather_than_approximating():
    """A flat-bps stand-in has no fixed minimum, and the minimum is what makes
    small positions uneconomic. Guessing here would produce a fundable position
    that cannot pay its own spread."""
    out = text(call("size_position", instrument="XFRA:BMW", portfolio_value=200000,
                    price=6.20, stop_price=5.60, adv_20d=900000))
    assert "REFUSED" in out


def test_a_thesis_with_an_unfalsifiable_breaker_is_refused():
    e = err(call("compose_thesis", instrument="MYX:1155",
                 breakers=[{"statement": "it goes up", "query": "", "store": "kb_filings"}]))
    assert "cannot be checked" in e["message"]


def test_fewer_than_two_breakers_forfeits_the_stance():
    out = text(call("compose_thesis", instrument="MYX:1155", stance="accumulate",
                    breakers=[{"statement": "NIM < 2%", "query": "nim < 0.02",
                               "store": "kb_filings"}]))
    assert "actionable: NO" in out
    assert "stance reached: no_view" in out


def test_the_red_team_is_never_silent():
    out = text(call("compose_thesis", instrument="MYX:1155", stance="accumulate",
                    evidence=[{"agent": "a1_fundamentals", "text": "CASA 24%"}],
                    breakers=[{"statement": "a", "query": "q", "store": "s"},
                              {"statement": "b", "query": "q", "store": "s"}]))
    assert "RED TEAM" in out
    assert "(silent" not in out


def test_a_prediction_cannot_be_logged_without_a_thesis(tmp_path):
    e = err(call("log_prediction", instrument="MYX:1155", direction=1,
                 horizon_days=63, confidence=0.6, thesis="   ",
                 db=str(tmp_path / "p.db")))
    assert "no thesis" in e["message"]


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
def test_impossible_confidence_is_refused(bad, tmp_path):
    e = err(call("log_prediction", instrument="MYX:1155", direction=1,
                 horizon_days=63, confidence=bad, thesis="x",
                 db=str(tmp_path / "p.db")))
    assert "confidence" in e["message"]


def test_a_direction_that_is_not_a_direction_is_refused(tmp_path):
    e = err(call("log_prediction", instrument="MYX:1155", direction=0,
                 horizon_days=63, confidence=0.6, thesis="x",
                 db=str(tmp_path / "p.db")))
    assert "+1 or -1" in e["message"]


def test_calibration_cannot_be_invented_before_a_record_exists(tmp_path):
    out = text(call("calibration_status", db=str(tmp_path / "empty.db")))
    assert "cannot be back-filled" in out


def test_a_concept_cannot_be_taught_before_its_prerequisites():
    out = text(call("explain_concept", concept="kelly"))
    assert "has to come first" in out


def test_mastery_of_a_concept_that_does_not_exist_is_refused():
    assert "not in the curriculum" in err(call("explain_concept", concept="kelly",
                                               mastered=["probability"]))["message"]


# --- decomposition before explanation ---------------------------------------
def test_a_market_wide_fall_is_attributed_to_the_market():
    out = text(call("why_did_it_move", instrument="MYX:1155",
                    instrument_return=-0.09, market_return=-0.08, sector_return=-0.02))
    assert "market_driven" in out
    assert "unexplained" in out


def test_one_measured_leg_against_one_typed_leg_is_refused():
    """It is a subtraction dressed as a decomposition."""
    e = err(call("why_did_it_move", instrument="XNAS:NVDA", instrument_return=0.07))
    assert "not a decomposition" in e["message"]


def test_supplied_returns_are_labelled_as_supplied():
    out = text(call("why_did_it_move", instrument="MYX:1155",
                    instrument_return=-0.09, market_return=-0.08))
    assert "as SUPPLIED by the caller, not measured" in out


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
    assert "YYYY-MM-DD" in err(call("get_prices", instrument="XNAS:NVDA",
                                    as_at="last tuesday"))["message"]


# --- every answer carries its disclaimer ------------------------------------
@pytest.mark.parametrize("name,args", [
    ("why_did_it_move", {"instrument": "MYX:1155", "instrument_return": -0.09,
                         "market_return": -0.08}),
    ("check_portfolio_risk", {"positions": [{"instrument": "MYX:1155", "weight": 0.2,
                                             "sector": "bank", "country": "MY"}]}),
    ("size_position", {"instrument": "MYX:1155", "portfolio_value": 200000,
                       "price": 6.20, "stop_price": 5.60, "adv_20d": 900000}),
])
def test_analysis_tools_never_read_as_a_recommendation(name, args):
    assert "Not financial advice" in text(call(name, **args))


# --- inputs that are not quantities -----------------------------------------
# Every case here produced a REAL POSITION before the guards existed. None of
# them crashed; each returned a number that looked like an answer.

@pytest.mark.parametrize("field,value", [
    ("portfolio_value", float("nan")),
    ("portfolio_value", float("inf")),
    ("price", float("nan")),
    ("stop_price", float("inf")),
    ("adv_20d", float("nan")),
])
def test_a_non_finite_input_is_refused_before_it_reaches_a_cap(field, value):
    args = dict(instrument="MYX:1155", portfolio_value=200000, price=6.20,
                stop_price=5.60, adv_20d=900000)
    args[field] = value
    assert "finite" in err(call("size_position", **args))["message"]


def test_a_negative_portfolio_cannot_invert_the_concentration_cap():
    """The serious one. A negative portfolio makes concentration_cap negative,
    and a negative cap WINS binding() - the same inversion docs/05 3.5 records
    for liquidity_cap, arriving through a new door. The engine fix stopped a cap
    from computing negative; nothing stopped a caller supplying a negative
    portfolio to compute it from."""
    e = err(call("size_position", instrument="MYX:1155", portfolio_value=-200000,
                 price=6.20, stop_price=5.60, adv_20d=900000))
    assert "must be positive" in e["message"]
    assert "inverts the caps" in e["message"]


@pytest.mark.parametrize("field", ["portfolio_value", "price", "stop_price", "adv_20d",
                                   "risk_per_trade", "single_name_limit", "participation"])
def test_every_quantity_argument_rejects_zero(field):
    """Zero ADV yielded a liquidity cap of 0 and reported '0 units' as a sizing
    outcome rather than as an untradeable instrument."""
    args = dict(instrument="MYX:1155", portfolio_value=200000, price=6.20,
                stop_price=5.60, adv_20d=900000)
    args[field] = 0
    assert "positive" in err(call("size_position", **args))["message"]


def test_a_non_finite_return_cannot_reach_a_verdict():
    """docs/05 3.5: a NaN return once reached a verdict as `nan% unexplained`."""
    e = err(call("why_did_it_move", instrument="MYX:1155",
                 instrument_return=float("nan"), market_return=-0.08))
    assert "must be finite" in e["message"]


def test_a_non_finite_weight_is_refused():
    e = err(call("check_portfolio_risk",
                 positions=[{"instrument": "MYX:1155", "weight": float("inf"),
                             "sector": "bank", "country": "MY"}]))
    assert "finite" in e["message"]


def test_a_negative_weight_is_refused_as_out_of_scope_not_absorbed():
    e = err(call("check_portfolio_risk",
                 positions=[{"instrument": "MYX:1155", "weight": -0.2,
                             "sector": "bank", "country": "MY"}]))
    assert "negative weight is a short" in e["message"]


def test_deeply_nested_arguments_do_not_hang_up_the_server():
    """json.loads raises RecursionError, not JSONDecodeError. An uncaught one
    ends the session: one line from a client kills the server."""
    nested = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
              '{"name":"market_info","arguments":{"market":'
              + "[" * 5000 + "]" * 5000 + "}}}")
    out = io.StringIO()
    S.serve(io.StringIO(nested + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'), out)
    answered = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    assert any(a.get("id") == 2 for a in answered), "the server died on one bad line"


def test_the_cost_floor_does_not_soften_under_repeated_asking():
    """The realistic abuse: a model that wants a position and keeps asking."""
    for pv in (5000, 4000, 3000, 2000, 1000, 500, 100):
        out = text(call("size_position", instrument="MYX:1155", portfolio_value=pv,
                        price=6.20, stop_price=5.60, adv_20d=900000))
        assert "NO POSITION" in out or "REFUSED" in out, f"yielded at {pv}"


def test_no_tool_on_the_surface_is_execution_shaped():
    banned = [t for t in S.tools
              if any(w in t.lower()
                     for w in ("order", "buy", "sell", "execute", "trade", "broker"))]
    assert not banned
