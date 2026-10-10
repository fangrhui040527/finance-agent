"""A move is tested against the window it spans, and a question is followed across its rewordings.

Each test fails on the code before 2026-10-08:

  * `decompose` divided an N-session residual by the ONE-session sigma and
    subtracted one session's drift, so under pure noise about 27% of ordinary
    5-session windows read "significant at 5%";
  * the pack's "1d" return ran between the last two COMMON days, so a proxy
    with a missing row turned a two-session move into a "1d" one tested
    against a daily sigma (IHH, 2026-09-23: +4.99% at 4.11 sigma, the second
    session flat in the cache);
  * the open-question ledger keyed a question on its exact wording, so the
    writer's nightly rewording closed it and reopened it at one night.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

from core.market.prices import PriceSeries
from engines.attribution.decompose import Component, decompose
from knowledge.feedback_questions import answered, open_questions
from knowledge.pack import market_fit, measure
from tests.test_pack import DAY, FakeFeed

W = (date(2026, 9, 28), date(2026, 10, 2))


def _fit(seed: int = 7, sigma: float = 0.01):
    rng = random.Random(seed)
    mkt = [rng.gauss(0.0, 0.009) for _ in range(260)]
    inst = [0.0004 + 0.9 * m + rng.gauss(0.0, sigma) for m in mkt]
    return market_fit(inst, mkt)


def test_a_five_session_window_of_pure_noise_is_significant_about_five_percent_of_the_time():
    fit = _fit()
    rng = random.Random(11)
    alpha, beta = fit.coefficients[0], fit.coefficients[1]
    s = fit.residual_sigma
    hits = 0
    trials = 2000
    for _ in range(trials):
        m5 = sum(rng.gauss(0.0, 0.009) for _ in range(5))
        noise = sum(rng.gauss(0.0, s) for _ in range(5))
        realised = 5 * alpha + beta * m5 + noise
        exp = decompose("X", W, m5, 0.0, {}, realised, 0.0, fit, sessions=5)
        hits += abs(exp.significance.standardised_ar) > 1.96
    rate = hits / trials
    assert 0.02 <= rate <= 0.09, f"{rate:.1%} of noise weeks read significant"


def test_the_drift_leg_is_one_alpha_per_session():
    fit = _fit()
    exp = decompose("X", W, 0.01, 0.0, {}, 0.02, 0.0, fit, sessions=5)
    drift = next(c.contribution for c in exp.components if c.component is Component.DRIFT)
    assert math.isclose(drift, 5 * fit.coefficients[0], rel_tol=1e-12)
    assert math.isclose(
        sum(c.contribution for c in exp.components), exp.total_return_base, abs_tol=1e-12
    ), "the components still sum to the return"
    assert "5-session window" in exp.estimation_note


def test_one_session_is_unchanged():
    fit = _fit()
    one = decompose("X", (W[1], W[1]), 0.01, 0.0, {}, 0.03, 0.0, fit)
    same = decompose("X", (W[1], W[1]), 0.01, 0.0, {}, 0.03, 0.0, fit, sessions=1)
    assert one.significance.standardised_ar == same.significance.standardised_ar
    assert math.isclose(
        one.significance.standardised_ar,
        (0.03 - fit.coefficients[0] - fit.coefficients[1] * 0.01) / fit.residual_sigma,
        rel_tol=1e-9,
    )


class _ProxyGapFeed(FakeFeed):
    """^KLSE has no row for the session before DAY; the name has one."""

    def fetch(self, iid, start=None, end=None):
        s = super().fetch(iid, start=start, end=end)
        if iid == "MYX:^KLSE":
            gap = DAY - timedelta(days=1)
            s = PriceSeries(iid, [b for b in s.raw() if b.day != gap])
        return s


def test_a_1d_row_across_a_proxy_gap_is_labelled_and_tested_as_two_sessions():
    m = measure(_ProxyGapFeed(), "MYX:1155", "Maybank", DAY)
    assert not m.error and m.last_day == DAY
    assert m.span == 2
    assert "SPANS 2 SESSIONS" in m.dating and str(DAY - timedelta(days=1)) in m.dating
    assert "SPANS 2 SESSIONS" in m.row()
    assert "2-session window" in m.estimation


def test_an_ordinary_row_spans_one_session():
    m = measure(FakeFeed(), "MYX:1155", "Maybank", DAY)
    assert m.span == 1 and "SPANS" not in m.row()


def _page(directory, day: str, carried: list[str]) -> None:
    (directory / f"{day}.json").write_text(
        json.dumps({"day": day, "open_questions_carried": carried, "names": []}), encoding="utf-8"
    )


def test_a_reworded_question_with_the_same_stamp_is_the_same_question(tmp_path):
    _page(
        tmp_path,
        "2026-09-29",
        [
            "Tenaga fell 2.52 sigma with no collected row. What did the company, the Energy "
            "Commission or a Bursa filing publish on 09-29? (since 2026-09-29)"
        ],
    )
    _page(
        tmp_path,
        "2026-09-30",
        [
            "Tenaga fell 2.52 sigma on 09-29 with no collected row and gave back three-quarters "
            "of it on 09-30. What did the company, the Energy Commission or a Bursa filing "
            "publish on 09-29? (since 2026-09-29)"
        ],
    )
    assert answered(tmp_path) == [], "a rewording is not an answer"
    (q,) = open_questions(tmp_path)
    assert q.nights == 2 and q.first_asked == date(2026, 9, 29)
    assert "gave back three-quarters" in q.text, "the newest wording is the question now"


def test_two_different_questions_with_one_stamp_stay_two(tmp_path):
    a = "Which source carries Bursa ex-dates? (since 2026-09-23)"
    b = "Should the pack refuse a row across a blank proxy bar? (since 2026-09-23)"
    _page(tmp_path, "2026-09-23", [a, b])
    _page(tmp_path, "2026-09-24", [a])
    assert [q.text for q in open_questions(tmp_path)] == [a]
    assert [q.text for q in answered(tmp_path)] == [b]
