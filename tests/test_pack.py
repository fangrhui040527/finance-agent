"""The feedback pack: both legs measured, betas from the window before the day."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from core.market.feed import PriceFeedError, market_proxy_for
from core.market.prices import Bar, PriceSeries
from knowledge.pack import build_pack, market_fit, measure, write_pack

DAY = date(2026, 9, 4)
NOW = datetime(2026, 9, 4, 22, 30, tzinfo=UTC)


def _series(iid: str, beta: float, last_shock: float) -> PriceSeries:
    """200 sessions; the instrument follows its proxy with `beta`, plus a shock on the last day.

    The proxy's return stream is seeded by the PROXY's id, so the same market
    path is served whether the proxy is fetched itself or followed by a name.
    """
    proxy_id = market_proxy_for(iid) or iid
    is_proxy = proxy_id == iid
    market = random.Random(sum(map(ord, proxy_id)))
    noise = random.Random(sum(map(ord, iid)) * 7)
    days = [
        DAY - timedelta(days=i)
        for i in range(260, -1, -1)
        if (DAY - timedelta(days=i)).weekday() < 5
    ]
    proxy, own = 100.0, 50.0
    bars = []
    for i, d in enumerate(days):
        m = market.gauss(0.0003, 0.009)
        proxy *= 1 + m
        shock = last_shock if i == len(days) - 1 else 0.0
        own *= 1 + beta * m + noise.gauss(0, 0.004) + shock
        px = proxy if is_proxy else own
        bars.append(Bar(d, px, px * 1.01, px * 0.99, px, 1_000_000))
    return PriceSeries(iid, bars)


@dataclass
class FakeFeed:
    shock: float = -0.03
    source_used: str = "cache"
    missing: tuple = ()

    def fetch(self, iid, start=None, end=None):
        if iid in self.missing:
            raise PriceFeedError(f"{iid}: no bars cached")
        beta = 1.3 if iid.startswith("XNAS") else 0.8
        s = _series(iid, beta, self.shock if market_proxy_for(iid) != iid else 0.0)
        if end is not None:
            s = PriceSeries(iid, [b for b in s.raw() if b.day <= end])
        return s


@dataclass
class Cfg:
    watchlist: tuple = ("MYX:1155", "XNAS:NVDA")
    holdings: tuple = ()
    corpus_db: str = ":memory:"
    facts_db: str = ":memory:"
    base_currency: str = "MYR"


def test_measure_takes_both_legs_from_the_same_sessions_and_decomposes():
    m = measure(FakeFeed(), "MYX:1155", "Maybank", DAY)
    assert not m.error and m.proxy == "MYX:0820EA" and m.last_day == DAY
    assert m.r1 is not None and m.m1 is not None and m.r5 is not None
    assert m.r1 < -0.02, "the shock is in the instrument's last return"
    assert m.beta is not None and 0.5 < m.beta < 1.2
    assert m.verdict in ("no_identified_catalyst", "market_driven", "not_significant")
    assert m.unexplained is not None and 0 <= m.unexplained <= 1
    assert "sector beta fixed at 0" in m.estimation and "shrunk 20%" in m.estimation
    assert set(m.components) >= {"market", "idiosyncratic"}


def test_a_name_whose_bars_are_missing_is_no_data_not_a_typed_leg():
    m = measure(FakeFeed(missing=("MYX:0820EA",)), "MYX:1155", "Maybank", DAY)
    assert m.error and "no bars cached" in m.error and m.r1 is None
    assert "NO DATA" in m.row()


def test_a_market_without_a_proxy_is_refused_not_guessed():
    m = measure(FakeFeed(), "XHKG:0005", "HSBC", DAY)
    assert "no market proxy" in m.error


def test_market_fit_needs_the_minimum_window_and_widens_to_two_factors():
    assert market_fit([0.01] * 10, [0.01] * 10) is None
    rng = random.Random(1)
    mkt = [rng.gauss(0, 0.01) for _ in range(150)]
    inst = [1.2 * m + rng.gauss(0, 0.003) for m in mkt]
    fit = market_fit(inst, mkt)
    assert fit is not None and len(fit.coefficients) == 3 and fit.coefficients[2] == 0.0
    assert 1.0 < fit.coefficients[1] < 1.3 and fit.shrinkage == 0.2


def test_the_pack_carries_every_section_and_writes_where_told(tmp_path):
    text = build_pack(
        Cfg(),
        DAY,
        corpus_path=str(tmp_path / "c.db"),
        facts_path=str(tmp_path / "f.db"),
        feed=FakeFeed(),
        now=NOW,
        previous_dir=tmp_path / "fb",
    )
    for heading in (
        "# Feedback pack 2026-09-04",
        "## Moves",
        "# Digest 2026-09-04",
        "## Facts, per name",
        "## Macro",
        "## Previous pages",
    ):
        assert heading in text, heading
    assert "| Maybank (MYX:1155) |" in text and "| NVIDIA (XNAS:NVDA) |" in text
    assert "NOTHING COLLECTED" in text and "NO MACRO SERIES" in text, "empty stores say so"
    assert "- none yet" in text
    path = write_pack(text, DAY, tmp_path / "fb")
    assert path.name == "2026-09-04.pack.md" and path.read_text(encoding="utf-8").startswith(
        "# Feedback pack"
    )


def test_previous_pages_are_listed_newest_three_before_the_day(tmp_path):
    fb = tmp_path / "fb"
    fb.mkdir()
    for d in ("2026-08-30", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"):
        (fb / f"{d}.md").write_text("# x", encoding="utf-8")
    (fb / "README.md").write_text("# not a page", encoding="utf-8")
    text = build_pack(
        Cfg(),
        DAY,
        corpus_path=str(tmp_path / "c.db"),
        facts_path=str(tmp_path / "f.db"),
        feed=FakeFeed(),
        now=NOW,
        previous_dir=fb,
    )
    tail = text[text.index("## Previous pages") :]
    assert "2026-09-01.md" in tail and "2026-09-02.md" in tail and "2026-09-03.md" in tail
    assert "2026-08-30" not in tail and "2026-09-04.md" not in tail and "2026-09-05" not in tail


def test_the_cli_writes_the_pack_and_exits_zero_even_when_prices_are_unreachable(
    tmp_path, monkeypatch, capsys
):
    import ask
    import knowledge.pack as pack

    monkeypatch.setattr(
        pack,
        "build_pack",
        lambda cfg, day, **kw: build_pack(
            cfg,
            day,
            feed=FakeFeed(missing=("XNAS:SPY", "MYX:0820EA")),
            corpus_path=str(tmp_path / "c.db"),
            facts_path=str(tmp_path / "f.db"),
            now=NOW,
            previous_dir=tmp_path / "fb",
        ),
    )
    code = ask.main(["pack", "--date", "2026-09-04", "--write", "--out", str(tmp_path / "fb")])
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("NO DATA") >= 2 and (tmp_path / "fb" / "2026-09-04.pack.md").exists()


@dataclass
class GappyFeed(FakeFeed):
    """A feed where the PROXY skips a session the name printed.

    0820EA is a thinly traded ETF and did not print on 2026-09-07. The six Bursa
    names then reported Friday's move on a page dated Monday, two of them
    sign-flipped, and nothing in the pack said so.
    """

    blank: tuple = ()

    def fetch(self, iid, start=None, end=None):
        s = super().fetch(iid, start=start, end=end)
        if iid in self.blank:
            s = PriceSeries(iid, [b for b in s.raw() if b.day != DAY])
        return s


def test_a_proxy_that_did_not_print_makes_the_row_mis_dated_and_says_so():
    m = measure(GappyFeed(blank=("MYX:0820EA",)), "MYX:1155", "Maybank", DAY)
    assert not m.error
    assert m.last_day == DAY - timedelta(days=1) and m.own_last == DAY  # the session before
    assert m.mis_dated and "MIS-DATED" in m.dating and "MYX:0820EA" in m.dating
    assert "MIS-DATED" in m.row()


def test_a_market_that_was_simply_shut_is_correctly_dated_not_flagged():
    """Both legs quiet is a holiday, not a fault; only a silent fallback is."""
    m = measure(GappyFeed(blank=("MYX:0820EA", "MYX:1155")), "MYX:1155", "Maybank", DAY)
    assert not m.error and not m.mis_dated
    assert m.last_day == DAY - timedelta(days=1) and "correctly dated" in m.dating
    assert "MIS-DATED" not in m.row()
