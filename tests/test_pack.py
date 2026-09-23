"""The feedback pack: both legs measured, betas from the window before the day."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from core.market.bars import drop_carried_rows
from core.market.feed import PriceFeedError, market_proxy_for
from core.market.prices import Bar, PriceSeries
from knowledge.pack import build_pack, estimation_slice, market_fit, measure, write_pack

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
    assert not m.error and m.proxy == "MYX:^KLSE" and m.last_day == DAY
    assert m.r1 is not None and m.m1 is not None and m.r5 is not None
    assert m.r1 < -0.02, "the shock is in the instrument's last return"
    assert m.beta is not None and 0.5 < m.beta < 1.2
    assert m.verdict in ("no_identified_catalyst", "market_driven", "not_significant")
    assert m.unexplained is not None and 0 <= m.unexplained <= 1
    assert "sector beta fixed at 0" in m.estimation and "shrunk 20%" in m.estimation
    assert set(m.components) >= {"market", "idiosyncratic"}


def test_a_name_whose_bars_are_missing_is_no_data_not_a_typed_leg():
    m = measure(FakeFeed(missing=("MYX:^KLSE",)), "MYX:1155", "Maybank", DAY)
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
            feed=FakeFeed(missing=("XNAS:SPY", "MYX:^KLSE")),
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

    0820EA, the KLCI ETF this book used as its Bursa proxy until 2026-09-14, did
    not print on 2026-09-07. The six Bursa names then reported Friday's move on a
    page dated Monday, two of them sign-flipped, and nothing in the pack said so.

    The ETF is gone and these tests stay, because the fault is not the ETF's: ANY
    proxy can miss a session its names printed - a holiday one market observes and
    the other does not, a vendor gap, a halt - and the labelling is what makes that
    legible instead of silent.
    """

    blank: tuple = ()

    def fetch(self, iid, start=None, end=None):
        s = super().fetch(iid, start=start, end=end)
        if iid in self.blank:
            s = PriceSeries(iid, [b for b in s.raw() if b.day != DAY])
        return s


def test_a_proxy_that_did_not_print_makes_the_row_mis_dated_and_says_so():
    m = measure(GappyFeed(blank=("MYX:^KLSE",)), "MYX:1155", "Maybank", DAY)
    assert not m.error
    assert m.last_day == DAY - timedelta(days=1) and m.own_last == DAY  # the session before
    assert m.mis_dated and "MIS-DATED" in m.dating and "MYX:^KLSE" in m.dating
    assert "MIS-DATED" in m.row()


def test_a_market_that_was_simply_shut_is_correctly_dated_not_flagged():
    """Both legs quiet is a holiday, not a fault; only a silent fallback is."""
    m = measure(GappyFeed(blank=("MYX:^KLSE", "MYX:1155")), "MYX:1155", "Maybank", DAY)
    assert not m.error and not m.mis_dated and not m.stale
    assert m.last_day == DAY - timedelta(days=1) and "correctly dated" in m.dating
    assert "MIS-DATED" not in m.row() and "STALE" not in m.row()


def test_a_name_that_did_not_print_when_its_market_did_is_stale_not_a_holiday():
    """The mirror of the MIS-DATED case. The note used to say "no session for
    either" whenever the NAME lacked a later bar, without looking at the proxy,
    so a halted or delisted name was labelled a market holiday."""
    m = measure(GappyFeed(blank=("MYX:1155",)), "MYX:1155", "Maybank", DAY)
    assert not m.error
    assert m.last_day == DAY - timedelta(days=1) and m.mkt_last == DAY
    assert m.stale and not m.mis_dated
    assert m.dating.startswith("STALE NAME: MYX:^KLSE printed 2026-09-04 but MYX:1155 did not")
    assert f"every figure in this row is the {m.last_day} session" in m.dating
    assert "correctly dated" not in m.dating
    assert "**STALE NAME: this is the 2026-09-03 session**" in m.row()


@dataclass
class PulledAtFeed(FakeFeed):
    """FakeFeed plus an answer to "when was this pulled": US legs at 14:05 UTC on
    DAY, 35 minutes into Nasdaq's session; Bursa legs after Bursa's 09:00 shut."""

    def fetched_at(self, iid):
        if iid.startswith("XNAS"):
            return datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
        return datetime(2026, 9, 4, 9, 30, tzinfo=UTC)


def test_a_row_decomposed_from_a_bar_pulled_mid_session_says_so():
    """2026-09-22: the only US bars on `main` for the day were 14:05 UTC quotes,
    and the moves table decomposed them like closes. A Bursa leg pulled after
    its own 09:00 shut is settled, whatever Nasdaq was doing."""
    nvda = measure(PulledAtFeed(), "XNAS:NVDA", "NVIDIA", DAY)
    assert not nvda.error and nvda.last_day == DAY
    assert nvda.provisional.startswith("PROVISIONAL: XNAS:NVDA pulled 2026-09-04 14:05Z")
    assert "XNAS:SPY pulled 2026-09-04 14:05Z" in nvda.provisional, "the proxy leg too"
    assert "**PROVISIONAL: the 2026-09-04 session so far, not the close**" in nvda.row()
    maybank = measure(PulledAtFeed(), "MYX:1155", "Maybank", DAY)
    assert not maybank.error and maybank.provisional == ""
    assert "PROVISIONAL" not in maybank.row()
    # a feed that cannot say when it pulled is not guessed at
    assert measure(FakeFeed(), "XNAS:NVDA", "NVIDIA", DAY).provisional == ""


def test_the_pack_lists_provisional_rows(tmp_path):
    text = build_pack(
        Cfg(),
        DAY,
        corpus_path=str(tmp_path / "c.db"),
        facts_path=str(tmp_path / "f.db"),
        feed=PulledAtFeed(),
        now=NOW,
        previous_dir=tmp_path / "fb",
    )
    assert "1 of 2 rows are PROVISIONAL" in text
    assert "- NVIDIA (XNAS:NVDA): PROVISIONAL: XNAS:NVDA pulled 2026-09-04 14:05Z" in text


def test_the_pack_lists_stale_names_apart_from_mis_dated_rows(tmp_path):
    text = build_pack(
        Cfg(),
        DAY,
        corpus_path=str(tmp_path / "c.db"),
        facts_path=str(tmp_path / "f.db"),
        feed=GappyFeed(blank=("MYX:1155",)),
        now=NOW,
        previous_dir=tmp_path / "fb",
    )
    assert "1 of 2 rows are STALE NAMES" in text and "MIS-DATED" not in text
    assert "- STALE NAME: MYX:^KLSE printed 2026-09-04 but MYX:1155 did not" in text


# --- rows the vendor writes for a day the exchange was shut ---------------------
THU, FRI, TUE, WED = date(2026, 5, 28), date(2026, 5, 29), date(2026, 6, 2), date(2026, 6, 3)


def test_a_carried_close_is_dropped_and_a_flat_but_traded_day_is_kept():
    """Maybank around the 2026-06-01 holiday, as Yahoo wrote it: Tuesday is the
    Friday close carried forward on zero volume. A share that really printed at
    the previous close, on volume, is a session and stays."""
    thu = Bar(THU, 10.94, 10.96, 10.50, 10.50, 27_822_800)
    fri = Bar(FRI, 10.54, 10.66, 10.50, 10.64, 61_428_200)
    carried = Bar(TUE, 10.64, 10.64, 10.64, 10.64, 0)
    wed = Bar(WED, 10.34, 10.68, 10.32, 10.42, 24_954_500)
    assert drop_carried_rows([thu, fri, carried, wed]) == [thu, fri, wed]
    flat_traded = Bar(TUE, 10.64, 10.64, 10.64, 10.64, 312_000)
    assert drop_carried_rows([thu, fri, flat_traded, wed]) == [thu, fri, flat_traded, wed]


def test_an_index_row_repeated_whole_is_dropped_however_many_times_it_repeats():
    """^KLSE for 2026-05-29, 06-01 and 06-02 in the cache: one row printed three times."""
    fri = Bar(FRI, 1688.1801, 1694.65, 1680.59, 1683.0699, 1_664_465_000)
    mon = Bar(date(2026, 6, 1), 1688.1801, 1694.65, 1680.59, 1683.0699, 1_664_465_000)
    tue = Bar(TUE, 1688.1801, 1694.65, 1680.59, 1683.0699, 1_664_465_000)
    wed = Bar(WED, 1687.13, 1693.09, 1672.74, 1672.74, 416_284_100)
    assert drop_carried_rows([fri, mon, tue, wed]) == [fri, wed]


def test_a_zero_volume_bar_whose_prices_moved_is_a_session():
    """The index prints 0 volume on the current day before the vendor fills it in."""
    fri = Bar(FRI, 1700.0, 1710.0, 1690.0, 1705.0, 300_000_000)
    today = Bar(TUE, 1705.0, 1720.0, 1700.0, 1715.0, 0)
    assert drop_carried_rows([fri, today]) == [fri, today]


def test_the_first_bar_is_always_kept():
    lone = Bar(TUE, 10.64, 10.64, 10.64, 10.64, 0)
    assert drop_carried_rows([]) == [] and drop_carried_rows([lone]) == [lone]


@dataclass
class HolidayFeed(FakeFeed):
    """DAY as Yahoo writes a Bursa holiday: the share's previous close carried
    forward on zero volume, the index's previous row repeated whole. Both pass the
    bar parser, which checks a bar's own arithmetic and cannot know the market was
    shut; both used to reach the pack as a 0.00% session with a verdict."""

    def fetch(self, iid, start=None, end=None):
        s = super().fetch(iid, start=start, end=end)
        bars = [b for b in s.raw() if b.day != DAY]
        prev = bars[-1]
        if market_proxy_for(iid) == iid:
            filler = Bar(DAY, prev.open, prev.high, prev.low, prev.close, prev.volume)
        else:
            filler = Bar(DAY, prev.close, prev.close, prev.close, prev.close, 0)
        return PriceSeries(iid, bars + [filler])


def test_a_holiday_the_vendor_wrote_as_a_row_is_no_session_not_a_flat_verdict():
    m = measure(HolidayFeed(), "MYX:1155", "Maybank", DAY)
    assert not m.error
    assert m.last_day == DAY - timedelta(days=1), "Thursday, the last session that traded"
    assert m.own_last == m.mkt_last == m.last_day
    assert m.r1 != 0.0 and m.m1 != 0.0
    assert not m.mis_dated and not m.stale
    assert "no session for MYX:1155 or MYX:^KLSE after 2026-09-03" in m.dating
    assert "correctly dated" in m.dating


# --- the estimation window --------------------------------------------------------
def test_estimation_slice_ends_before_a_gap_and_caps_the_lookback():
    idx = list(range(500))[estimation_slice(500)]
    assert len(idx) == 260 and idx[-1] == 500 - 12 and idx[0] == 500 - 271
    short = list(range(150))[estimation_slice(150)]
    assert len(short) == 139 and short[-1] == 138, "shortens to what there is; the gap is kept"
    assert list(range(8))[estimation_slice(8)] == []


def test_the_beta_window_ends_ten_sessions_before_the_event_and_says_which():
    """docs/03 section 2.2 rule 1. The pack used to fit the 120 returns right up to
    the event - equal to the minimum, so every row said "short window", and a
    move that took a week to play out sat in its own sigma."""
    m = measure(FakeFeed(), "MYX:1155", "Maybank", DAY)
    days = [b.day for b in FakeFeed().fetch("MYX:1155", end=DAY).raw()]  # both legs share them
    n_returns = len(days) - 1
    window = days[1:][estimation_slice(n_returns)]
    assert len(window) == min(260, n_returns - 11) and window[-1] == days[-12]
    assert f"betas from {len(window)} sessions" in m.estimation
    assert f"window {window[0]}..{window[-1]} (10 sessions before the event)" in m.estimation


@dataclass
class ShortFeed(FakeFeed):
    """A recent listing: 125 sessions, which was enough for the old 120-session fit."""

    keep: int = 125

    def fetch(self, iid, start=None, end=None):
        s = super().fetch(iid, start=start, end=end)
        return PriceSeries(iid, s.raw()[-self.keep :])


def test_below_the_floor_once_the_gap_is_taken_there_is_no_estimate():
    m = measure(ShortFeed(), "MYX:1155", "Maybank", DAY)
    assert not m.error and m.beta is None and m.unexplained is None
    assert m.verdict == "attribution_unavailable"
    assert m.estimation.startswith(
        "no estimate: 113 sessions before the 10-session gap, 120 needed"
    )


# --- the currency the returns are in ------------------------------------------------
def test_a_nasdaq_name_is_measured_in_its_own_currency_and_says_the_myr_leg_is_not():
    """No FX return is measured, so a USD return was being labelled MYR with a
    currency leg of 0.00% - a conversion that never happened."""
    m = measure(FakeFeed(), "XNAS:NVDA", "NVIDIA", DAY)
    assert m.currency == "USD"
    assert m.estimation.endswith("; returns in USD, MYR leg not measured")
    assert m.components.get("currency", 0.0) == 0.0
    local = measure(FakeFeed(), "MYX:1155", "Maybank", DAY)
    assert local.currency == "MYR" and "leg not measured" not in local.estimation
    paper = measure(FakeFeed(), "XNAS:NVDA", "NVIDIA", DAY, "USD")  # the USD paper book
    assert "leg not measured" not in paper.estimation
