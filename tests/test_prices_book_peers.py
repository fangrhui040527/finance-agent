"""`prices --book` warms the graph peers of the book too, same market only, capped.

Until 2026-09-19 the collector's price step warmed the book and each market's
proxy and nothing else, so the sixteen peer rows that peer_set, the workup and
comps had pulled into the cache were never refetched: eleven US peers sat at
2026-09-02 and five Bursa peers at 2026-08-31 beside book rows dated yesterday.
The peers come off the graph, so the graph is scripted here; the feed records
what it was asked for.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

import ask
from core.config import load as load_config
from core.market.feed import ChainedFeed
from core.market.prices import Bar, PriceSeries

BOOK = ("MYX:1155", "XNAS:NVDA")
PEERS = {
    "MYX:1155": {"MYX:1023", "MYX:1295", "XNAS:JPM"},  # JPM is another market
    "XNAS:NVDA": {"XNAS:AMD", "MYX:1155"},  # 1155 is already in the book
}


class _Scripted:
    """Answers one bar for anything, and remembers the order it was asked in."""

    name = "scripted"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def fetch(self, iid, start=None, end=None):
        self.asked.append(iid)
        return PriceSeries(iid, [Bar(date(2026, 9, 18), 1.0, 2.0, 0.5, 1.5, 100.0)])


@pytest.fixture
def scripted(monkeypatch):
    feed = _Scripted()
    monkeypatch.setattr(ask, "_feed", lambda: ChainedFeed([feed]))
    monkeypatch.setattr(
        ask, "load_config", lambda: replace(load_config(), watchlist=BOOK, holdings=())
    )
    monkeypatch.setattr(ask, "_peer_lookup", lambda: lambda iid, asof: PEERS.get(iid, set()))
    return feed


def test_the_peers_of_every_book_name_are_fetched_after_the_book_and_its_proxies(scripted, capsys):
    assert ask.main(["prices", "--book"]) == 0
    out = capsys.readouterr().out
    # The book and the proxies first, exactly as before; then the peers.
    assert scripted.asked[:4] == ["MYX:1155", "XNAS:NVDA", "MYX:^KLSE", "XNAS:SPY"]
    assert set(scripted.asked[4:]) == {"MYX:1023", "MYX:1295", "XNAS:AMD"}
    # The other-market peer is not warmed, and a peer that is a book name is
    # fetched once, as the book name.
    assert "XNAS:JPM" not in scripted.asked
    assert scripted.asked.count("MYX:1155") == 1
    assert "peers          3 added from the graph (cap 24)" in out
    assert "peer of MYX:1155" in out and "peer of XNAS:NVDA" in out
    assert "cached         7 of 7  (2 book, 2 proxies, 3 peers)" in out


def test_the_cap_holds_and_is_shared_round_robin_across_the_book(scripted, monkeypatch, capsys):
    """A name with forty peers must not crowd out the other name's two: when the
    cap binds, each book name keeps its nearest peers."""
    many = {f"XNAS:P{i:02d}" for i in range(40)}
    monkeypatch.setattr(
        ask,
        "_peer_lookup",
        lambda: lambda iid, asof: many if iid == "XNAS:NVDA" else {"MYX:1023", "MYX:1295"},
    )
    assert ask.main(["prices", "--book"]) == 0
    peers = scripted.asked[4:]
    assert len(peers) == ask.PEER_WARM_CAP == 24
    assert {"MYX:1023", "MYX:1295"} <= set(peers)
    assert len([p for p in peers if p.startswith("XNAS:P")]) == 22
    assert "cached         28 of 28  (2 book, 2 proxies, 24 peers)" in capsys.readouterr().out


def test_no_graph_means_the_book_alone_and_says_so(scripted, monkeypatch, capsys):
    monkeypatch.setattr(ask, "_peer_lookup", lambda: None)
    assert ask.main(["prices", "--book"]) == 0
    out = capsys.readouterr().out
    assert scripted.asked == ["MYX:1155", "XNAS:NVDA", "MYX:^KLSE", "XNAS:SPY"]
    assert "peers          skipped: no graph built" in out
    assert "cached         4 of 4  (2 book, 2 proxies, 0 peers)" in out


def test_a_broken_graph_costs_the_peers_and_not_the_book(scripted, monkeypatch, capsys):
    def _boom(iid, asof):
        raise RuntimeError("graph.db is not a database")

    monkeypatch.setattr(ask, "_peer_lookup", lambda: _boom)
    assert ask.main(["prices", "--book"]) == 0
    captured = capsys.readouterr()
    assert scripted.asked == ["MYX:1155", "XNAS:NVDA", "MYX:^KLSE", "XNAS:SPY"]
    assert "peers          skipped: RuntimeError: graph.db is not a database" in captured.err
    assert "cached         4 of 4" in captured.out


def test_the_lookup_is_the_graph_when_one_is_built_and_none_when_it_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge.graph.build.DEFAULT_DB", str(tmp_path / "no-graph.db"))
    assert ask._peer_lookup() is None
    from mcp_server.tools import graph_peers

    monkeypatch.setattr("knowledge.graph.build.DEFAULT_DB", "data/graph.db")
    assert ask._peer_lookup() is graph_peers


def test_book_peers_are_ordered_deterministically_and_keyed_to_the_name_they_came_from():
    peers = ask._book_peers(
        list(BOOK), date(2026, 9, 19), lambda iid, asof: PEERS.get(iid, set()), cap=24
    )
    assert peers == {"MYX:1023": "MYX:1155", "XNAS:AMD": "XNAS:NVDA", "MYX:1295": "MYX:1155"}
    assert list(peers) == ["MYX:1023", "XNAS:AMD", "MYX:1295"]  # one per name per pass
