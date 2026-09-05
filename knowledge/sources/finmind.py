"""FinMind: Taiwan fundamentals with history, free, the rest of the Goodinfo answer.

TWSE's OpenAPI (knowledge/sources/twse.py) is official but snapshot-only.
FinMind (finmindtrade.com) is the community's free Taiwan data API and
carries the history: 300 requests an hour with no token, 600 with a free
one (`FINMIND_TOKEN`, optional - the first collector here whose key is not
required, so `key_env` stays unset and the token is read directly). Three
datasets per watched name:

  TaiwanStockMonthRevenue                 24 months of revenue
  TaiwanStockFinancialStatements          8 quarters: revenue, EPS, net income, gross profit
  TaiwanStockInstitutionalInvestorsBuySell 20 sessions of foreign net buying

`known_at` is the fetch day: FinMind gives periods, not filing dates, and
stamping a statement knowable on its period end would be look-ahead. A quota
answer (HTTP 402) is a note naming the dataset, never a retry storm. Names
come from `[sources] read_only`; nothing here is ever traded.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

from knowledge.facts import Observation, as_decimal
from knowledge.sources.base import (
    Collector,
    PlanExcluded,
    Pull,
    SourceError,
    local_code,
    parse_date,
)

URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN_ENV = "FINMIND_TOKEN"

STATEMENT_CONCEPTS = {
    "Revenue": ("revenue", "TWD"),
    "EPS": ("eps", "TWD"),
    "IncomeAfterTaxes": ("net_income", "TWD"),
    "GrossProfit": ("gross_profit", "TWD"),
}


class FinMindCollector(Collector):
    name = "finmind"
    key_env = None  # optional; see module docstring

    def _headers(self) -> dict:
        token = (self._key or os.environ.get(TOKEN_ENV, "")).strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        names = [i for i in instruments if i.upper().startswith("XTAI:")]
        if not names:
            return pull
        today = self.today()
        failures: list[str] = []
        for iid in names:
            code = local_code(iid)
            for dataset, start, step in (
                ("TaiwanStockMonthRevenue", today - timedelta(days=730), self._revenue),
                ("TaiwanStockFinancialStatements", today - timedelta(days=730), self._statements),
                (
                    "TaiwanStockInstitutionalInvestorsBuySell",
                    today - timedelta(days=30),
                    self._flows,
                ),
            ):
                try:
                    rows = self._rows(dataset, code, start.isoformat())
                except PlanExcluded as e:
                    pull.notes.append(f"{iid} {dataset}: quota or plan boundary - {e}")
                    continue
                except SourceError as e:
                    failures.append(f"{iid} {dataset}: {e}")
                    continue
                step(pull, iid, rows, today)
        if failures and len(failures) >= 3 * len(names):
            raise SourceError("every FinMind dataset failed. First: " + failures[0])
        pull.notes.extend(failures)
        pull.requests = self.requests
        return pull

    def _rows(self, dataset: str, code: str, start: str) -> list[dict]:
        payload = self.get_json(
            URL, {"dataset": dataset, "data_id": code, "start_date": start}, headers=self._headers()
        )
        if not isinstance(payload, dict):
            raise SourceError(f"finmind {dataset}: expected an object")
        status = payload.get("status")
        if status not in (None, 200, "200"):
            msg = str(payload.get("msg") or "")[:120]
            if status in (402, "402", 429, "429") or "limit" in msg.lower():
                raise PlanExcluded(f"finmind {dataset}: {status} {msg}")
            raise SourceError(f"finmind {dataset}: {status} {msg}")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise SourceError(f"finmind {dataset}: no data list")
        return [r for r in rows if isinstance(r, dict)]

    def _revenue(self, pull: Pull, iid: str, rows: list[dict], today) -> None:
        for r in rows:
            period = parse_date(r.get("date"))
            value = as_decimal(r.get("revenue"))
            if period is None or value is None:
                continue
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "revenue_month",
                    known_at=max(today, period),
                    value=value,
                    period_end=period,
                    unit="TWD",
                    currency="TWD",
                    payload={"year": r.get("revenue_year"), "month": r.get("revenue_month")},
                )
            )

    def _statements(self, pull: Pull, iid: str, rows: list[dict], today) -> None:
        for r in rows:
            mapped = STATEMENT_CONCEPTS.get(str(r.get("type") or ""))
            if mapped is None:
                continue
            concept, currency = mapped
            period = parse_date(r.get("date"))
            value = as_decimal(r.get("value"))
            if period is None or value is None:
                continue
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    concept,
                    known_at=max(today, period),
                    value=value,
                    period_end=period,
                    unit=currency,
                    currency=currency,
                    payload={"origin_name": r.get("origin_name")},
                )
            )

    def _flows(self, pull: Pull, iid: str, rows: list[dict], today) -> None:
        for r in rows:
            if str(r.get("name") or "") != "Foreign_Investor":
                continue
            day = parse_date(r.get("date"))
            bought, sold = as_decimal(r.get("buy")), as_decimal(r.get("sell"))
            if day is None or bought is None or sold is None:
                continue
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "foreign_net_buy",
                    known_at=max(today, day),
                    value=Decimal(bought - sold),
                    period_end=day,
                    unit="shares",
                )
            )
