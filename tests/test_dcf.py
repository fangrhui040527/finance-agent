"""Cost of capital, the scenario DCF and its sanity checks, and the comparables."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from agents.base import AgentContext
from agents.evidence.agents import A2Valuation
from core.guardrails.defaults import default_engine
from engines.fundamentals.ratios import Statements
from engines.valuation.comps import own_history, peer_multiples, three_contexts
from engines.valuation.cost_of_capital import (
    CostOfCapital,
    cost_of_capital_text,
    cost_of_equity,
    derive,
    load_table,
    staleness,
    synthetic_spread,
    wacc,
)
from engines.valuation.dcf import (
    Assumptions,
    Scenario,
    default_scenarios,
    implied_growth,
    project,
    scenario_range,
    valuation_text,
)
from engines.valuation.sanity import History, check
from knowledge.facts import FactBook, Observation, SeriesPoint
from knowledge.retrieval.pipeline import Router
from tests.statement_fixtures import ASOF, IID, us_store

TABLE_YAML = """
source: test table
licence: attributed
as_of: 2026-07-01
stale_after_days: 400
mature_market_erp: 0.0417
implied_erp: {US: 0.0445}
equity_volatility_multiplier: 1.5545
country:
  US: {name: United States, rating: Aaa, crp: 0.0, erp: 0.0445}
  MY: {name: Malaysia, rating: A3, crp: null, erp: null}
industry_betas:
  "Software (System & Application)": {unlevered: 1.10, levered: 1.20}
  "Bank (Money Center)": {unlevered: null, levered: null}
archetype_industry: {software: "Software (System & Application)", bank: "Bank (Money Center)"}
synthetic_rating_spreads:
  - {min_cover: 8.5, rating: AAA, spread: 0.006}
  - {min_cover: 4.25, rating: A, spread: 0.012}
  - {min_cover: -1000, rating: D, spread: null}
statutory_tax: {XKLS: 0.24, XNAS: 0.21}
long_run_nominal_growth: {US: 0.04, MY: 0.05}
"""


@pytest.fixture
def table(tmp_path):
    p = tmp_path / "coc.yaml"
    p.write_text(TABLE_YAML, encoding="utf-8")
    return load_table(p)


def make_book(path, *, with_beta=True):
    """A fact book for one US name; ``with_beta=False`` leaves the vendor beta out.

    The observations table is append-only, so a test that wants the beta absent builds a
    second book rather than deleting a row.
    """
    b = FactBook(path)
    b.add_series(
        [SeriesPoint("fred", "DGS10", date(2026, 1, 5), Decimal("4.20"), known_at=date(2026, 1, 6))]
    )
    beta = (
        [Observation("finnhub", IID, "beta", known_at=date(2026, 2, 1), value=Decimal("1.2"))]
        if with_beta
        else []
    )
    b.add_observations(
        beta
        + [
            Observation(
                "finnhub", IID, "market_cap_musd", known_at=date(2026, 2, 1), value=Decimal("3000")
            ),
            Observation("finnhub", IID, "pe_ttm", known_at=date(2026, 1, 10), value=Decimal("20")),
            Observation("finnhub", IID, "pe_ttm", known_at=date(2026, 2, 10), value=Decimal("22")),
            Observation("finnhub", IID, "pe_ttm", known_at=date(2026, 2, 20), value=Decimal("25")),
            Observation(
                "finnhub", "XNAS:PEER1", "pe_ttm", known_at=date(2026, 2, 20), value=Decimal("18")
            ),
            Observation(
                "finnhub", "XNAS:PEER2", "pe_ttm", known_at=date(2026, 2, 20), value=Decimal("30")
            ),
        ]
    )
    return b


@pytest.fixture
def book(tmp_path):
    b = make_book(tmp_path / "facts.db")
    yield b
    b.close()


# --- cost of capital ---------------------------------------------------------------------


def test_the_table_loads_and_flags_its_own_staleness(table):
    assert (
        table.present
        and table.mature_erp == Decimal("0.0417")
        and table.country["US"]["erp"] == 0.0445
    )
    assert staleness(table, date(2027, 1, 1)) is None
    assert "days old" in (staleness(table, date(2027, 9, 1)) or "")
    absent = load_table("nowhere.yaml")
    assert not absent.present and absent.country == {}


def test_capm_wacc_and_the_synthetic_ladder(table):
    assert cost_of_equity(Decimal("0.042"), Decimal("1.2"), Decimal("0.0445")) == Decimal("0.0954")
    w = wacc(Decimal("0.10"), Decimal("0.05"), Decimal("0.25"), Decimal(900), Decimal(100))
    assert w == Decimal("0.09375")
    assert synthetic_spread(table, Decimal(20)) == (Decimal("0.006"), "synthetic rating AAA")
    assert synthetic_spread(table, Decimal(5)) == (Decimal("0.012"), "synthetic rating A")
    spread, label = synthetic_spread(table, Decimal("0.1"))
    assert spread is None and "not transcribed" in label
    assert synthetic_spread(table, None)[0] is None


def test_derive_labels_every_input_for_a_us_name(book, table):
    s = Statements.from_store(us_store(), IID, ASOF)
    c = derive(IID, book, table, ASOF, "software", s)
    assert c.rf == Decimal("0.042") and c.rf_source.startswith("DGS10 2026-01-05")
    assert c.beta == Decimal("1.2") and c.beta_source.startswith("finnhub beta")
    assert c.erp == Decimal("0.0445") and "total equity risk premium" in c.erp_source
    assert c.ke == Decimal("0.0954") and c.kd == Decimal("0.048") and "AAA" in c.kd_source
    assert (
        c.tax_rate is not None
        and abs(c.tax_rate - Decimal("0.2105")) < Decimal("0.001")
        and "effective" in c.tax_source
    )
    assert c.wacc is not None and abs(c.wacc - Decimal("0.0954")) < Decimal("0.0002"), (
        "debt is tiny against a 3bn market cap"
    )
    assert c.discount[1] == "wacc" and not c.missing
    assert "cost_of_capital#country:US" in c.citations
    text = cost_of_capital_text(c)
    assert "risk-free 4.20%" in text and "WACC 9.5" in text


def test_derive_says_what_a_malaysian_name_lacks_instead_of_guessing(book, table):
    c = derive("MYX:1155", book, table, ASOF, "bank", None)
    assert "no MY government yield stored" in c.rf_source and c.rf == Decimal("0.042")
    assert c.erp == table.mature_erp and any(
        "not transcribed" in cv and "understated" in cv for cv in c.caveats
    )
    assert c.beta is None and "beta" in c.missing and "not transcribed" in c.beta_source
    assert c.ke is None and c.wacc is None and c.discount[0] is None
    assert c.tax_rate == Decimal("0.24") and "statutory" in c.tax_source


def test_a_malaysian_yield_from_a_stopped_upstream_is_used_and_said_out_loud(tmp_path, table):
    """DBN:GOVT_YIELD_MY is the ringgit risk-free rate and its upstream stopped
    at 2025-05. Dropping it would silently change the discount rate on every
    Malaysian name; presenting it undated would let a sixteen-month-old rate
    read as today's. So it is used, and every WACC built on it carries the
    reason it cannot be taken for current."""
    b = FactBook(tmp_path / "my.db")
    try:
        b.add_series(
            [
                SeriesPoint(
                    "dbnomics",
                    "DBN:GOVT_YIELD_MY",
                    date(2025, 5, 1),
                    Decimal("3.19"),
                    known_at=date(2026, 9, 6),
                )
            ]
        )
        c = derive("MYX:1155", b, table, date(2026, 9, 10), "bank", None)
    finally:
        b.close()
    assert c.rf == Decimal("0.0319"), "the stored yield still sets the rate"
    assert "ENDED" in c.rf_source and "IMF/IFS stopped publishing at 2025-05" in c.rf_source
    assert "497 days old" in c.rf_source
    assert any("rests on a stopped series" in cv for cv in c.caveats)
    assert "risk-free rate" not in c.missing, "the rate is present, only old"


def test_derive_borrows_the_industry_beta_and_cites_its_row(tmp_path, table):
    book = make_book(tmp_path / "no-beta.db", with_beta=False)
    try:
        s = Statements.from_store(us_store(), IID, ASOF)
        c = derive(IID, book, table, ASOF, "software", s)
    finally:
        book.close()
    assert c.beta == Decimal("1.20") and c.beta_source.startswith("industry beta: Software")
    assert "cost_of_capital#industry:Software (System & Application)" in c.citations


# --- the DCF -----------------------------------------------------------------------------


def test_project_matches_a_hand_example_and_reports_the_terminal_share():
    a = Assumptions(
        2,
        Decimal("0.10"),
        Decimal("0.04"),
        Decimal("0.20"),
        Decimal("0.25"),
        Decimal("0.5"),
        Decimal("0.10"),
    )
    r = project(Decimal(1000), a, net_debt=Decimal(100), shares=Decimal(10))
    assert r.revenues == (Decimal(1100), Decimal(1144)) and r.cash_flows == (
        Decimal(115),
        Decimal("149.6"),
    )
    assert abs(r.ev - Decimal("1703.09")) < Decimal("0.05")
    assert r.terminal_share is not None and r.terminal_share > Decimal("0.75")
    assert r.equity is not None and abs(r.equity - Decimal("1603.09")) < Decimal("0.05")
    assert r.per_share is not None and abs(r.per_share - Decimal("160.309")) < Decimal("0.01")
    higher = project(
        Decimal(1000),
        Assumptions(
            2,
            Decimal("0.10"),
            Decimal("0.04"),
            Decimal("0.20"),
            Decimal("0.25"),
            Decimal("0.5"),
            Decimal("0.12"),
        ),
    )
    assert higher.ev < r.ev, "a higher discount rate lowers the value"
    with pytest.raises(ValueError, match="perpetuity"):
        project(
            Decimal(1000),
            Assumptions(
                2,
                Decimal("0.10"),
                Decimal("0.10"),
                Decimal("0.2"),
                Decimal("0.25"),
                Decimal("0.5"),
                Decimal("0.10"),
            ),
        )


def test_implied_growth_equals_the_valuation_agents_reverse_dcf(registry):
    a2 = A2Valuation(
        AgentContext(
            router=Router({}),
            engine=default_engine(registry.allowlist()),
            now=datetime(2026, 9, 6, tzinfo=UTC),
        )
    )
    theirs = a2.reverse_dcf(60.0, 5.0, 0.10)[0].numbers["implied_growth"]
    ours = implied_growth(Decimal(60), Decimal(5), Decimal("0.10"))
    assert abs(float(ours) - theirs) < 1e-6


def _coc(**over) -> CostOfCapital:
    base = dict(
        instrument_id=IID,
        asof=ASOF,
        country="US",
        rf=Decimal("0.042"),
        rf_source="t",
        beta=Decimal("1.2"),
        beta_source="t",
        erp=Decimal("0.0445"),
        erp_source="t",
        crp=Decimal(0),
        ke=Decimal("0.0954"),
        kd=Decimal("0.048"),
        kd_source="t",
        tax_rate=Decimal("0.21"),
        tax_source="t",
        e_value=Decimal(3_000_000_000),
        d_value=Decimal(550),
        wacc=Decimal("0.095"),
    )
    base.update(over)
    return CostOfCapital(**base)


def test_sanity_checks_name_the_offence_and_grade_it():
    coc = _coc()
    hist = History(
        margin_max=Decimal("0.20"), revenue_cagr=Decimal("0.10"), reinvestment=Decimal("0.4")
    )
    flags = check(
        terminal_growth=Decimal("0.05"),
        discount=Decimal("0.03"),
        growth_first_year=Decimal("0.30"),
        margin=Decimal("0.25"),
        reinvestment=Decimal(0),
        terminal_share=Decimal("0.8"),
        coc=coc,
        table=load_table("nowhere.yaml"),
        country="US",
        history=hist,
    )
    codes = {f.code: f.severity for f in flags}
    assert codes["terminal_above_rf"] == "fatal" and codes["discount_too_low"] == "fatal"
    assert codes["terminal_share"] == "material" and codes["margin_above_history"] == "material"
    assert codes["growth_above_history"] == "material" and codes["no_reinvestment"] == "minor"
    clean = check(
        terminal_growth=Decimal("0.03"),
        discount=Decimal("0.095"),
        growth_first_year=Decimal("0.10"),
        margin=Decimal("0.18"),
        reinvestment=Decimal("0.4"),
        terminal_share=Decimal("0.6"),
        coc=coc,
        table=load_table("nowhere.yaml"),
        country="US",
        history=hist,
    )
    assert clean == ()
    assert any(
        f.code == "ke_below_kd"
        for f in check(
            terminal_growth=Decimal("0.03"),
            discount=Decimal("0.095"),
            growth_first_year=Decimal("0.1"),
            margin=Decimal("0.1"),
            reinvestment=Decimal("0.4"),
            terminal_share=None,
            coc=_coc(kd=Decimal("0.12")),
            table=load_table("nowhere.yaml"),
            country="US",
            history=History(),
        )
    )


def test_default_scenarios_come_from_the_record_and_the_range_is_bear_to_bull(table):
    s = Statements.from_store(us_store(), IID, ASOF)
    coc = _coc()
    scenarios, reasons = default_scenarios(s, coc, table, "US")
    assert not reasons and [sc.name for sc in scenarios] == ["bear", "base", "bull"]
    base = scenarios[1].assumptions
    assert base.terminal_growth == Decimal("0.04"), "min(rf 4.2%, US ceiling 4%)"
    assert abs(base.growth_first_year - Decimal("0.1111")) < Decimal(
        "0.001"
    ) and base.margin == Decimal("0.2")
    assert any("revenue CAGR" in b for b in scenarios[1].basis)
    vr = scenario_range(s, coc, scenarios, table, "US", "USD")
    assert vr.refused is None and vr.low is not None and vr.high is not None and vr.low < vr.high
    assert set(vr.results) == {"bear", "base", "bull"} and vr.per_share["base"] is not None
    text = valuation_text(vr, scenarios)
    assert (
        "range (equity value)" in text
        and "a range, not a target" in text
        and "what must be true for base" in text
    )


def test_a_fatal_scenario_is_dropped_and_too_few_survivors_is_a_refusal(table):
    s = Statements.from_store(us_store(), IID, ASOF)
    coc = _coc()
    ok = Assumptions(
        5,
        Decimal("0.08"),
        Decimal("0.03"),
        Decimal("0.19"),
        Decimal("0.21"),
        Decimal("0.4"),
        Decimal("0.095"),
    )
    bad = Assumptions(
        5,
        Decimal("0.08"),
        Decimal("0.08"),
        Decimal("0.19"),
        Decimal("0.21"),
        Decimal("0.4"),
        Decimal("0.095"),
    )
    vr = scenario_range(s, coc, (Scenario("bear", ok), Scenario("bull", bad)), table, "US")
    assert "bull" in vr.dropped and "exceeds the risk-free rate" in vr.dropped["bull"][0]
    assert vr.refused is not None and "fewer than two scenarios" in vr.refused
    assert "REFUSED" in valuation_text(vr)


def test_scenarios_are_refused_without_the_record(table):
    from core.market.pointintime import FactStore

    s = Statements.from_store(FactStore(), "MYX:1155", ASOF)
    scenarios, reasons = default_scenarios(s, _coc(wacc=None, ke=None), table, "MY")
    assert scenarios == () and any("fewer than two annual operating margins" in r for r in reasons)
    assert any("no discount rate" in r for r in reasons)


# --- comparables -------------------------------------------------------------------------


def test_peer_band_refuses_a_set_of_one_and_places_the_multiple_in_its_history(book):
    band, why = peer_multiples(book, {"XNAS:PEER1"}, "pe_ttm", ASOF)
    assert band is None and "below the minimum of 2" in why
    band, why = peer_multiples(book, {"XNAS:PEER1", "XNAS:PEER2", "XNAS:NOBODY"}, "pe_ttm", ASOF)
    assert (
        band is not None
        and band.n == 2
        and band.median == Decimal(24)
        and band.missing == ("XNAS:NOBODY",)
    )
    hist = own_history(book, IID, "pe_ttm", ASOF)
    assert [v for _, v in hist] == [Decimal(20), Decimal(22), Decimal(25)]
    tc = three_contexts(
        book,
        IID,
        {"XNAS:PEER1", "XNAS:PEER2"},
        "pe_ttm",
        ASOF,
        Decimal(100),
        Decimal(5),
        Decimal("0.095"),
    )
    assert tc.current == Decimal(25) and tc.history_percentile == Decimal(1)
    assert tc.peer_band is not None and tc.implied_growth is not None
    assert "own history: 100% percentile of 3 snapshots" in tc.text() and "peers (2)" in tc.text()
    early = three_contexts(book, IID, set(), "pe_ttm", date(2026, 1, 15))
    assert (
        early.history_n == 1
        and early.history_percentile is None
        and "no peer set supplied" in early.peer_reason
    )
