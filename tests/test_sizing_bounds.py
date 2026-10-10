"""Every sizing surface holds the same limits and prices the same account.

Each test fails on the code before 2026-10-08:

  * size_position, allocate_capital and rebalance_book checked their limits
    only for being positive, so single_name_limit=1.0 put the whole book in
    one name;
  * size_position costed the venue's schedule, not the configured broker's,
    and approved positions below the account's own cost floor;
  * allocate tested broker-priced costs against the venue's floor, refusing
    every US name with a USD 100,000,000 "minimum";
  * rebalance summed USD holdings into MYR as bare numbers;
  * the trim loop cut the heaviest name in the book, not in the breach.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from core.config import Holding
from engines.risk.concentration import Limits
from engines.sizing.allocate import Candidate, allocate
from engines.sizing.rebalance import rebalance
from markets.brokers import cost_at
from mcp_server import tools as T
from mcp_server.protocol import ToolError
from web.app import create_app

POST_HEADERS = {"X-Requested-With": "FinPlanet"}
ARGS = dict(instrument="MYX:1155", portfolio_value=100000, price=10, stop_price=9.5, adv_20d=1e9)


@pytest.mark.parametrize(
    "override",
    [
        {"single_name_limit": 1.0},
        {"single_name_limit": 0.10},
        {"risk_per_trade": 0.5},
        {"participation": 1.0},
    ],
)
def test_size_position_refuses_a_limit_past_its_bound(override):
    with pytest.raises(ToolError):
        T.size_position(**ARGS, **override)


def test_a_call_can_tighten_the_single_name_cap():
    out = T.size_position(**ARGS, single_name_limit=0.05)
    assert "SIZING" in out


def test_allocate_and_rebalance_refuse_a_loosened_cap_too():
    with pytest.raises(ToolError):
        T.allocate_capital(names=[], portfolio_value=100000, single_name_limit=0.5)
    with pytest.raises(ToolError):
        T.rebalance_book(names=[], risk_per_trade=0.3)


def test_the_web_surface_answers_a_loosened_cap_with_422(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.learning.store.DEFAULT_PATH", tmp_path / "learning.db")
    client = TestClient(create_app())
    for bad in ({"single_name_limit": 1.0}, {"risk_per_trade": 0.5}, {"participation": 0.5}):
        resp = client.post("/api/sizing", json={**ARGS, **bad}, headers=POST_HEADERS)
        assert resp.status_code == 422, bad


def test_size_position_prices_the_configured_broker_like_the_cli():
    """moomoo_my on Nasdaq: 35 bps round trip, so USD 1,800 of NVDA is under the floor."""
    out = T.size_position(
        "XNAS:NVDA",
        portfolio_value=100000,
        price=180,
        stop_price=170,
        adv_20d=5e7,
        fx_myr_per_unit=4.2,
    )
    assert "moomoo_my" in out
    assert "NO POSITION" in out, out


def _us(iid: str, price: str) -> Candidate:
    px = Decimal(price)
    return Candidate(
        instrument_id=iid,
        price=px,
        stop_price=px * Decimal("0.9"),
        adv_20d=Decimal("1e8"),
        sector=iid,
        country="US",
        currency="USD",
        lot_size=1,
        mic="XNAS",
        fx_base_per_quote=Decimal("4.2"),
        round_trip_cost_at=cost_at("XNAS", "moomoo_my", px),
        broker="moomoo_my",
    )


def test_allocate_tests_a_broker_priced_us_name_against_the_brokers_floor():
    names = [_us(f"XNAS:N{i}", "180") for i in range(6)]
    a = allocate(Decimal("500000"), names, limits=Limits(country=0.9, non_base_currency=0.9))
    assert not a.refusal, a.refusal
    assert not any("100,000,000" in x.reason for x in a.excluded)


def test_rebalance_values_a_usd_holding_in_myr():
    book = [Holding(id=f"MYX:100{i}", units=2000, sector=f"s{i}") for i in range(5)]
    book.append(Holding(id="XNAS:AAPL", units=100, sector="tech"))
    prices = {h.id: Decimal("5") for h in book[:5]} | {"XNAS:AAPL": Decimal("250")}
    advs = {h.id: Decimal("1e8") for h in book}
    r = rebalance(book, [], prices, advs, fx_rates={"USD": Decimal("4.2")})
    assert r.equity == Decimal("155000.0")
    aapl = next(p for p in r.positions_now if p.instrument_id == "XNAS:AAPL")
    assert aapl.weight == pytest.approx(105000 / 155000), "USD 25,000 at 4.2 is MYR 105,000"


def test_rebalance_refuses_a_foreign_holding_with_no_rate():
    book = [Holding(id="MYX:1001", units=2000), Holding(id="XNAS:AAPL", units=100)]
    prices = {"MYX:1001": Decimal("5"), "XNAS:AAPL": Decimal("250")}
    r = rebalance(book, [], prices, {})
    assert r.refusal and "USD" in r.refusal and "rate" in r.refusal


def _my(iid: str, sector: str, price: str) -> Candidate:
    return Candidate(
        instrument_id=iid,
        price=Decimal(price),
        stop_price=Decimal(price) * Decimal("0.95"),
        adv_20d=Decimal("1e12"),
        sector=sector,
        country="MY",
        mic="XKLS",
        lot_size=100,
    )


def test_the_trim_cuts_inside_the_breached_sector_not_the_heaviest_name_overall():
    banks = [_my(f"MYX:10{i}", "bank", "7.90") for i in range(5)]
    tech = [_my(f"MYX:20{i}", f"tech{i}", "1.00") for i in range(3)]
    a = allocate(Decimal("1000000"), banks + tech, limits=Limits(country=1.0))
    assert not a.refusal, a.refusal
    bank_weight = sum(x.weight for x in a.lines if x.instrument_id.startswith("MYX:10"))
    assert bank_weight <= 0.25 + 1e-9
    assert all(
        x.weight == pytest.approx(0.08) for x in a.lines if x.instrument_id.startswith("MYX:20")
    ), "the tech names were never in breach"
