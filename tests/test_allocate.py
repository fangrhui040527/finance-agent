"""Phase B: capital split across nominated names, or a refusal with the reason.

The allocator answers the question AFTER choosing names. It never chooses
them, and it never constructs a position - a position needs two written
breakers, and an allocation is a capital answer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engines.risk.concentration import Limits
from engines.sizing.allocate import Candidate, allocate
from markets.registry import get as market_get

RT = market_get("XKLS").fee_schedule.round_trip


def c(
    iid: str, sector: str = "bank", price="10.00", stop="9.20", adv="20000000", **kw
) -> Candidate:
    return Candidate(
        instrument_id=iid,
        price=Decimal(price),
        stop_price=Decimal(stop),
        adv_20d=Decimal(adv),
        sector=sector,
        country="MY",
        mic="XKLS",
        lot_size=100,
        round_trip_cost_at=RT,
        **kw,
    )


def six(sectors: list[str] | None = None) -> list[Candidate]:
    sectors = sectors or ["bank", "telco", "consumer", "energy", "plantation", "reit"]
    return [c(f"MYX:100{i}", s) for i, s in enumerate(sectors)]


# --- the happy path ------------------------------------------------------------------


def test_six_names_are_funded_within_every_limit():
    a = allocate(Decimal("200000"), six())
    assert not a.refusal
    assert len(a.lines) == 6
    assert all(x.units % 100 == 0 for x in a.lines)  # whole board lots
    assert all(x.weight <= 0.08 + 1e-9 for x in a.lines)  # single-name cap
    assert a.deployed <= Decimal("200000")
    assert a.cash == Decimal("200000") - a.deployed


def test_the_split_reports_its_binding_cap_per_name():
    a = allocate(Decimal("200000"), six())
    assert {x.binding_cap for x in a.lines} == {"concentration"}
    assert "bound by concentration" in a.explain()


def test_the_output_refuses_to_be_read_as_a_set_of_positions():
    text = allocate(Decimal("200000"), six()).explain()
    assert "CAPITAL split, not a set of positions" in text
    assert "two falsifiable breakers" in text


def test_liquidity_binds_a_thin_name():
    names = six()
    names[0] = c("MYX:1000", "bank", adv="100000")  # 5% of ADV is only 5,000
    a = allocate(Decimal("200000"), names)
    thin = next(x for x in a.lines if x.instrument_id == "MYX:1000")
    assert thin.binding_cap == "liquidity"


# --- refusals ------------------------------------------------------------------------


def test_too_few_names_is_refused_not_concentrated():
    a = allocate(Decimal("200000"), six()[:2])
    assert "below the 5-position floor" in a.refusal
    assert not a.lines


def test_no_capital_is_refused():
    assert "no investable capital" in allocate(Decimal("0"), six()).refusal


def test_no_candidates_is_refused():
    assert "no candidates were nominated" in allocate(Decimal("200000"), []).refusal


def test_capital_too_small_to_fund_anything_says_which_cap_stopped_it():
    a = allocate(Decimal("3000"), six())
    assert "could be funded at this capital level" in a.refusal
    assert any("does not fund one 100-share lot" in x.reason for x in a.excluded)


def test_an_effective_bets_floor_refuses_a_book_that_is_one_bet():
    """With perfect correlation six names behave as one, and the arithmetic
    that says 'six positions' is exactly what the floor exists to distrust."""
    names = six()
    corr = [[1.0] * 6 for _ in range(6)]
    a = allocate(Decimal("200000"), names, corr=corr)
    # check() catches it first and words it better than the standalone gate:
    # "correlated holdings are one bet wearing several names".
    assert "effective_bets" in a.refusal
    assert "one bet" in a.refusal


# --- per-name exclusions, each with a reason ------------------------------------------


def test_a_stop_above_entry_is_excluded_by_name():
    names = six()
    names[0] = c("MYX:1000", "bank", price="10.00", stop="10.50")
    a = allocate(Decimal("200000"), names)
    assert any(
        x.instrument_id == "MYX:1000" and "stop is at or above entry" in x.reason
        for x in a.excluded
    )


def test_a_foreign_name_without_a_rate_is_excluded_not_guessed():
    names = six()
    names[0] = Candidate(
        instrument_id="XNAS:NVDA",
        price=Decimal("120"),
        stop_price=Decimal("108"),
        adv_20d=Decimal("9000000"),
        sector="tech",
        country="US",
        currency="USD",
        lot_size=1,
        mic="XNAS",
    )
    a = allocate(Decimal("200000"), names)
    reason = next(x.reason for x in a.excluded if x.instrument_id == "XNAS:NVDA")
    assert "no MYR rate supplied" in reason


def test_a_name_below_the_cost_floor_is_excluded_with_the_number():
    """Scaled small enough, a position cannot pay for its own round trip."""
    names = [
        c(f"MYX:200{i}", s, price="50.00")
        for i, s in enumerate(["bank", "telco", "consumer", "energy", "plantation", "reit"])
    ]
    a = allocate(Decimal("30000"), names)
    assert a.excluded
    assert any(
        "minimum economic position" in x.reason or "does not fund one" in x.reason
        for x in a.excluded
    )
    assert any("cap of MYR" in x.reason for x in a.excluded)  # the number that stopped it


# --- the trim loop --------------------------------------------------------------------


def test_a_sector_breach_is_trimmed_rather_than_refused():
    """Six banks at the single-name cap would be 40% of one sector against a
    25% limit; trimming to fit is the right answer, refusing would not be."""
    names = [c(f"MYX:300{i}", "bank") for i in range(6)]
    a = allocate(Decimal("200000"), names)
    assert not a.refusal, a.refusal
    sector_weight = sum(x.weight for x in a.lines)
    assert sector_weight <= 0.25 + 1e-9
    assert len(a.lines) == 6  # trimmed, not dropped


def test_the_trim_loop_terminates_on_an_impossible_book():
    """A limit trimming cannot satisfy must end as a refusal, not a hang."""
    tight = Limits(single_name=0.08, sector=0.01, min_positions=1, min_effective_bets=3.0)
    a = allocate(Decimal("200000"), [c(f"MYX:400{i}", "bank") for i in range(6)], limits=tight)
    assert a.refusal or all(x.weight <= 0.01 + 1e-9 for x in a.lines)


# --- the surfaces ----------------------------------------------------------------------

SPECS = [
    "MYX:1155:10.68:9.90:20000000:bank",
    "MYX:1023:6.40:5.95:18000000:telco",
    "MYX:5296:2.10:1.95:9000000:consumer",
    "MYX:6012:4.55:4.20:12000000:energy",
    "MYX:4197:7.80:7.20:15000000:plantation",
    "MYX:1961:22.40:21.00:8000000:reit",
]


def test_the_mcp_tool_allocates_and_discloses_a_typed_figure():
    from mcp_server import tools as T

    text = T.allocate_capital(names=SPECS, portfolio_value=200000)
    assert "ALLOCATION over 6 name(s)" in text
    assert "capital was SUPPLIED, not derived" in text
    assert "Not financial advice" in text


def test_the_mcp_tool_refuses_with_no_names_and_says_why():
    from mcp_server import tools as T

    text = T.allocate_capital(names=[], portfolio_value=200000)
    assert "NO CANDIDATES" in text
    assert "does not choose them" in text


def test_a_malformed_candidate_is_a_tool_error():
    from mcp_server import tools as T
    from mcp_server.protocol import ToolError

    with pytest.raises(ToolError, match="MIC:CODE:PRICE:STOP:ADV:SECTOR"):
        T.allocate_capital(names=["MYX:1155:10"], portfolio_value=200000)


def test_price_and_adv_are_required_unless_measured():
    from mcp_server import tools as T
    from mcp_server.protocol import ToolError

    with pytest.raises(ToolError, match="required unless"):
        T.allocate_capital(names=["MYX:1155::9.90::bank"], portfolio_value=200000)


def test_the_tool_is_on_the_wire():
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "allocate_capital" in {t["name"] for t in resp["result"]["tools"]}


def test_the_cli_allocates(capsys):
    import ask

    args = ["allocate", "--portfolio", "200000"]
    for spec in SPECS:
        args += ["--name", spec]
    assert ask.main(args) == 0
    out = capsys.readouterr().out
    assert "ALLOCATION over 6 name(s)" in out


def test_the_cli_refuses_without_names(capsys):
    import ask

    assert ask.main(["allocate", "--portfolio", "200000"]) == 2
    assert "does not choose them" in capsys.readouterr().err


def test_web_allocate_matches_the_mcp_tool():
    from fastapi.testclient import TestClient

    from mcp_server import tools as T
    from web.app import create_app

    client = TestClient(create_app())
    body = client.post(
        "/api/allocate",
        json={"names": SPECS, "portfolio_value": 200000},
        headers={"X-Requested-With": "FinPlanet"},
    ).json()
    assert body["text"] == T.allocate_capital(names=SPECS, portfolio_value=200000)


def test_undeployed_cash_states_the_arithmetic_that_caused_it():
    """6 names x an 8% cap cannot absorb more than 48%; a user reading a large
    cash balance with no explanation reads it as a bug."""
    a = allocate(Decimal("200000"), six())
    note = " ".join(a.notes)
    assert "8% single-name cap" in note
    assert "48% of capital" in note
    assert "raising the cap concentrates" in note
