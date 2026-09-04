"""The `[paper]` settings: caps the file may tighten and never loosen.

`core/config.py` builds one of these from `config.toml` after `HARD_BOUNDS`
has refused anything outside the agreed profile (docs/22 section 5). The
defaults here ARE the conservative profile, so an absent block runs the book
exactly as agreed rather than more loosely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

DEFAULT_START = date(2026, 9, 8)


@dataclass(frozen=True)
class PaperSettings:
    start_date: date = DEFAULT_START
    initial_cash_usd: Decimal = Decimal(1000)
    database: str = "data/paper.db"
    #: Ceiling on any one name as a share of equity at the time of the decision.
    max_weight_per_name: Decimal = Decimal("0.25")
    #: Names required once invested weight exceeds the ramp ceiling; below it the
    #: arithmetic minimum applies (rules.min_names).
    min_names_when_invested: int = 4
    #: Share of equity that is never deployed.
    cash_floor: Decimal = Decimal("0.20")
    #: A position whose close is this far below its average cost exits at the
    #: next open, in its own currency.
    stop_loss: Decimal = Decimal("0.08")
    #: Drawdown from peak equity past which no target may raise a weight.
    drawdown_halt: Decimal = Decimal("0.08")
    #: Consideration changed over five weekdays, as a share of equity.
    weekly_turnover_cap: Decimal = Decimal("0.50")
    slippage_bps_xkls: int = 10
    slippage_bps_xnas: int = 5
    observe_weeks: int = 2
    ramp_weeks: int = 4
    ramp_max_invested: Decimal = Decimal("0.40")
    control_rebalance: str = "monthly"

    @property
    def max_invested(self) -> Decimal:
        return Decimal(1) - self.cash_floor

    def slippage_bps(self, mic: str) -> int:
        return self.slippage_bps_xkls if mic == "XKLS" else self.slippage_bps_xnas

    @classmethod
    def from_dict(cls, d: dict, base: PaperSettings | None = None) -> PaperSettings:
        """The settings a book was opened with, read back from its `caps_json`."""
        base = base or cls()

        def _dec(k, cur):
            return Decimal(str(d[k])) if k in d else cur

        def _int(k, cur):
            return int(d[k]) if k in d else cur

        start = date.fromisoformat(d["start_date"]) if "start_date" in d else base.start_date
        return cls(
            start_date=start,
            initial_cash_usd=_dec("initial_cash_usd", base.initial_cash_usd),
            database=base.database,
            max_weight_per_name=_dec("max_weight_per_name", base.max_weight_per_name),
            min_names_when_invested=_int("min_names_when_invested", base.min_names_when_invested),
            cash_floor=_dec("cash_floor", base.cash_floor),
            stop_loss=_dec("stop_loss", base.stop_loss),
            drawdown_halt=_dec("drawdown_halt", base.drawdown_halt),
            weekly_turnover_cap=_dec("weekly_turnover_cap", base.weekly_turnover_cap),
            slippage_bps_xkls=_int("slippage_bps_xkls", base.slippage_bps_xkls),
            slippage_bps_xnas=_int("slippage_bps_xnas", base.slippage_bps_xnas),
            observe_weeks=_int("observe_weeks", base.observe_weeks),
            ramp_weeks=_int("ramp_weeks", base.ramp_weeks),
            ramp_max_invested=_dec("ramp_max_invested", base.ramp_max_invested),
            control_rebalance=str(d.get("control_rebalance", base.control_rebalance)),
        )

    def as_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "initial_cash_usd": str(self.initial_cash_usd),
            "max_weight_per_name": str(self.max_weight_per_name),
            "min_names_when_invested": self.min_names_when_invested,
            "cash_floor": str(self.cash_floor),
            "stop_loss": str(self.stop_loss),
            "drawdown_halt": str(self.drawdown_halt),
            "weekly_turnover_cap": str(self.weekly_turnover_cap),
            "slippage_bps_xkls": self.slippage_bps_xkls,
            "slippage_bps_xnas": self.slippage_bps_xnas,
            "observe_weeks": self.observe_weeks,
            "ramp_weeks": self.ramp_weeks,
            "ramp_max_invested": str(self.ramp_max_invested),
            "control_rebalance": self.control_rebalance,
        }
