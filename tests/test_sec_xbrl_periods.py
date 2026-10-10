"""Unreported quarters are derived from year-to-date figures and dated by their last input.

Each test fails on the code before 2026-10-08:

  * `_observations` kept only quarter and year spans and dropped every 6- and
    9-month figure. A 10-Q tags its cash-flow statement only year to date, so
    cash from operations, capex and depreciation had a Q1 a year and nothing
    else, no fourth quarter was derived, and TTM fell back to the last fiscal
    year: NVDA's net income ran to 2026-07-26 against cash from operations to
    2026-01-25, and accruals flagged a 47% gap that was only the mismatch
    (sources-2);
  * `_derive_fourth_quarters` stamped FY - (Q1+Q2+Q3) with the 10-K's filed
    date, computed from the newest version of each quarter, so MSFT's FY25 Q4
    depreciation (6.3B) read as known on 2025-07-30 although its quarters were
    first filed between 2025-10-29 and 2026-04-29, and the Q4 the 10-K day
    could compute was never stored (facts-valuation-3).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from engines.fundamentals.ratios import Statements
from knowledge.facts import FactBook, Observation
from knowledge.sources.sec_xbrl import SecCompanyFacts
from tests.conftest import FakeResponse

NVDA, MSFT = "XNAS:NVDA", "XNAS:MSFT"
CFO = "NetCashProvidedByUsedInOperatingActivities"


def _fact(start, end, val, filed, form="10-Q", fy=2026, fp="Q1"):
    return {
        "start": start,
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "fy": fy,
        "fp": fp,
        "accn": f"acc-{filed}",
    }


def _collect(iid: str, gaap: dict, today: date = date(2026, 10, 6)) -> list[Observation]:
    doc = {"facts": {"us-gaap": gaap}}
    clock = lambda: datetime(today.year, today.month, today.day, tzinfo=UTC)  # noqa: E731
    c = SecCompanyFacts(clock=clock, opener=lambda req, timeout=None: FakeResponse(json.dumps(doc)))
    pull = c.collect(clock(), (iid,))
    assert pull.requests == 1 and pull.observations, pull.notes
    return pull.observations


def _rows(obs: list[Observation], concept: str) -> dict[date, dict[Decimal, Observation]]:
    out: dict[date, dict[Decimal, Observation]] = {}
    for o in obs:
        if o.concept == concept and o.period_end is not None and o.value is not None:
            out.setdefault(o.period_end, {})[o.value] = o
    return out


def _dated_by_last_input(obs: list[Observation]) -> None:
    derived = [o for o in obs if o.payload.get("derived")]
    assert derived
    for o in derived:
        last = max(date.fromisoformat(i["filed"]) for i in o.payload["inputs"])
        assert o.known_at == last, (o.period_end, o.value, o.known_at, last)
        assert o.period_end is not None and o.known_at >= o.period_end


def _statements(tmp_path, iid: str, obs: list[Observation], asof: date) -> Statements:
    with FactBook(tmp_path / "facts.db") as book:
        book.add_observations(obs)
        return Statements.from_store(book.as_fact_store([iid], asof), iid, asof)


def test_year_to_date_cash_flows_become_discrete_quarters_and_a_current_ttm(tmp_path):
    # NVDA's shape: the 10-Qs tag cash from operations as 3, 6 and 9 months from the
    # fiscal year's start, the 10-K the year, and next year's 10-Qs repeat the
    # prior-year figures as comparatives.
    fy26, fy27 = "2025-01-27", "2026-01-26"
    obs = _collect(
        NVDA,
        {
            CFO: {
                "units": {
                    "USD": [
                        _fact(fy26, "2025-04-27", 100, "2025-05-28"),
                        _fact(fy26, "2025-07-27", 250, "2025-08-27", fp="Q2"),
                        _fact(fy26, "2025-10-26", 450, "2025-11-19", fp="Q3"),
                        _fact(fy26, "2026-01-25", 700, "2026-02-25", "10-K", fp="FY"),
                        _fact(fy27, "2026-04-26", 300, "2026-05-20", fy=2027),
                        _fact(fy26, "2025-04-27", 100, "2026-05-20", fy=2027),
                        _fact(fy27, "2026-07-26", 650, "2026-08-26", fy=2027, fp="Q2"),
                        _fact(fy26, "2025-07-27", 250, "2026-08-26", fy=2027, fp="Q2"),
                    ]
                }
            }
        },
    )
    q = _rows(obs, "cash_from_operations")
    expected = {
        date(2025, 4, 27): (100, date(2025, 5, 28), None),
        date(2025, 7, 27): (150, date(2025, 8, 27), "6M - 3M"),
        date(2025, 10, 26): (200, date(2025, 11, 19), "9M - 6M"),
        date(2026, 1, 25): (250, date(2026, 2, 25), "FY - 9M"),
        date(2026, 4, 26): (300, date(2026, 5, 20), None),
        date(2026, 7, 26): (350, date(2026, 8, 26), "6M - 3M"),
    }
    assert set(q) == set(expected), "one discrete quarter per period, Q1 to Q4"
    for end, (value, known_at, how) in expected.items():
        assert list(q[end]) == [Decimal(value)], (end, list(q[end]))
        row = q[end][Decimal(value)]
        assert row.known_at == known_at and row.payload.get("derived") == how, end
    assert _rows(obs, "cash_from_operations_fy")[date(2026, 1, 25)].keys() == {Decimal(700)}
    q2 = q[date(2025, 7, 27)][Decimal(150)].payload
    assert q2["fp"] == "Q2" and [(i["span"], i["end"], i["value"]) for i in q2["inputs"]] == [
        ("6M", "2025-07-27", "250"),
        ("3M", "2025-04-27", "100"),
    ], "a derived row names the figures it came from"
    _dated_by_last_input(obs)

    s = _statements(tmp_path, NVDA, obs, date(2026, 10, 6))
    ttm, inputs = s.ttm("cash_from_operations")
    assert ttm == Decimal(200 + 250 + 300 + 350)
    assert sorted(inputs) == [
        "cash_from_operations@2025-10-26",
        "cash_from_operations@2026-01-25",
        "cash_from_operations@2026-04-26",
        "cash_from_operations@2026-07-26",
    ], "TTM runs to the latest 10-Q, not back to the fiscal year to 2026-01-25"


def test_a_reported_quarter_wins_over_the_one_its_year_to_date_figures_imply():
    # MSFT's shape: the cash-flow statement carries a three-month column beside the
    # year to date for Q2 only here, a rounding apart from 6M - 3M.
    fy25 = "2024-07-01"
    obs = _collect(
        MSFT,
        {
            CFO: {
                "units": {
                    "USD": [
                        _fact(fy25, "2024-09-30", 100, "2024-10-30", fy=2025),
                        _fact(fy25, "2024-12-31", 250, "2025-01-29", fy=2025, fp="Q2"),
                        _fact("2024-10-01", "2024-12-31", 149, "2025-01-29", fy=2025, fp="Q2"),
                        _fact(fy25, "2025-03-31", 450, "2025-04-30", fy=2025, fp="Q3"),
                        _fact(fy25, "2025-06-30", 700, "2025-07-30", "10-K", 2025, "FY"),
                    ]
                }
            }
        },
    )
    q = _rows(obs, "cash_from_operations")
    reported = q[date(2024, 12, 31)]
    assert list(reported) == [Decimal(149)], "the reported quarter stands; 150 is never stored"
    assert "derived" not in reported[Decimal(149)].payload
    q3, q4 = q.get(date(2025, 3, 31), {}), q.get(date(2025, 6, 30), {})
    assert list(q3) == [Decimal(200)], "the quarter with no reported figure is 9M - 6M"
    assert q3[Decimal(200)].payload["derived"] == "9M - 6M"
    assert list(q4) == [Decimal(250)] and q4[Decimal(250)].payload["derived"] == "FY - 9M"
    _dated_by_last_input(obs)


def test_a_fourth_quarter_from_quarters_filed_after_the_10k_is_dated_by_the_last_of_them(
    tmp_path,
):
    # MSFT's Depreciation tag as stored on 2026-10-07: the FY25 10-K carried the year,
    # and FY25's three quarters appeared only as comparatives in the FY26 10-Qs.
    obs = _collect(
        MSFT,
        {
            "Depreciation": {
                "units": {
                    "USD": [
                        _fact("2024-07-01", "2025-06-30", 22000, "2025-07-30", "10-K", 2025, "FY"),
                        _fact("2024-07-01", "2024-09-30", 4700, "2025-10-29"),
                        _fact("2024-10-01", "2024-12-31", 5200, "2026-01-28", fp="Q2"),
                        _fact("2025-01-01", "2025-03-31", 5800, "2026-04-29", fp="Q3"),
                    ]
                }
            }
        },
    )
    q4 = _rows(obs, "depreciation")[date(2025, 6, 30)]
    assert list(q4) == [Decimal(6300)]
    row = q4[Decimal(6300)]
    assert row.payload["derived"] == "FY - Q1..Q3" and row.payload["fp"] == "Q4"
    assert row.known_at == date(2026, 4, 29), "known when the last quarter was filed"
    _dated_by_last_input(obs)

    early = _statements(tmp_path, MSFT, obs, date(2025, 8, 15))
    assert early.series("depreciation") == [], "nothing public on 2025-08-15 implied 6.3B"
    fy = early.latest_annual("depreciation")
    assert fy is not None and fy.value == Decimal(22000)


def test_restated_inputs_make_later_rows_and_never_backdate_the_restated_figure(tmp_path):
    # The quarters were filed on time, then restated in the next year's 10-Q
    # comparatives. The 10-K day could compute 22000 - 14500 = 7500; each
    # restatement makes a later row, and the fully restated 6300 is known only
    # when its last input is.
    obs = _collect(
        MSFT,
        {
            "DepreciationDepletionAndAmortization": {
                "units": {
                    "USD": [
                        _fact("2024-07-01", "2025-06-30", 22000, "2025-07-30", "10-K", 2025, "FY"),
                        _fact("2024-07-01", "2024-09-30", 4000, "2024-10-30", fy=2025),
                        _fact("2024-10-01", "2024-12-31", 5000, "2025-01-29", fy=2025, fp="Q2"),
                        _fact("2025-01-01", "2025-03-31", 5500, "2025-04-30", fy=2025, fp="Q3"),
                        _fact("2024-07-01", "2024-09-30", 4700, "2025-10-29"),
                        _fact("2024-10-01", "2024-12-31", 5200, "2026-01-28", fp="Q2"),
                        _fact("2025-01-01", "2025-03-31", 5800, "2026-04-29", fp="Q3"),
                    ]
                }
            },
            # A year-to-date input restated in a comparative: 6M - 3M re-derives on
            # the day the restated Q1 lands, and not before.
            CFO: {
                "units": {
                    "USD": [
                        _fact("2024-07-01", "2024-09-30", 100, "2024-10-30", fy=2025),
                        _fact("2024-07-01", "2024-12-31", 250, "2025-01-29", fy=2025, fp="Q2"),
                        _fact("2024-07-01", "2024-09-30", 110, "2025-10-29"),
                    ]
                }
            },
        },
    )
    q4 = _rows(obs, "depreciation")[date(2025, 6, 30)]
    assert {v: o.known_at for v, o in q4.items()} == {
        Decimal(7500): date(2025, 7, 30),
        Decimal(6800): date(2025, 10, 29),
        Decimal(6600): date(2026, 1, 28),
        Decimal(6300): date(2026, 4, 29),
    }
    q2 = _rows(obs, "cash_from_operations")[date(2024, 12, 31)]
    assert {v: o.known_at for v, o in q2.items()} == {
        Decimal(150): date(2025, 1, 29),
        Decimal(140): date(2025, 10, 29),
    }
    _dated_by_last_input(obs)

    for asof, want in ((date(2025, 8, 15), 7500), (date(2026, 5, 1), 6300)):
        s = _statements(tmp_path, MSFT, obs, asof)
        fact = s.latest("depreciation")
        assert fact is not None and fact.period_end == date(2025, 6, 30), asof
        assert fact.value == Decimal(want), (asof, fact.value)
