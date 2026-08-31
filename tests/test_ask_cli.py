"""The entrypoint docs/14 month 1 needs.

Before this existed the runbook told the operator to ask the system questions
and there was no way to ask - the agents were classes with no entrypoint.
"""

import ask


def run(args, capsys):
    code = ask.main(args)
    return code, capsys.readouterr().out


# -- why ---------------------------------------------------------------------


def test_a_market_wide_fall_is_reported_as_the_market(capsys):
    code, out = run(
        ["why", "MYX:1155", "--move", "-0.090", "--market", "-0.080", "--sector", "-0.020"], capsys
    )
    assert code == 0
    assert "market_driven" in out
    assert "No cause was sought" in out


def test_an_idiosyncratic_move_reports_its_unexplained_share(capsys):
    _, out = run(
        [
            "why",
            "XNAS:NVDA",
            "--move",
            "0.072",
            "--market",
            "0.004",
            "--sector",
            "0.002",
            "--currency",
            "USD",
        ],
        capsys,
    )
    assert "no_identified_catalyst" in out
    assert "92%" in out
    assert "historically reverse" in out


def test_the_bars_show_every_component_before_any_sentence(capsys):
    _, out = run(
        ["why", "MYX:1155", "--move", "-0.090", "--market", "-0.080", "--sector", "-0.020"], capsys
    )
    for component in ("market", "sector", "style", "currency", "idiosyncratic"):
        assert component in out
    assert out.index("unexplained") < out.index("This was the market")


def test_stated_betas_are_labelled_as_stated_not_estimated(capsys):
    """The synthetic fit is a stand-in. If the output ever stops saying so, a
    reader will take a made-up beta for a measured one."""
    _, out = run(["why", "MYX:1155", "--move", "-0.09", "--market", "-0.08"], capsys)
    assert "betas are stated, not estimated" in out


def test_a_real_history_file_suppresses_the_stated_beta_warning(tmp_path, capsys):
    import random

    rng = random.Random(3)
    csv = tmp_path / "h.csv"
    csv.write_text(
        "instrument,market,sector\n"
        + "\n".join(
            f"{1.1 * m + 0.5 * s + rng.gauss(0, 0.004):.6f},{m:.6f},{s:.6f}"
            for m, s in ((rng.gauss(0, 0.01), rng.gauss(0, 0.008)) for _ in range(200))
        )
    )
    _, out = run(
        ["why", "MYX:1155", "--move", "-0.09", "--market", "-0.08", "--history", str(csv)], capsys
    )
    assert "betas are stated" not in out
    assert "market" in out


def test_too_little_history_refuses_to_guess(tmp_path, capsys):
    csv = tmp_path / "short.csv"
    csv.write_text("\n".join("0.01,0.01,0.01" for _ in range(30)))
    _, out = run(
        ["why", "X", "--move", "-0.09", "--market", "-0.08", "--history", str(csv)], capsys
    )
    assert "attribution unavailable" in out.lower()


# -- plan --------------------------------------------------------------------


def test_planning_shows_the_route_and_its_cost_in_ringgit(capsys):
    code, out = run(["plan", "why did maybank fall today", "--instrument", "MYX:1155"], capsys)
    assert code == 0
    assert "why_it_moved" in out
    assert "RM" in out
    assert "a9_attribution" in out


def test_an_execution_request_is_refused_with_a_card_not_a_traceback(capsys):
    code, out = run(["plan", "buy 1000 shares of tenaga for me"], capsys)
    assert code == 2, "a refusal exits non-zero but is not a crash"
    assert "CANNOT ANSWER THIS" in out
    assert "no broker connection by design" in out


def test_a_budget_too_small_is_refused_rather_than_cheapened(capsys):
    code, out = run(
        ["plan", "should i buy nvidia", "--instrument", "XNAS:NVDA", "--budget", "0.05"], capsys
    )
    assert code == 2
    assert "above the RM 0.05 budget" in out


def test_the_allowlist_comes_from_the_registry():
    """Regression guard: a hand-written allowlist here would drift from the
    registry the same way the six agent ids did."""
    from core.registry.loader import load

    reg = load("agents/registry.yaml")
    ctx = ask.context()
    allow = next(r for r in ctx.engine.rules if hasattr(r, "allowed"))
    assert allow.allowed == reg.allowlist()


def test_a_collinear_history_is_a_refusal_not_a_traceback(tmp_path, capsys):
    """The engine raises on a singular design matrix, which is correct. The CLI
    must turn that into attribution_unavailable rather than a stack trace."""
    csv = tmp_path / "flat.csv"
    csv.write_text("\n".join("0.01,0.01,0.01" for _ in range(200)))
    code, out = run(
        ["why", "X", "--move", "-0.09", "--market", "-0.08", "--history", str(csv)], capsys
    )
    assert code == 0
    assert "attribution unavailable" in out.lower()
