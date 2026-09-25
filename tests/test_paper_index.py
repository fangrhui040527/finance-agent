"""The index book: a passive, equal-weight basket of a whole index, beside decided and control.

The control holds equal lots of the nine-name watchlist, which at USD 1,000
and 100-share Bursa lots is three names. The index book holds every fundable
member of a published list at equal value on a notional large enough for
whole lots, under the control's own phases, floor, fees and rebalance clock.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.market.cache import PriceCache
from core.market.feed import ChainedFeed, YahooFeed
from engines.paper.book import mark
from engines.paper.index import COST_HEADROOM, INDEX_NOTIONAL_USD, REASON, index_units
from engines.paper.report import status
from engines.paper.rules import Fundable, phase_for
from engines.paper.settings import PaperSettings
from engines.paper.store import CONTROL, DECIDED, INDEX, PaperStore
from engines.paper.universe import (
    AGREES,
    DISAGREES,
    UNVERIFIED,
    check,
    load_universe,
    name_verdict,
)
from tests.conftest import PAPER_PRICES, SyntheticFeed, opener_for

S = PaperSettings(start_date=date(2026, 3, 2))
RAMP_DAY = date(2026, 3, 16)  # Monday of week 3
FULL_DAY = date(2026, 4, 13)  # Monday of week 7

#: Twelve members at prices spread the way Bursa's are: a few sen to RM 90.
MEMBERS = {
    "MYX:1155": ("Malayan Banking", 10.50),
    "MYX:5347": ("Tenaga Nasional", 13.60),
    "MYX:5183": ("Petronas Chemicals Group", 4.16),
    "MYX:4707": ("Nestle (Malaysia)", 90.0),
    "MYX:5296": ("MR D.I.Y. Group", 1.60),
    "MYX:0166": ("Inari Amertron", 2.40),
    "MYX:5398": ("Gamuda", 4.90),
    "MYX:7084": ("QL Resources", 4.30),
    "MYX:6012": ("Maxis", 3.50),
    "MYX:4715": ("Genting Malaysia", 2.30),
    "MYX:5211": ("Sunway", 4.70),
    "MYX:1066": ("RHB Bank", 6.30),
}


def _at(d: date, h=9, m=30):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _universe_file(tmp: Path, members=MEMBERS) -> Path:
    lines = ["as_of: 2026-03-01", 'source: "test"', "names:"]
    lines += [f'  - {{id: "{iid}", name: "{n}", segment: T}}' for iid, (n, _) in members.items()]
    path = tmp / "tiny.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _Named:
    """A synthetic feed that also says what name each code is listed under."""

    def __init__(self, inner, names: dict[str, str]):
        self.inner, self.names = inner, names
        self.name = getattr(inner, "name", "named")
        self.source_used = getattr(inner, "source_used", "named")

    def fetch(self, *a, **k):
        return self.inner.fetch(*a, **k)

    def listed_name(self, iid):
        return self.names.get(iid)


@pytest.fixture
def index_env(paper_env):
    prices = {**PAPER_PRICES, **{iid: p for iid, (_, p) in MEMBERS.items()}}
    paper_env.feed = SyntheticFeed(date(2025, 9, 1), date(2026, 7, 31), prices=prices)
    path = _universe_file(paper_env.tmp)
    paper_env.universe = path
    return paper_env


def _open(env, day=RAMP_DAY, notional=INDEX_NOTIONAL_USD, universe=None):
    env.store.open_book(
        INDEX,
        day,
        notional,
        {"universe": str(universe or env.universe), "universe_names": len(MEMBERS)},
    )


def _mark(env, d, slot="bursa_close"):
    return mark(env.store, env.cfg, env.feed, env.fx, day=d, slot=slot, now=_at(d))


def _fund(iid, lot_usd, lot=100, error=""):
    lot_usd = Decimal(str(lot_usd))
    return Fundable(
        iid,
        "MYR",
        lot,
        lot_usd / lot * 4,
        date(2026, 3, 13),
        lot_usd,
        lot_usd / INDEX_NOTIONAL_USD,
        0 if error else int(Decimal(250_000) // lot_usd),
        Decimal("0.003"),
        error,
    )


# -- the shipped universe -------------------------------------------------------------


def test_the_shipped_fbm100_file_is_a_hundred_distinct_bursa_codes():
    u = load_universe()
    assert len(u.members) == 100 == len(set(u.ids))
    assert all(iid.startswith("MYX:") and iid[4:].isalnum() or "SS" in iid for iid in u.ids)
    segments = [m.segment for m in u.members]
    assert segments.count("KLCI") == 30 and segments.count("MID70") == 70
    # Not yet checked against Bursa's list, and the file says so where a reader looks.
    assert "memory" in u.source


@pytest.mark.parametrize(
    "body, message",
    [
        ("names: []\n", "empty"),
        ('names:\n  - {id: "1155", name: "Maybank"}\n', "market prefix"),
        (
            'names:\n  - {id: "MYX:1155", name: "A"}\n  - {id: "MYX:1155", name: "B"}\n',
            "listed twice",
        ),
        ('names:\n  - {id: "MYX:1155"}\n', "needs an `id` and a `name`"),
        ("names: [\n", "not YAML"),
    ],
)
def test_a_universe_that_would_shrink_or_double_the_benchmark_is_refused(tmp_path, body, message):
    p = tmp_path / "u.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_universe(p)


# -- the name check -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected, listed, verdict",
    [
        ("Malayan Banking", "MALAYAN BANKING BHD", AGREES),
        ("Malayan Banking", "MAYBANK", AGREES),
        ("Nestle (Malaysia)", "Nestlé (Malaysia) Berhad", AGREES),
        ("MR D.I.Y. Group", "MR D.I.Y. GROUP (M) BHD", AGREES),
        ("Petronas Chemicals Group", "PCHEM", AGREES),
        ("Malakoff Corporation", "Lotte Chemical Titan Holding Berhad", DISAGREES),
        ("Maxis", "Mah Sing Group Berhad", DISAGREES),
        ("Public Bank", None, UNVERIFIED),
        ("Public Bank", "", UNVERIFIED),
    ],
)
def test_the_listed_name_catches_another_company_not_another_spelling(expected, listed, verdict):
    assert name_verdict(expected, listed) == verdict


def test_check_reads_the_name_each_feed_recorded(tmp_path):
    u = load_universe(_universe_file(tmp_path))
    feed = _Named(None, {"MYX:1155": "Malayan Banking Berhad", "MYX:6012": "Mah Sing Group"})
    rows = {r.member.instrument_id: r for r in check(u, feed)}
    assert rows["MYX:1155"].verdict == AGREES
    assert rows["MYX:6012"].verdict == DISAGREES and rows["MYX:6012"].excluded
    assert rows["MYX:5347"].verdict == UNVERIFIED and not rows["MYX:5347"].excluded


# -- sizing ----------------------------------------------------------------------------------


def test_equal_value_in_whole_lots_never_past_the_ramp_ceiling():
    funds = [_fund(f"MYX:{i:04d}", 250 + 37 * i) for i in range(1, 101)]
    funds.append(_fund("MYX:4707", 2250))  # the dearest lot on the board
    equity = INDEX_NOTIONAL_USD
    units = index_units(funds, equity, phase_for(RAMP_DAY, S), S)
    assert len(units) == 101, "every fundable member holds at least one lot"
    lot_usd = {f.instrument_id: f.lot_usd for f in funds}
    values = {iid: lot_usd[iid] * u / 100 for iid, u in units.items()}
    assert all(u % 100 == 0 for u in units.values())
    ceiling = Decimal("0.40") * equity * (1 - COST_HEADROOM)  # the entry costs, paid up front
    assert sum(values.values()) <= ceiling
    target = ceiling / 101
    # Nearest whole lot: no name is off its equal share by more than half a lot.
    assert all(abs(v - target) <= lot_usd[iid] / 2 for iid, v in values.items())


def test_full_phase_doubles_the_invested_share_and_observe_holds_nothing():
    funds = [_fund(f"MYX:{i:04d}", 300 + 11 * i) for i in range(1, 51)]
    ramp = index_units(funds, INDEX_NOTIONAL_USD, phase_for(RAMP_DAY, S), S)
    full = index_units(funds, INDEX_NOTIONAL_USD, phase_for(FULL_DAY, S), S)
    spent = lambda u: sum(  # noqa: E731
        f.lot_usd * u.get(f.instrument_id, 0) / 100 for f in funds
    )
    assert spent(ramp) <= Decimal(400_000) < spent(full) <= Decimal(800_000)
    assert index_units(funds, INDEX_NOTIONAL_USD, phase_for(date(2026, 3, 3), S), S) == {}


def test_a_name_with_no_price_or_a_lot_above_the_cap_is_left_out():
    funds = [_fund("MYX:0001", 300), _fund("MYX:0002", 0, error="no bars"), _fund("MYX:0003", 400)]
    units = index_units(funds, INDEX_NOTIONAL_USD, phase_for(RAMP_DAY, S), S)
    assert set(units) == {"MYX:0001", "MYX:0003"}


# -- the store -------------------------------------------------------------------------------


def test_the_index_book_opens_once_and_only_beside_an_open_ledger(tmp_path, paper_env):
    empty = PaperStore(tmp_path / "empty.db")
    with pytest.raises(ValueError, match="paper init"):
        empty.open_book(INDEX, RAMP_DAY, Decimal(1000), {})
    empty.close()
    store = paper_env.store
    with pytest.raises(ValueError, match="opened by"):
        store.open_book(CONTROL, RAMP_DAY, Decimal(1000), {})
    with pytest.raises(ValueError, match="above zero"):
        store.open_book(INDEX, RAMP_DAY, Decimal(0), {})
    assert store.open_books() == (DECIDED, CONTROL) and not store.has_book(INDEX)
    store.open_book(INDEX, RAMP_DAY, INDEX_NOTIONAL_USD, {"universe": "x.yaml"})
    assert store.open_books() == (DECIDED, CONTROL, INDEX)
    assert store.initial_cash(INDEX) == INDEX_NOTIONAL_USD
    assert store.initial_cash(DECIDED) == Decimal(1000), "the decided book is untouched"
    assert store.book_opened_on(INDEX) == RAMP_DAY
    assert store.book_terms(INDEX) == {"universe": "x.yaml"}
    with pytest.raises(ValueError, match="no reset"):
        store.open_book(INDEX, FULL_DAY, INDEX_NOTIONAL_USD, {})


# -- marking ----------------------------------------------------------------------------------


def test_a_ledger_without_the_index_book_marks_two_books_as_before(index_env):
    r = _mark(index_env, RAMP_DAY)
    assert set(r.marks) == {DECIDED, CONTROL} and not r.index_targets and not r.index_summary


def test_the_index_rebalances_at_its_first_mark_and_fills_at_the_next_open(index_env):
    env = index_env
    _open(env)
    r = _mark(env, RAMP_DAY)
    assert r.exit_code == 0, r.render()
    assert set(r.marks) == {DECIDED, CONTROL, INDEX}
    assert r.marks[INDEX].equity_usd == INDEX_NOTIONAL_USD
    assert len(r.index_targets) == 12 and all(t.reason == REASON for t in r.index_targets)
    assert all(t.book == INDEX for t in r.index_targets)
    assert "index rebalance: 12 target(s), 12 name(s) to hold" in r.render()

    r2 = _mark(env, date(2026, 3, 17))
    held = env.store.state(INDEX).positions
    assert len(held) == 12 and all(p.units % 100 == 0 for p in held)
    m = r2.marks[INDEX]
    # Sized under the 40% ceiling at the decision's closes; marked at the next
    # close, after a session's drift - the same as the control.
    invested = m.positions_usd / m.equity_usd
    assert Decimal("0.35") < invested <= Decimal("0.41")
    spent = -sum((c.cash_delta_usd for c in env.store.changes(INDEX)), Decimal(0))
    assert spent <= Decimal("0.41") * INDEX_NOTIONAL_USD
    weights = [Decimal(p["weight"]) for p in m.positions]
    assert max(weights) - min(weights) < Decimal("0.01"), "equal value, to within a lot"
    # A hundred opens are counted on the page, not listed.
    assert "open 12" in r2.render() and "MYX:4707" not in r2.render().split("index")[1][:200]
    # The two books the user trades by are exactly what they were.
    assert env.store.state(DECIDED).positions == ()
    assert all(p.instrument_id in env.cfg.watchlist for p in env.store.state(CONTROL).positions)

    r3 = _mark(env, date(2026, 3, 18))
    assert not r3.index_targets, "not again until the month or the phase turns"


def test_a_new_month_and_a_new_phase_each_rebalance_the_index(index_env):
    env = index_env
    _open(env)
    _mark(env, RAMP_DAY)
    _mark(env, date(2026, 3, 17))
    assert len(_mark(env, date(2026, 4, 1)).index_targets) == 12, "a new month"
    _mark(env, date(2026, 4, 2))
    r = _mark(env, FULL_DAY)
    assert len(r.index_targets) == 12, "a new phase"
    filled = _mark(env, date(2026, 4, 14))
    m = env.store.latest_mark(INDEX)
    assert Decimal("0.75") < m.positions_usd / m.equity_usd <= Decimal("0.81")
    # Sized inside the ceiling by the entry costs, so the cash floor refuses
    # none of them: the order entries are applied in decides nothing.
    assert [a.status for a in filled.applied[INDEX]] == ["applied"] * 12


def test_a_session_before_the_index_opened_marks_only_the_two_books(index_env):
    env = index_env
    _open(env, day=date(2026, 3, 18))
    r = _mark(env, RAMP_DAY)
    assert set(r.marks) == {DECIDED, CONTROL} and env.store.marks(INDEX) == []
    assert set(_mark(env, date(2026, 3, 18)).marks) == {DECIDED, CONTROL, INDEX}


def test_a_code_listed_as_another_company_is_left_out_and_said(index_env):
    env = index_env
    env.feed = _Named(env.feed, {"MYX:6012": "Mah Sing Group Berhad", "MYX:1155": "MAYBANK"})
    _open(env)
    r = _mark(env, RAMP_DAY)
    ids = {t.instrument_id for t in r.index_targets}
    assert "MYX:6012" not in ids and "MYX:1155" in ids and len(ids) == 11
    assert "MYX:6012 (Mah Sing Group Berhad)" in r.index_summary


def test_a_member_with_no_price_is_skipped_and_a_held_one_is_kept(index_env):
    env = index_env
    _open(env)
    _mark(env, RAMP_DAY)
    _mark(env, date(2026, 3, 17))
    del env.feed.series["MYX:5398"]  # the cache loses Gamuda's bars
    r = _mark(env, date(2026, 4, 1))
    assert "MYX:5398" not in {t.instrument_id for t in r.index_targets}
    assert "held but unpriced, left as held: MYX:5398" in r.index_summary
    assert env.store.state(INDEX).position("MYX:5398") is not None


def test_nothing_priced_writes_nothing_so_the_next_mark_tries_again(index_env, tmp_path):
    env = index_env
    _open(env, universe=_universe_file(tmp_path, {"MYX:9999": ("Nobody", 1.0)}))
    r = _mark(env, RAMP_DAY)
    assert r.index_targets == [] and "no member is priced" in r.index_summary
    assert env.store.last_rebalance(INDEX, REASON) is None


def test_a_broken_universe_file_costs_the_rebalance_not_the_marks(index_env, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("names: [\n", encoding="utf-8")
    _open(index_env, universe=bad)
    r = _mark(index_env, RAMP_DAY)
    assert set(r.marks) == {DECIDED, CONTROL, INDEX}
    assert any("index: rebalance not written" in p for p in r.problems)


def test_status_shows_the_index_over_its_own_window(index_env):
    env = index_env
    assert status(env.store, env.cfg, env.feed, env.fx, day=RAMP_DAY).index is None
    _mark(env, date(2026, 3, 13))
    _open(env)
    _mark(env, RAMP_DAY)
    _mark(env, date(2026, 3, 17))
    st = status(env.store, env.cfg, env.feed, env.fx, day=date(2026, 3, 17))
    line = st.index
    assert line is not None and line.names_held == 12 and line.members == 12
    assert line.universe == "TINY" and line.opened_on == RAMP_DAY
    assert line.index_return == line.equity_usd / INDEX_NOTIONAL_USD - 1
    assert line.decided_return == 0, "the decided book held cash over the window"
    text = st.render()
    assert "index book: TINY, equal weight, opened 2026-03-16" in text
    assert "a scale for percentages, not money" in text
    assert f"since {RAMP_DAY}: index" in text
    j = json.loads(json.dumps(st.as_json(), default=str))["index"]
    assert j["names_held"] == 12 and j["since"] == "2026-03-16"


def test_the_pack_counts_the_index_day_instead_of_listing_it(index_env):
    from knowledge.paper.pack import build_paper_pack

    env = index_env
    _open(env)
    _mark(env, RAMP_DAY)
    _mark(env, date(2026, 3, 17))
    text = build_paper_pack(
        env.cfg,
        date(2026, 3, 17),
        store=env.store,
        feed=env.feed,
        fx=env.fx,
        learning_path=env.tmp / "learning.db",
        previous_dir=None,
    )
    assert "### index" in text and "- 12 position change(s): open 12; fees USD" in text
    assert "| index | 2026-03-17 |" in text


# -- the listed name, captured from Yahoo -------------------------------------------------------


YAHOO_KL = json.dumps(
    {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "MYR",
                        "symbol": "6012.KL",
                        "gmtoffset": 28800,
                        "longName": "Maxis Berhad",
                        "shortName": "MAXIS",
                    },
                    "timestamp": [1767316500, 1767575700],  # 09:15 KL, 2026-01-02 / 05
                    "indicators": {
                        "quote": [
                            {
                                "open": [3.5, 3.52],
                                "high": [3.55, 3.56],
                                "low": [3.48, 3.5],
                                "close": [3.52, 3.54],
                                "volume": [1e6, 9e5],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }
)


def test_yahoo_keeps_the_name_it_lists_a_code_under_and_asks_for_the_range_given(tmp_path):
    cache = PriceCache(tmp_path / "c.db", today=lambda: "2026-01-05")
    seen: list = []
    feed = YahooFeed(opener=opener_for(YAHOO_KL, seen), cache=cache, range_="2y")
    feed.fetch("MYX:6012")
    assert "range=2y" in seen[0].full_url
    assert cache.name("yahoo", "6012.KL") == "Maxis Berhad"
    assert feed.listed_name("MYX:6012") == "Maxis Berhad"
    assert ChainedFeed([feed]).listed_name("MYX:6012") == "Maxis Berhad"
    assert YahooFeed(cache=cache).range == "5y", "the default is unchanged"
    assert feed.listed_name("MYX:1155") is None


# -- the command line -----------------------------------------------------------------------


def test_paper_init_index_opens_once_on_an_open_ledger(tmp_path, capsys, monkeypatch):
    import ask

    monkeypatch.setenv("FINPLANET_OFFLINE", "1")
    db = str(tmp_path / "p.db")
    assert ask.main(["paper", "init", "--index", "--db", db, "--date", "2026-09-25"]) == 2
    assert "NO BOOK" in capsys.readouterr().err
    assert ask.main(["paper", "init", "--db", db, "--start", "2026-09-08"]) == 0
    capsys.readouterr()
    assert ask.main(["paper", "init", "--index", "--db", db, "--date", "2026-09-25"]) == 0
    out = capsys.readouterr().out
    assert "opened the index book" in out and "notional USD 1,000,000.00" in out
    assert "100 names" in out and "engines/paper/data/fbm100.yaml" in out
    with PaperStore(db) as store:
        assert store.book_opened_on(INDEX) == date(2026, 9, 25)
        terms = store.book_terms(INDEX)
        assert terms["universe"] == "engines/paper/data/fbm100.yaml"
        assert terms["universe_names"] == 100 and terms["start_date"] == "2026-09-08"
        assert store.initial_cash(DECIDED) == Decimal(1000)
    assert ask.main(["paper", "init", "--index", "--db", db, "--date", "2026-09-26"]) == 2
    assert "no reset" in capsys.readouterr().err
    assert ask.main(["paper", "init", "--index", "--db", db, "--notional", "lots"]) == 2


class _Cached(_Named):
    def fetched_at(self, iid):
        return _at(date(2026, 9, 25)) if iid in self.names else None


def test_paper_universe_prints_both_names_and_exits_3_on_another_company(
    tmp_path, capsys, monkeypatch
):
    import ask

    path = _universe_file(tmp_path)
    names = {"MYX:1155": "Malayan Banking Berhad", "MYX:6012": "Maxis Berhad"}
    monkeypatch.setattr(ask, "default_feed", lambda: _Cached(None, names))
    args = ["paper", "universe", "--db", str(tmp_path / "none.db"), "--universe", str(path)]
    assert ask.main(args) == 0
    out = capsys.readouterr().out
    assert "UNIVERSE  12 names as of 2026-03-01 (test)  - no index book opened yet" in out
    assert "listed as Malayan Banking Berhad" in out
    assert "12 names: 2 with a cached price; names agree 2, DISAGREE 0, unverified 10" in out
    names["MYX:6012"] = "Mah Sing Group Berhad"
    assert ask.main(args) == 3
    assert "DISAGREES: the code lists another company" in capsys.readouterr().out


class _Recording:
    """Answers one bar for anything and remembers what it was asked."""

    name = "recording"
    source_used = "recording"

    def __init__(self, fail=()):
        self.asked: list[str] = []
        self.fail = set(fail)

    def fetch(self, iid, start=None, end=None):
        from core.market.feed import NoData
        from core.market.prices import Bar, PriceSeries

        self.asked.append(iid)
        if iid in self.fail:
            raise NoData(f"{iid}: nothing listed")
        return PriceSeries(iid, [Bar(date(2026, 9, 25), 1.0, 2.0, 0.5, 1.5, 100.0)])

    def fetched_at(self, iid):
        return None


def test_prices_book_fetches_the_index_members_only_once_the_book_is_open(
    tmp_path, capsys, monkeypatch
):
    from dataclasses import replace

    import ask
    from core.config import load as load_config

    db = tmp_path / "p.db"
    cfg = load_config()
    cfg = replace(
        cfg,
        watchlist=("MYX:1155",),
        holdings=(),
        paper=replace(cfg.paper, database=str(db)),
    )
    book, index = _Recording(), _Recording(fail={"MYX:6012"})
    monkeypatch.setattr(ask, "load_config", lambda: cfg)
    monkeypatch.setattr(ask, "_feed", lambda: ChainedFeed([book]))
    monkeypatch.setattr(ask, "_peer_lookup", lambda: None)
    monkeypatch.setattr(ask, "_index_feed", lambda feed: index)
    monkeypatch.setattr(ask, "INDEX_FETCH_PAUSE", 0)

    assert ask.main(["prices", "--book"]) == 0
    assert index.asked == [] and "index" not in capsys.readouterr().out.split("peers")[-1]

    with PaperStore(db) as store:
        store.init_books(cfg.paper, date(2026, 9, 8))
        store.open_book(
            INDEX,
            date(2026, 9, 25),
            INDEX_NOTIONAL_USD,
            {"universe": str(_universe_file(tmp_path)), "universe_names": 12},
        )
    assert ask.main(["prices", "--book"]) == 0, "an index member's failure is not the book's"
    out = capsys.readouterr().out
    # MYX:1155 is a book name: fetched once, by the book loop, at full history.
    assert "MYX:1155" not in index.asked and len(index.asked) == 11
    assert "index          10 of 11 members not already above cached" in out
    assert "(12 in the universe, range 2y); failed: MYX:6012" in out
