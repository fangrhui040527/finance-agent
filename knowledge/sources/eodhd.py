"""EODHD fundamentals: quarterly and annual statements behind an optional key.

EODHD (eodhd.com) covers 60+ exchanges including Bursa Malaysia, which is the
one market this book holds and no free statement source reaches. Its free
plan is small and honest about it: 20 API credits a day, and a fundamentals
request costs 10, so two names a day, US only. The paid Fundamentals Data Feed
(100,000 calls a day, KLSE included) and the All-In-One plan lift both limits;
EODHD_PLAN says which one the key is on, and the collector shapes the run to
it rather than to the free budget it was first written against.

  key       EODHD_API_KEY, optional: without it the collector records itself as
            skipped with the variable named (the FinMind shape, the sweep's
            semantics), and nothing else changes.
  plan      EODHD_PLAN: free | fundamentals | all-in-one, `free` unless set,
            read once per run. On the free plan MAX_NAMES_PER_RUN names a run,
            US only, rotated by the day of the year and the slot so the same
            two names are not asked every time, and every deferred name gets a
            note saying when its turn comes. On a paid plan EVERY book name,
            EVERY run, KLSE and Taiwan included: at 100,000 calls a day there
            is nothing to ration, and a rotation would only make the newest
            quarter arrive days late. The first note of every pull names the
            plan, so the sweeps table says which budget the run was shaped to.
  refusals  402/403 and a body that says "limit" or "not available" are the
            plan's boundary: a note naming the name, never a retry storm. A
            KLSE or Taiwan name on the free plan gets the exact note "EODHD
            free plan: US only; the Fundamentals plan covers KLSE".

Concept keys follow engines/fundamentals/ratios.py: `quarterly` rows under the
plain key, `yearly` rows under `<key>_fy`, balance-sheet lines as instants;
`known_at` is the filing date the vendor gives, else the fetch day (FMP's
rule), never the period end.
"""

from __future__ import annotations

import os
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from knowledge.facts import Observation, as_decimal
from knowledge.sources.base import (
    Collector,
    KeyMissing,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_date,
)

URL = "https://eodhd.com/api/fundamentals/{symbol}"
KEY_ENV = "EODHD_API_KEY"
PLAN_ENV = "EODHD_PLAN"
SYMBOL_SUFFIX = {"XKLS": "KLSE", "XNAS": "US", "XNYS": "US", "XTAI": "TW"}
FREE_PLAN_MARKETS = frozenset({"XNAS", "XNYS"})
PAID_PLAN_MARKETS = frozenset(SYMBOL_SUFFIX)
MAX_NAMES_PER_RUN = 2
SLOT_ORDER = ("bursa_close", "us_preopen", "us_close", "weekly", "all")


@dataclass(frozen=True)
class Plan:
    """What one EODHD plan lets a run ask for."""

    name: str
    markets: frozenset[str]
    #: Names one run asks for; 0 is every name in the book, every run.
    names_per_run: int

    def describe(self) -> str:
        if self.names_per_run:
            return f"{self.name} ({self.names_per_run} names a day, US only, rotated)"
        return f"{self.name} (every book name every run; KLSE and TW included)"


PLANS: dict[str, Plan] = {
    "free": Plan("free", FREE_PLAN_MARKETS, MAX_NAMES_PER_RUN),
    "fundamentals": Plan("fundamentals", PAID_PLAN_MARKETS, 0),
    "all-in-one": Plan("all-in-one", PAID_PLAN_MARKETS, 0),
}


def plan_from_env(raw: str | None = None) -> Plan:
    """The plan EODHD_PLAN names, or the free plan when it is unset.

    A value that names no plan is a SourceError, not a quiet fall back to
    free: an operator who paid for the Fundamentals feed and misspelt the
    variable would otherwise watch the rotation carry on at two names a day
    and read it as the plan not having taken effect at EODHD's end.
    """
    value = (raw if raw is not None else os.environ.get(PLAN_ENV, "")).strip().lower() or "free"
    try:
        return PLANS[value]
    except KeyError:
        raise SourceError(
            f"{PLAN_ENV}={value!r} names no EODHD plan this collector knows; "
            f"one of {', '.join(PLANS)}"
        ) from None


INCOME = {
    "totalRevenue": "revenue",
    "costOfRevenue": "cost_of_revenue",
    "grossProfit": "gross_profit",
    "operatingIncome": "operating_income",
    "netIncome": "net_income",
    "ebitda": "ebitda",
    "sellingGeneralAdministrative": "sga",
    "interestExpense": "interest_expense",
    "incomeTaxExpense": "income_tax",
    "incomeBeforeTax": "pretax_income",
    "depreciationAndAmortization": "depreciation",
}
BALANCE = {
    "totalAssets": "total_assets",
    "totalCurrentAssets": "current_assets",
    "totalCurrentLiabilities": "current_liabilities",
    "totalLiab": "total_liabilities",
    "netReceivables": "receivables",
    "propertyPlantAndEquipmentNet": "ppe_net",
    "longTermDebt": "long_term_debt",
    "shortTermDebt": "short_term_debt",
    "totalStockholderEquity": "equity",
    "retainedEarnings": "retained_earnings",
    "cash": "cash",
    "commonStockSharesOutstanding": "shares_outstanding",
}
CASHFLOW = {
    "totalCashFromOperatingActivities": "cash_from_operations",
    "capitalExpenditures": "capex",
    "dividendsPaid": "dividends_paid",
    "salePurchaseOfStock": "buybacks",
    "freeCashFlow": "free_cash_flow",
}
STATEMENTS = (("Income_Statement", INCOME), ("Balance_Sheet", BALANCE), ("Cash_Flow", CASHFLOW))


def market_of(instrument_id: str) -> str:
    """The MIC behind an instrument id (MYX:1155 trades on XKLS); '' when unknown."""
    from markets.registry import mic_of

    try:
        return mic_of(instrument_id)
    except ValueError:
        return ""


def symbol_for(instrument_id: str) -> str | None:
    _, _, code = instrument_id.partition(":")
    suffix = SYMBOL_SUFFIX.get(market_of(instrument_id))
    return f"{local_code(instrument_id) or code}.{suffix}" if suffix else None


class EodhdFundamentals(Collector):
    name = "eodhd"
    key_env = None  # optional; read directly so a missing key is a skip, not a failure

    def __init__(self, *args, plan: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # An explicit plan name wins over EODHD_PLAN, the way `key` wins over
        # EODHD_API_KEY: a test or a one-off run names it without touching the
        # environment the scheduled run reads.
        self._plan_name = plan

    def _token(self) -> str:
        token = (self._key or os.environ.get(KEY_ENV, "")).strip()
        if not token:
            raise KeyMissing(f"eodhd needs {KEY_ENV} (optional); skipped until it is set")
        return token

    def plan(self) -> Plan:
        return plan_from_env(self._plan_name)

    @staticmethod
    def rotation(
        instruments: tuple[str, ...],
        today: date,
        slot: str,
        *,
        plan_markets: AbstractSet[str] = FREE_PLAN_MARKETS,
        names_per_run: int = MAX_NAMES_PER_RUN,
    ) -> tuple[list[str], list[str]]:
        """The names this run asks for, and the ones deferred to a later turn.

        ONLY NAMES THE PLAN CAN SERVE ENTER THE ROTATION. This is the whole
        finding: the free plan is US-only, and rotating over the book as a whole
        spent every weekday slot on symbols guaranteed to be refused. On
        2026-09-07, with a valid key in place, the day's three slots asked for
        `1155.KLSE`, `5347.KLSE`, `5183.KLSE`, `5225.KLSE`, `8869.KLSE` and
        `3182.KLSE` - six refusals - and the three US names the plan DOES cover
        came up only in `weekly`, which fires on Sundays. The account's own
        dashboard read "0 of 20 API calls, most recent usage: never".

        A name outside the plan is not deferred either. Deferred means "next in
        rotation", and a Bursa name on the free plan is never next; it needs a
        paid plan, and saying so once is more use than saying "later" every day.

        `plan_markets` and `names_per_run` are parameters so a paid plan is one
        environment variable rather than an edit here: `Plan` widens the first
        and zeroes the second, and every name is asked for every run.
        """
        eligible = [i for i in instruments if symbol_for(i) and market_of(i) in plan_markets]
        names = eligible or [i for i in instruments if symbol_for(i)]
        if names_per_run <= 0 or len(names) <= names_per_run:
            return names, []
        slot_ix = SLOT_ORDER.index(slot) if slot in SLOT_ORDER else 0
        offset = (today.timetuple().tm_yday * names_per_run + slot_ix * names_per_run) % len(names)
        ordered = names[offset:] + names[:offset]
        return ordered[:names_per_run], ordered[names_per_run:]

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        token = self._token()
        plan = self.plan()  # once per run, not once per name
        today = self.today()
        asked, deferred = self.rotation(
            instruments, today, slot, plan_markets=plan.markets, names_per_run=plan.names_per_run
        )
        pull.notes.append(f"EODHD plan: {plan.describe()}")
        for iid in deferred:
            pull.notes.append(
                f"{iid}: deferred by the {plan.names_per_run}-a-day credit budget; next in rotation"
            )
        # Named once per run, not once per name: six identical lines saying the
        # same thing about the same plan is a wall, not a message.
        outside = [
            i
            for i in instruments
            if symbol_for(i) and market_of(i) not in plan.markets and i not in asked
        ]
        if outside:
            pull.notes.append(
                f"outside the {plan.name} plan (US only), so not asked for: {', '.join(outside)}. "
                f"The EODHD Fundamentals plan covers KLSE and TW."
            )
        failures: list[str] = []
        for iid in asked:
            symbol = symbol_for(iid)
            mic = market_of(iid)
            try:
                payload = self.get_json(
                    URL.format(symbol=symbol),
                    {"api_token": token, "fmt": "json", "filter": "Financials"},
                )
            except PlanExcluded:
                if mic not in plan.markets:
                    pull.notes.append(
                        f"{iid}: EODHD free plan: US only; the Fundamentals plan covers KLSE"
                    )
                else:
                    pull.notes.append(f"{iid}: outside the plan or over today's credits")
                continue
            except SourceError as e:
                failures.append(f"{iid}: {e}")
                continue
            message = (
                str((payload or {}).get("message") or (payload or {}).get("error") or "")
                if isinstance(payload, dict)
                else ""
            )
            if message and any(
                w in message.lower()
                for w in ("limit", "exceeded", "not available", "not supported")
            ):
                pull.notes.append(f"{iid}: EODHD said: {message[:120]}")
                continue
            rows = self._observations(iid, payload, today)
            if not rows:
                failures.append(f"{iid}: no Financials in the reply")
                continue
            pull.observations.extend(rows)
        if asked and failures and len(failures) == len(asked):
            raise SourceError("every name failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    def _observations(self, iid: str, payload, today: date) -> list[Observation]:
        if not isinstance(payload, dict):
            return []
        fin = payload.get("Financials") or payload
        out: list[Observation] = []
        for section, mapping in STATEMENTS:
            block = fin.get(section) or {}
            currency = str(block.get("currency_symbol") or "")
            for cadence, suffix in (("quarterly", ""), ("yearly", "_fy")):
                rows = block.get(cadence) or {}
                for period_key, row in rows.items() if isinstance(rows, dict) else ():
                    if not isinstance(row, dict):
                        continue
                    period = parse_date(row.get("date") or period_key)
                    if period is None:
                        continue
                    filed = parse_date(row.get("filing_date"))
                    known = filed if filed is not None and filed >= period else max(today, period)
                    for field_name, concept in mapping.items():
                        value = as_decimal(row.get(field_name))
                        if value is None:
                            continue
                        key = concept if section == "Balance_Sheet" else f"{concept}{suffix}"
                        if section == "Balance_Sheet" and cadence == "yearly":
                            continue  # instants: the quarterly series already carries the year-end
                        out.append(
                            Observation(
                                self.name,
                                iid,
                                key,
                                known_at=known,
                                value=Decimal(value),
                                period_end=period,
                                unit=currency or "ccy",
                                currency=currency,
                                payload={
                                    "cadence": cadence,
                                    "field": field_name,
                                    "filing_date": str(row.get("filing_date") or ""),
                                },
                            )
                        )
        return out
