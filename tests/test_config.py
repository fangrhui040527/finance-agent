"""Settings load within bounds a file cannot widen.

`Limits` says "Defaults are user-configurable within bounds; the bounds are not."
The obvious config implementation - read TOML, splat into the dataclass - breaks
that promise silently. These tests are the promise.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import HARD_BOUNDS, Config, ConfigError, find, load
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
    c = (
        load(Path("/nonexistent/config.toml"))
        if False
        else Config(
            base_currency="MYR",
            markets=("XKLS",),
            fx_myr_per_usd=Decimal("4.15"),
            risk_per_trade=Decimal("0.0075"),
            target_volatility=Decimal("0.20"),
            max_participation=Decimal("0.05"),
            limits=Limits(),
            emergency_months=6,
            debt_hurdle=Decimal("0.08"),
            daily_budget_myr=Decimal("25"),
            per_question_budget_myr=Decimal("5"),
            database="data/learning.db",
            min_graded_for_calibration=30,
        )
    )
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
    """Named a market with no adapter -> refused.

    The unsupported MIC is chosen at runtime rather than hard-coded. This test
    used to name XHKG, and silently changed meaning the day Hong Kong was
    registered: it went on passing for a while, then failed for a reason that
    had nothing to do with the guard it exists to hold.
    """
    from markets.registry import supported

    unsupported = next(m for m in ("XFRA", "XETR", "XAMS", "XPAR") if m not in supported())
    with pytest.raises(ConfigError, match="does not create one"):
        load(write(tmp_path, f'[account]\nmarkets = ["XKLS", "{unsupported}"]\n'))


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


def test_a_local_override_wins_over_the_committed_file(tmp_path, monkeypatch):
    # The one test about WHICH FILE WINS must not have the winner pinned for it.
    # The suite-wide fixture pins FINPLANET_CONFIG so no test reads an
    # operator's real position; here the search order is the subject.
    monkeypatch.delenv("FINPLANET_CONFIG", raising=False)
    (tmp_path / "config.toml").write_text("[limits]\nsingle_name = 0.08\n")
    (tmp_path / "config.local.toml").write_text("[limits]\nsingle_name = 0.05\n")
    assert find(tmp_path).name == "config.local.toml"
    assert load(find(tmp_path)).limits.single_name == 0.05


def test_the_pin_beats_the_search_and_refuses_a_path_that_is_not_there(tmp_path, monkeypatch):
    """FINPLANET_CONFIG is how a test says "the shipped file, whatever this box
    has". It has to beat config.local.toml or it would not do that job, and it
    has to REFUSE a missing path rather than fall back: a typo that quietly
    loaded a different financial position would make every number downstream
    right about the wrong file."""
    (tmp_path / "config.toml").write_text("[limits]\nsingle_name = 0.08\n")
    (tmp_path / "config.local.toml").write_text("[limits]\nsingle_name = 0.05\n")

    monkeypatch.setenv("FINPLANET_CONFIG", str(tmp_path / "config.toml"))
    assert find(tmp_path).name == "config.toml"
    assert load(find(tmp_path)).limits.single_name == 0.08

    monkeypatch.setenv("FINPLANET_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(ConfigError, match="FINPLANET_CONFIG"):
        find(tmp_path)


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
    for token in (
        "ask.py why",
        "ask.py plan",
        "predict.py log",
        "predict.py due",
        "predict.py grade",
        "predict.py status",
    ):
        assert token in bat, f"run.bat does not expose {token}"


def test_the_batch_file_refuses_to_run_without_a_venv():
    bat = BAT.read_text()
    assert "No virtual environment found" in bat
    assert "run install" in bat


# -- no re-duplication -------------------------------------------------------


def test_the_planning_rate_has_one_python_source():
    """It was in four places: config.toml, .env.example, a literal in the config
    loader, and the ledger constant - with no code reading the env var at all.
    Editing .env did nothing, which is worse than the value being wrong."""
    from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD

    loader = (ROOT / "core" / "config.py").read_text()
    assert "DEFAULT_FX_MYR_PER_USD" in loader
    assert "4.15" not in loader, "the loader must not restate the rate literal"
    assert load(ROOT / "config.toml").fx_myr_per_usd == DEFAULT_FX_MYR_PER_USD


def test_the_env_example_holds_no_settings_that_config_toml_owns():
    """Two places to set one number is one place too many: whichever the reader
    edits, the other silently wins."""
    env = (ROOT / ".env.example").read_text()
    for key in ("FX_MYR_PER_USD", "DAILY_BUDGET_MYR"):
        assert key not in env, f"{key} duplicates config.toml and is read by nothing"


def test_every_env_example_key_is_actually_used_somewhere():
    """A key that sets nothing is worse than a missing key: it reads as
    configured."""
    import re

    from tests._repo import iter_source_files

    env = (ROOT / ".env.example").read_text()
    keys = re.findall(r"^([A-Z_]+)=", env, re.M)
    compose = (ROOT / "infra" / "docker-compose.yml").read_text()
    # Compose is not the only consumer: an adapter that reads os.environ counts
    # too. Checking only compose forces a growing exemption list, and the
    # exemptions are exactly where an unread key would hide.
    code = "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in iter_source_files()
        if "os.environ" in p.read_text(encoding="utf-8", errors="replace")
    )
    unused = [
        k for k in keys if k not in compose and k not in code and k not in {"ANTHROPIC_API_KEY"}
    ]
    assert not unused, f"env keys referenced nowhere: {unused}"


def test_no_package_contains_only_an_init_file():
    """knowledge/provenance/ was one: created in P0, superseded by
    core/provenance/, and left behind as an importable empty package."""
    from tests._repo import iter_source_files

    inits = [p for p in iter_source_files() if p.name == "__init__.py"]
    empty = []
    for init in inits:
        pkg = init.parent
        # Recursive on purpose: git's `pkg/*.py` pathspec spans `/`, so a
        # package whose only children are subpackages was never flagged.
        siblings = [p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts]
        if len(siblings) == 1:
            empty.append(pkg.relative_to(ROOT).as_posix())
    assert not empty, f"packages with nothing in them: {empty}"


# -- read-only names ---------------------------------------------------------


def test_read_only_names_load_and_never_overlap_the_book(tmp_path):
    cfg = load(ROOT / "config.toml")
    assert "XTAI:2330" in cfg.read_only
    assert not set(cfg.read_only) & (set(cfg.watchlist) | set(cfg.holdings))
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    bad = tmp_path / "bad.toml"
    bad.write_text(
        text.replace('read_only = ["XTAI:2330"]', 'read_only = ["XNAS:NVDA"]'), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="read_only"):
        load(bad)
    typo = tmp_path / "typo.toml"
    typo.write_text(
        text.replace('read_only = ["XTAI:2330"]', 'read_only = ["2330"]'), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="market prefix"):
        load(typo)
