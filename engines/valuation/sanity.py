"""The DCF checks that do not depend on the modeller's mood.

Green, Hand and Zhang (2016) counted a median of three errors and four
questionable judgements per published sell-side DCF. The checks here are the
mechanical subset: each compares an assumption with the risk-free rate, the
long-run growth ceiling, or the business's own record, and each is either
fatal (the scenario is refused) or a caveat that travels with the range.

  terminal growth above the risk-free rate or the long-run ceiling  fatal
  discount rate below risk-free plus half the equity premium        fatal
  cost of equity below cost of debt                                 fatal
  terminal value above 75 percent of enterprise value               material
  projected margin above the business's own historical maximum      material
  first-year growth above twice the historical compound rate        material
  zero reinvestment while history shows reinvestment                minor

What they cannot catch is whether the story is true; that is the thesis's
and the red team's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engines.valuation.cost_of_capital import CostOfCapital, CostOfCapitalTable

TERMINAL_SHARE_MAX = Decimal("0.75")
FATAL, MATERIAL, MINOR = "fatal", "material", "minor"


@dataclass(frozen=True)
class SanityFlag:
    code: str
    severity: str
    message: str

    @property
    def fatal(self) -> bool:
        return self.severity == FATAL


@dataclass(frozen=True)
class History:
    """What the business's own record says, for the checks that compare to it."""

    margin_max: Decimal | None = None
    revenue_cagr: Decimal | None = None
    reinvestment: Decimal | None = None


def check(
    *,
    terminal_growth: Decimal,
    discount: Decimal,
    growth_first_year: Decimal,
    margin: Decimal,
    reinvestment: Decimal,
    terminal_share: Decimal | None,
    coc: CostOfCapital,
    table: CostOfCapitalTable,
    country: str,
    history: History,
) -> tuple[SanityFlag, ...]:
    flags: list[SanityFlag] = []
    ceiling = table.long_run_growth.get(country)
    if coc.rf is not None and terminal_growth > coc.rf:
        flags.append(
            SanityFlag(
                "terminal_above_rf",
                FATAL,
                f"terminal growth {terminal_growth:.2%} exceeds the risk-free rate {coc.rf:.2%}",
            )
        )
    if ceiling is not None and terminal_growth > ceiling:
        flags.append(
            SanityFlag(
                "terminal_above_ceiling",
                FATAL,
                f"terminal growth {terminal_growth:.2%} exceeds the {country} long-run nominal growth ceiling {ceiling:.2%}",
            )
        )
    if coc.rf is not None and coc.erp is not None and discount < coc.rf + coc.erp / 2:
        flags.append(
            SanityFlag(
                "discount_too_low",
                FATAL,
                f"discount rate {discount:.2%} is below risk-free plus half the equity premium",
            )
        )
    if coc.ke is not None and coc.kd is not None and coc.ke < coc.kd:
        flags.append(
            SanityFlag(
                "ke_below_kd",
                FATAL,
                f"cost of equity {coc.ke:.2%} is below the cost of debt {coc.kd:.2%}: an input error",
            )
        )
    if terminal_share is not None and terminal_share > TERMINAL_SHARE_MAX:
        flags.append(
            SanityFlag(
                "terminal_share",
                MATERIAL,
                f"terminal value is {terminal_share:.0%} of enterprise value; the explicit forecast is doing little work",
            )
        )
    if history.margin_max is not None and margin > history.margin_max:
        flags.append(
            SanityFlag(
                "margin_above_history",
                MATERIAL,
                f"projected margin {margin:.1%} exceeds the historical maximum {history.margin_max:.1%} without a stated reason",
            )
        )
    if (
        history.revenue_cagr is not None
        and history.revenue_cagr > 0
        and growth_first_year > 2 * history.revenue_cagr
    ):
        flags.append(
            SanityFlag(
                "growth_above_history",
                MATERIAL,
                f"first-year growth {growth_first_year:.1%} is more than twice the historical compound rate {history.revenue_cagr:.1%}",
            )
        )
    if reinvestment == 0 and history.reinvestment not in (None, Decimal(0)):
        flags.append(
            SanityFlag(
                "no_reinvestment",
                MINOR,
                "zero reinvestment while history shows the business reinvests to grow",
            )
        )
    return tuple(flags)
