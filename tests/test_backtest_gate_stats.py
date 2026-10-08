"""The backtest gate's statistics are on the right scale, its trial count survives a moving window, and a reasserted graph edge comes back.

Each test fails on the code before 2026-10-08.
"""

from __future__ import annotations

import random
from datetime import date

from engines.backtest.harness import run
from engines.backtest.trials import TrialLedger
from knowledge.graph.build import build
from knowledge.graph.entity_graph import Confidence, EdgeKind
from knowledge.graph.extractors.base import Extractor, company_node, edge, sorted_payload
from knowledge.graph.store import GraphStore


def test_zero_edge_noise_clears_the_deflated_sharpe_gate_at_about_the_nominal_rate():
    """The gate is P >= 0.95. Fed the annualised Sharpe it passed ~47% of pure noise."""
    rng = random.Random(3)
    trials, passed = 600, 0
    for _ in range(trials):
        noise = [rng.gauss(0.0, 0.01) for _ in range(300)]
        rep = run(noise, noise, {}, n_trials=1)
        passed += rep.deflated_sharpe >= 0.95
    assert passed / trials <= 0.08, f"{passed / trials:.1%} of noise strategies passed"


def test_a_real_edge_still_clears_it():
    rng = random.Random(5)
    edge_returns = [0.002 + rng.gauss(0.0, 0.01) for _ in range(750)]
    assert run(edge_returns, edge_returns, {}, n_trials=1).deflated_sharpe >= 0.95


def test_a_rule_tried_on_a_window_one_month_later_is_the_same_experiment(tmp_path):
    led = TrialLedger(tmp_path / "trials.db")
    old0, old1 = date(2021, 9, 7), date(2026, 9, 4)
    for i in range(3):
        led.record(f"rule{i}", "fbm100", old0, old1, 1250, 0.1, 0.01)
    new0, new1 = date(2021, 10, 7), date(2026, 10, 6)
    led.record("rule3", "fbm100", new0, new1, 1250, 0.1, 0.01)
    assert led.distinct_rules("fbm100", new0, new1) == 4


def test_a_window_that_barely_overlaps_is_a_different_experiment(tmp_path):
    led = TrialLedger(tmp_path / "trials.db")
    led.record("rule0", "fbm100", date(2010, 1, 1), date(2020, 1, 1), 2500, 0.1, 0.01)
    led.record("rule1", "fbm100", date(2015, 1, 1), date(2025, 1, 1), 2500, 0.1, 0.01)
    assert led.distinct_rules("fbm100", date(2015, 1, 1), date(2025, 1, 1)) == 1


class _Pair(Extractor):
    name = "pair"

    def __init__(self, emit: bool):
        self.emit = emit

    def extract(self):
        a, b = company_node("MYX:1155"), company_node("MYX:1023")
        rows = (
            [
                edge(
                    a["id"],
                    b["id"],
                    EdgeKind.COMPETES_WITH,
                    doc="d",
                    confidence=Confidence.EXTRACTED,
                    valid_from=date(2020, 1, 1),
                )
            ]
            if self.emit
            else []
        )
        return sorted_payload([a, b], rows)


def test_an_edge_closed_by_prune_comes_back_when_the_sources_assert_it_again(tmp_path):
    with GraphStore(tmp_path / "g.db") as s:
        build(s, extractors=[_Pair(True)], skip_markets=True)
        build(s, extractors=[_Pair(False)], skip_markets=True, prune_on=date(2026, 6, 1))
        assert not [e for e in s.live_edges(date(2026, 7, 1)) if e.kind is EdgeKind.COMPETES_WITH]
        report = build(s, extractors=[_Pair(True)], skip_markets=True, today=date(2026, 7, 1))
        assert len(report.reasserted) == 1 and "reasserted" in report.describe()
        live = [e for e in s.live_edges(date(2026, 7, 2)) if e.kind is EdgeKind.COMPETES_WITH]
        assert len(live) == 1 and live[0].valid_from == date(2026, 7, 1)
        assert s.counts()["closed"] == 1, "the closed interval is history and stays"
        again = build(s, extractors=[_Pair(True)], skip_markets=True, today=date(2026, 7, 2))
        assert again.reasserted == [], "an open edge is not reasserted twice"
