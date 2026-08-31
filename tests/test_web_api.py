"""P6a: the web API. One truth with the MCP tools, refusal as content, POSTs guarded."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcp_server import tools as T
from web.app import create_app

POST_HEADERS = {"X-Requested-With": "FinPlanet"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Store paths point into tmp so a test can never touch data/.
    monkeypatch.setattr("agents.learning.store.DEFAULT_PATH", tmp_path / "learning.db")
    return TestClient(create_app())


# --- envelope and parity --------------------------------------------------------


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["data"]["status"] == "ok"


def test_markets_text_is_byte_identical_to_the_mcp_tool(client):
    body = client.get("/api/markets").json()
    assert body["text"] == T.market_info()
    assert len(body["data"]) >= 11
    assert {"mic", "currency", "cost_floor_bps"} <= set(body["data"][0])


def test_single_market_parity(client):
    body = client.get("/api/markets/XKLS").json()
    assert body["text"] == T.market_info("XKLS")
    assert "MINIMUM ECONOMIC POSITION" in body["text"]


def test_sizing_parity_and_no_position_is_not_an_error(client):
    args = {
        "instrument": "MYX:1155",
        "portfolio_value": 200000,
        "price": 6.2,
        "stop_price": 5.6,
        "adv_20d": 900000,
    }
    resp = client.post("/api/sizing", json=args, headers=POST_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == T.size_position(**args)
    assert "SIZING" in body["text"]
    assert body["disclaimer"]


def test_a_refusal_is_promoted_into_the_envelope(client):
    args = {
        "instrument": "MYX:1155",
        "portfolio_value": 200000,
        "price": 5.0,
        "stop_price": 6.0,  # stop above entry
        "adv_20d": 900000,
    }
    body = client.post("/api/sizing", json=args, headers=POST_HEADERS).json()
    assert body["ok"] is True  # a refusal is a successful answer
    assert body["refusal"] is not None
    assert body["refusal"]["reason"].startswith("REFUSED")
    assert body["text"] == T.size_position(**args)


def test_why_supplied_returns_parity(client):
    args = {"instrument": "MYX:1155", "instrument_return": -0.03, "market_return": -0.01}
    body = client.post("/api/why", json=args, headers=POST_HEADERS).json()
    assert body["text"] == T.why_did_it_move(
        instrument="MYX:1155", instrument_return=-0.03, market_return=-0.01
    )
    assert "unexplained" in body["text"]


def test_plan_parity_including_the_price_target_refusal(client):
    body = client.post(
        "/api/plan", json={"question": "what price will it hit next month"}, headers=POST_HEADERS
    ).json()
    assert body["refusal"] is not None
    assert body["text"] == T.plan_question("what price will it hit next month")


def test_thesis_parity(client):
    args = {
        "instrument": "MYX:1155",
        "breakers": [
            {"statement": "NIM under 2.0", "query": "nim<2.0", "store": "kb_filings"},
            {"statement": "CASA under 22", "query": "casa<22", "store": "kb_filings"},
        ],
        "stance": "accumulate",
    }
    body = client.post("/api/thesis", json=args, headers=POST_HEADERS).json()
    assert "THESIS" in body["text"] and "RED TEAM" in body["text"]
    assert body["text"] == T.compose_thesis(
        "MYX:1155",
        evidence=[],
        breakers=args["breakers"],
        stance="accumulate",
        horizon_months=12,
    )


def test_bad_arguments_are_422_not_500(client):
    resp = client.post(
        "/api/thesis",
        json={"instrument": "MYX:1155", "stance": "moon"},
        headers=POST_HEADERS,
    )
    assert resp.status_code == 422


def test_predictions_round_trip_through_the_store(client, tmp_path):
    args = {
        "instrument": "MYX:1155",
        "direction": 1,
        "horizon_days": 63,
        "confidence": 0.6,
        "thesis": "NIM stabilises",
    }
    logged = client.post("/api/predictions", json=args, headers=POST_HEADERS).json()
    assert "logged MYX:1155-" in logged["text"]
    listing = client.get("/api/predictions").json()
    assert listing["data"]["counts"]["pending"] == 1
    assert listing["data"]["pending"][0]["statement"] == "NIM stabilises"


def test_hypotheses_round_trip(client):
    created = client.post(
        "/api/hypotheses",
        json={"title": "Banks re-rate", "thesis": "NIM stabilises above 2.25%"},
        headers=POST_HEADERS,
    ).json()
    assert "registered H-" in created["text"]
    listing = client.get("/api/hypotheses").json()
    assert listing["data"][0]["status"] == "exploring"


def test_agents_come_from_the_registry(client):
    body = client.get("/api/agents").json()
    assert len(body["data"]) == 16
    a0 = next(a for a in body["data"] if a["id"] == "a0_supervisor")
    assert a0["eval_suite"]


def test_config_carries_the_hard_bounds(client):
    body = client.get("/api/config").json()
    assert body["data"]["base_currency"] == "MYR"
    assert body["data"]["hard_bounds"]


def test_backend_names_the_stub_when_keyless(client):
    body = client.get("/api/backend").json()
    assert body["data"]["is_stub"] is True
    assert "NOT a model" in body["text"]


def test_calibration_parity(client):
    body = client.get("/api/calibration").json()
    assert body["text"] == T.calibration_status()


# --- the POST guard -------------------------------------------------------------


def test_a_post_without_the_header_is_403(client):
    resp = client.post("/api/plan", json={"question": "why"})
    assert resp.status_code == 403
    assert "X-Requested-With" in resp.json()["refusal"]["reason"]


def test_gets_need_no_header(client):
    assert client.get("/api/health").status_code == 200


def test_security_headers_are_present(client):
    resp = client.get("/api/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


# --- deployment shape -----------------------------------------------------------


def test_the_server_binds_loopback_only():
    from web import serve

    assert serve.HOST == "127.0.0.1"


def test_trace_run_ids_are_sanitised(client):
    resp = client.get("/api/trace/runs/..%2f..%2fetc")
    assert resp.status_code == 404
