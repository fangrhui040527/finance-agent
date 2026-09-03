"""FX rates from Bank Negara Malaysia's public API, into the FxStore.

`FxStore` has existed since the money contract with nothing populating it: the
rate was supplied per call, and `config.toml`'s `fx_myr_per_usd = 4.15` is a
planning constant that says so. This is the seam that fills the store from the
central bank of the base currency - keyless, official, MYR-native.

Same rules as every other feed here:

  * **A broken source never looks like a quiet one.** Transport failure and
    malformed payloads raise `FxFeedError`; there is no empty-store fallback.
  * **Dated or nothing.** Every rate carries the date BNM published it; the
    store's `rate_asof` bisects to the last known rate on or before the ask.
  * **Units are honoured.** BNM quotes some currencies per 100 units (JPY,
    IDR, KRW, ...). Dividing by the published unit is the difference between
    a right number and one wrong by two orders of magnitude that still looks
    plausible on screen.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from core.market.prices import FxStore
from core.net.breaker import CircuitBreaker

BNM_API = "https://api.bnm.gov.my/public/exchange-rate"
#: BNM's API requires this Accept header; without it the answer is a 406.
ACCEPT = "application/vnd.BNM.API.v1+json"
BASE = "MYR"


class FxFeedError(RuntimeError):
    """The rate source could not be reached or answered with something unusable."""


class BnmFxFeed:
    """Bank Negara Malaysia daily middle rates, quoted as MYR per foreign unit."""

    name = "bnm"
    TIMEOUT = 30

    def __init__(self, opener: Callable | None = None, sleep: Callable | None = None) -> None:
        self._opener = opener
        self._sleep = sleep
        self._breaker = CircuitBreaker("bnm")

    def fetch_rates(self) -> list[tuple[str, date, Decimal]]:
        """[(currency, date, MYR per ONE unit)], validated. Raises on anything else."""
        import json
        import urllib.error
        import urllib.request

        from core.net.breaker import CircuitOpen
        from core.net.retry import with_retry

        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(
            BNM_API,
            headers={
                "Accept": ACCEPT,
                "User-Agent": "finplanet-analyst-mind/0.1 (personal research)",
            },
        )

        def _transport() -> bytes:
            with opener(req, timeout=self.TIMEOUT) as resp:
                return resp.read()

        try:
            self._breaker.before_call()
            if self._sleep is not None:
                body = with_retry(_transport, sleep=self._sleep)
            else:
                body = with_retry(_transport)
        except CircuitOpen as e:
            raise FxFeedError(str(e)) from e
        except (urllib.error.URLError, OSError) as e:
            self._breaker.record_failure(e)
            raise FxFeedError(f"BNM fetch failed: {e}") from e
        self._breaker.record_success()

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise FxFeedError(f"BNM returned non-JSON: {body[:200]!r}") from e
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise FxFeedError(f"BNM response has no data list: {str(payload)[:200]!r}")

        out: list[tuple[str, date, Decimal]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("currency_code", "")).upper()
            rate_block = row.get("rate") or {}
            raw_rate = rate_block.get("middle_rate")
            raw_date = rate_block.get("date")
            unit = row.get("unit", 1)
            if len(code) != 3 or raw_rate is None or not raw_date:
                continue  # a malformed ROW is skipped; a malformed FILE raised above
            try:
                per_unit = Decimal(str(raw_rate)) / Decimal(str(unit or 1))
                d = date.fromisoformat(str(raw_date))
            except (InvalidOperation, ValueError):
                continue
            if per_unit <= 0:
                continue  # Money.convert would refuse it anyway; drop at the seam
            out.append((code, d, per_unit))
        if not out:
            raise FxFeedError("BNM answered, but no row parsed to a usable dated rate")
        return out

    def populate(self, store: FxStore) -> int:
        """Fill the store with (MYR per foreign unit) rates. Returns rows added."""
        rates = self.fetch_rates()
        for code, d, per_unit in rates:
            # Stored as quote->MYR: rate_asof("USD", "MYR", day) answers
            # "how many MYR is one USD", the direction sizing asks in.
            store.add(code, BASE, d, per_unit)
        return len(rates)
