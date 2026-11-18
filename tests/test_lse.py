"""The four London Strategic Edge collectors (knowledge/sources/lse.py).

Offline: a fake opener answers each LSE path in the shape the `lse-data`
client's docs/api.md gives. No test reads the real API, and no test value is
real LSE data.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from knowledge.sources import catalog, lse
from knowledge.sources.base import KeyRejected, PlanExcluded, SourceError
from knowledge.sources.registry import collector_for
from tests.conftest import FakeResponse, http_error

NOW = datetime(2026, 11, 3, 21, 20, tzinfo=UTC)
SINCE = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
CLOCK = lambda: NOW  # noqa: E731
KEY = "lse_live_TESTKEY0000"


@pytest.fixture
def private(monkeypatch):
    monkeypatch.setenv(lse.PRIVATE_ENV, "true")


def router(routes: dict, calls: list | None = None):
    """An opener answering by URL path. A value may be a callable(query)."""

    def open_(req, timeout=None):
        url = urlparse(req.full_url)
        if calls is not None:
            calls.append((req.full_url, dict(req.header_items())))
        answer = routes.get(url.path)
        if callable(answer):
            answer = answer(parse_qs(url.query))
        if answer is None:
            raise http_error(404, '{"error":"data-unavailable"}', url=req.full_url)
        if isinstance(answer, Exception):
            raise answer
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return FakeResponse(body)

    return open_


def make(cls, opener):
    return cls(opener=opener, sleep=lambda s: None, key=KEY, clock=CLOCK)


# -- the licence guard ------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "false", "False", "1", "yes"])
@pytest.mark.parametrize(
    "cls", [lse.LseMalaysiaMacro, lse.LseCalendar, lse.LseOptions, lse.LsePrices]
)
def test_a_public_or_unknown_repository_is_a_skip_with_no_request(monkeypatch, cls, value):
    if value is None:
        monkeypatch.delenv(lse.PRIVATE_ENV, raising=False)
    else:
        monkeypatch.setenv(lse.PRIVATE_ENV, value)
    calls: list = []
    c = make(cls, router({}, calls))
    with pytest.raises(PlanExcluded, match="LSE's terms forbid"):
        c.collect(SINCE, ("XNAS:NVDA",), slot="us_close")
    assert calls == [] and c.requests == 0


def test_the_guard_comes_before_the_key(monkeypatch):
    """A public repository skips even with no key: never a key failure."""
    monkeypatch.delenv(lse.PRIVATE_ENV, raising=False)
    monkeypatch.delenv("LSE_API_KEY", raising=False)
    c = lse.LseMalaysiaMacro(opener=router({}), sleep=lambda s: None, clock=CLOCK)
    with pytest.raises(PlanExcluded):
        c.collect(SINCE)


def test_the_key_is_a_header_never_in_the_url(private):
    calls: list = []
    routes = {"/v3/macro/series": {"rows": [{"day": "2026-09-01", "value": 1.2}]}}
    make(lse.LseMalaysiaMacro, router(routes, calls)).collect(SINCE)
    assert calls
    for url, headers in calls:
        assert KEY not in url
        assert headers.get("X-api-key") == KEY


def test_a_refused_key_is_key_rejected(private):
    opener = router({"/v3/macro/series": http_error(401, '{"error":"invalid-key"}')})
    with pytest.raises(KeyRejected):
        make(lse.LseMalaysiaMacro, opener).collect(SINCE)


# -- 1. Malaysian macro -----------------------------------------------------------


def test_macro_stores_each_series_with_its_title(private):
    def series(q):
        code = q["code"][0]
        if code == "MY10Y":
            return {
                "rows": [
                    {"day": "2026-09-09", "close": "3.41"},
                    {"day": "2026-09-10", "close": "3.43"},
                ]
            }
        if code == "MY.inflation_rate":
            return {"rows": [{"day": "2026-09-01", "value": 1.3}, {"day": "bad", "value": 1}]}
        return {"rows": []}

    pull = make(lse.LseMalaysiaMacro, router({"/v3/macro/series": series})).collect(SINCE)
    by_id = {}
    for p in pull.series:
        by_id.setdefault(p.series_id, []).append(p)
    assert [p.value for p in by_id["LSE:MY10Y"]] == [Decimal("3.41"), Decimal("3.43")]
    assert by_id["LSE:MY10Y"][0].payload["title"].startswith("Malaysia 10Y")
    assert [p.obs_date for p in by_id["LSE:MY.inflation_rate"]] == [date(2026, 9, 1)]
    assert all(p.known_at == NOW.date() for p in pull.series)
    assert pull.requests == len(lse.MY_SERIES)


def test_one_missing_series_is_a_note_every_missing_is_a_failure(private):
    def one(q):
        return None if q["code"][0] == "MY.manufacturing_pmi" else {"rows": []}

    pull = make(lse.LseMalaysiaMacro, router({"/v3/macro/series": one})).collect(SINCE)
    assert any("MY.manufacturing_pmi" in n for n in pull.notes)
    with pytest.raises(SourceError, match="every series failed"):
        make(lse.LseMalaysiaMacro, router({})).collect(SINCE)


# -- 2. The calendar, including a prepared (202) export ----------------------------

CAL_CSV = (
    "id,datetime,region_code,event,period_hint,actual,previous,consensus,forecast\n"
    "101,2026-11-05T07:00:00Z,MY,Interest Rate Decision,,,2.75,2.75,\\N\n"
    "102,2026-11-03T04:00:00Z,MY,Exports YoY,SEP,6.1,5.0,4.2,\n"
    ",2026-11-03T04:00:00Z,MY,No id,,,,,\n"
)


def test_calendar_rows_become_macro_events(private):
    def export(q):
        assert q["dataset"] == ["economic_calendar"] and q["format"] == ["csv"]
        return CAL_CSV if q["country"] == ["MY"] else None  # US: no rows

    pull = make(lse.LseCalendar, router({"/v3/reference/export": export})).collect(SINCE)
    by_id = {e.event_id: e for e in pull.events}
    assert set(by_id) == {"101:scheduled", "102:print"}
    sched, printed = by_id["101:scheduled"], by_id["102:print"]
    assert sched.kind == "macro_release" and sched.instrument_id == "MACRO:MY"
    assert sched.payload["forecast"] is None and sched.payload["consensus"] == "2.75"
    assert printed.kind == "macro_print" and printed.title == "MY Exports YoY: 6.1 vs 4.2 expected"
    assert printed.announced_at == datetime(2026, 11, 3, 4, 0, tzinfo=UTC)
    assert any(n.startswith("US: no calendar rows") for n in pull.notes)


def test_a_prepared_export_is_polled_then_fetched(private):
    polls = {"n": 0}

    def status(q):
        polls["n"] += 1
        return {
            "job_id": "j1",
            "status": "ready" if polls["n"] >= 2 else "building",
            "download_url": "/v3/jobs/j1/file",
        }

    routes = {
        "/v3/reference/export": {"job_id": "j1", "status": "building", "status_url": "/v3/jobs/j1"},
        "/v3/jobs/j1": status,
        "/v3/jobs/j1/file": CAL_CSV,
    }
    pull = make(lse.LseCalendar, router(routes)).collect(SINCE)
    assert {e.event_id for e in pull.events} == {"101:scheduled", "102:print"}
    # MY: building, building, ready; US: ready on its first poll.
    assert polls["n"] == 3


def test_a_job_file_off_the_origin_is_refused(private):
    routes = {
        "/v3/reference/export": {
            "job_id": "j1",
            "status": "ready",
            "download_url": "https://elsewhere.example/file",
        },
    }
    with pytest.raises(SourceError, match="off the server's origin"):
        make(lse.LseCalendar, router(routes)).collect(SINCE)


def test_a_failed_job_fails_the_region(private):
    routes = {"/v3/reference/export": {"job_id": "j1", "status": "failed", "error": "boom"}}
    with pytest.raises(SourceError, match="every region failed"):
        make(lse.LseCalendar, router(routes)).collect(SINCE)


# -- 3. Options ---------------------------------------------------------------------


def contract(kind, strike, expiry, dte, iv, volume):
    return {
        "contract_type": kind,
        "strike": strike,
        "expiry": expiry,
        "dte": dte,
        "iv": iv,
        "volume_today": volume,
    }


CHAIN = [
    contract("call", 200, "2026-12-04", 31, 0.50, 100),
    contract("put", 200, "2026-12-04", 31, 0.54, 60),
    contract("call", 210, "2026-12-04", 31, 0.48, 40),
    contract("call", 200, "2026-11-27", 24, 0.70, 10),
    contract("put", 205, "2026-12-04", 31, None, 0),
]


def test_summarise_chain_takes_the_atm_strike_at_the_expiry_nearest_30_days():
    s = lse.summarise_chain(CHAIN, "201.5")
    assert s is not None
    assert s["atm"] == {"expiry": "2026-12-04", "dte": 31, "strike": "200", "legs": 2}
    assert s["atm_iv"] == Decimal("0.5200")
    assert (s["put_volume"], s["call_volume"]) == (60, 150)
    assert s["put_call"] == Decimal("0.4000")


def test_summarise_chain_with_no_strike_keeps_the_volume():
    rows = [contract("put", None, "x", 30, 0.4, 5), contract("call", None, "x", 30, 0.4, 10)]
    s = lse.summarise_chain(rows, "100")
    assert s is not None and s["atm_iv"] is None and s["put_call"] == Decimal("0.5000")


def test_summarise_chain_with_nothing_usable_is_none():
    assert lse.summarise_chain([], "100") is None
    assert lse.summarise_chain([contract("put", 1, "x", 30, None, 0)], None) is None


def test_options_store_two_lines_per_name(private):
    def chain(q):
        assert q["min_dte"] == ["20"] and q["max_dte"] == ["45"]
        if q["underlying"] == ["MSFT"]:
            return None
        return {"underlying_price": 201.5, "greeks_as_of": "2026-11-03T20:30:00Z", "rows": CHAIN}

    pull = make(lse.LseOptions, router({"/v3/options/chain": chain})).collect(
        SINCE, ("XNAS:NVDA", "XNAS:MSFT"), slot="us_close"
    )
    metrics = {(o.instrument_id, o.concept): o for o in pull.observations}
    assert set(metrics) == {
        ("XNAS:NVDA", "lse_atm_iv_30d"),
        ("XNAS:NVDA", "lse_put_call_volume_ratio"),
    }
    iv = metrics[("XNAS:NVDA", "lse_atm_iv_30d")]
    assert iv.value == Decimal("0.5200") and iv.period_end == date(2026, 11, 3)
    assert [iid for iid, _ in pull.failed] == ["XNAS:MSFT"]


# -- 4. Prices ----------------------------------------------------------------------

CANDLES = (
    "time,open,high,low,close,volume,trades\n"
    "2026-10-30T00:00:00Z,200,205,199,204.5,1000,10\n"
    "2026-11-02T00:00:00Z,204,206,201,202.25,900,9\n"
)


def test_prices_store_a_close_per_day(private):
    def export(q):
        assert q["market"] == ["us_stocks"] and q["timeframe"] == ["1d"] and q["format"] == ["csv"]
        return CANDLES if q["symbol"] == ["AAPL"] else None

    pull = make(lse.LsePrices, router({"/v3/export": export})).collect(
        SINCE, ("XNAS:AAPL", "XNAS:MSFT"), slot="us_close"
    )
    closes = [(o.period_end, o.value) for o in pull.observations]
    assert closes == [
        (date(2026, 10, 30), Decimal("204.5")),
        (date(2026, 11, 2), Decimal("202.25")),
    ]
    assert pull.observations[0].payload["volume"] == "1000"
    assert pull.failed == [] and any("XNAS:MSFT: no daily candles" in n for n in pull.notes)


# -- wiring -------------------------------------------------------------------------


def test_catalogued_registered_and_before_finbert():
    names = list(catalog.CATALOG)
    for n in ("lse_macro_my", "lse_calendar", "lse_options", "lse_prices"):
        assert catalog.spec(n).key_env == "LSE_API_KEY"
        assert names.index(n) < names.index("finbert_tone")
        assert collector_for(n).name == n
    assert names[-1] == "finbert_tone"
