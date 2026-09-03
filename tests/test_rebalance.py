"""Phase C: what to change versus what is held, with the cost of changing it.

A rebalance is the allocation question asked against a book that already
exists. Two rules decide whether the answer is usable: a trade worth less than
its own round trip is not a trade, and a book that cannot be valued cannot be
rebalanced - which is an answer, not a crash.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import Holding
from engines.sizing.allocate import Candidate
from engines.sizing.rebalance import rebalance
from markets.registry import get as market_get

RT = market_get("XKLS").fee_schedule.round_trip

PRICES = {
    "MYX:1155": Decimal("10.00"),
    "MYX:1023": Decimal("6.40"),
    "MYX:5296": Decimal("2.10"),
    "MYX:6012": Decimal("4.55"),
    "MYX:4197": Decimal("7.80"),
    "MYX:1961": Decimal("22.40"),
}
ADVS = dict.fromkeys(PRICES, Decimal("20000000"))
SECTORS = ["bank", "telco", "consumer", "energy", "plantation", "reit"]


def book(units: dict[str, int]) -> list[Holding]:
    return [
        Holding(
            iid,
            Decimal(n),
            avg_cost=PRICES[iid],
            stop=PRICES[iid] * Decimal("0.92"),
            sector=SECTORS[i],
        )
        for i, (iid, n) in enumerate(units.items())
    ]


def nominate(iid: str, sector: str) -> Candidate:
    return Candidate(
        instrument_id=iid,
        price=PRICES[iid],
        stop_price=PRICES[iid] * Decimal("0.92"),
        adv_20d=ADVS[iid],
        sector=sector,
        country="MY",
        mic="XKLS",
        lot_size=100,
        round_trip_cost_at=RT,
    )


#: A MYR 500,000 portfolio holding exactly what the limits allow. Six Malaysian
#: names would each take 8% of capital under the single-name cap, but six of
#: them is 48% of one COUNTRY against a 40% limit, so the trim loop cuts every
#: line back to roughly 6.6%. This is what "already at target" looks like, and
#: it is the only book shape where every delta should read `hold`.
CAPITAL = Decimal("500000")
SIX = {
    "MYX:1155": 3300,
    "MYX:1023": 5200,
    "MYX:5296": 15900,
    "MYX:6012": 7300,
    "MYX:4197": 4300,
    "MYX:1961": 1400,
}


# --- valuing the book ------------------------------------------------------------


def test_the_book_is_valued_at_live_prices():
    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    assert r.equity == sum(PRICES[i] * n for i, n in SIX.items())
    assert not r.refusal


def test_a_holding_without_units_cannot_be_valued_and_says_so():
    holdings = book(SIX) + [Holding("MYX:7113")]
    r = rebalance(
        holdings,
        [],
        PRICES | {"MYX:7113": Decimal("5")},
        ADVS | {"MYX:7113": Decimal("1e7")},
        CAPITAL,
    )
    assert any("no units" in n for n in r.notes)
    # excluded from equity, not guessed at zero
    assert r.equity == sum(PRICES[i] * n for i, n in SIX.items())


def test_an_empty_book_with_no_names_refuses():
    r = rebalance([], [], PRICES, ADVS)
    assert "nothing to rebalance" in r.refusal


def test_a_missing_price_is_named_not_skipped():
    r = rebalance(
        book(SIX), [], {k: v for k, v in PRICES.items() if k != "MYX:1155"}, ADVS, CAPITAL
    )
    assert "MYX:1155" in r.refusal
    assert "no price" in r.refusal


def test_a_book_below_the_implied_minimum_says_what_the_minimum_is():
    """MYR 4,705.88 is the smallest Bursa position that pays for its own round
    trip; an 8% cap puts it inside a portfolio of at least MYR 58,824. A book
    under that cannot be rebalanced, and the number is the useful part."""
    small = dict.fromkeys(PRICES, 100)
    r = rebalance(book(small), [], PRICES, ADVS)
    assert "cost floor" in r.refusal
    assert "58,824" in r.refusal
    assert "4,705.88" in r.refusal


# --- the deltas ------------------------------------------------------------------


def test_an_evenly_held_book_at_target_is_all_holds():
    """Six names sized at their own target produce no trades worth making."""
    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    assert {d.action for d in r.deltas} == {"hold"}, [
        (d.instrument_id, d.action, d.units_now, d.units_target) for d in r.deltas
    ]
    assert len(r.deltas) == 6


def test_an_oversized_name_is_trimmed_with_the_cost_of_trimming():
    heavy = dict(SIX)
    heavy["MYX:1155"] = 20000  # MYR 200,000, far above the 8% cap
    r = rebalance(book(heavy), [], PRICES, ADVS, CAPITAL)
    d = next(x for x in r.deltas if x.instrument_id == "MYX:1155")
    assert d.action == "trim"
    assert d.units_target < d.units_now
    assert d.cost > 0
    assert "MYR" in r.explain()


def test_a_nominated_name_not_held_opens():
    held = {k: v for k, v in SIX.items() if k != "MYX:1961"}
    r = rebalance(book(held), [nominate("MYX:1961", "reit")], PRICES, ADVS, CAPITAL)
    d = next(x for x in r.deltas if x.instrument_id == "MYX:1961")
    assert d.action == "open"
    assert d.units_now == 0 and d.units_target > 0


def test_a_trade_worth_less_than_its_round_trip_is_held_instead():
    """The defect this pins: a 1-lot nudge worth MYR 210 against a MYR 4,705.88
    minimum economic trade reads as a small correction worth making."""
    nudged = dict(SIX)
    nudged["MYX:5296"] = 15900 + 100  # a hair over target, worth MYR 210
    r = rebalance(book(nudged), [], PRICES, ADVS, CAPITAL)
    d = next(x for x in r.deltas if x.instrument_id == "MYX:5296")
    assert d.units_now != 15900  # the drift is real
    assert d.action == "hold"  # and not worth correcting
    assert "costs more to make than the drift it corrects" in d.reason
    assert "4,705.88" in d.reason  # the number that decided it
    assert d.cost == 0 and d.units_target == d.units_now


def test_a_position_through_its_own_stop_is_named_before_anything_else():
    """A live price at or below the stop is not a data error, which is how the
    allocator would report it. It is the user's own rule, already triggered."""
    prices = PRICES | {"MYX:1155": Decimal("8.00")}  # stop sits at 9.20
    r = rebalance(book(SIX), [], prices, ADVS, CAPITAL)
    d = next(x for x in r.deltas if x.instrument_id == "MYX:1155")
    assert d.action == "stop hit"
    assert d.units_target == d.units_now  # no target was computed for it
    assert any(n.startswith("STOP BREACHED: MYX:1155") for n in r.notes)
    text = r.explain()
    assert text.index("STOP BREACHED") < text.index("bound by")  # reported first


def test_a_breached_stop_survives_a_refusal():
    """It is most needed exactly when no target could be built."""
    prices = {k: Decimal("8.00") if k == "MYX:1155" else v for k, v in PRICES.items()}
    r = rebalance(book(SIX)[:5], [], prices, ADVS, Decimal("40000"))
    assert r.refusal
    assert "STOP BREACHED: MYX:1155" in r.explain()


def test_a_held_name_that_cannot_be_funded_is_named_not_dropped():
    thin = dict(SIX)
    holdings = book(thin)
    holdings[0] = Holding(
        "MYX:1155", Decimal(3300), avg_cost=PRICES["MYX:1155"], stop=Decimal("9.20"), sector="bank"
    )
    r = rebalance(holdings, [], PRICES, {**ADVS, "MYX:1155": Decimal("1000")}, CAPITAL)
    assert any("held but not fundable" in n for n in r.notes)


def test_a_malaysia_only_book_is_bounded_by_the_country_limit_and_says_so():
    """The most consequential thing this surfaces for a Bursa-only investor:
    the 40% country limit stops deployment long before capital does, and the
    line that reports `concentration` hides which limit decided the number."""
    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    assert {d.binding_cap for d in r.deltas} == {"country"}
    assert any("bounded by the country limit" in n for n in r.notes)


def test_every_delta_carries_its_binding_cap_and_cost():
    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    for d in r.deltas:
        assert d.binding_cap
        assert d.cost >= 0


# --- what it refuses to do -------------------------------------------------------


def test_the_output_is_not_a_set_of_positions():
    text = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL).explain()
    assert "not a set of positions" in text.lower() or "CAPITAL" in text


def test_no_advice_verbs_survive_the_output_rail():
    from core.guardrails.policy import AdviceLanguagePolicy

    text = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL).explain().lower()
    for phrase in AdviceLanguagePolicy.BANNED:
        assert phrase not in text


def test_a_holding_with_no_stop_loses_the_risk_cap_and_the_output_says_which():
    holdings = [
        Holding(iid, Decimal(SIX[iid]), avg_cost=PRICES[iid], stop=None, sector=SECTORS[i])
        for i, iid in enumerate(PRICES)
    ]
    r = rebalance(holdings, [], PRICES, ADVS, CAPITAL)
    assert any("no stop" in n for n in r.notes)
    assert all(d.binding_cap != "risk" for d in r.deltas)


def test_positions_before_and_after_are_both_reported():
    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    assert len(r.positions_now) == 6
    assert r.positions_target  # the shape A12 will be run over
    # Weighed against the WHOLE portfolio, cash included - the denominator the
    # limits are defined over and the one allocate() sized against.
    assert abs(sum(p.weight for p in r.positions_now) - float(r.equity / CAPITAL)) < 1e-6


def test_the_target_shape_passes_the_limits_it_was_built_from():
    """Measured against deployed value instead of capital, a freshly built
    target reported single-name breaches at 16.7% and country at 100%."""
    from engines.risk.concentration import Limits, check

    r = rebalance(book(SIX), [], PRICES, ADVS, CAPITAL)
    assert check(list(r.positions_target), None, Limits()) == []


def test_capital_below_the_book_value_is_a_contradiction_that_is_named():
    r = rebalance(book(SIX), [], PRICES, ADVS, Decimal("10000"))
    assert any("is less than the book is worth" in n for n in r.notes)


# --- the surfaces ----------------------------------------------------------------


def _cfg_with_book(tmp_path):
    base = Path("config.toml").read_text(encoding="utf-8")
    rows = ", ".join(
        f'{{ id = "{iid}", units = 1000, avg_cost = {PRICES[iid]}, '
        f'stop = {PRICES[iid] * Decimal("0.92"):.2f}, sector = "{SECTORS[i]}" }}'
        for i, iid in enumerate(PRICES)
    )
    p = tmp_path / "config.toml"
    p.write_text(base.replace("holdings = []", f"holdings = [{rows}]"), encoding="utf-8")
    return p


def test_the_mcp_tool_refuses_an_empty_book_with_the_reason():
    from mcp_server import tools as T

    text = T.rebalance_book()
    assert "NOTHING TO REBALANCE" in text or "nothing to rebalance" in text
    assert "Not financial advice" in text


def test_the_mcp_tool_is_on_the_wire():
    from mcp_server.server import S

    resp = S.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "rebalance_book" in {t["name"] for t in resp["result"]["tools"]}


def test_the_cli_refuses_an_empty_book(capsys):
    import ask

    assert ask.main(["rebalance"]) == 2
    assert "holdings" in capsys.readouterr().err


def test_web_rebalance_matches_the_mcp_tool():
    from fastapi.testclient import TestClient

    from mcp_server import tools as T
    from web.app import create_app

    body = (
        TestClient(create_app())
        .post("/api/rebalance", json={}, headers={"X-Requested-With": "FinPlanet"})
        .json()
    )
    assert body["text"] == T.rebalance_book()


@pytest.mark.parametrize("units", [0, -1])
def test_a_nonpositive_holding_is_refused_at_load(tmp_path, units):
    from pathlib import Path

    from core.config import ConfigError, load

    base = Path("config.toml").read_text(encoding="utf-8")
    p = tmp_path / "config.toml"
    p.write_text(
        base.replace("holdings = []", f'holdings = [{{ id = "MYX:1155", units = {units} }}]'),
        encoding="utf-8",
    )
    if units < 0:
        with pytest.raises(ConfigError, match="cannot be negative"):
            load(p)
    else:
        assert load(p).book[0].units == 0


# --- the MCP path, with the feed stood in for ------------------------------------


class _Bar:
    def __init__(self, close: float) -> None:
        self.close = close


class _Series:
    def __init__(self, close: Decimal) -> None:
        self._close = close

    def raw(self):
        return [_Bar(float(self._close))]

    def adv(self, n: int) -> float:
        return 20_000_000.0


class _Feed:
    def __init__(self, missing: str = "") -> None:
        self._missing = missing

    def fetch(self, iid: str, end=None):
        if iid == self._missing:
            from core.market.feed import PriceFeedError

            raise PriceFeedError(f"no data for {iid}")
        return _Series(PRICES[iid])


def _stand_in(monkeypatch, tmp_path, missing: str = "", capital: str = "") -> None:
    """A config with a real book, and a feed that answers without a network."""
    import core.config as C
    from mcp_server import tools as T

    base = Path("config.toml").read_text(encoding="utf-8")
    rows = ", ".join(
        f'{{ id = "{iid}", units = {SIX[iid]}, avg_cost = {PRICES[iid]}, '
        f'stop = {PRICES[iid] * Decimal("0.92"):.2f}, sector = "{SECTORS[i]}" }}'
        for i, iid in enumerate(PRICES)
    )
    text = re.sub(r"^holdings = .*$", f"holdings = [{rows}]", base, count=1, flags=re.M)
    if capital:
        text = text.replace("liquid_assets = 0.0", f"liquid_assets = {capital}")
        text = text.replace("essential_monthly_spend = 0.0", "essential_monthly_spend = 3000.0")
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    real = C.load
    monkeypatch.setattr(C, "load", lambda path=None: real(p))
    monkeypatch.setattr(T, "_feed", lambda: _Feed(missing))


def test_the_mcp_tool_reports_deltas_and_the_shape_either_side(monkeypatch, tmp_path):
    from mcp_server import tools as T

    _stand_in(monkeypatch, tmp_path)
    text = T.rebalance_book(portfolio_value=500000)
    assert "REBALANCE  book MYR" in text
    assert "BEFORE" in text and "AFTER" in text
    assert "bound by" in text
    assert "Not financial advice" in text


def test_the_mcp_tool_defaults_capital_to_the_book_itself(monkeypatch, tmp_path):
    from mcp_server import tools as T

    _stand_in(monkeypatch, tmp_path)
    text = T.rebalance_book()
    assert "the book's own market value" in text


def test_an_unpriced_holding_stops_the_whole_answer(monkeypatch, tmp_path):
    from mcp_server import tools as T

    _stand_in(monkeypatch, tmp_path, missing="MYX:5296")
    text = T.rebalance_book(portfolio_value=500000)
    assert "NO PRICE for MYX:5296" in text
    assert "every weight would be wrong" in text


def test_from_plan_without_a_capital_block_says_so(monkeypatch, tmp_path):
    from mcp_server import tools as T

    _stand_in(monkeypatch, tmp_path)
    assert "NO PLAN" in T.rebalance_book(from_plan=True)


def test_from_plan_uses_the_waterfall_when_there_is_one(monkeypatch, tmp_path):
    from mcp_server import tools as T

    _stand_in(monkeypatch, tmp_path, capital="900000.0")
    text = T.rebalance_book(from_plan=True)
    assert "capital derived through the waterfall" in text


def test_the_cli_prints_what_the_tool_returns(monkeypatch, tmp_path, capsys):
    import ask

    _stand_in(monkeypatch, tmp_path)
    assert ask.main(["rebalance", "--portfolio", "500000"]) == 0
    assert "REBALANCE  book MYR" in capsys.readouterr().out
