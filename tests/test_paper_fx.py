"""USD/MYR for a USD book: as-of from the log, the config as fallback, the spread named."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.market.fxlog import FxLog
from engines.paper.fx import UsdMyr


def test_asof_takes_the_last_published_rate_on_or_before_the_day(tmp_path):
    path = tmp_path / "fx.db"
    with FxLog(path) as log:
        log.record("USD", date(2026, 3, 2), Decimal("4.10"))
        log.record("USD", date(2026, 3, 4), Decimal("4.20"))
    fx = UsdMyr(path, Decimal("4.0"), Decimal("0.005"))
    assert fx.asof(date(2026, 3, 3)) == fx.asof(date(2026, 3, 2))
    assert (
        fx.asof(date(2026, 3, 3)).rate == Decimal("4.10")
        and fx.asof(date(2026, 3, 3)).source == "bnm"
    )
    assert fx.asof(date(2026, 3, 9)).rate == Decimal("4.20")
    q = fx.asof(date(2026, 3, 1))
    assert q.source == "config" and q.rate == Decimal("4.0")


def test_a_missing_log_falls_back_to_the_config_rate_and_says_so(tmp_path):
    fx = UsdMyr(tmp_path / "nowhere.db", Decimal("4.055"), Decimal("0.005"))
    q = fx.asof(date(2026, 9, 4))
    assert (q.rate, q.source, q.rate_date) == (Decimal("4.055"), "config", date(2026, 9, 4))


def test_spread_costs_the_book_in_both_directions():
    fx = UsdMyr(None, Decimal("4.0"), Decimal("0.005"))
    q = fx.asof(date(2026, 3, 2))
    myr = Decimal(400)
    mid = fx.myr_to_usd_mid(myr, q)
    assert mid == Decimal(100)
    assert fx.usd_needed_to_buy_myr(myr, q) > mid > fx.usd_received_for_myr(myr, q)
    # the identities the ledger relies on
    tol = Decimal("0.000001")
    assert abs(fx.usd_needed_to_buy_myr(myr, q) * q.rate * (1 - fx.spread) - myr) < tol
    assert abs(fx.usd_received_for_myr(myr, q) * q.rate * (1 + fx.spread) - myr) < tol
    assert fx.usd_to_myr_mid(mid, q) == myr
