"""Phase 1 - the capital-planning surface: config shape, and the three CLI rows.

feature/capital-plan (merged at b5190ea) gave the config a [capital] block and
a table form for holdings. The property owner's summary, pinned here:

  * `.stated` separates "nothing entered" from "the answer is zero" - the
    shipped config has the block with zeros and is NOT a stated plan.
  * Wrong shapes are refused, never defaulted: a rate written as 17, a
    misspelled key, a goal missing its date, negative money.
  * `Config.holdings` (bare ids) is unchanged, so every old consumer is
    untouched; the table form lands in `Config.book` as `Holding`s.
  * An allocate line reports the limit that DECIDED the size (six Malaysian
    names report "country", not the per-name cap the trim loop overrode).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.config import ConfigError, load
from qa.conftest import ROOT


def write_cfg(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# -- the [capital] block --------------------------------------------------------

def test_the_shipped_config_has_the_block_but_no_stated_plan():
    cfg = load(ROOT / "config.toml")
    assert cfg.capital.stated is False, "zeros are an unfilled form, not a plan"
    assert cfg.capital.liquid_assets == 0
    assert cfg.book == ()
    assert "no [capital] plan" in cfg.describe()


def test_a_stated_plan_parses_goals_and_liabilities_into_typed_rows(tmp_path):
    cfg = load(write_cfg(tmp_path, """
[capital]
liquid_assets = 120000
essential_monthly_spend = 4000
planned_monthly_contribution = 1500
goals = [{ name = "car", amount = 20000, months_away = 12 }]
liabilities = [{ name = "card", balance = 8000, annual_rate = 0.18 }]
"""))
    plan = cfg.capital
    assert plan.stated is True
    assert plan.liquid_assets == Decimal(120000)
    assert plan.goals[0].name == "car" and plan.goals[0].months_away == 12
    assert plan.liabilities[0].annual_rate == Decimal("0.18")
    assert "liquid" in cfg.describe()


@pytest.mark.parametrize("body, message", [
    ("[capital]\nliabilities = [{ name = 'card', balance = 100, annual_rate = 17 }]\n",
     "write 0.17 for 17%"),
    ("[capital]\nliquid_asets = 5\n", "unknown \\[capital\\] keys"),
    ("[capital]\ngoals = [{ name = 'car', amount = 20000 }]\n", "months_away"),
    ("[capital]\nliquid_assets = -5\n", "outside the permitted range"),
])
def test_wrong_capital_shapes_are_refused_never_defaulted(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load(write_cfg(tmp_path, body))


def test_a_negative_goal_is_refused_by_the_parser(tmp_path):
    with pytest.raises(ConfigError, match="goal"):
        load(write_cfg(tmp_path,
                       "[capital]\ngoals = [{ name = 'x', amount = -1, months_away = 3 }]\n"))


# -- holdings: bare ids and tables ---------------------------------------------

def test_holdings_tables_land_in_the_book_and_bare_ids_stay_bare(tmp_path):
    cfg = load(write_cfg(tmp_path, """
[account]
holdings = [
  "XNAS:NVDA",
  { id = "MYX:1155", units = 1000, avg_cost = 9.80, stop = 9.10, sector = "bank" },
  { id = "MYX:5225" },
]
"""))
    assert cfg.holdings == ("XNAS:NVDA", "MYX:1155", "MYX:5225"), (
        "the id tuple is unchanged in shape, so every existing consumer is untouched")
    by_id = {h.id: h for h in cfg.book}
    full = by_id["MYX:1155"]
    assert full.valued and full.units == 1000 and full.sector == "bank"
    assert not by_id["MYX:5225"].valued, "no units means the book cannot value it"


@pytest.mark.parametrize("body, message", [
    ("[account]\nholdings = [{ id = 'MYX:1155', unitz = 5 }]\n", "unknown"),
    ("[account]\nholdings = [{ id = '1155', units = 5 }]\n", "no market prefix"),
])
def test_a_bad_holding_table_fails_at_load_like_a_bad_id_always_has(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load(write_cfg(tmp_path, body))


# -- the three CLI rows, keyless -------------------------------------------------

def test_capital_on_the_shipped_config_refuses_and_names_the_floor(run_cli):
    proc = run_cli(["ask.py", "capital"])
    assert proc.returncode == 2
    out = proc.stdout + proc.stderr
    assert "no [capital] plan" in out
    assert "bypasses the emergency floor" in out


SIX_NAMES = [
    "--name", "MYX:1155:10.68:9.90:20000000:bank",
    "--name", "MYX:5225:1.50:1.35:9000000:property",
    "--name", "MYX:7113:3.20:2.90:8000000:glove",
    "--name", "MYX:6012:4.80:4.40:12000000:telco",
    "--name", "MYX:5681:22.00:20.00:15000000:consumer",
    "--name", "MYX:3816:7.40:6.80:7000000:energy",
]


def test_allocate_sizes_six_names_and_reports_the_limit_that_decided(run_cli):
    proc = run_cli(["ask.py", "allocate", "--portfolio", "200000", *SIX_NAMES])
    assert proc.returncode == 0, proc.stderr
    assert "ALLOCATION over 6 name(s)" in proc.stdout
    assert "country" in proc.stdout, (
        "six Malaysian names: the 40% country limit decides before the 8% per-name cap, "
        "and the line must name the limit that DECIDED, not the one the trim loop overrode")


def test_allocate_with_two_names_refuses_below_the_position_floor(run_cli):
    proc = run_cli(["ask.py", "allocate", "--portfolio", "200000", *SIX_NAMES[:4]])
    assert "below the 5-position floor" in (proc.stdout + proc.stderr)


def test_allocate_with_no_names_says_it_does_not_choose_them(run_cli):
    proc = run_cli(["ask.py", "allocate", "--portfolio", "200000"])
    assert proc.returncode == 2
    assert "does not choose them" in proc.stderr


def test_rebalance_on_an_empty_book_refuses_and_points_at_holdings(run_cli):
    proc = run_cli(["ask.py", "rebalance"])
    assert proc.returncode == 2
    assert "holdings" in proc.stderr
