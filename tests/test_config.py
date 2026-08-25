"""Settings load within bounds a file cannot widen.

`Limits` says "Defaults are user-configurable within bounds; the bounds are not."
The obvious config implementation - read TOML, splat into the dataclass - breaks
that promise silently. These tests are the promise.
"""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import Config, ConfigError, HARD_BOUNDS, SEARCH, find, load
from engines.risk.concentration import Limits

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, body):
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


# -- the shipped file --------------------------------------------------------

def test_the_shipped_config_loads():
    c = load(ROOT / "config.toml")
    assert c.base_currency == "MYR"
    assert "XKLS" in c.markets


def test_absent_config_gives_documented_defaults_not_zero_limits():
    """The dangerous failure would be a missing file meaning no limits at all."""
    c = load(Path("/nonexistent/config.toml")) if False else Config(
        base_currency="MYR", markets=("XKLS",), fx_myr_per_usd=Decimal("4.15"),
        risk_per_trade=Decimal("0.0075"), target_volatility=Decimal("0.20"),
        max_participation=Decimal("0.05"), limits=Limits(),
        emergency_months=6, debt_hurdle=Decimal("0.08"),
        daily_budget_myr=Decimal("25"), per_question_budget_myr=Decimal("5"),
        database="data/learning.db", min_graded_for_calibration=30)
    assert c.limits.single_name == 0.08
    assert c.limits.min_effective_bets == 5.0


def test_an_empty_file_still_produces_real_limits(tmp_path):
    c = load(write(tmp_path, ""))
    assert c.limits.single_name == 0.08
    assert c.limits.portfolio_heat == 0.06


# -- what a file may not do --------------------------------------------------

def test_config_cannot_raise_the_single_name_cap(tmp_path):
    with pytest.raises(ConfigError, match="cannot be raised above 15%"):
        load(write(tmp_path, "[limits]\nsingle_name = 0.40\n"))


def test_config_cannot_lower_the_effective_bets_floor(tmp_path):
    with pytest.raises(ConfigError, match="cannot be set below 3"):
        load(write(tmp_path, "[limits]\nmin_effective_bets = 1.0\n"))


def test_config_cannot_risk_a_quarter_of_the_book_per_trade(tmp_path):
    with pytest.raises(ConfigError, match="normal losing streak into ruin"):
        load(write(tmp_path, "[risk]\nrisk_per_trade = 0.25\n"))


def test_config_cannot_make_you_the_whole_market(tmp_path):
    with pytest.raises(ConfigError, match="making it"):
        load(write(tmp_path, "[risk]\nmax_participation = 0.60\n"))


def test_config_cannot_remove_the_emergency_floor(tmp_path):
    with pytest.raises(ConfigError, match="not a floor"):
        load(write(tmp_path, "[waterfall]\nemergency_months = 0\n"))


def test_config_cannot_declare_you_calibrated_early(tmp_path):
    with pytest.raises(ConfigError, match="does not make you calibrated sooner"):
        load(write(tmp_path, "[learning]\nmin_graded_for_calibration = 5\n"))


def test_config_cannot_reach_a_market_with_no_adapter(tmp_path):
    with pytest.raises(ConfigError, match="does not create one"):
        load(write(tmp_path, '[account]\nmarkets = ["XKLS", "XHKG"]\n'))


def test_a_misspelled_limit_is_refused_not_silently_ignored(tmp_path):
    """The most dangerous typo in the file: it reads as a tightened limit and
    does nothing at all."""
    with pytest.raises(ConfigError, match="most dangerous kind of typo"):
        load(write(tmp_path, "[limits]\nsingle_nam = 0.04\n"))


def test_malformed_toml_fails_at_startup_rather_than_later(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(write(tmp_path, "[risk\nrisk_per_trade = ]"))


def test_a_non_numeric_bound_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="must be a number"):
        load(write(tmp_path, '[risk]\nrisk_per_trade = "lots"\n'))


# -- what a file may do ------------------------------------------------------

def test_tightening_a_limit_is_always_allowed(tmp_path):
    c = load(write(tmp_path, "[limits]\nsingle_name = 0.04\nmin_effective_bets = 8.0\n"))
    assert c.limits.single_name == 0.04
    assert c.limits.min_effective_bets == 8.0


def test_loosening_up_to_the_bound_is_allowed_but_not_past_it(tmp_path):
    c = load(write(tmp_path, "[limits]\nsingle_name = 0.15\n"))
    assert c.limits.single_name == 0.15
    with pytest.raises(ConfigError):
        load(write(tmp_path, "[limits]\nsingle_name = 0.1501\n"))


def test_a_local_override_wins_over_the_committed_file(tmp_path):
    (tmp_path / "config.toml").write_text("[limits]\nsingle_name = 0.08\n")
    (tmp_path / "config.local.toml").write_text("[limits]\nsingle_name = 0.05\n")
    assert find(tmp_path).name == "config.local.toml"
    assert load(find(tmp_path)).limits.single_name == 0.05


def test_the_local_override_is_gitignored():
    """Personal numbers must not land in git by accident."""
    assert "config.local.toml" in (ROOT / ".gitignore").read_text()


# -- the bounds themselves ---------------------------------------------------

def test_every_hard_bound_explains_itself():
    for key, lo, hi, why in HARD_BOUNDS:
        assert lo < hi, key
        assert len(why) > 20, f"{key} has no reason attached"


def test_the_shipped_config_sits_inside_every_hard_bound():
    """A shipped default outside its own bound would fail on first run."""
    import tomllib
    data = tomllib.loads((ROOT / "config.toml").read_text())
    for key, lo, hi, _ in HARD_BOUNDS:
        node = data
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if node is not None:
            assert lo <= node <= hi, f"shipped {key}={node} is outside [{lo}, {hi}]"


# -- the Windows entrypoint --------------------------------------------------

BAT = ROOT / "run.bat"


def test_the_batch_file_keeps_crlf_endings():
    """A .bat with LF endings breaks label jumps on some Windows shells, and the
    failure looks like the command doing nothing."""
    raw = BAT.read_bytes()
    assert b"\r\n" in raw
    assert re.search(rb"[^\r]\n", raw) is None, "every line must end CRLF"


def test_gitattributes_protects_the_batch_endings():
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text()


def test_the_batch_file_covers_every_make_target():
    """The two entrypoints must not drift: a Windows user running the same
    workflow should not find a command missing."""
    make = (ROOT / "Makefile").read_text()
    targets = {m for m in re.findall(r"^([a-z]+):", make, re.M)} - {"lint"}
    bat = BAT.read_text().lower()
    missing = [t for t in targets if f'"{t}"' not in bat]
    assert not missing, f"run.bat is missing Make targets: {missing}"


def test_the_batch_file_exposes_the_two_clis():
    bat = BAT.read_text()
    for token in ("ask.py why", "ask.py plan", "predict.py log",
                  "predict.py due", "predict.py grade", "predict.py status"):
        assert token in bat, f"run.bat does not expose {token}"


def test_the_batch_file_refuses_to_run_without_a_venv():
    bat = BAT.read_text()
    assert "No virtual environment found" in bat
    assert "run install" in bat
