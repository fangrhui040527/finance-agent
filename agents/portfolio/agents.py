"""A12-A13, the portfolio layer.

These two are where the eggs-in-one-basket rule is actually enforced. Every
other agent produces text; these produce a number that a cap can reject.

docs/05: the caps bind BEFORE the view is expressed, not after. An agent that
computes a size and then checks limits has already anchored on the wrong number.
"""

from __future__ import annotations

from decimal import Decimal

from agents.base import Agent, Finding
from core.contracts.money import BASE_CURRENCY
from core.llm.tiers import TaskClass
from engines.risk.concentration import (
    Limits,
    Position,
    check,
    correlation_clusters,
    effective_number_of_bets,
    hhi,
)
from engines.sizing.caps import (
    CapSet,
    ImplausibleEdge,
    concentration_cap,
    cost_floor_bps,
    cost_floor_value,
    kelly_cap,
    liquidity_cap,
    risk_budget_cap,
    to_base,
    to_quote,
)
from engines.sizing.decision import NoPosition, size
from engines.sizing.waterfall import Goal, Liability, Waterfall, compute
from markets.registry import market_currency

#: docs/05 section 5. Drawdown governs risk appetite mechanically, because the
#: moment it is discretionary it gets overridden at exactly the wrong time.
DRAWDOWN_TIERS: tuple[tuple[float, Decimal, str], ...] = (
    (0.10, Decimal("0.75"), "risk per trade cut to 75%: review, do not react"),
    (0.15, Decimal("0.50"), "risk per trade halved; no new positions outside existing themes"),
    (0.20, Decimal("0.25"), "quarter risk; new positions require a written post-mortem first"),
    (0.25, Decimal("0.00"), "no new risk. Close the laptop, write the review, wait a full month."),
)


class A12PortfolioRisk(Agent):
    """Reads the whole book. Reports every breach, not the first."""

    agent_id = "a12_portfolio_risk"
    collections = ("kb_method_risk",)
    tools = ("concentration_check", "heat", "effective_bets", "drawdown_state", "stress")
    tier = TaskClass.RISK_COMMENT

    def run(
        self,
        positions: list[Position],
        corr: list[list[float]] | None = None,
        limits: Limits | None = None,
        base_currency: str = "MYR",
        peak_equity: Decimal | None = None,
        equity: Decimal | None = None,
    ) -> list[Finding]:
        self._guard_tool("concentration_check")
        limits = limits or Limits()
        breaches = check(positions, corr, limits, base_currency)
        weights = [p.weight for p in positions]

        out: list[Finding] = []
        if not positions:
            return [Finding(self.agent_id, "empty_book", "no positions held")]

        h = hhi(weights)
        bets = effective_number_of_bets(weights, corr) if corr else float(len(positions))
        heat = sum(p.risk_to_stop for p in positions)

        # The headline finding is deliberately the pair, not HHI alone. A book of
        # ten correlated banks has a comfortable HHI and one bet.
        out.append(
            Finding(
                self.agent_id,
                "concentration",
                f"{len(positions)} positions, HHI {h:.3f}, effective bets {bets:.2f}, "
                f"portfolio heat {heat:.1%}",
                numbers={
                    "hhi": h,
                    "effective_bets": bets,
                    "heat": heat,
                    "positions": float(len(positions)),
                },
                caveats=(
                    [
                        "HHI looks diversified but the correlated names collapse into "
                        "far fewer independent bets"
                    ]
                    if h <= limits.hhi and bets < limits.min_effective_bets
                    else []
                ),
            )
        )

        for b in breaches:
            out.append(
                Finding(
                    self.agent_id,
                    "breach",
                    f"{b.limit} breach: {b.actual:.3f} against a limit of {b.allowed:.3f}"
                    + (f" ({b.detail})" if b.detail else ""),
                    numbers={"observed": b.actual, "limit": b.allowed},
                )
            )

        if corr:
            clusters = correlation_clusters(corr)
            big = [c for c in clusters if len(c) > 1]
            if big:
                named = ["+".join(positions[i].instrument_id for i in c) for c in big]
                out.append(
                    Finding(
                        self.agent_id,
                        "cluster",
                        "correlated groups that move together: " + "; ".join(named),
                        numbers={"clusters": float(len(big))},
                    )
                )

        if peak_equity and equity:
            out.extend(self.drawdown_state(equity, peak_equity))
        return out

    def drawdown_state(self, equity: Decimal, peak_equity: Decimal) -> list[Finding]:
        self._guard_tool("drawdown_state")
        if peak_equity <= 0:
            return []
        dd = float((peak_equity - equity) / peak_equity)
        scalar, action = Decimal("1.00"), "full risk budget available"
        for threshold, s, a in DRAWDOWN_TIERS:
            if dd >= threshold:
                scalar, action = s, a
        return [
            Finding(
                self.agent_id,
                "drawdown",
                f"drawdown {dd:.1%} from peak: {action}",
                numbers={"drawdown": dd, "risk_scalar": float(scalar)},
                caveats=(
                    ["this tier is mechanical and is not overridable by conviction"]
                    if scalar < 1
                    else []
                ),
            )
        ]

    def stress(self, positions: list[Position], shocks: dict[str, float]) -> list[Finding]:
        """What a named historical shock does to this exact book. Not VaR - VaR
        is an average of a distribution that does not exist in a crisis."""
        self._guard_tool("stress")
        out = []
        for name, shock in sorted(shocks.items()):
            loss = sum(p.weight * shock for p in positions)
            out.append(
                Finding(
                    self.agent_id,
                    "stress",
                    f"{name}: book would move {loss:+.1%} before any correlation breakdown",
                    numbers={"shock": shock, "portfolio_impact": loss},
                    caveats=["correlations rise towards 1 in a real crisis; this is optimistic"],
                )
            )
        return out


class A13Sizing(Agent):
    """Turns a stance into a lot count, or into a documented refusal."""

    agent_id = "a13_sizing"
    collections = ("kb_method_risk",)
    tools = (
        "investable_capital",
        "risk_budget_cap",
        "kelly_cap",
        "concentration_cap",
        "liquidity_cap",
        "cost_floor",
        "vol_target_scalar",
        "lot_round",
    )
    tier = TaskClass.RISK_COMMENT

    def investable_capital(
        self,
        liquid_assets: Decimal,
        essential_monthly_spend: Decimal,
        goals: list[Goal] | None = None,
        liabilities: list[Liability] | None = None,
        planned_monthly_contribution: Decimal = Decimal(0),
        emergency_months: int = 6,
    ) -> tuple[Waterfall, list[Finding]]:
        self._guard_tool("investable_capital")
        w = compute(
            liquid_assets,
            essential_monthly_spend,
            goals or [],
            liabilities or [],
            planned_monthly_contribution,
            emergency_months,
        )
        caveats = []
        if w.investable == 0:
            caveats.append(
                "emergency floor and near-term goals consume everything liquid; "
                "the correct amount to invest today is zero"
            )
        return w, [
            Finding(
                self.agent_id,
                "investable_capital",
                f"investable capital is {w.investable:,.2f} of {liquid_assets:,.2f} liquid",
                numbers={"investable": float(w.investable), "liquid": float(liquid_assets)},
                caveats=caveats,
            )
        ]

    def caps(
        self,
        portfolio_value: Decimal,
        stop_distance_frac: Decimal,
        adv_20d: Decimal,
        round_trip_cost_at,
        risk_per_trade: Decimal = Decimal("0.0075"),
        single_name_limit: Decimal = Decimal("0.08"),
        participation: Decimal = Decimal("0.05"),
        win_rate: Decimal | float | None = None,
        payoff: Decimal | float | None = None,
        n_trades: int = 0,
        mic: str | None = None,
        fx_base_per_quote: Decimal | None = None,
        fx_asof=None,
    ) -> tuple[CapSet, list[Finding]]:
        """`portfolio_value` is MYR; `adv_20d` and the fee schedule are the
        market's own currency. Those meet in a `min()`, so one of them has to
        move - and the portfolio is the side that converts, because lot sizes,
        ticks and fee minimums are only meaningful natively.

        The market's currency is read off its adapter rather than taken as an
        argument: the adapter already declares it, and a caller who could pass
        it could pass it wrong.
        """
        self._guard_tool("risk_budget_cap")
        quote = market_currency(mic)
        pv = to_quote(portfolio_value, quote, fx_base_per_quote, fx_asof)
        risk = risk_budget_cap(pv, risk_per_trade, stop_distance_frac)
        conc = concentration_cap(pv, single_name_limit)
        liq = liquidity_cap(adv_20d, participation)
        floor = cost_floor_value(round_trip_cost_at, mic)

        # The liquidity cap is a LINEAR participation model. Above ~5% of ADV
        # impact grows with the square root and linear understates it; below
        # 0.5% fees dominate. Naming the regime keeps the cap honest about
        # what it is and is not modelling.
        from engines.backtest.costs import impact_model_for

        regime = impact_model_for(float(participation))
        impact_note = (
            f"liquidity cap uses the linear participation model at "
            f"{float(participation):.1%} of ADV"
        )
        if regime == "sqrt":
            impact_note += (
                " - ABOVE the linear regime; square-root impact says the true cost is higher"
            )
        elif regime == "fixed":
            impact_note += " - below the impact floor; fees dominate at this size"

        kelly, notes = None, [impact_note]
        if win_rate is not None and payoff is not None:
            self._guard_tool("kelly_cap")
            try:
                kelly = kelly_cap(pv, Decimal(str(win_rate)), Decimal(str(payoff)), n_trades)
            except ImplausibleEdge as e:
                notes.append(f"Kelly cap refused: {e}")
            except ValueError as e:
                notes.append(f"Kelly cap unavailable: {e}")

        caps = CapSet(
            risk=risk,
            kelly=kelly,
            concentration=conc,
            liquidity=liq,
            cost_floor=floor,
            currency=quote,
        )
        binding, value = caps.binding()
        value_base = to_base(value, quote, fx_base_per_quote, fx_asof)
        floor_base = to_base(floor, quote, fx_base_per_quote, fx_asof)
        if quote != BASE_CURRENCY:
            notes.append(
                f"caps are quoted in {quote} at {fx_base_per_quote} {BASE_CURRENCY} per "
                f"{quote}; the {BASE_CURRENCY} figures move with that rate even if the "
                "position does not"
            )

        # Only show the MYR figure alongside when it is a DIFFERENT number.
        # Printing "USD 9,500 (MYR 39,900)" is information; printing
        # "MYR 40,000 (MYR 40,000)" trains the reader to skip the parenthesis.
        def both(v: Decimal, v_base: Decimal) -> str:
            native = f"{quote} {v:,.2f}"
            return native if quote == BASE_CURRENCY else f"{native} = {BASE_CURRENCY} {v_base:,.2f}"

        return caps, [
            Finding(
                self.agent_id,
                "caps",
                f"binding cap is {binding.value} at {both(value, value_base)}; "
                f"cost floor requires at least {both(floor, floor_base)} "
                f"({cost_floor_bps(mic)} bps round trip on {mic or 'default'})",
                numbers={
                    "risk": float(risk),
                    "concentration": float(conc),
                    "liquidity": float(liq),
                    "cost_floor": float(floor),
                    "kelly": float(kelly) if kelly is not None else -1.0,
                    "binding": float(value),
                    "binding_base": float(value_base),
                    "cost_floor_base": float(floor_base),
                },
                caveats=notes,
            )
        ]

    def run(self, **kwargs) -> list[Finding]:
        """Full decision. NoPosition is an outcome, not a failure."""
        self._guard_tool("lot_round")
        try:
            d = size(**kwargs)
        except NoPosition as e:
            return [
                Finding(
                    self.agent_id,
                    "no_position",
                    str(e.reason),
                    caveats=[
                        "no position is the correct answer more often than "
                        "the interface makes it feel"
                    ],
                )
            ]
        native = f"{d.currency} {d.target_value:,.2f}"
        both = (
            native
            if d.currency == BASE_CURRENCY
            else f"{native} = {BASE_CURRENCY} {d.base_value:,.2f}"
        )
        return [
            Finding(
                self.agent_id,
                "size",
                f"{d.target_units:,} units of {d.instrument_id} "
                f"({both}), bound by {d.binding_cap.value}",
                numbers={
                    "units": float(d.target_units),
                    "value": float(d.target_value),
                    "value_base": float(d.base_value),
                    "tranches": float(len(d.tranches)),
                },
                caveats=list(d.notes),
            )
        ]


def plan_capital(cfg, ctx) -> tuple:
    """Config -> the waterfall, through A13. One path, so no surface re-derives it.

    Returns (Waterfall | None, findings). None means the plan was never stated:
    that is different from a plan whose answer is zero, and every caller has to
    say which of the two it is.
    """
    from engines.sizing.waterfall import Goal as WGoal
    from engines.sizing.waterfall import Liability as WLiability

    plan = cfg.capital
    if not plan.stated:
        return None, []
    a13 = A13Sizing(ctx)
    return a13.investable_capital(
        liquid_assets=plan.liquid_assets,
        essential_monthly_spend=plan.essential_monthly_spend,
        goals=[WGoal(g.name, g.amount, g.months_away) for g in plan.goals],
        liabilities=[WLiability(x.name, x.balance, x.annual_rate) for x in plan.liabilities],
        planned_monthly_contribution=plan.planned_monthly_contribution,
        emergency_months=cfg.emergency_months,
    )


#: Printed wherever a caller supplies investable capital by hand. The three
#: locked steps of the waterfall are exactly what a typed number skips, and a
#: bypass nobody is told about is the same as a bypass nobody chose.
TYPED_CAPITAL_NOTE = (
    "capital was SUPPLIED, not derived: the emergency floor, near-term goals "
    "and debt hurdle were not applied to it. Fill [capital] in config.toml and "
    "use --from-plan to have them applied."
)
