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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from core.provenance.ledger import DEFAULT_FX_MYR_PER_USD, DEFAULT_FX_SPREAD_PER_SIDE
from engines.paper.settings import PaperSettings
from engines.risk.concentration import Limits

#: config.local.toml wins when present, so personal numbers stay out of git.
SEARCH = ("config.local.toml", "config.toml")

#: Bounds that no file may cross. Distinct from defaults, which live in the file.
#: (key, minimum, maximum, why)
HARD_BOUNDS: tuple[tuple[str, float, float, str], ...] = (
    (
        "risk.risk_per_trade",
        0.0,
        0.02,
        "risking more than 2% per trade turns a normal losing streak into ruin",
    ),
    (
        "risk.target_volatility",
        0.02,
        1.00,
        "a volatility target outside 2%-100% annualised is not a target",
    ),
    (
        "risk.max_participation",
        0.0,
        0.10,
        "above 10% of daily volume you are not taking the price, you are making it",
    ),
    ("waterfall.emergency_months", 3, 24, "an emergency floor below three months is not a floor"),
    ("waterfall.debt_hurdle", 0.0, 1.0, "the debt hurdle is an annual rate"),
    ("budget.daily_myr", 0.0, 10_000.0, "a daily budget above RM 10,000 is a typo, not a decision"),
    (
        "budget.per_question_myr",
        0.0,
        1_000.0,
        "a per-question budget above RM 1,000 is a typo, not a decision",
    ),
    (
        "capital.liquid_assets",
        0.0,
        1_000_000_000.0,
        "a liquid balance must be a positive number under a billion; outside that "
        "it is a typo, a sign error, or a different kind of problem entirely",
    ),
    (
        "capital.essential_monthly_spend",
        0.0,
        10_000_000.0,
        "essential monthly spending is what the emergency floor multiplies; a "
        "wrong figure here moves the floor, which is the one number that never bends",
    ),
    (
        "capital.planned_monthly_contribution",
        0.0,
        10_000_000.0,
        "one month of planned contributions is held back as a cash buffer, so a "
        "number this large would swallow the whole plan",
    ),
    (
        "monitor.spend_fraction",
        0.0,
        2.0,
        "an alert threshold above twice the budget can never fire before the "
        "budget rail already has",
    ),
    (
        "monitor.p95_latency_ms",
        0.0,
        600_000.0,
        "a latency threshold above ten minutes is not a threshold",
    ),
    (
        "monitor.dropped_claim_rate",
        0.0,
        1.0,
        "a share of dropped claims is a fraction of the claims checked; a "
        "threshold above 1.0 can never fire and reads as monitoring",
    ),
    (
        "monitor.silence_hours",
        0,
        8_760,
        "silence detection beyond a year is not detection",
    ),
    # The paper book (docs/22). Each of these is a ceiling or a floor the file
    # may tighten and never loosen: the profile was agreed, and a config edit
    # is not a re-agreement.
    (
        "paper.initial_cash_usd",
        100.0,
        100_000.0,
        "a paper book outside USD 100-100,000 is a typo, not a decision",
    ),
    (
        "paper.max_weight_per_name",
        0.05,
        0.25,
        "above a quarter of the book one name is the book",
    ),
    (
        "paper.min_names_when_invested",
        2,
        9,
        "fewer than two names is not a book; more than the watchlist is impossible",
    ),
    ("paper.cash_floor", 0.20, 1.0, "the cash floor is the one number that never bends downward"),
    (
        "paper.stop_loss",
        0.02,
        0.08,
        "a stop looser than 8% from cost is not the conservative profile",
    ),
    (
        "paper.drawdown_halt",
        0.02,
        0.08,
        "the halt line is the last guard; it cannot be moved past 8%",
    ),
    ("paper.weekly_turnover_cap", 0.0, 0.50, "above half the book a week the fees eat the quarter"),
    ("paper.slippage_bps_xkls", 0, 100, "slippage above 100 bps is a model of a different market"),
    ("paper.slippage_bps_xnas", 0, 100, "slippage above 100 bps is a model of a different market"),
    (
        "paper.observe_weeks",
        2,
        8,
        "fewer than two observe weeks grades no prediction before money moves",
    ),
    ("paper.ramp_weeks", 0, 12, "a ramp longer than the quarter is not a ramp"),
    ("paper.ramp_max_invested", 0.0, 0.40, "the ramp ceiling is half the full cap by agreement"),
    (
        "learning.min_graded_for_calibration",
        30,
        1_000,
        "below 30 graded calls a calibration table measures luck; lowering the "
        "threshold does not make you calibrated sooner",
    ),
)


class ConfigError(ValueError):
    """A malformed or out-of-bounds config is a startup failure, never a warning."""


@dataclass(frozen=True)
class Goal:
    """Money already spoken for, and when. Inside 24 months it holds cash."""

    name: str
    amount: Decimal
    months_away: int


@dataclass(frozen=True)
class Liability:
    """A debt. Above the hurdle it outranks equities, because paying off a 17%
    card is a guaranteed 17% return no equity thesis can honestly promise."""

    name: str
    balance: Decimal
    annual_rate: Decimal


@dataclass(frozen=True)
class Holding:
    """What you actually own. `units` and `avg_cost` are optional so a file
    that lists bare ids keeps loading - but without units the book cannot be
    valued, and anything that needs a value says so rather than assuming one."""

    id: str
    units: Decimal | None = None
    avg_cost: Decimal | None = None
    #: Where you would stop out. Optional, and its absence is not fatal: the
    #: risk-budget cap simply cannot be computed for this name, and anything
    #: that sizes it says which cap it lost rather than inventing a stop.
    stop: Decimal | None = None
    #: Sector for the concentration check. "unknown" is honest and it is also
    #: how a book of six banks looks like six sectors, so it is reported.
    sector: str = "unknown"

    @property
    def valued(self) -> bool:
        return self.units is not None


@dataclass(frozen=True)
class CapitalPlan:
    """Your financial position, from which investable capital is DERIVED.

    docs/05 section 2: before any question about which stock, there is a
    question about how much money is allowed to be in stocks at all. This is
    the input to that question; `engines/sizing/waterfall.compute` answers it.
    """

    liquid_assets: Decimal = Decimal(0)
    essential_monthly_spend: Decimal = Decimal(0)
    planned_monthly_contribution: Decimal = Decimal(0)
    goals: tuple[Goal, ...] = ()
    liabilities: tuple[Liability, ...] = ()

    @property
    def stated(self) -> bool:
        """False when nothing has been entered. An unstated plan must never be
        treated as a plan whose answer happens to be zero."""
        return self.liquid_assets > 0 or self.essential_monthly_spend > 0


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
    # Monitor thresholds. Defaulted so an older config file still loads, and
    # so a fresh installation watches itself without being told to.
    alert_spend_fraction: Decimal = Decimal("0.8")
    alert_p95_latency_ms: float = 20_000.0
    alert_dropped_claim_rate: Decimal = Decimal("0.2")
    #: 0 disables. A personal tool is allowed to sit idle; a scheduled one is not.
    alert_silence_hours: int = 0
    #: The same question asked of the SWEEP rather than the model ledger, because
    #: `ask.py sweep` makes no model calls and `alert_silence_hours` therefore
    #: cannot see it. 0 disables. Quiet until a source has succeeded once.
    alert_sweep_silence_hours: int = 0
    #: Days of collector history `slots_missed` counts over. `sweep_silence`
    #: asks whether the collector STOPPED; this asks whether it fired as often
    #: as its own cron says it should, which one manual run cannot mask.
    alert_slot_window_days: int = 0
    #: Days over which every name in the book must have collected SOMETHING.
    #: `sweep_silence` asks whether the collector stopped and `slots_missed`
    #: whether it fired; neither can see one name inside a working sweep going
    #: quiet. Petronas Chemicals held zero articles for a week while the sweep
    #: reported `ok` every run, because it was searched only as "Petronas
    #: Chemicals" and never as "PCHEM". 0 disables.
    alert_name_coverage_days: int = 0
    #: The financial position the waterfall turns into investable capital.
    capital: CapitalPlan = CapitalPlan()
    #: Holdings with units where the file gives them; `holdings` keeps the bare
    #: ids so every existing consumer is untouched.
    book: tuple[Holding, ...] = ()
    # Defaulted so a hand-built Config stays easy to write in tests, and so an
    # older config file loads without them. An empty holdings/watchlist is a
    # legitimate starting state - it just means nothing can escalate yet, which
    # describe() says out loud rather than leaving you to discover.
    holdings: tuple[str, ...] = ()
    watchlist: tuple[str, ...] = ()
    #: Names the collectors read but the book never holds: a Taiwan name as an
    #: NVDA supply-chain read, for instance. The paper book, the pack's moves
    #: table and every sizing engine ignore them; `ask.py facts` and the pack's
    #: facts block show them under 'watched, not held'.
    read_only: tuple[str, ...] = ()
    fx_spread_per_side: Decimal = DEFAULT_FX_SPREAD_PER_SIDE
    """What a currency conversion costs, one way. See the constant: unmeasured,
    and on a US position plausibly larger than every trading fee combined."""
    broker: str | None = None
    """Whose fee schedule this account actually pays.

    None means the venue's own schedule, which is what every market adapter
    ships. Naming a broker replaces it for the venues that broker prices
    differently - markets/xnas.py models a ZERO-COMMISSION US account, and an
    account that is not that shape sized against it is funded into positions
    that cannot pay for their own round trip.
    """
    provenance_db: str = "data/provenance.db"
    daemon_budget_myr: Decimal = Decimal("10.0")
    #: Feeds a scheduled sweep reads. `[sources] enabled` described these for
    #: months and nothing loaded it, so enabling a source did exactly nothing -
    #: the same defect class as the unreachable waterfall in details/10, and it
    #: fails the same way: silently, looking like a quiet world.
    sources: tuple[str, ...] = ()
    gdelt_languages: tuple[str, ...] = ()
    gdelt_countries: tuple[str, ...] = ()
    gdelt_query: str = ""
    #: Floored at GDELT's own documented 15-minute minimum by the adapter.
    gdelt_poll_minutes: int = 15
    corpus_db: str = "data/corpus.db"
    #: Where structured pulls - facts, events, series, documents - are kept.
    facts_db: str = "data/facts.db"
    #: Languages an article may be in to be kept. Empty keeps every language,
    #: which on GDELT means 38% of the corpus is in languages nobody on the
    #: book reads and none of which ever linked a Bursa name.
    languages: tuple[str, ...] = ()
    #: The paper book (docs/22): a hypothetical USD ledger and its caps.
    paper: PaperSettings = PaperSettings()
    #: Drawdown from peak at which `ask.py watch` opens an alert on the paper book.
    alert_paper_drawdown: Decimal = Decimal("0.08")
    source: str = "<defaults>"

    def describe(self) -> str:
        return (
            f"config from {self.source}\n"
            f"  base currency        {self.base_currency}\n"
            f"  markets              {', '.join(self.markets)}\n"
            f"  broker               {self.broker or 'none (venue schedules)'}\n"
            f"  risk per trade       {self.risk_per_trade:.2%}\n"
            f"  single-name cap      {self.limits.single_name:.0%}"
            f"   (hard ceiling 15%)\n"
            f"  min effective bets   {self.limits.min_effective_bets:.1f}"
            f"   (hard floor 3.0)\n"
            f"  portfolio heat       {self.limits.portfolio_heat:.1%}\n"
            f"  max participation    {self.max_participation:.1%}\n"
            f"  daily budget         RM {self.daily_budget_myr:.2f}"
            f"   (interactive)\n"
            f"  daemon budget        RM {self.daemon_budget_myr:.2f}"
            f"   (unattended; separate so a runaway job cannot eat the above)\n"
            f"  prediction log       {self.database}\n"
            f"  provenance ledger    {self.provenance_db}\n"
            f"  investable capital   "
            + (
                f"{self.capital.liquid_assets:,.2f} liquid, "
                f"{self.capital.essential_monthly_spend:,.2f}/month essential"
                if self.capital.stated
                else "(no [capital] plan - `ask.py size` cannot derive it)"
            )
            + "\n"
            f"  holdings             {', '.join(self.holdings) or '(none)'}\n"
            f"  watchlist            {', '.join(self.watchlist) or '(none)'}"
            + (
                "\n  NOTE: with neither holdings nor watchlist set, the escalation "
                "gate\n        (knowledge/news/features.py should_escalate) can never "
                "fire\n        and nothing will ever reach the review queue."
                if not (self.holdings or self.watchlist)
                else ""
            )
        )


def _instruments(data: dict, dotted: str) -> tuple[str, ...]:
    """Instrument ids, each validated against a market that actually exists.

    A typo here does not crash - it silently narrows what the escalation gate
    can ever match, so the system goes quiet for a name you think it is watching.
    Refusing at load is the only place that is visible.
    """
    from markets.registry import known_prefixes, mic_of

    raw = _get(data, dotted, [])
    if isinstance(raw, str):
        raise ConfigError(f"{dotted} must be a list of instrument ids, not a string")
    out = []
    for item in raw:
        # A holdings entry may be a table with units; the id is what this
        # function is for, and the table form is validated in _book.
        ident = str(item["id"]).strip() if isinstance(item, dict) else str(item).strip()
        try:
            mic_of(ident)
        except ValueError:
            raise ConfigError(
                f"{dotted}: {ident!r} has no market prefix. Write e.g. 'MYX:1155' "
                f"or 'XNAS:NVDA'. Known prefixes: {', '.join(known_prefixes())}"
            ) from None
        out.append(ident)
    dupes = {i for i in out if out.count(i) > 1}
    if dupes:
        raise ConfigError(f"{dotted} lists {sorted(dupes)} more than once")
    return tuple(out)


def _strings(data: dict, dotted: str) -> tuple[str, ...]:
    """A list of plain strings, refusing the single-string form.

    `gdelt_languages = "eng"` is a list of three one-character languages to
    Python and to nobody else. It would narrow the feed to nothing and report
    a quiet world.
    """
    raw = _get(data, dotted, [])
    if isinstance(raw, str):
        raise ConfigError(f'{dotted} must be a list, not a string. Write ["{raw}"].')
    return tuple(str(x).strip() for x in raw if str(x).strip())


def _sources(data: dict) -> tuple[str, ...]:
    """Feeds to sweep, each validated against an adapter that actually exists.

    Same shape as the broker check below, for the same reason: naming a source
    here does not create it. An enabled name with no adapter would ingest
    nothing every night, and an empty corpus is indistinguishable from a world
    in which nothing happened.
    """
    from knowledge.feeds.registry import UnknownSource, adapter_for, is_news_source
    from knowledge.sources.catalog import CATALOG
    from knowledge.sources.registry import UnknownCollector, collector_for, is_collector

    names = _strings(data, "sources.enabled")
    for name in names:
        if name not in CATALOG:
            raise ConfigError(
                f"sources.enabled: no adapter registered for source {name!r} - it is not in "
                f"the source catalogue (knowledge/sources/catalog.py). "
                f"Known: {', '.join(sorted(CATALOG))}"
            )
        try:
            if is_news_source(name):
                adapter_for(name)
            elif is_collector(name):
                collector_for(name)
            else:
                raise ConfigError(
                    f"sources.enabled: {name!r} is catalogued but has neither a feed adapter "
                    f"nor a collector - the catalogue entry is ahead of the code."
                )
        except (UnknownSource, UnknownCollector) as e:
            raise ConfigError(f"sources.enabled: {e}") from None
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"sources.enabled lists {sorted(dupes)} more than once")
    return names


def _capital(data: dict) -> CapitalPlan:
    """The [capital] block. Every entry validated, nothing defaulted silently."""
    section = data.get("capital", {}) or {}
    known = {
        "liquid_assets",
        "essential_monthly_spend",
        "planned_monthly_contribution",
        "goals",
        "liabilities",
    }
    unknown = set(section) - known
    if unknown:
        raise ConfigError(
            f"unknown [capital] keys {sorted(unknown)}. A misspelled key here is "
            "silently ignored, and the floor it was meant to raise never moves."
        )

    def money(value, where: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigError(f"{where} must be a number, got {type(value).__name__}")
        d = Decimal(str(value))
        if d < 0:
            raise ConfigError(f"{where} cannot be negative")
        return d

    goals = []
    for i, raw in enumerate(section.get("goals") or []):
        if not isinstance(raw, dict):
            raise ConfigError(
                f"[capital] goals[{i}] must be a table like "
                '{ name = "car", amount = 30000, months_away = 18 }'
            )
        missing = [k for k in ("name", "amount", "months_away") if k not in raw]
        if missing:
            raise ConfigError(f"[capital] goals[{i}] is missing {missing}")
        months = raw["months_away"]
        if isinstance(months, bool) or not isinstance(months, int) or months < 0:
            raise ConfigError(f"[capital] goals[{i}].months_away must be a whole number of months")
        goals.append(
            Goal(str(raw["name"]), money(raw["amount"], f"goals[{i}].amount"), int(months))
        )

    liabilities = []
    for i, raw in enumerate(section.get("liabilities") or []):
        if not isinstance(raw, dict):
            raise ConfigError(
                f"[capital] liabilities[{i}] must be a table like "
                '{ name = "card", balance = 4000, annual_rate = 0.17 }'
            )
        missing = [k for k in ("name", "balance", "annual_rate") if k not in raw]
        if missing:
            raise ConfigError(f"[capital] liabilities[{i}] is missing {missing}")
        rate = money(raw["annual_rate"], f"liabilities[{i}].annual_rate")
        if rate > 1:
            raise ConfigError(
                f"[capital] liabilities[{i}].annual_rate is {rate}; write 0.17 for 17%, "
                "not 17 - a rate read as 1700% would repay every debt before any equity"
            )
        liabilities.append(
            Liability(str(raw["name"]), money(raw["balance"], f"liabilities[{i}].balance"), rate)
        )

    return CapitalPlan(
        liquid_assets=money(section.get("liquid_assets", 0), "[capital] liquid_assets"),
        essential_monthly_spend=money(
            section.get("essential_monthly_spend", 0), "[capital] essential_monthly_spend"
        ),
        planned_monthly_contribution=money(
            section.get("planned_monthly_contribution", 0),
            "[capital] planned_monthly_contribution",
        ),
        goals=tuple(goals),
        liabilities=tuple(liabilities),
    )


def _book(data: dict) -> tuple[Holding, ...]:
    """account.holdings as Holdings. Accepts a bare id or a table with units.

    The bare form is kept because every earlier config file uses it, and a
    settings file that stops loading after an upgrade is a worse failure than
    a book that cannot be valued.
    """
    from markets.registry import known_prefixes, mic_of

    raw = _get(data, "account.holdings", [])
    if isinstance(raw, str):
        raise ConfigError("account.holdings must be a list, not a string")

    def money(value, where: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigError(f"{where} must be a number, got {type(value).__name__}")
        d = Decimal(str(value))
        if d < 0:
            raise ConfigError(f"{where} cannot be negative")
        return d

    out: list[Holding] = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            if "id" not in item:
                raise ConfigError(f"account.holdings[{i}] is a table with no id")
            ident = str(item["id"]).strip()
            unknown = set(item) - {"id", "units", "avg_cost", "stop", "sector"}
            if unknown:
                raise ConfigError(
                    f"account.holdings[{i}] has unknown keys {sorted(unknown)}; "
                    "expected id, units, avg_cost, stop, sector"
                )
            units = money(item["units"], f"holdings[{i}].units") if "units" in item else None
            cost = (
                money(item["avg_cost"], f"holdings[{i}].avg_cost") if "avg_cost" in item else None
            )
            stop = money(item["stop"], f"holdings[{i}].stop") if "stop" in item else None
            sector = str(item.get("sector", "unknown")).strip() or "unknown"
        else:
            ident, units, cost, stop, sector = str(item).strip(), None, None, None, "unknown"
        try:
            mic_of(ident)
        except ValueError:
            raise ConfigError(
                f"account.holdings: {ident!r} has no market prefix. Write e.g. "
                f"'MYX:1155'. Known prefixes: {', '.join(known_prefixes())}"
            ) from None
        out.append(Holding(ident, units, cost, stop, sector))
    ids = [h.id for h in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ConfigError(f"account.holdings lists {sorted(dupes)} more than once")
    return tuple(out)


def _get(data: dict, dotted: str, default):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _paper(data: dict) -> PaperSettings:
    """The [paper] block. Unknown keys refused; the bounds already checked."""
    section = data.get("paper", {}) or {}
    defaults = PaperSettings()
    known = {
        "start_date",
        "initial_cash_usd",
        "database",
        "max_weight_per_name",
        "min_names_when_invested",
        "cash_floor",
        "stop_loss",
        "drawdown_halt",
        "weekly_turnover_cap",
        "slippage_bps_xkls",
        "slippage_bps_xnas",
        "observe_weeks",
        "ramp_weeks",
        "ramp_max_invested",
        "control_rebalance",
    }
    unknown = set(section) - known
    if unknown:
        raise ConfigError(
            f"unknown [paper] keys {sorted(unknown)}. A misspelled cap here is silently "
            "ignored, and the book runs on the default it was meant to tighten."
        )

    def num(key, default: Decimal) -> Decimal:
        if key not in section:
            return default
        v = section[key]
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ConfigError(f"paper.{key} must be a number, got {type(v).__name__}")
        return Decimal(str(v))

    def whole(key, default: int) -> int:
        if key not in section:
            return default
        v = section[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError(f"paper.{key} must be a whole number, got {v!r}")
        return int(v)

    raw_start = section.get("start_date", defaults.start_date)
    if isinstance(raw_start, date):
        start = raw_start
    else:
        try:
            start = date.fromisoformat(str(raw_start))
        except ValueError:
            raise ConfigError(f"paper.start_date must be YYYY-MM-DD, got {raw_start!r}") from None

    cadence = str(section.get("control_rebalance", defaults.control_rebalance))
    if cadence != "monthly":
        raise ConfigError("paper.control_rebalance: only 'monthly' is implemented")

    return PaperSettings(
        start_date=start,
        initial_cash_usd=num("initial_cash_usd", defaults.initial_cash_usd),
        database=str(section.get("database", defaults.database)),
        max_weight_per_name=num("max_weight_per_name", defaults.max_weight_per_name),
        min_names_when_invested=whole("min_names_when_invested", defaults.min_names_when_invested),
        cash_floor=num("cash_floor", defaults.cash_floor),
        stop_loss=num("stop_loss", defaults.stop_loss),
        drawdown_halt=num("drawdown_halt", defaults.drawdown_halt),
        weekly_turnover_cap=num("weekly_turnover_cap", defaults.weekly_turnover_cap),
        slippage_bps_xkls=whole("slippage_bps_xkls", defaults.slippage_bps_xkls),
        slippage_bps_xnas=whole("slippage_bps_xnas", defaults.slippage_bps_xnas),
        observe_weeks=whole("observe_weeks", defaults.observe_weeks),
        ramp_weeks=whole("ramp_weeks", defaults.ramp_weeks),
        ramp_max_invested=num("ramp_max_invested", defaults.ramp_max_invested),
        control_rebalance=cadence,
    )


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


#: An explicit pin, for callers that must not read whatever file happens to be
#: on disk. `config.local.toml` is where an operator keeps a REAL financial
#: position, and it deliberately shadows `config.toml` - which is correct for
#: running the product and wrong for testing it. A suite that reads it is
#: measuring the developer's box: green on a machine with no personal config
#: and red on the machine that actually uses the tool, which is the same
#: failure `keyless_env` exists to prevent for API keys.
CONFIG_ENV = "FINPLANET_CONFIG"


def find(start: Path | None = None) -> Path | None:
    import os

    pinned = os.environ.get(CONFIG_ENV, "").strip()
    if pinned:
        p = Path(pinned)
        if not p.exists():
            # Never fall back. A typo here would silently load a DIFFERENT
            # financial position than the one asked for, and every number
            # downstream would be right about the wrong file.
            raise ConfigError(
                f"{CONFIG_ENV}={pinned!r} does not exist. It pins which config "
                f"is loaded; unset it to search {', '.join(SEARCH)} instead."
            )
        return p
    base = start or Path.cwd()
    for name in SEARCH:
        p = base / name
        if p.exists():
            return p
    return None


def _store_path(raw: object) -> str:
    """Confine a relative store path to the project tree.

    `stress/run.py` note, carried since the suite was written: a config saying
    `database = "../../../../tmp/pwned.db"` had that string stored verbatim and
    a database opened wherever it pointed. Low risk on a single-operator tool -
    they own the file either way - but a path that walks out of the project is
    never what a config file meant, and normalising it costs nothing.

    Two rules, and the split is what keeps this from being a nuisance:

      * an ABSOLUTE path is honoured exactly. An operator who writes
        `/var/lib/finplanet/corpus.db` means that file, and tests that point at
        a temporary directory rely on it.
      * a RELATIVE path has its `..` and `.` segments dropped, so it stays
        under the working tree. `../../../../tmp/pwned.db` becomes
        `tmp/pwned.db`; `data/corpus.db`, which is every real path in this
        repository, is returned untouched.

    Dropping the segments rather than raising is deliberate: `load` must not
    start failing on a config it used to accept, and there is a legitimate
    reading of the escaping path (a sibling checkout) that an error message
    could not distinguish from a typo.
    """
    import ntpath
    import posixpath

    text = str(raw)
    if posixpath.isabs(text) or ntpath.isabs(text) or ntpath.splitdrive(text)[0]:
        return text
    parts = [seg for seg in text.replace("\\", "/").split("/") if seg not in ("", ".", "..")]
    return "/".join(parts) if parts else text


def load(path: str | Path | None = None) -> Config:
    """Load settings. Absent file means documented defaults, never zero limits."""
    p = Path(path) if path else find()
    if p is None:
        data, source = {}, "<defaults>"
    else:
        try:
            data = tomllib.loads(Path(p).read_text(encoding="utf-8"))
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

    broker = str(_get(data, "account.broker", "")).strip() or None
    if broker is not None:
        from markets.brokers import known_brokers

        if broker not in known_brokers():
            raise ConfigError(
                f"unknown broker {broker!r}. A broker is one entry in "
                f"markets/brokers.BROKER_SCHEDULES; naming one here does not "
                f"create it. Known: {known_brokers()}"
            )

    def dec(k, d):
        return Decimal(str(_get(data, k, d)))

    def _int(k, d):
        v = _get(data, k, d)
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ConfigError(f"{k} must be a number, got {type(v).__name__}")
        return int(v)

    def _float(k, d):
        v = _get(data, k, d)
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ConfigError(f"{k} must be a number, got {type(v).__name__}")
        return float(v)

    sources = _sources(data)
    read_only = _instruments(data, "sources.read_only")
    held = set(_instruments(data, "account.holdings")) | set(
        _instruments(data, "account.watchlist")
    )
    overlap = sorted(set(read_only) & held)
    if overlap:
        raise ConfigError(
            f"sources.read_only lists {overlap}, which the book already holds or watches; "
            "a name is read-only or in the book, not both"
        )

    return Config(
        base_currency=str(_get(data, "account.base_currency", "MYR")).upper(),
        markets=markets,
        broker=broker,
        fx_myr_per_usd=dec("account.fx_myr_per_usd", DEFAULT_FX_MYR_PER_USD),
        fx_spread_per_side=dec("account.fx_spread_per_side", DEFAULT_FX_SPREAD_PER_SIDE),
        risk_per_trade=dec("risk.risk_per_trade", 0.0075),
        target_volatility=dec("risk.target_volatility", 0.20),
        max_participation=dec("risk.max_participation", 0.05),
        limits=limits,
        holdings=_instruments(data, "account.holdings"),
        watchlist=_instruments(data, "account.watchlist"),
        read_only=read_only,
        provenance_db=_store_path(_get(data, "provenance.database", "data/provenance.db")),
        sources=sources,
        gdelt_languages=_strings(data, "sources.gdelt_languages"),
        gdelt_countries=_strings(data, "sources.gdelt_countries"),
        gdelt_query=str(_get(data, "sources.gdelt_query", "")),
        gdelt_poll_minutes=_int("sources.gdelt_poll_minutes", 15),
        corpus_db=_store_path(_get(data, "sources.corpus_database", "data/corpus.db")),
        facts_db=_store_path(_get(data, "sources.facts_database", "data/facts.db")),
        languages=_strings(data, "sources.languages"),
        daemon_budget_myr=dec("budget.daemon_daily_myr", 10.0),
        emergency_months=_int("waterfall.emergency_months", 6),
        debt_hurdle=dec("waterfall.debt_hurdle", 0.08),
        daily_budget_myr=dec("budget.daily_myr", 25.0),
        per_question_budget_myr=dec("budget.per_question_myr", 5.0),
        database=_store_path(_get(data, "learning.database", "data/learning.db")),
        min_graded_for_calibration=_int("learning.min_graded_for_calibration", 30),
        alert_spend_fraction=dec("monitor.spend_fraction", 0.8),
        alert_p95_latency_ms=_float("monitor.p95_latency_ms", 20_000.0),
        alert_dropped_claim_rate=dec("monitor.dropped_claim_rate", 0.2),
        alert_silence_hours=_int("monitor.silence_hours", 0),
        alert_sweep_silence_hours=_int("monitor.sweep_silence_hours", 0),
        alert_slot_window_days=_int("monitor.slot_window_days", 0),
        alert_name_coverage_days=_int("monitor.name_coverage_days", 0),
        capital=_capital(data),
        book=_book(data),
        paper=_paper(data),
        alert_paper_drawdown=dec("monitor.paper_drawdown", 0.08),
        source=source,
    )
