"""EODHD fundamentals: quarterly and annual statements behind an optional key.

EODHD (eodhd.com) covers 60+ exchanges including Bursa Malaysia, which is the
one market this book holds and no free statement source reaches. Its free
plan is small and honest about it: 20 API credits a day, and a fundamentals
request costs 10, so two names a day, US only. The paid Fundamentals plan
lifts both limits and covers KLSE; the adapter is written for that day and
runs within the free budget until then.

  key       EODHD_API_KEY, optional: without it the collector records itself as
            skipped with the variable named (the FinMind shape, the sweep's
            semantics), and nothing else changes.
  budget    MAX_NAMES_PER_RUN names per run, rotated by the day of the year and
            the slot so the same two names are not asked every time; every
            deferred name gets a note saying when its turn comes.
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
SYMBOL_SUFFIX = {"XKLS": "KLSE", "XNAS": "US", "XNYS": "US", "XTAI": "TW"}
FREE_PLAN_MARKETS = {"XNAS", "XNYS"}
MAX_NAMES_PER_RUN = 2
SLOT_ORDER = ("bursa_close", "us_preopen", "us_close", "weekly", "all")

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

    def _token(self) -> str:
        token = (self._key or os.environ.get(KEY_ENV, "")).strip()
        if not token:
            raise KeyMissing(f"eodhd needs {KEY_ENV} (optional); skipped until it is set")
        return token

    @staticmethod
    def rotation(
        instruments: tuple[str, ...], today: date, slot: str
    ) -> tuple[list[str], list[str]]:
        """The names this run asks for, and the ones deferred to a later turn."""
        names = [i for i in instruments if symbol_for(i)]
        if len(names) <= MAX_NAMES_PER_RUN:
            return names, []
        slot_ix = SLOT_ORDER.index(slot) if slot in SLOT_ORDER else 0
        offset = (
            today.timetuple().tm_yday * MAX_NAMES_PER_RUN + slot_ix * MAX_NAMES_PER_RUN
        ) % len(names)
        ordered = names[offset:] + names[:offset]
        return ordered[:MAX_NAMES_PER_RUN], ordered[MAX_NAMES_PER_RUN:]

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        token = self._token()
        today = self.today()
        asked, deferred = self.rotation(instruments, today, slot)
        for iid in deferred:
            pull.notes.append(
                f"{iid}: deferred by the {MAX_NAMES_PER_RUN}-a-day credit budget; next in rotation"
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
                if mic not in FREE_PLAN_MARKETS:
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
