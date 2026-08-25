"""A12-A13, the portfolio layer.

These two are where the eggs-in-one-basket rule is actually enforced. Every
other agent produces text; these produce a number that a cap can reject.

docs/05: the caps bind BEFORE the view is expressed, not after. An agent that
computes a size and then checks limits has already anchored on the wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from agents.base import Agent, AgentContext, Finding
from core.llm.tiers import TaskClass
from engines.risk.concentration import (
    Breach, Limits, Position, check, correlation_clusters,
    effective_number_of_bets, hhi,
)
from engines.sizing.caps import (
    Band, CapSet, ImplausibleEdge, concentration_cap, cost_floor_bps,
    cost_floor_value, kelly_cap, liquidity_cap, risk_budget_cap,
)
from engines.sizing.decision import NoPosition, SizingDecision, size
from engines.sizing.waterfall import Goal, Liability, Waterfall, compute


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
        out.append(Finding(
            self.agent_id, "concentration",
            f"{len(positions)} positions, HHI {h:.3f}, effective bets {bets:.2f}, "
            f"portfolio heat {heat:.1%}",
            numbers={"hhi": h, "effective_bets": bets, "heat": heat,
                     "positions": float(len(positions))},
            caveats=(["HHI looks diversified but the correlated names collapse into "
                      "far fewer independent bets"]
                     if h <= limits.hhi and bets < limits.min_effective_bets else []),
        ))

        for b in breaches:
            out.append(Finding(
                self.agent_id, "breach",
                f"{b.limit} breach: {b.actual:.3f} against a limit of {b.allowed:.3f}"
                + (f" ({b.detail})" if b.detail else ""),
                numbers={"observed": b.actual, "limit": b.allowed},
            ))

        if corr:
            clusters = correlation_clusters(corr)
            big = [c for c in clusters if len(c) > 1]
            if big:
                named = ["+".join(positions[i].instrument_id for i in c) for c in big]
                out.append(Finding(
                    self.agent_id, "cluster",
                    "correlated groups that move together: " + "; ".join(named),
                    numbers={"clusters": float(len(big))},
                ))

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
        return [Finding(
            self.agent_id, "drawdown",
            f"drawdown {dd:.1%} from peak: {action}",
            numbers={"drawdown": dd, "risk_scalar": float(scalar)},
            caveats=(["this tier is mechanical and is not overridable by conviction"]
                     if scalar < 1 else []),
        )]

    def stress(self, positions: list[Position],
               shocks: dict[str, float]) -> list[Finding]:
        """What a named historical shock does to this exact book. Not VaR - VaR
        is an average of a distribution that does not exist in a crisis."""
        self._guard_tool("stress")
        out = []
        for name, shock in sorted(shocks.items()):
            loss = sum(p.weight * shock for p in positions)
            out.append(Finding(
                self.agent_id, "stress",
                f"{name}: book would move {loss:+.1%} before any correlation breakdown",
                numbers={"shock": shock, "portfolio_impact": loss},
                caveats=["correlations rise towards 1 in a real crisis; this is optimistic"],
            ))
        return out


class A13Sizing(Agent):
    """Turns a stance into a lot count, or into a documented refusal."""

    agent_id = "a13_sizing"
    collections = ("kb_method_risk",)
    tools = ("investable_capital", "risk_budget_cap", "kelly_cap", "concentration_cap",
             "liquidity_cap", "cost_floor", "vol_target_scalar", "lot_round")
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
        w = compute(liquid_assets, essential_monthly_spend, goals or [], liabilities or [],
                    planned_monthly_contribution, emergency_months)
        caveats = []
        if w.investable == 0:
            caveats.append("emergency floor and near-term goals consume everything liquid; "
                           "the correct amount to invest today is zero")
        return w, [Finding(
            self.agent_id, "investable_capital",
            f"investable capital is {w.investable:,.2f} of {liquid_assets:,.2f} liquid",
            numbers={"investable": float(w.investable), "liquid": float(liquid_assets)},
            caveats=caveats,
        )]

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
    ) -> tuple[CapSet, list[Finding]]:
        self._guard_tool("risk_budget_cap")
        risk = risk_budget_cap(portfolio_value, risk_per_trade, stop_distance_frac)
        conc = concentration_cap(portfolio_value, single_name_limit)
        liq = liquidity_cap(adv_20d, participation)
        floor = cost_floor_value(round_trip_cost_at, mic)

        kelly, notes = None, []
        if win_rate is not None and payoff is not None:
            self._guard_tool("kelly_cap")
            try:
                kelly = kelly_cap(portfolio_value, Decimal(str(win_rate)),
                                  Decimal(str(payoff)), n_trades)
            except ImplausibleEdge as e:
                notes.append(f"Kelly cap refused: {e}")
            except ValueError as e:
                notes.append(f"Kelly cap unavailable: {e}")

        caps = CapSet(risk=risk, kelly=kelly, concentration=conc, liquidity=liq, cost_floor=floor)
        binding, value = caps.binding()
        return caps, [Finding(
            self.agent_id, "caps",
            f"binding cap is {binding.value} at {value:,.2f}; "
            f"cost floor requires at least {floor:,.2f} "
            f"({cost_floor_bps(mic)} bps round trip on {mic or 'default'})",
            numbers={"risk": float(risk), "concentration": float(conc),
                     "liquidity": float(liq), "cost_floor": float(floor),
                     "kelly": float(kelly) if kelly is not None else -1.0,
                     "binding": float(value)},
            caveats=notes,
        )]

    def run(self, **kwargs) -> list[Finding]:
        """Full decision. NoPosition is an outcome, not a failure."""
        self._guard_tool("lot_round")
        try:
            d = size(**kwargs)
        except NoPosition as e:
            return [Finding(self.agent_id, "no_position", str(e.reason),
                            caveats=["no position is the correct answer more often than "
                                     "the interface makes it feel"])]
        return [Finding(
            self.agent_id, "size",
            f"{d.target_units:,} units of {d.instrument_id} "
            f"({d.target_value:,.2f}), bound by {d.binding_cap.value}",
            numbers={"units": float(d.target_units), "value": float(d.target_value),
                     "tranches": float(len(d.tranches))},
            caveats=list(d.notes),
        )]
