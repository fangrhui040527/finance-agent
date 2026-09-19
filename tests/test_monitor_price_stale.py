"""`price_stale`: a cached price whose fetch day trails its market's last session.

The 2026-09-19 finding: sixteen peer rows in data/price_cache.db were a
fortnight behind the book rows beside them, and no rule noticed, because the
collector was running (`sweep_silence` quiet) and the book was being marked
(`paper_stale` quiet). Every case here builds its own cache; the repository's
own is tracked and ages.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from core.config import load as load_config
from core.market.cache import PriceCache
from core.monitor import ALERT, WARN, _price_rules, evaluate
from core.provenance.ledger import ProvenanceLedger

CSV = "date,open,high,low,close,volume\n2026-09-18,1,2,0.5,1.5,100\n"
ERROR_PAGE = "<!DOCTYPE html><html><body>403 Forbidden</body></html>"

#: Saturday: both markets' last finished session is Friday 2026-09-18.
SATURDAY = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _cfg(**over):
    over.setdefault("watchlist", ("MYX:1155", "XNAS:NVDA"))
    over.setdefault("holdings", ())
    over.setdefault("corpus_db", "tests/no-such-corpus.db")
    over.setdefault("facts_db", "tests/no-such-facts.db")
    return replace(load_config(), **over)


def _cache(path: Path, rows: list[tuple[str, str, str]], body: str = CSV) -> str:
    """A cache holding one row per (feed, symbol, fetched_on)."""
    cache = PriceCache(path, today=lambda: "2026-09-19")
    cache.conn.executemany(
        "INSERT INTO price_csv (feed, symbol, fetched_on, body) VALUES (?,?,?,?)",
        [(feed, symbol, day, body) for feed, symbol, day in rows],
    )
    cache.conn.commit()
    cache.close()
    return str(path)


def _stale(alerts):
    return [a for a in alerts if a.rule == "price_stale"]


def test_a_fresh_book_and_a_book_one_session_behind_are_quiet(tmp_path):
    """One session behind is one missed run, which is lateness: the us_close
    slot may land in the next UTC day. Two is a fault."""
    path = _cache(
        tmp_path / "p.db",
        [("yahoo", "1155.KL", "2026-09-18"), ("yahoo", "NVDA", "2026-09-17")],
    )
    assert _price_rules(_cfg(), SATURDAY, path) == []


def test_a_stale_book_name_is_an_alert_that_names_it_and_the_next_step(tmp_path):
    path = _cache(
        tmp_path / "p.db",
        [("yahoo", "1155.KL", "2026-09-18"), ("yahoo", "NVDA", "2026-09-16")],
    )
    alerts = _stale(_price_rules(_cfg(), SATURDAY, path))
    assert len(alerts) == 1 and alerts[0].severity == ALERT
    assert alerts[0].title == (
        "1 cached price behind the market's last session: XNAS:NVDA (book, 2 sessions behind)"
    )
    assert "2 sessions behind, book allowed 1: XNAS:NVDA" in alerts[0].detail
    assert "`ask.py prices --book`" in alerts[0].next_step
    (row,) = alerts[0].evidence["stale"]
    assert row == {
        "name": "XNAS:NVDA",
        "symbol": "NVDA",
        "feed": "yahoo",
        "role": "book",
        "market": "XNAS",
        "fetched_on": "2026-09-16",
        "last_session": "2026-09-18",
        "sessions_behind": 2,
        "allowed": 1,
    }


def test_a_peer_gets_a_trading_week_and_is_a_warning_on_its_own(tmp_path):
    """The two-week-old peers of 2026-09-19, in miniature: CSX (US) and 1023.KL
    (Bursa) are in no book and behind no proxy, so they are peers, and the alert
    names both with their own market's session count."""
    path = _cache(
        tmp_path / "p.db",
        [
            ("yahoo", "1155.KL", "2026-09-18"),
            ("yahoo", "NVDA", "2026-09-18"),
            ("yahoo", "INTC", "2026-09-11"),  # Fri; 14-18 is five sessions: allowed
            ("yahoo", "CSX", "2026-09-02"),  # twelve sessions
            ("yahoo", "1023.KL", "2026-08-31"),  # fourteen
        ],
    )
    alerts = _stale(_price_rules(_cfg(), SATURDAY, path))
    assert len(alerts) == 1 and alerts[0].severity == WARN
    assert alerts[0].title == (
        "2 cached prices behind the market's last session: "
        "1023.KL (peer, 14 sessions behind), CSX (peer, 12 sessions behind)"
    )
    assert "INTC" not in alerts[0].title
    assert (
        "14 sessions behind, peer allowed 5: 1023.KL; 12 sessions behind, peer allowed 5: CSX"
        in (alerts[0].detail)
    )


def test_the_book_is_named_before_the_peers_and_makes_the_alert_an_alert(tmp_path):
    rows = [("yahoo", f"P{i}", "2026-09-01") for i in range(8)]  # eight peers, thirteen behind
    rows.append(("yahoo", "NVDA", "2026-09-15"))  # a book name, three behind
    path = _cache(tmp_path / "p.db", rows)
    (alert,) = _stale(_price_rules(_cfg(), SATURDAY, path))
    assert alert.severity == ALERT
    assert alert.title.startswith(
        "9 cached prices behind the market's last session: XNAS:NVDA (book, 3 sessions behind), P0"
    )
    assert alert.title.endswith(" and 3 more")
    assert [r["name"] for r in alert.evidence["stale"]][:2] == ["XNAS:NVDA", "P0"]


def test_a_weekend_is_not_staleness_and_neither_is_a_session_still_running(tmp_path):
    """Monday 09:00 UTC: Bursa shut at 08:45, so Monday is its last session and a
    Friday row is one behind; Nasdaq opens at 14:30, so ITS last session is still
    Friday and a Thursday row is one behind. Both quiet. By Monday's US close the
    Thursday row is two behind."""
    path = _cache(
        tmp_path / "p.db",
        [("yahoo", "1155.KL", "2026-09-18"), ("yahoo", "NVDA", "2026-09-17")],
    )
    monday_morning = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    assert _price_rules(_cfg(), monday_morning, path) == []
    monday_after_us_close = datetime(2026, 9, 21, 21, 30, tzinfo=UTC)
    (alert,) = _stale(_price_rules(_cfg(), monday_after_us_close, path))
    assert "XNAS:NVDA (book, 2 sessions behind)" in alert.title
    assert "MYX:1155" not in alert.title


def test_a_holiday_is_not_staleness(tmp_path, monkeypatch):
    """The adapters ship with no holidays, so the calendar is given one here:
    with Friday 18 shut, a Wednesday row is one session behind on Saturday."""
    from markets import registry
    from markets.xnas import XNAS

    path = _cache(tmp_path / "p.db", [("yahoo", "NVDA", "2026-09-16")])
    assert _stale(_price_rules(_cfg(), SATURDAY, path))  # two behind on the shipped calendar
    monkeypatch.setitem(registry._CACHE, "XNAS", XNAS(holidays=frozenset({date(2026, 9, 18)})))
    assert _price_rules(_cfg(), SATURDAY, path) == []


def test_the_proxy_is_judged_like_the_book(tmp_path):
    path = _cache(tmp_path / "p.db", [("yahoo", "^KLSE", "2026-09-16")])
    (alert,) = _stale(_price_rules(_cfg(), SATURDAY, path))
    assert alert.severity == ALERT
    assert "MYX:^KLSE (proxy, 2 sessions behind)" in alert.title


def test_a_name_is_judged_by_its_freshest_row_across_feeds(tmp_path):
    """The chain serves the first source that answers and leaves the other's row
    to age behind it. That row is not the one being read, so it is not a stop."""
    path = _cache(
        tmp_path / "p.db",
        [("stooq", "nvda.us", "2026-09-01"), ("yahoo", "NVDA", "2026-09-18")],
    )
    assert _price_rules(_cfg(), SATURDAY, path) == []


def test_an_error_page_and_an_unreadable_symbol_are_not_judged(tmp_path):
    """A 403 page under a symbol's name is not a price, and the cache drops it on
    its next read. A symbol no suffix table accounts for has no calendar to be
    judged against, and guessing one would judge it against the wrong market."""
    path = _cache(tmp_path / "p.db", [("yahoo", "CSX", "2026-08-01")], body=ERROR_PAGE)
    assert _price_rules(_cfg(), SATURDAY, path) == []
    path = _cache(
        tmp_path / "q.db", [("yahoo", "ABC.XX", "2026-08-01"), ("other", "CSX", "2026-08-01")]
    )
    assert _price_rules(_cfg(), SATURDAY, path) == []


def test_no_cache_file_is_a_no_op_that_creates_none(tmp_path):
    missing = tmp_path / "never" / "price_cache.db"
    assert _price_rules(_cfg(), SATURDAY, str(missing)) == []
    assert not missing.exists() and not missing.parent.exists()


def test_the_rule_is_registered_where_watch_and_open_alerts_read(tmp_path, monkeypatch):
    """`evaluate` is what `check` runs and `ask.py watch` records, so the rule
    has to be in it - and it reads the module default when no path is given."""
    monkeypatch.setattr("core.monitor.FEEDBACK_DIR", "tests/no-such-feedback")
    ledger = tmp_path / "led.db"
    ProvenanceLedger(ledger).close()
    path = _cache(tmp_path / "p.db", [("yahoo", "CSX", "2026-09-02")])

    alerts = evaluate(_cfg(), db=str(ledger), now=SATURDAY, price_cache=path)
    assert [a.rule for a in _stale(alerts)] == ["price_stale"]

    monkeypatch.setattr("core.monitor.PRICE_CACHE_DB", path)
    assert _stale(evaluate(_cfg(), db=str(ledger), now=SATURDAY))
    monkeypatch.setattr("core.monitor.PRICE_CACHE_DB", str(tmp_path / "absent.db"))
    assert not _stale(evaluate(_cfg(), db=str(ledger), now=SATURDAY))


def test_the_feeds_read_their_own_symbols_back_and_refuse_the_rest():
    from core.market.feed import StooqFeed, YahooFeed

    y, s = YahooFeed(), StooqFeed()
    assert y.instrument_of("1155.KL") == "XKLS:1155"
    assert y.instrument_of("CSX") == "XNAS:CSX"
    assert y.instrument_of("^KLSE") == "MYX:^KLSE"
    assert y.instrument_of("ABC.XX") is None
    assert s.instrument_of("nvda.us") == "XNAS:NVDA"
    assert s.instrument_of("1155.my") == "XKLS:1155"
    assert s.instrument_of("CSX") is None  # Stooq has no bare spelling
    # Round trip for every id the book writes.
    for iid in ("MYX:1155", "XNAS:NVDA", "MYX:^KLSE"):
        assert y.instrument_of(y.symbol_for(iid)) in (iid, iid.replace("MYX:", "XKLS:"))
