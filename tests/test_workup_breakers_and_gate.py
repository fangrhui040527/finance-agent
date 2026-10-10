"""Every workup breaker runs and can fire, and the quality gate has the market cap and archetype.

Each test fails on the code before 2026-10-08:

  * every suggested breaker selected a `value` column the observations table
    does not have (it has value_text and value_num), so each one failed with
    "no such column: value" and the restatement and revenue-growth breakers
    could never fire; the "price above the bull case" breaker read pe_ttm, so
    it set a P/E near 30 against a value per share. The only test asserted
    that "SELECT" appeared in the query;
  * the quality gate called quality_report and A1's quality_scores without the
    market cap or the archetype, so Altman Z was "4 of 5 components; missing
    market_cap" with snapshots stored, and a bank was listed as missing a
    market cap instead of having Altman refused as not applicable.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from engines.analysis.workup import run_workup
from engines.fundamentals.ratios import Statements
from knowledge.facts import FactBook, Observation
from mcp_server.tools import context
from tests.statement_fixtures import BALANCE_T, FILED_T, T1, T
from tests.test_analyst_tools import AAPL, ASAT
from tests.test_analyst_tools import analyst_book as _analyst_book

#: the SEC-shaped fixture (two fiscal years, quarters, a balance sheet, finnhub snapshots)
analyst_book = _analyst_book

ASOF = date.fromisoformat(ASAT)
LATER = date(2026, 4, 1)  # after the workup date: what a breaker check would find


def _sec(concept: str, period_end: date, known_at: date, value, iid: str = AAPL) -> Observation:
    """A statement line as sec_xbrl stores it."""
    return Observation(
        "sec_xbrl",
        iid,
        concept,
        known_at,
        Decimal(value),
        period_end=period_end,
        unit="USD",
        currency="USD",
    )


def _snap(concept: str, value, known_at: date, iid: str = AAPL) -> Observation:
    """A finnhub metric snapshot: no period, no currency on the row."""
    return Observation("finnhub", iid, concept, known_at, Decimal(value))


def _add(db, *rows: Observation) -> None:
    with FactBook(db) as book:
        book.add_observations(rows)


def _workup(db, iid: str = AAPL, archetype: str | None = "software"):
    with FactBook(db) as book:
        return run_workup(iid, book, context(), asof=ASOF, archetype=archetype, currency="USD")


def _run(db, query: str) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(query).fetchall()
    finally:
        con.close()


def _breaker(w, opening: str):
    found = [b for b in w.breakers if b.statement.startswith(opening)]
    assert found, f"no breaker opening {opening!r} in {[b.statement for b in w.breakers]}"
    return found[0]


def _figure(pattern: str, text: str) -> Decimal:
    """The number a breaker's statement names, read back out of it."""
    m = re.search(pattern, text)
    assert m, f"{pattern!r} not in {text!r}"
    return Decimal(m.group(1).replace(",", ""))


def _altman(step):
    return next(f for f in step.findings if f.text.startswith("altman_z:"))


# --- facts-valuation-4: the breakers ---------------------------------------------------------


def test_every_suggested_breaker_query_runs_against_the_fact_book(analyst_book):
    from engines.analysis.workup import _suggest_breakers

    def runs(breakers) -> None:
        for b in breakers:
            try:
                _run(analyst_book, b.query)
            except sqlite3.Error as e:
                pytest.fail(f"breaker {b.statement!r} does not run: {e}\n{b.query}")

    _add(analyst_book, _snap("pe_ttm", "20", date(2026, 2, 1)))
    suggested = list(_workup(analyst_book).breakers)
    runs(suggested)
    # The fixture raises no accruals or leverage flag, so those two shapes are
    # asked for directly; together with the workup's own they are all five.
    with FactBook(analyst_book) as book:
        s = Statements.from_store(book.as_fact_store([AAPL], ASOF), AAPL, ASOF)
        vr = SimpleNamespace(per_share={"bull": Decimal("40")})
        for flags in (["accruals", "net_debt_to_ebitda"], []):
            suggested += _suggest_breakers(book, AAPL, ASOF, s, flags, vr, Decimal("0.08"))
    runs(suggested)
    kinds = {
        k
        for b in suggested
        for k in ("accruals", "net debt", "revenue growth", "price above", "a stored annual")
        if b.statement.startswith(k)
    }
    assert len(kinds) == 5


def test_the_restatement_and_growth_breakers_fire_on_later_filings_not_on_day_one(analyst_book):
    # The 2026 10-K re-reported fiscal 2024 revenue: a restatement on file before
    # the workup, as NVDA's rounded fiscal 2015 lines were. Not news for a thesis.
    _add(analyst_book, _sec("revenue_fy", T1, FILED_T, 901))
    w = _workup(analyst_book)
    restated = _breaker(w, "a stored annual line is restated")
    growth = _breaker(w, "revenue growth below")
    assert _run(analyst_book, restated.query) == []
    threshold = _figure(r"below (-?[\d.]+)%", growth.statement) / 100
    assert threshold > 0

    # After the workup: two quarters flat on a year earlier, fiscal 2025 restated down.
    _add(
        analyst_book,
        _sec("revenue", date(2026, 3, 31), date(2026, 5, 1), 240),
        _sec("revenue", date(2026, 6, 30), date(2026, 8, 1), 250),
        _sec("revenue_fy", T, LATER, 950),
    )
    assert _run(analyst_book, restated.query) == [("sec_xbrl", "revenue_fy", T.isoformat(), 2)]
    rows = {date.fromisoformat(p): Decimal(str(v)) for p, v in _run(analyst_book, growth.query)}
    latest = sorted(rows, reverse=True)[:2]
    assert latest == [date(2026, 6, 30), date(2026, 3, 31)]
    yoy = [rows[p] / rows[p.replace(year=p.year - 1)] - 1 for p in latest]
    assert all(g < threshold for g in yoy)


def test_the_price_breaker_reads_a_price_against_the_bull_value_never_the_multiple(analyst_book):
    # eps_ttm 1.5 is in the fixture; a P/E of 1 makes the implied price 1.50
    _add(analyst_book, _snap("pe_ttm", "1", date(2026, 2, 1)))
    price = _breaker(_workup(analyst_book), "price above the bull case")
    assert "eps_ttm x pe_ttm" in price.statement
    bull = _figure(r"\(([\d,.]+) per share\)", price.statement)
    assert bull > Decimal("1.5")
    assert _run(analyst_book, price.query) == [("2026-02-01", 1.5, 0)]

    # a later snapshot that puts the implied price at twice the bull case fires it
    pe = (bull / Decimal("1.5") * 2).quantize(Decimal("0.01"))
    _add(analyst_book, _snap("pe_ttm", pe, LATER))
    [(day, implied, above)] = _run(analyst_book, price.query)
    assert day == LATER.isoformat() and above == 1
    assert implied == pytest.approx(float(pe * Decimal("1.5")))


def test_a_stored_close_is_the_price_when_the_book_holds_one(analyst_book):
    from engines.analysis.workup import _price_query

    tw = "XTAI:2330"
    rows = []
    # as twse_openapi stores a quote: the trading day as period, seen the day after
    for day, close, pe in ((25, "1010", "24.6"), (26, "1040", "25.3")):
        traded, seen = date(2026, 2, day), date(2026, 2, day + 1)
        rows.append(
            Observation(
                "twse_openapi",
                tw,
                "close",
                seen,
                Decimal(close),
                period_end=traded,
                unit="TWD",
                currency="TWD",
            )
        )
        rows.append(
            Observation(
                "twse_openapi", tw, "pe_ttm", seen, Decimal(pe), period_end=traded, unit="x"
            )
        )
    _add(analyst_book, *rows)
    with FactBook(analyst_book) as book:
        query, basis = _price_query(book, tw, ASOF, "1020.00") or ("", "")
    assert basis == "the stored close"
    assert _run(analyst_book, query) == [("2026-02-26", 1040.0, 1)]


# --- facts-valuation-5: the quality gate ------------------------------------------------------


def test_altman_z_is_computed_when_a_market_cap_is_stored(analyst_book):
    # the fixture's cap is 3,000m known 2026-02-01; a later one must not be read
    _add(analyst_book, _snap("market_cap_musd", "9000", LATER))
    step = _workup(analyst_book).step(4)
    altman = _altman(step)
    assert "market_cap" not in step.missing
    assert "market cap 3,000m USD known 2026-02-01" in step.summary
    # 1.2(400/2000) + 1.4(600/2000) + 3.3(200/2000) + 0.6(3,000e6/1000) + 1000/2000
    assert altman.numbers["altman_z"] == pytest.approx(1_800_001.49)
    assert altman.text == "altman_z: safe zone"


def test_a_market_cap_in_another_currency_is_refused_with_the_reason(analyst_book):
    adr = "XNAS:ASML"  # statements in EUR, the snapshot in millions of USD
    _add(
        analyst_book,
        *(
            Observation(
                "fmp", adr, c, FILED_T, Decimal(v), period_end=T, unit="EUR", currency="EUR"
            )
            for c, v in BALANCE_T.items()
        ),
        _snap("market_cap_musd", "900000", date(2026, 2, 1), iid=adr),
    )
    step = _workup(analyst_book, adr, None).step(4)
    assert "market cap not used: stored in USD, total liabilities in EUR" in step.summary
    assert "market_cap" in step.missing


def test_a_bank_gets_altman_refused_as_not_applicable_not_missing_a_market_cap(analyst_book):
    step = _workup(analyst_book, archetype="bank").step(4)
    assert _altman(step).text.startswith("altman_z: not applicable to a bank")
    assert "market_cap" not in step.missing
    assert "altman_z not applicable to a bank" in step.summary
    # the refused score leaves the count rather than holding the gate at partial
    assert step.summary.startswith("3 of 3 scores fully computable") and step.status == "done"
