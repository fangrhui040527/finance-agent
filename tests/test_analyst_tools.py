"""The analyst tools and CLI commands on a filled and on an empty fact book."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from knowledge.facts import FactBook, Observation, SeriesPoint
from tests.statement_fixtures import (
    ANNUAL_T,
    ANNUAL_T1,
    BALANCE_T,
    BALANCE_T1,
    FILED_T,
    FILED_T1,
    PRIOR_Q4,
    QUARTERLY,
    QUARTERS,
    T1,
    T,
)

AAPL = "XNAS:AAPL"
ASAT = "2026-03-01"


@pytest.fixture
def analyst_book(tmp_path, monkeypatch):
    """A config whose fact book holds SEC-shaped statement lines for one US name."""
    corpus_db, facts_db = tmp_path / "c.db", tmp_path / "f.db"
    shipped = open("config.toml", encoding="utf-8").read()
    cfg = shipped.replace(
        'corpus_database = "data/corpus.db"', f'corpus_database = "{corpus_db.as_posix()}"'
    )
    cfg = cfg.replace(
        'facts_database = "data/facts.db"', f'facts_database = "{facts_db.as_posix()}"'
    )
    path = tmp_path / "config.toml"
    path.write_text(cfg, encoding="utf-8")
    monkeypatch.setenv("FINPLANET_CONFIG", str(path))
    rows = []
    for c, v in ANNUAL_T.items():
        rows.append(
            Observation(
                "sec_xbrl",
                AAPL,
                f"{c}_fy",
                FILED_T,
                Decimal(v),
                period_end=T,
                unit="USD",
                currency="USD",
            )
        )
    for c, v in ANNUAL_T1.items():
        rows.append(
            Observation(
                "sec_xbrl",
                AAPL,
                f"{c}_fy",
                FILED_T1,
                Decimal(v),
                period_end=T1,
                unit="USD",
                currency="USD",
            )
        )
    for c, v in BALANCE_T.items():
        rows.append(
            Observation(
                "sec_xbrl", AAPL, c, FILED_T, Decimal(v), period_end=T, unit="USD", currency="USD"
            )
        )
        rows.append(
            Observation(
                "sec_xbrl",
                AAPL,
                f"{c}_fy",
                FILED_T,
                Decimal(v),
                period_end=T,
                unit="USD",
                currency="USD",
            )
        )
    for c, v in BALANCE_T1.items():
        rows.append(
            Observation(
                "sec_xbrl", AAPL, c, FILED_T1, Decimal(v), period_end=T1, unit="USD", currency="USD"
            )
        )
        rows.append(
            Observation(
                "sec_xbrl",
                AAPL,
                f"{c}_fy",
                FILED_T1,
                Decimal(v),
                period_end=T1,
                unit="USD",
                currency="USD",
            )
        )
    for c, values in QUARTERLY.items():
        for (end, filed), v in zip(QUARTERS, values):
            rows.append(
                Observation(
                    "sec_xbrl",
                    AAPL,
                    c,
                    filed,
                    Decimal(v),
                    period_end=end,
                    unit="USD",
                    currency="USD",
                )
            )
    for c, v in PRIOR_Q4.items():
        rows.append(
            Observation(
                "sec_xbrl", AAPL, c, FILED_T1, Decimal(v), period_end=T1, unit="USD", currency="USD"
            )
        )
    rows.append(
        Observation(
            "sec_xbrl",
            AAPL,
            "shares_outstanding",
            date(2025, 11, 1),
            Decimal(100),
            period_end=date(2025, 9, 30),
            unit="shares",
        )
    )
    rows.append(Observation("finnhub", AAPL, "beta", date(2026, 2, 1), Decimal("1.2")))
    rows.append(Observation("finnhub", AAPL, "market_cap_musd", date(2026, 2, 1), Decimal("3000")))
    rows.append(Observation("finnhub", AAPL, "eps_ttm", date(2026, 2, 1), Decimal("1.5")))
    rows.append(Observation("finnhub", "XNAS:MSFT", "pe_ttm", date(2026, 2, 1), Decimal("30")))
    rows.append(Observation("finnhub", "XNAS:NVDA", "pe_ttm", date(2026, 2, 1), Decimal("40")))
    with FactBook(facts_db) as book:
        book.add_observations(rows)
        book.add_series(
            [
                SeriesPoint(
                    "fred", "DGS10", date(2026, 1, 5), Decimal("4.20"), known_at=date(2026, 1, 6)
                )
            ]
        )
    return facts_db


def test_ratio_sheet_tool_reports_ratios_and_quality_and_refuses_an_empty_name(analyst_book):
    from mcp_server.tools import ToolError, ratio_sheet

    text = ratio_sheet(AAPL, as_at=ASAT)
    assert "22 of 22 ratios computable" in text and "gross_margin: 40.0%" in text
    assert "earnings quality:" in text and "beneish_m: below the -1.78 threshold" in text
    assert "Not financial advice" in text
    empty = ratio_sheet("MYX:1155", as_at=ASAT)
    assert empty.startswith("NO STATEMENTS STORED for MYX:1155") and "eodhd" in empty
    with pytest.raises(ToolError):
        ratio_sheet(AAPL, as_at="yesterday")
    with pytest.raises(ToolError):
        ratio_sheet("NOPE:1", as_at=ASAT)


def test_cost_of_capital_tool_labels_inputs_and_cites_the_table(analyst_book):
    from mcp_server.tools import cost_of_capital

    text = cost_of_capital(AAPL, as_at=ASAT, archetype="software")
    assert "risk-free 4.20%" in text and "finnhub beta" in text and "cost of equity 9.54%" in text
    assert "cites kb_method_valuation:cost_of_capital#" in text
    my = cost_of_capital("MYX:1155", as_at=ASAT, archetype="bank")
    assert "missing: beta" in my and "not transcribed" in my


def test_valuation_range_tool_is_a_range_or_a_refusal_never_a_point(analyst_book):
    from mcp_server.tools import valuation_range

    text = valuation_range(AAPL, as_at=ASAT, archetype="software", peers=["XNAS:MSFT", "XNAS:NVDA"])
    assert "range (equity value):" in text and "a range, not a target" in text
    assert "bear:" in text and "bull:" in text and "what must be true for base" in text
    assert (
        "peers (2): median 35.00" in text
        and "growth the price requires" not in text
        or "reverse DCF" in text
    )
    empty = valuation_range("MYX:1155", as_at=ASAT)
    assert empty.startswith("NO STATEMENTS STORED for MYX:1155")


def test_the_cli_commands_exit_by_outcome(analyst_book, capsys):
    import ask

    assert ask.main(["ratios", AAPL, "--as-at", ASAT]) == 0
    assert "22 of 22" in capsys.readouterr().out
    assert ask.main(["ratios", "MYX:1155", "--as-at", ASAT]) == 1
    capsys.readouterr()
    assert (
        ask.main(["valuation", AAPL, "--as-at", ASAT, "--archetype", "software", "--coc-only"]) == 0
    )
    assert "WACC" in capsys.readouterr().out
    assert ask.main(["valuation", AAPL, "--as-at", ASAT, "--archetype", "software"]) == 0
    assert "range (equity value)" in capsys.readouterr().out
    assert ask.main(["valuation", "MYX:1155", "--as-at", ASAT]) == 1
    capsys.readouterr()
    assert ask.main(["ratios", "NOPE:1"]) == 2
