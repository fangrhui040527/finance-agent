"""Reading an account, and the four ways that can go wrong quietly.

Every test here runs against a double. Nothing opens a socket, nothing needs
the optional futu-api package, and nothing needs a moomoo account - which is
also the honest limitation: these prove the LOGIC is right, not that moomoo
returns what this code expects. Only a real run against a live OpenD can do
that, and this environment cannot make one.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.broker.account import AccountSnapshot, BrokerError, Position, to_instrument_id
from core.broker.moomoo import MoomooAccountFeed

D = Decimal
RET_OK, RET_ERROR = 0, -1


class FakeFrame:
    """Stands in for the pandas DataFrame futu-api returns."""

    def __init__(self, records):
        self._records = records

    def to_dict(self, how):
        assert how == "records"
        return list(self._records)


class FakeContext:
    """A gateway that returns what we tell it to, and records that it closed."""

    def __init__(self, positions=(), account=({"cash": "100.00"},), fail=None):
        self._positions, self._account, self._fail = positions, account, fail
        self.closed = False

    def position_list_query(self):
        if self._fail == "positions":
            return RET_ERROR, "gateway not connected"
        return RET_OK, FakeFrame(self._positions)

    def accinfo_query(self):
        if self._fail == "account":
            return RET_ERROR, "not logged in"
        return RET_OK, FakeFrame(self._account)

    def close(self):
        self.closed = True


def feed(ctx):
    return MoomooAccountFeed(open_context=lambda: ctx)


BURSA_ROW = {
    "code": "MY.1155",
    "qty": "2000",
    "cost_price": "10.68",
    "market_val": "21360.00",
    "currency": "MYR",
}


# --- the happy path ---------------------------------------------------------------


def test_a_position_arrives_in_this_systems_own_vocabulary():
    """The broker says MY.1155. Everything downstream says MYX:1155. If the
    translation did not happen here it would happen nowhere, and the id would
    miss the alias map and inherit the DEFAULT cost floor."""
    snap = feed(FakeContext(positions=[BURSA_ROW])).snapshot()
    assert [p.instrument_id for p in snap.positions] == ["MYX:1155"]
    assert snap.positions[0].units == D(2000)
    assert snap.positions[0].avg_cost == D("10.68")
    assert snap.cash == D("100.00")
    assert snap.source == "moomoo:MY"


def test_the_snapshot_says_where_it_came_from():
    """A figure read from a broker and one typed into config.toml are
    different kinds of claim, and the difference has to survive to the screen."""
    snap = feed(FakeContext(positions=[BURSA_ROW])).snapshot()
    assert "moomoo" in snap.source
    assert snap.as_of.tzinfo is not None, "an as-of without a timezone is not an as-of"


def test_the_connection_is_closed_even_when_the_read_fails():
    """OpenD has a connection limit. A leaked socket per snapshot presents
    later as an intermittent failure to read a portfolio, which is the worst
    shape of bug to debug."""
    ctx = FakeContext(fail="positions")
    with pytest.raises(BrokerError):
        feed(ctx).snapshot()
    assert ctx.closed


# --- the four quiet failures ------------------------------------------------------


def test_a_broken_link_is_not_an_empty_account():
    """THE one that matters. An empty position list is the input to every
    concentration and rebalancing figure in this system. A dead gateway that
    read as "you own nothing" would not crash - it would quietly re-plan a
    portfolio around a book that does not exist."""
    with pytest.raises(BrokerError, match="gateway not connected"):
        feed(FakeContext(fail="positions")).snapshot()


def test_an_account_that_genuinely_holds_nothing_returns_a_snapshot():
    """The other half of the same rule: empty is a real answer and must not
    be dressed up as a failure either."""
    snap = feed(FakeContext(positions=[])).snapshot()
    assert snap.is_empty and snap.positions == () and snap.cash == D("100.00")


def test_a_missing_number_is_refused_rather_than_zeroed():
    """Broker payloads carry 'N/A' where a figure is unavailable. A zero cost
    basis is not a small error - it reads as a free position."""
    row = dict(BURSA_ROW, cost_price="N/A")
    with pytest.raises(BrokerError, match="cost_price"):
        feed(FakeContext(positions=[row])).snapshot()


def test_an_unmapped_market_is_refused_rather_than_passed_through():
    row = dict(BURSA_ROW, code="XX.0001")
    with pytest.raises(BrokerError, match="no instrument mapping"):
        feed(FakeContext(positions=[row])).snapshot()


def test_a_closed_position_is_not_a_holding():
    """The broker keeps listing a code after it is sold, at qty 0."""
    snap = feed(
        FakeContext(positions=[BURSA_ROW, dict(BURSA_ROW, code="MY.5296", qty="0")])
    ).snapshot()
    assert [p.instrument_id for p in snap.positions] == ["MYX:1155"]


def test_a_short_position_is_refused_because_nothing_downstream_models_one():
    row = dict(BURSA_ROW, qty="-100")
    with pytest.raises(ValueError, match="Short positions"):
        feed(FakeContext(positions=[row])).snapshot()


def test_a_gateway_that_answers_in_the_wrong_shape_says_so():
    class Nonsense:
        def position_list_query(self):
            return "not a tuple"

        def accinfo_query(self):
            return RET_OK, FakeFrame([{"cash": "1"}])

    with pytest.raises(BrokerError, match="expected"):
        feed(Nonsense()).snapshot()


def test_the_optional_dependency_is_named_when_it_is_missing():
    """A bare ImportError would send an operator looking in the wrong place."""
    from core.broker.moomoo import _default_context

    with pytest.raises(BrokerError, match="futu-api"):
        _default_context("MY", "127.0.0.1", 11111)


# --- the vocabulary ---------------------------------------------------------------


@pytest.mark.parametrize(
    "broker,ours", [("MY.1155", "MYX:1155"), ("US.NVDA", "XNAS:NVDA"), ("HK.00700", "XHKG:00700")]
)
def test_code_translation(broker, ours):
    assert to_instrument_id(broker) == ours


def test_a_code_with_no_market_is_refused():
    with pytest.raises(BrokerError, match="expected MARKET.CODE"):
        to_instrument_id("1155")


def test_a_position_needs_a_real_currency():
    with pytest.raises(ValueError, match="ISO code"):
        Position("MYX:1155", D(1), D(1), D(1), "ringgit")


def test_the_snapshot_is_frozen():
    """A portfolio that can be edited in place after it is read is a portfolio
    whose provenance claim is worthless."""
    from dataclasses import FrozenInstanceError

    snap = AccountSnapshot((), D(0), "MYR", datetime.now(UTC), "test")
    with pytest.raises(FrozenInstanceError):
        snap.cash = D(1)  # type: ignore[misc]


# --- through the CLI --------------------------------------------------------------
#
# These patch the GATEWAY, not `snapshot`, so the real snapshot code path runs
# end to end - the translation, the refusals and the closing are all exercised.


def _run(args, capsys):
    import ask

    code = ask.main(args)
    return code, capsys.readouterr()


def _gateway(monkeypatch, ctx=None, error=None):
    import core.broker.moomoo as m

    def fake(market, host, port):
        if error:
            raise BrokerError(error)
        return ctx

    monkeypatch.setattr(m, "_default_context", fake)


def test_the_cli_reports_holdings_and_offers_a_paste_line(capsys, monkeypatch):
    _gateway(monkeypatch, FakeContext(positions=[BURSA_ROW]))
    code, out = _run(["positions"], capsys)
    assert code == 0
    assert "MYX:1155" in out.out and "moomoo:MY" in out.out
    assert 'id = "MYX:1155"' in out.out, "the point is a line you can paste into config.toml"


def test_the_cli_separates_a_dead_link_from_an_empty_account(capsys, monkeypatch):
    """Exit 3 and stderr for a failure; exit 0 and a plain sentence for an
    account that really holds nothing. The distinction the feed draws, carried
    all the way to the screen."""
    _gateway(monkeypatch, error="gateway not connected")
    code, out = _run(["positions"], capsys)
    assert code == 3 and "unavailable" in out.err

    _gateway(monkeypatch, FakeContext(positions=[]))
    code, out = _run(["positions"], capsys)
    assert code == 0 and "holds nothing" in out.out


def test_the_cli_never_writes_config(capsys, monkeypatch):
    """A holdings list feeds every concentration figure here. Changing it must
    be a decision with a diff, not a side effect of looking."""
    before = pathlib.Path("config.toml").read_text(encoding="utf-8")
    _gateway(monkeypatch, FakeContext(positions=[BURSA_ROW]))
    _run(["positions"], capsys)
    assert pathlib.Path("config.toml").read_text(encoding="utf-8") == before
