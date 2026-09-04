"""The [paper] block loads, tightens only, and the monitor watches the book."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.config import ConfigError, load
from core.monitor import _paper_rules
from engines.paper.store import DECIDED, MarkRow, PaperStore

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path, block: str) -> Path:
    base = (ROOT / "config.toml").read_text(encoding="utf-8")
    start = base.index("[paper]")
    end = base.index("[monitor]")
    p = tmp_path / "config.toml"
    p.write_text(base[:start] + block + "\n\n" + base[end:], encoding="utf-8")
    return p


def test_the_shipped_block_loads_the_agreed_profile():
    cfg = load(ROOT / "config.toml")
    s = cfg.paper
    assert (s.start_date, s.initial_cash_usd, s.database) == (
        date(2026, 9, 8),
        Decimal(1000),
        "data/paper.db",
    )
    assert (s.max_weight_per_name, s.cash_floor, s.stop_loss, s.drawdown_halt) == (
        Decimal("0.25"),
        Decimal("0.20"),
        Decimal("0.08"),
        Decimal("0.08"),
    )
    assert (
        s.max_invested == Decimal("0.80")
        and s.slippage_bps("XKLS") == 10
        and s.slippage_bps("XNAS") == 5
    )
    assert cfg.alert_paper_drawdown == Decimal("0.08")


def test_an_absent_block_is_the_default_profile(tmp_path):
    p = _write(tmp_path, "")
    assert load(p).paper == replace(load(ROOT / "config.toml").paper)


def test_an_unknown_key_is_refused(tmp_path):
    p = _write(tmp_path, "[paper]\nmax_weight = 0.2\n")
    with pytest.raises(ConfigError, match=r"unknown \[paper\] keys"):
        load(p)


@pytest.mark.parametrize(
    "line",
    [
        "max_weight_per_name = 0.30",
        "cash_floor = 0.10",
        "stop_loss = 0.09",
        "drawdown_halt = 0.10",
        "weekly_turnover_cap = 0.60",
        "ramp_max_invested = 0.50",
        "observe_weeks = 1",
    ],
)
def test_the_caps_cannot_be_loosened(tmp_path, line):
    p = _write(tmp_path, f"[paper]\n{line}\n")
    with pytest.raises(ConfigError, match="not configurable"):
        load(p)


def test_the_caps_can_be_tightened_and_the_date_may_be_a_string(tmp_path):
    p = _write(tmp_path, '[paper]\nmax_weight_per_name = 0.20\nstart_date = "2026-10-05"\n')
    s = load(p).paper
    assert s.max_weight_per_name == Decimal("0.20") and s.start_date == date(2026, 10, 5)
    p = _write(tmp_path, '[paper]\nstart_date = "soon"\n')
    with pytest.raises(ConfigError, match="YYYY-MM-DD"):
        load(p)


def _mark(store, day, equity, peak, halted, cash=None):
    dd = (Decimal(1) - Decimal(equity) / Decimal(peak)).quantize(Decimal("0.0001"))
    store.record_mark(
        MarkRow(
            DECIDED,
            day,
            "us_close",
            datetime(day.year, day.month, day.day, tzinfo=UTC),
            Decimal(cash if cash is not None else equity),
            Decimal(0),
            Decimal(equity),
            Decimal(peak),
            dd,
            halted,
            "full",
            Decimal(4),
            day,
            "config",
            [],
        )
    )


def test_the_monitor_is_quiet_until_marked_then_names_drawdown_and_staleness(paper_env):
    env = paper_env
    now = datetime(2026, 3, 25, 22, 0, tzinfo=UTC)
    assert _paper_rules(env.cfg, now) == []
    _mark(env.store, date(2026, 3, 25), 990, 1000, False)
    assert _paper_rules(env.cfg, now) == []
    _mark(env.store, date(2026, 3, 25), 910, 1000, True)
    rules = {a.rule: a for a in _paper_rules(env.cfg, now)}
    assert "paper_drawdown" in rules and "9.00%" in rules["paper_drawdown"].title
    late = datetime(2026, 4, 1, 22, 0, tzinfo=UTC)
    rules = {a.rule for a in _paper_rules(env.cfg, late)}
    assert {"paper_drawdown", "paper_stale"} <= rules


def test_the_monitor_flags_a_cap_breached_by_drift(paper_env):
    env = paper_env
    now = datetime(2026, 3, 25, 22, 0, tzinfo=UTC)
    store: PaperStore = env.store
    store.record_mark(
        MarkRow(
            DECIDED,
            date(2026, 3, 25),
            "us_close",
            now,
            Decimal(150),
            Decimal(850),
            Decimal(1000),
            Decimal(1000),
            Decimal(0),
            False,
            "full",
            Decimal(4),
            date(2026, 3, 25),
            "config",
            [
                {
                    "instrument_id": "XNAS:NVDA",
                    "units": 1,
                    "avg_cost": "224",
                    "currency": "USD",
                    "close": "300",
                    "close_day": "2026-03-25",
                    "stale": False,
                    "value_usd": "300",
                    "weight": "0.30",
                    "pnl_open_usd": "76",
                }
            ],
        )
    )
    rules = {a.rule: a for a in _paper_rules(env.cfg, now)}
    assert "paper_cap_breach" in rules
    assert (
        "XNAS:NVDA above 25%" in rules["paper_cap_breach"].title
        and "cash 15.0% below" in rules["paper_cap_breach"].title
    )
