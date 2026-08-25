"""User settings, loaded within bounds that a file cannot widen.

`Limits` already says it: "Defaults are user-configurable within bounds; the
bounds are not." This module is what makes that survive contact with a config
file, because the obvious implementation - read TOML, splat it into the
dataclass - hands an attacker, or a frustrated user at 2am, the ability to turn
every risk limit off by editing one line.

So three rules hold here:

  1. Every limit is constructed through `Limits`, so its __post_init__ bounds
     apply to config exactly as they apply to code.
  2. Values that are not bounded by a dataclass are bounded here, explicitly,
     with the reason in the error.
  3. There is nothing in this file that can enable a tool, reach a market with
     no adapter, or disable a rail. Those are not settings.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD
from engines.risk.concentration import Limits

#: config.local.toml wins when present, so personal numbers stay out of git.
SEARCH = ("config.local.toml", "config.toml")

#: Bounds that no file may cross. Distinct from defaults, which live in the file.
#: (key, minimum, maximum, why)
HARD_BOUNDS: tuple[tuple[str, float, float, str], ...] = (
    ("risk.risk_per_trade", 0.0, 0.02,
     "risking more than 2% per trade turns a normal losing streak into ruin"),
    ("risk.target_volatility", 0.02, 1.00,
     "a volatility target outside 2%-100% annualised is not a target"),
    ("risk.max_participation", 0.0, 0.10,
     "above 10% of daily volume you are not taking the price, you are making it"),
    ("waterfall.emergency_months", 3, 24,
     "an emergency floor below three months is not a floor"),
    ("waterfall.debt_hurdle", 0.0, 1.0,
     "the debt hurdle is an annual rate"),
    ("budget.daily_myr", 0.0, 10_000.0,
     "a daily budget above RM 10,000 is a typo, not a decision"),
    ("budget.per_question_myr", 0.0, 1_000.0,
     "a per-question budget above RM 1,000 is a typo, not a decision"),
    ("learning.min_graded_for_calibration", 30, 1_000,
     "below 30 graded calls a calibration table measures luck; lowering the "
     "threshold does not make you calibrated sooner"),
)


class ConfigError(ValueError):
    """A malformed or out-of-bounds config is a startup failure, never a warning."""


@dataclass(frozen=True)
class Config:
    base_currency: str
    markets: tuple[str, ...]
    fx_myr_per_usd: Decimal
    risk_per_trade: Decimal
    target_volatility: Decimal
    max_participation: Decimal
    limits: Limits
    emergency_months: int
    debt_hurdle: Decimal
    daily_budget_myr: Decimal
    per_question_budget_myr: Decimal
    database: str
    min_graded_for_calibration: int
    source: str = "<defaults>"

    def describe(self) -> str:
        return (
            f"config from {self.source}\n"
            f"  base currency        {self.base_currency}\n"
            f"  markets              {', '.join(self.markets)}\n"
            f"  risk per trade       {self.risk_per_trade:.2%}\n"
            f"  single-name cap      {self.limits.single_name:.0%}"
            f"   (hard ceiling 15%)\n"
            f"  min effective bets   {self.limits.min_effective_bets:.1f}"
            f"   (hard floor 3.0)\n"
            f"  portfolio heat       {self.limits.portfolio_heat:.1%}\n"
            f"  max participation    {self.max_participation:.1%}\n"
            f"  daily budget         RM {self.daily_budget_myr:.2f}\n"
            f"  prediction log       {self.database}"
        )


def _get(data: dict, dotted: str, default):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _check_bounds(data: dict) -> None:
    for key, lo, hi, why in HARD_BOUNDS:
        value = _get(data, key, None)
        if value is None:
            continue
        if not isinstance(value, (int, float)):
            raise ConfigError(f"{key} must be a number, got {value!r}")
        if not lo <= value <= hi:
            raise ConfigError(
                f"{key} = {value} is outside the permitted range [{lo}, {hi}]: {why}. "
                "This bound is not configurable."
            )


def _limits(data: dict) -> Limits:
    """Straight through the dataclass, so its bounds apply to config too."""
    section = data.get("limits", {}) or {}
    known = {f for f in Limits.__dataclass_fields__}
    unknown = set(section) - known
    if unknown:
        raise ConfigError(
            f"unknown [limits] keys {sorted(unknown)}. A misspelled limit silently "
            "does nothing, which is the most dangerous kind of typo in this file."
        )
    try:
        return Limits(**section)
    except ValueError as e:
        raise ConfigError(f"[limits] rejected: {e}. This bound is not configurable.") from None


def find(start: Path | None = None) -> Path | None:
    base = start or Path.cwd()
    for name in SEARCH:
        p = base / name
        if p.exists():
            return p
    return None


def load(path: str | Path | None = None) -> Config:
    """Load settings. Absent file means documented defaults, never zero limits."""
    p = Path(path) if path else find()
    if p is None:
        data, source = {}, "<defaults>"
    else:
        try:
            data = tomllib.loads(Path(p).read_text())
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{p} is not valid TOML: {e}") from None
        source = str(p)

    _check_bounds(data)
    limits = _limits(data)

    markets = tuple(_get(data, "account.markets", ["XKLS", "XNAS"]))
    from markets.registry import supported
    unknown = [m for m in markets if m not in supported()]
    if unknown:
        raise ConfigError(
            f"no adapter for {unknown}. A market is one adapter class plus a registry "
            f"entry; listing a MIC here does not create one. Supported: {supported()}"
        )

    dec = lambda k, d: Decimal(str(_get(data, k, d)))
    return Config(
        base_currency=str(_get(data, "account.base_currency", "MYR")).upper(),
        markets=markets,
        fx_myr_per_usd=dec("account.fx_myr_per_usd", DEFAULT_FX_MYR_PER_USD),
        risk_per_trade=dec("risk.risk_per_trade", 0.0075),
        target_volatility=dec("risk.target_volatility", 0.20),
        max_participation=dec("risk.max_participation", 0.05),
        limits=limits,
        emergency_months=int(_get(data, "waterfall.emergency_months", 6)),
        debt_hurdle=dec("waterfall.debt_hurdle", 0.08),
        daily_budget_myr=dec("budget.daily_myr", 25.0),
        per_question_budget_myr=dec("budget.per_question_myr", 5.0),
        database=str(_get(data, "learning.database", "data/learning.db")),
        min_graded_for_calibration=int(_get(data, "learning.min_graded_for_calibration", 30)),
        source=source,
    )
