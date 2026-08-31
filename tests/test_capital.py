"""Phase A: the capital plan reaches a user, and a typed figure says what it skips.

The defect this pins: the waterfall was complete, correct and unreachable, so
`size --portfolio <number>` made the caller supply the very number the
emergency floor, near-term goals and debt hurdle exist to produce - bypassing
all three on every real invocation, silently.
"""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import CapitalPlan, ConfigError, Goal, Holding, Liability, load

BASE = Path("config.toml").read_text(encoding="utf-8")


def _cfg(tmp_path: Path, **edits) -> Path:
    """A real config.toml with [capital] filled in."""
    s = BASE
    for old, new in edits.items():
        s = s.replace(old.replace("__", " "), new)
    p = tmp_path / "config.toml"
    p.write_text(s, encoding="utf-8")
    return p


PLAN = {
    "liquid_assets = 0.0": "liquid_assets = 120000.0",
    "essential_monthly_spend = 0.0": "essential_monthly_spend = 4500.0",
    "planned_monthly_contribution = 0.0": "planned_monthly_contribution = 2000.0",
    "goals = []": 'goals = [{ name = "car", amount = 30000, months_away = 18 }]',
    "liabilities = []": 'liabilities = [{ name = "card", balance = 8000, annual_rate = 0.17 }]',
}


def _write(tmp_path: Path, subs: dict) -> Path:
    s = BASE
    for old, new in subs.items():
        assert old in s, old
        s = s.replace(old, new)
    p = tmp_path / "config.toml"
    p.write_text(s, encoding="utf-8")
    return p


# --- config -----------------------------------------------------------------------


def test_the_shipped_config_parses_and_states_no_plan():
    cfg = load()
    assert isinstance(cfg.capital, CapitalPlan)
    assert cfg.capital.stated is False  # nothing entered is NOT a plan that says zero


def test_a_filled_plan_loads_with_its_goals_and_liabilities(tmp_path):
    cfg = load(_write(tmp_path, PLAN))
    assert cfg.capital.stated
    assert cfg.capital.liquid_assets == Decimal("120000.0")
    assert cfg.capital.goals == (Goal("car", Decimal("30000"), 18),)
    assert cfg.capital.liabilities == (Liability("card", Decimal("8000"), Decimal("0.17")),)


def test_a_percentage_written_as_seventeen_is_refused(tmp_path):
    subs = dict(PLAN)
    subs["liabilities = []"] = 'liabilities = [{ name = "card", balance = 8000, annual_rate = 17 }]'
    with pytest.raises(ConfigError, match="write 0.17 for 17%"):
        load(_write(tmp_path, subs))


def test_a_misspelled_capital_key_is_refused_not_ignored(tmp_path):
    subs = dict(PLAN)
    subs["liquid_assets = 120000.0"] = "liquid_asset = 120000.0"
    subs = {**PLAN, "liquid_assets = 0.0": "liquid_asset = 120000.0"}
    with pytest.raises(ConfigError, match="unknown \\[capital\\] keys"):
        load(_write(tmp_path, subs))


def test_a_goal_missing_a_field_is_refused(tmp_path):
    subs = {**PLAN, "goals = []": 'goals = [{ name = "car", amount = 30000 }]'}
    with pytest.raises(ConfigError, match="missing \\['months_away'\\]"):
        load(_write(tmp_path, subs))


def test_negative_money_is_refused(tmp_path):
    """HARD_BOUNDS catches it before the parser does - the bound is the better
    error because it names the permitted range and the reason for it."""
    subs = {**PLAN, "liquid_assets = 0.0": "liquid_assets = -5.0"}
    with pytest.raises(ConfigError, match="outside the permitted range"):
        load(_write(tmp_path, subs))


def test_a_negative_goal_is_refused_by_the_parser(tmp_path):
    """Goals are not in HARD_BOUNDS (they are a list), so the parser is the
    only thing standing between a negative goal and a raised floor."""
    subs = {**PLAN, "goals = []": 'goals = [{ name = "car", amount = -1, months_away = 6 }]'}
    with pytest.raises(ConfigError, match="cannot be negative"):
        load(_write(tmp_path, subs))


# --- holdings, old shape and new ----------------------------------------------------


def test_bare_ids_still_load(tmp_path):
    cfg = load(_write(tmp_path, {"holdings = []": 'holdings = ["MYX:1155", "XNAS:NVDA"]'}))
    assert cfg.holdings == ("MYX:1155", "XNAS:NVDA")
    assert cfg.book == (Holding("MYX:1155"), Holding("XNAS:NVDA"))
    assert all(not h.valued for h in cfg.book)  # no units: the book cannot be valued


def test_holdings_with_units_load(tmp_path):
    cfg = load(
        _write(
            tmp_path,
            {"holdings = []": 'holdings = [{ id = "MYX:1155", units = 1000, avg_cost = 9.80 }]'},
        )
    )
    assert cfg.holdings == ("MYX:1155",)  # every existing consumer is untouched
    h = cfg.book[0]
    assert h.valued and h.units == Decimal("1000") and h.avg_cost == Decimal("9.80")


def test_a_holding_typo_still_fails_at_load(tmp_path):
    with pytest.raises(ConfigError, match="no market prefix"):
        load(_write(tmp_path, {"holdings = []": 'holdings = [{ id = "1155", units = 10 }]'}))


def test_unknown_holding_keys_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load(_write(tmp_path, {"holdings = []": 'holdings = [{ id = "MYX:1155", qty = 10 }]'}))


def test_the_shipped_config_is_valid_toml_and_documents_capital():
    data = tomllib.loads(BASE)
    assert "capital" in data
    assert set(data["capital"]) == {
        "liquid_assets",
        "essential_monthly_spend",
        "planned_monthly_contribution",
        "goals",
        "liabilities",
    }


# --- the waterfall, through the one shared path -------------------------------------


def _ctx():
    from datetime import UTC, datetime

    from agents.base import AgentContext
    from core.guardrails.defaults import default_engine
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router

    engine = default_engine(load_registry("agents/registry.yaml").allowlist())
    return AgentContext(router=Router({}), engine=engine, now=datetime.now(UTC))


def test_plan_capital_applies_every_lock(tmp_path):
    from agents.portfolio.agents import plan_capital

    cfg = load(_write(tmp_path, PLAN))
    waterfall, _ = plan_capital(cfg, _ctx())
    # 120,000 - (4,500 x 6) - 30,000 - 8,000 - 2,000
    assert waterfall.investable == Decimal("53000.0")
    labels = [s.label for s in waterfall.steps]
    assert "Emergency floor (6m)" in labels[0]
    assert [s.locked for s in waterfall.steps][:3] == [True, True, True]


def test_an_unstated_plan_returns_none_not_zero(tmp_path):
    from agents.portfolio.agents import plan_capital

    waterfall, findings = plan_capital(load(), _ctx())
    assert waterfall is None and findings == []


def test_a_plan_that_leaves_nothing_says_zero_and_caveats_it(tmp_path):
    from agents.portfolio.agents import plan_capital

    subs = {**PLAN, "liquid_assets = 0.0": "liquid_assets = 20000.0"}
    cfg = load(_write(tmp_path, subs))
    waterfall, findings = plan_capital(cfg, _ctx())
    assert waterfall.investable == 0
    caveats = [c for f in findings for c in f.caveats]
    assert any("correct amount to invest today is zero" in c for c in caveats)


# --- the CLI ------------------------------------------------------------------------


def test_capital_without_a_plan_explains_the_bypass(capsys, monkeypatch):
    import ask

    monkeypatch.setenv("FINPLANET_NO_DOTENV", "1")
    assert ask.main(["capital"]) == 2
    out = capsys.readouterr().out
    assert "no [capital] plan" in out
    assert "bypasses the emergency floor" in out


def test_size_discloses_that_a_typed_figure_skipped_the_locks(capsys):
    import ask

    code = ask.main(
        [
            "size",
            "MYX:1155",
            "--portfolio",
            "400000",
            "--price",
            "10.68",
            "--stop",
            "9.90",
            "--adv",
            "22000000",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "capital was SUPPLIED, not derived" in out
    assert "were not applied" in out


def test_size_needs_one_of_the_two_capital_sources(capsys):
    import ask

    assert (
        ask.main(["size", "MYX:1155", "--price", "10.68", "--stop", "9.90", "--adv", "22000000"])
        == 2
    )
    assert "--from-plan" in capsys.readouterr().err


# --- the floor both surfaces must enforce -------------------------------------------


def test_the_cli_refuses_below_the_minimum_economic_position(capsys):
    """It printed the floor and then ignored it, recommending positions the MCP
    tool refused for the same inputs."""
    import ask

    code = ask.main(
        [
            "size",
            "MYX:1155",
            "--portfolio",
            "53000",
            "--price",
            "10.68",
            "--stop",
            "9.90",
            "--adv",
            "22000000",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "no position" in out
    assert "minimum economic position" in out
    assert "cannot pay for itself" in out


def test_the_two_surfaces_agree_on_the_floor():
    from mcp_server import tools as T

    text = T.size_position(
        instrument="MYX:1155",
        portfolio_value=53000,
        price=10.68,
        stop_price=9.90,
        adv_20d=22000000,
    )
    assert "NO POSITION" in text and "minimum economic position" in text


# --- MCP and web ---------------------------------------------------------------------


def test_the_mcp_tool_is_on_the_wire_and_says_no_plan():
    import json

    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "investable_capital" in {t["name"] for t in resp["result"]["tools"]}

    call = S.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "investable_capital", "arguments": {}},
        }
    )
    assert "error" not in call, json.dumps(call)[:300]
    text = call["result"]["content"][0]["text"]
    assert "NO PLAN" in text  # the shipped config states none
    assert "BYPASSES" in text


def test_size_position_discloses_the_supplied_figure():
    from mcp_server import tools as T

    text = T.size_position(
        instrument="MYX:1155",
        portfolio_value=400000,
        price=10.68,
        stop_price=9.90,
        adv_20d=22000000,
    )
    assert "capital was SUPPLIED, not derived" in text


def test_the_web_endpoint_carries_the_plan_and_the_book():
    from fastapi.testclient import TestClient

    from web.app import create_app

    body = TestClient(create_app()).get("/api/capital").json()
    assert body["ok"] is True
    assert body["data"]["stated"] is False
    for key in ("liquid_assets", "goals", "liabilities", "book", "emergency_months"):
        assert key in body["data"], key


def test_web_capital_text_matches_the_mcp_tool():
    from fastapi.testclient import TestClient

    from mcp_server import tools as T
    from web.app import create_app

    body = TestClient(create_app()).get("/api/capital").json()
    assert body["text"] == T.investable_capital()
