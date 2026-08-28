"""The commands that reached the stranded agents.

Before these, `ask.py` instantiated two of sixteen registered agents. A10, A11,
A12, A13 and A14 were tested classes with no way for an operator to run them,
and the price feed had no entrypoint at all.
"""
import pytest

import ask
from core.market.feed import StooqFeed

CSV = """Date,Open,High,Low,Close,Volume
2026-01-02,10.00,10.40,9.90,10.30,1000000
2026-01-05,10.30,10.55,10.10,10.20,850000
2026-01-06,10.20,10.60,10.15,10.55,910000
2026-01-07,10.55,10.90,10.50,10.85,880000
"""

MKT = CSV.replace("10.55,910000", "10.30,910000").replace("10.85,880000", "10.40,880000")


def run(args, capsys):
    code = ask.main(args)
    return code, capsys.readouterr().out


class _Response:
    def __init__(self, body): self._body = body.encode()
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *e): return False


@pytest.fixture
def feed(monkeypatch):
    """Patch the feed factory, not the network: the CLI must go through it."""
    bodies = {}

    def install(mapping):
        bodies.update(mapping)

        def opener(req, timeout=None):
            for sym, body in bodies.items():
                if f"s={sym}" in req.full_url:
                    return _Response(body)
            return _Response("No data\n")

        monkeypatch.setattr(ask, "_feed", lambda: StooqFeed(opener=opener))
    return install


# -- thesis + red team (A10, A11) --------------------------------------------
def test_a_thesis_with_two_breakers_and_its_evidence_is_actionable(capsys):
    code, out = run(["thesis", "MYX:1155",
                     "--breaker", "NIM below 2.0%|nim < 0.020|kb_filings",
                     "--breaker", "CASA below 25%|casa < 0.25|kb_filings",
                     "--evidence", "a1_fundamentals=CASA fell to 24%",
                     "--evidence", "a2_valuation=P/B at the 12th percentile",
                     "--evidence", "a5_catalyst_events=results due in 3 weeks",
                     "--evidence", "a6_macro_regime=OPR on hold",
                     "--stance", "accumulate"], capsys)
    assert code == 0
    assert "actionable: yes" in out
    assert "red team" in out


def test_accumulating_without_valuation_evidence_is_downgraded_not_allowed(capsys):
    """A10 refuses to accumulate on fundamentals alone. The CLI must surface that
    rather than printing the stance it was asked for."""
    _, out = run(["thesis", "MYX:1155",
                  "--breaker", "NIM below 2.0%|nim < 0.020|kb_filings",
                  "--breaker", "CASA below 25%|casa < 0.25|kb_filings",
                  "--evidence", "a1_fundamentals=CASA fell to 24%",
                  "--stance", "accumulate"], capsys)
    assert "No view" in out
    assert "cannot accumulate without fundamentals and a valuation range" in out
    assert "[coverage]" in out, "gaps must draw a challenge, not silence"


def test_fewer_than_two_breakers_forfeits_the_stance(capsys):
    _, out = run(["thesis", "MYX:1155",
                  "--breaker", "NIM below 2.0%|nim < 0.020|kb_filings",
                  "--stance", "accumulate"], capsys)
    assert "actionable: no" in out
    assert "no stance may be taken" in out


def test_a_breaker_with_no_query_is_refused_by_the_cli(capsys):
    """docs/04 6: a breaker that cannot be checked is a wish. The CLI must not
    default a query in and route around the check."""
    with pytest.raises(SystemExit, match="statement|query|store"):
        ask.main(["thesis", "MYX:1155", "--breaker", "NIM falls"])


def test_the_red_team_is_never_silent_on_a_live_thesis(capsys):
    _, out = run(["thesis", "XNAS:NVDA",
                  "--breaker", "a|q|s", "--breaker", "b|q|s",
                  "--evidence", "a1_fundamentals=x",
                  "--evidence", "a2_valuation=y",
                  "--evidence", "a5_catalyst_events=z",
                  "--evidence", "a6_macro_regime=w",
                  "--stance", "accumulate"], capsys)
    assert "(silent" not in out


def test_malformed_evidence_is_refused():
    with pytest.raises(SystemExit, match="agent=text"):
        ask.main(["thesis", "MYX:1155", "--evidence", "no-equals-sign"])


# -- portfolio risk (A12) -----------------------------------------------------
def test_a_concentrated_book_reports_every_breach(capsys):
    code, out = run(["risk",
                     "--position", "MYX:1155:0.22:bank:MY:0.01",
                     "--position", "XNAS:NVDA:0.18:tech:US:0.02"], capsys)
    assert code == 0
    assert out.count("single_name breach") == 2
    assert "HHI" in out and "effective bets" in out


def test_an_empty_book_says_so_rather_than_dividing_by_zero(capsys):
    code, out = run(["risk"], capsys)
    assert code == 0
    assert "no positions held" in out


def test_a_malformed_position_is_refused_not_guessed():
    with pytest.raises(SystemExit, match="MIC:CODE:weight"):
        ask.main(["risk", "--position", "MYX:1155:0.22"])


def test_a_non_numeric_weight_is_refused():
    with pytest.raises(SystemExit, match="must be numbers"):
        ask.main(["risk", "--position", "MYX:1155:heavy:bank:MY"])


# -- sizing (A13) -------------------------------------------------------------
def test_sizing_uses_the_markets_own_fee_schedule(capsys):
    code, out = run(["size", "MYX:1155", "--portfolio", "200000",
                     "--price", "6.20", "--stop", "5.60", "--adv", "900000"], capsys)
    assert code == 0
    assert "XKLS fee schedule" in out
    assert "60 bps round trip on XKLS" in out, "MYX must resolve to Bursa's floor, not the default"


def test_the_documented_minimum_bursa_position_appears(capsys):
    """README records ~RM 4,700. A flat-bps cost model cannot produce it: fees as
    a constant fraction never fall with size, so the bisection runs to its
    ceiling and reports RM 100,000,000."""
    _, out = run(["size", "MYX:1155", "--portfolio", "200000",
                  "--price", "6.20", "--stop", "5.60", "--adv", "900000"], capsys)
    assert "4,705" in out
    assert "100,000,000" not in out


def test_a_portfolio_too_small_to_fund_a_lot_gets_no_position(capsys):
    _, out = run(["size", "MYX:1155", "--portfolio", "5000",
                  "--price", "6.20", "--stop", "5.60", "--adv", "900000"], capsys)
    assert "no position" in out


def test_a_stop_above_the_entry_is_refused(capsys):
    code, _ = run(["size", "MYX:1155", "--portfolio", "200000",
                   "--price", "6.20", "--stop", "6.50", "--adv", "900000"], capsys)
    assert code == 2


def test_an_unadaptered_market_falls_back_to_a_model_with_a_minimum(capsys):
    """Without a fixed minimum there is no floor to find at all."""
    code, out = run(["size", "XFRA:BMW", "--portfolio", "200000", "--cost-bps", "5",
                     "--price", "6.20", "--stop", "5.60", "--adv", "900000"], capsys)
    assert code == 0
    assert "no adapter for XFRA" in out
    assert "100,000,000" not in out


def test_a_cost_rate_above_the_floor_is_reported_as_impossible_not_as_rm_100m(capsys):
    """cost_floor_value bisects between 1 and 100,000,000. When the asymptotic
    round-trip cost already exceeds the floor, nothing satisfies it and the
    search returns its ceiling - which reads as a position requirement rather
    than the impossibility it is."""
    code, out = run(["size", "XFRA:BMW", "--portfolio", "200000", "--cost-bps", "46",
                     "--price", "6.20", "--stop", "5.60", "--adv", "900000"], capsys)
    assert code == 0
    assert "100,000,000" not in out
    assert "at ANY size" in out
    assert "no position" in out


# -- teacher (A14) ------------------------------------------------------------
def test_the_syllabus_lists_every_level(capsys):
    code, out = run(["learn", "--syllabus"], capsys)
    assert code == 0
    assert "L1" in out and "L8" in out


def test_a_concept_taught_before_its_prerequisites_is_refused(capsys):
    code, out = run(["learn", "kelly"], capsys)
    assert code == 2, "order is enforced, not suggested"
    assert "has to come first" in out


def test_a_concept_that_does_not_exist_exits_nonzero(capsys):
    """Exiting 0 would let a script believe it taught something."""
    code, _ = run(["learn", "kelly_criterion"], capsys)
    assert code == 2


def test_the_full_prerequisite_chain_unlocks_the_concept(capsys):
    chain = ["share", "compounding", "volatility", "trend_vs_noise",
             "factor_decomposition", "base_rate", "expected_value", "position_sizing"]
    args = ["learn", "kelly"]
    for k in chain:
        args += ["--mastered", k]
    code, out = run(args, capsys)
    assert code == 0
    assert "Kelly" in out


def test_claiming_mastery_of_a_concept_that_does_not_exist_is_refused(capsys):
    """It used to be accepted, then raise a bare KeyError from `level` on the
    next call - and a mis-typed prerequisite reads as unmet forever."""
    code, _ = run(["learn", "kelly", "--mastered", "probability"], capsys)
    assert code == 2


def test_next_concept_is_offered_with_no_argument(capsys):
    code, out = run(["learn"], capsys)
    assert code == 0 and out.strip()


# -- prices (the feed) --------------------------------------------------------
def test_prices_prints_bars_and_the_window_return(capsys, feed):
    feed({"nvda.us": CSV})
    code, out = run(["prices", "XNAS:NVDA"], capsys)
    assert code == 0
    assert "2026-01-02" in out and "2026-01-07" in out
    assert "return over the shown window" in out
    assert "ADV" in out and "ATR" in out


def test_prices_honours_an_as_at_date(capsys, feed):
    feed({"nvda.us": CSV})
    _, out = run(["prices", "XNAS:NVDA", "--on", "2026-01-05"], capsys)
    assert "2026-01-06" not in out


def test_an_unreachable_symbol_exits_nonzero_rather_than_printing_nothing(capsys, feed):
    feed({})
    code, _ = run(["prices", "XNAS:NVDA"], capsys)
    assert code == 3


# -- why --fetch --------------------------------------------------------------
def test_fetch_measures_both_legs_from_the_feed(capsys, feed):
    feed({"nvda.us": CSV, "spy.us": MKT})
    code, out = run(["why", "XNAS:NVDA", "--fetch", "--against", "XNAS:SPY",
                     "--days", "2", "--on", "2026-01-07",
                     "--move", "0", "--market", "0"], capsys)
    assert code == 0
    assert "measured" in out
    assert "stated, not measured" not in out


def test_fetch_without_a_market_proxy_is_refused(capsys):
    code, _ = run(["why", "XNAS:NVDA", "--fetch",
                   "--move", "0.05", "--market", "0.01"], capsys)
    assert code == 2, "a measured leg against a typed leg is not a decomposition"


def test_typed_returns_are_labelled_as_stated(capsys):
    _, out = run(["why", "MYX:1155", "--move", "-0.09", "--market", "-0.08"], capsys)
    assert "stated, not measured" in out


def test_too_few_bars_for_the_window_is_a_refusal_not_a_short_window(capsys, feed):
    feed({"nvda.us": CSV, "spy.us": MKT})
    code, _ = run(["why", "XNAS:NVDA", "--fetch", "--against", "XNAS:SPY",
                   "--days", "90", "--on", "2026-01-07",
                   "--move", "0", "--market", "0"], capsys)
    assert code == 3


# -- backend ------------------------------------------------------------------
def test_backend_says_plainly_when_it_is_not_a_model(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    code, out = run(["backend"], capsys)
    assert code == 0
    assert "EchoBackend" in out
    assert "NOT a model" in out


def test_backend_reports_the_real_one_when_a_key_exists(capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    code, out = run(["backend"], capsys)
    assert code == 0 and "AnthropicBackend" in out


def test_forcing_a_backend_without_its_key_exits_nonzero(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code, _ = run(["backend", "--use", "anthropic"], capsys)
    assert code == 3
