"""P5/P6: text features, event taxonomy, base rates, catalyst scoring."""

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from engines.attribution.decompose import Verdict, decompose
from engines.attribution.regression import huber_fit
from engines.events.catalyst import (
    attach,
    magnitude_plausibility,
    proximity,
    score_candidates,
    specificity,
)
from engines.events.taxonomy import (
    BaseRateTable,
    CapBand,
    Event,
    EventType,
    Observation,
    SurpriseBucket,
    bucket_surprise,
)
from knowledge.news.features import (
    LexiconExtractor,
    SourceReliability,
    near_duplicate_hash,
    should_escalate,
)

NOW = datetime(2026, 8, 12, tzinfo=UTC)
EX = LexiconExtractor()


# --- P5 features ---------------------------------------------------------
def test_polarity_separates_good_from_bad_news():
    good = EX.extract("Maybank profit rose and dividend approved", ["Maybank"])
    bad = EX.extract("Maybank posted a loss after an impairment writedown", ["Maybank"])
    assert good.polarity > 0 > bad.polarity


def test_intensity_and_uncertainty_separate_what_polarity_collapses():
    """The finding in docs/09 section 7: the two extra dimensions carry the signal."""
    probe = EX.extract(
        "Maybank may face a probe. The outcome is uncertain and could take time.", ["Maybank"]
    )
    plunge = EX.extract(
        "Maybank shares plunged sharply after the group cut FY27 guidance.", ["Maybank"]
    )
    assert probe.polarity < 0 and plunge.polarity < 0  # polarity says the same
    assert plunge.intensity > probe.intensity  # intensity does not
    assert probe.uncertainty > plunge.uncertainty


def test_forwardness_detects_guidance_language():
    fwd = EX.extract("Management expects FY27 revenue to grow and issued new guidance", ["X"])
    back = EX.extract("Revenue last year was 4.2 billion", ["X"])
    assert fwd.forwardness > back.forwardness


def test_relevance_needs_the_entity_to_actually_appear():
    assert EX.extract("Shipping rates rose in the north", ["Maybank"]).relevance == 0.0


def test_all_dimensions_stay_in_range():
    f = EX.extract("surge collapse crisis plunge record massive " * 20, ["X"])
    assert 0 <= f.relevance <= 1 and -1 <= f.polarity <= 1
    assert 0 <= f.intensity <= 1 and 0 <= f.uncertainty <= 1 and 0 <= f.forwardness <= 1


def test_escalation_gate_keeps_irrelevant_articles_off_the_api():
    f = EX.extract("Maybank Maybank Maybank profit rose", ["Maybank"])
    assert should_escalate(f, ["1155.KL"], holdings={"1155.KL"}, watchlist=set())
    assert not should_escalate(f, ["9999.KL"], holdings={"1155.KL"}, watchlist=set())


def test_low_relevance_never_escalates_even_for_a_holding():
    f = EX.extract("Shipping rates rose", ["Maybank"])
    assert not should_escalate(f, ["1155.KL"], holdings={"1155.KL"}, watchlist=set())


def test_wire_duplicates_collapse_to_one_hash():
    body = "The group cut guidance for the coming year amid weaker demand across segments"
    assert near_duplicate_hash(body) == near_duplicate_hash("(Reuters) " + body)
    assert near_duplicate_hash(body) != near_duplicate_hash("Unrelated text about palm oil")


def test_source_reliability_downweights_a_mismatching_domain():
    good, bad = SourceReliability(), SourceReliability()
    for _ in range(10):
        good.record(True)
    for i in range(10):
        bad.record(i < 3)
    assert good.score > 0.8 > bad.score


def test_unknown_domain_starts_neutral():
    assert SourceReliability().score == pytest.approx(0.5)


# --- P6 taxonomy ---------------------------------------------------------
def test_surprise_buckets_on_pre_announcement_consensus():
    assert bucket_surprise(1.20, 1.00) is SurpriseBucket.BIG_BEAT
    assert bucket_surprise(1.01, 1.00) is SurpriseBucket.INLINE
    assert bucket_surprise(0.80, 1.00) is SurpriseBucket.BIG_MISS
    assert bucket_surprise(1.0, None) is SurpriseBucket.NA


def test_effective_date_cannot_precede_announcement():
    with pytest.raises(ValueError, match="cannot precede"):
        Event("e", "X", EventType.INDEX_ADD, NOW, effective_at=NOW - timedelta(days=1))


def test_unconfirmed_event_is_not_citable():
    assert not Event("e", "X", EventType.MA_TARGET, NOW, confirmed=False).citable
    assert not Event("e", "X", EventType.MA_TARGET, NOW, source_doc_id=None).citable
    assert Event("e", "X", EventType.MA_TARGET, NOW, source_doc_id="ann-1").citable


def build_table(n_beats=60, n_div=40, seed=3) -> BaseRateTable:
    random.seed(seed)
    t = BaseRateTable()
    for i in range(n_beats):
        e = Event(
            f"h{i}",
            "X",
            EventType.EARNINGS_RESULT,
            NOW,
            market="XKLS",
            cap_band=CapBand.LARGE,
            surprise=SurpriseBucket.BIG_BEAT,
            source_doc_id="d",
        )
        t.observe(
            Observation(
                e, random.gauss(0.004, 0.01), random.gauss(0.029, 0.02), random.gauss(0.005, 0.03)
            )
        )
    for i in range(n_div):
        e = Event(
            f"d{i}",
            "X",
            EventType.DIVIDEND_CHANGE,
            NOW,
            market="XKLS",
            cap_band=CapBand.LARGE,
            source_doc_id="d",
        )
        t.observe(
            Observation(
                e, random.gauss(0, 0.003), random.gauss(0.002, 0.004), random.gauss(0, 0.01)
            )
        )
    return t


def test_base_rate_reports_n_and_iqr():
    br = build_table().lookup(
        EventType.EARNINGS_RESULT, "XKLS", CapBand.LARGE, SurpriseBucket.BIG_BEAT
    )
    assert br.n == 60 and br.iqr[0] < br.median_car < br.iqr[1]
    assert "n=60" in br.describe()


def test_thin_samples_are_labelled_as_such():
    t = build_table(n_beats=6)
    br = t.lookup(EventType.EARNINGS_RESULT, "XKLS", CapBand.LARGE, SurpriseBucket.BIG_BEAT)
    assert br.thin and "THIN SAMPLE" in br.describe()


def test_pre_drift_is_measured_separately_because_information_leaks():
    br = build_table().lookup(
        EventType.EARNINGS_RESULT, "XKLS", CapBand.LARGE, SurpriseBucket.BIG_BEAT
    )
    assert br.pre_drift_median > 0  # beats leak before the announcement


def test_lookup_falls_back_to_a_coarser_bucket():
    t = build_table()
    assert (
        t.lookup(EventType.EARNINGS_RESULT, "XKLS", CapBand.MICRO, SurpriseBucket.BIG_BEAT)
        is not None
    )


def test_too_few_observations_produce_no_base_rate():
    t = BaseRateTable()
    e = Event("x", "X", EventType.HALT, NOW, market="XKLS", source_doc_id="d")
    t.observe(Observation(e, 0.0, 0.01, 0.0))
    assert t.lookup(EventType.HALT, "XKLS", CapBand.MID) is None


# --- P6 scoring ----------------------------------------------------------
def fit():
    random.seed(11)
    rows = [[random.gauss(0, 0.01), random.gauss(0, 0.008)] for _ in range(250)]
    ys = [1.1 * a + 0.6 * b + random.gauss(0, 0.005) for a, b in rows]
    return huber_fit(rows, ys)


def big_idio_move():
    return decompose(
        "1155.KL", (date(2026, 8, 10), date(2026, 8, 12)), -0.002, 0.001, {}, 0.072, 0.0, fit()
    )


def market_move():
    return decompose(
        "1155.KL", (date(2026, 8, 1), date(2026, 8, 12)), -0.068, -0.015, {}, -0.094, 0.0, fit()
    )


def ev(eid, etype, **kw):
    kw.setdefault("source_doc_id", "ann-1")
    return Event(eid, "1155.KL", etype, NOW, market="XKLS", cap_band=CapBand.LARGE, **kw)


def test_no_candidates_are_scored_for_a_market_driven_move():
    """The rule that makes the whole engine honest."""
    m = market_move()
    cands = score_candidates(
        m,
        [ev("e1", EventType.EARNINGS_RESULT, surprise=SurpriseBucket.BIG_MISS)],
        build_table(),
        {"e1": 1},
        "XKLS",
    )
    assert cands == []
    m = attach(m, cands)
    assert m.verdict is Verdict.MARKET_DRIVEN


def test_unconfirmed_events_are_never_scored():
    cands = score_candidates(
        big_idio_move(),
        [Event("e9", "1155.KL", EventType.MA_TARGET, NOW, market="XKLS", confirmed=False)],
        build_table(),
        {"e9": 0},
        "XKLS",
    )
    assert cands == []


def test_all_six_factors_are_always_present():
    c = score_candidates(
        big_idio_move(),
        [ev("e1", EventType.EARNINGS_RESULT, surprise=SurpriseBucket.BIG_BEAT)],
        build_table(),
        {"e1": 1},
        "XKLS",
    )[0]
    assert set(c.breakdown.as_dict()) == {
        "prior",
        "proximity",
        "specificity",
        "direction",
        "magnitude",
        "source_trust",
    }


def test_a_routine_dividend_cannot_explain_a_seven_percent_move():
    """Magnitude plausibility doing its job."""
    cands = score_candidates(
        big_idio_move(),
        [
            ev("e1", EventType.EARNINGS_RESULT, surprise=SurpriseBucket.BIG_BEAT),
            ev("e2", EventType.DIVIDEND_CHANGE),
        ],
        build_table(),
        {"e1": 1, "e2": 1},
        "XKLS",
        source_kind={"e1": "exchange", "e2": "exchange"},
    )
    by = {c.cause_type: c for c in cands}
    assert by["earnings_result"].score > by["dividend_change"].score * 5
    assert by["dividend_change"].breakdown.magnitude < 0.2


def test_a_beat_does_not_explain_a_fall():
    down = decompose(
        "1155.KL", (date(2026, 8, 10), date(2026, 8, 12)), -0.002, 0.001, {}, -0.072, 0.0, fit()
    )
    c = score_candidates(
        down,
        [ev("e1", EventType.EARNINGS_RESULT, surprise=SurpriseBucket.BIG_BEAT)],
        build_table(),
        {"e1": 1},
        "XKLS",
    )[0]
    assert c.breakdown.direction == pytest.approx(0.2)


def test_insider_selling_is_not_a_signal_by_default():
    c = score_candidates(
        big_idio_move(), [ev("e1", EventType.INSIDER_SELL)], build_table(), {"e1": 1}, "XKLS"
    )[0]
    assert c.breakdown.prior <= 0.15


def test_proximity_decays_and_slower_markets_decay_slower():
    assert proximity(0, "XKLS") > proximity(5, "XKLS")
    assert proximity(3, "XKLS") > proximity(3, "XNAS")


def test_specificity_ranks_direct_over_peer_over_sector():
    direct = ev("a", EventType.CONTRACT_WIN)
    peer = Event("b", "9999.KL", EventType.CONTRACT_WIN, NOW, source_doc_id="d")
    assert specificity(direct, "1155.KL", {"9999.KL"}, False) == 1.0
    assert specificity(peer, "1155.KL", {"9999.KL"}, False) == 0.6
    assert specificity(peer, "1155.KL", set(), True) == 0.3


def test_no_catalyst_clearing_the_threshold_yields_the_no_news_verdict():
    m = big_idio_move()
    m = attach(
        m,
        score_candidates(
            m,
            [ev("e2", EventType.DIVIDEND_CHANGE)],
            build_table(),
            {"e2": 1},
            "XKLS",
            source_kind={"e2": "exchange"},
        ),
    )
    assert m.verdict is Verdict.NO_IDENTIFIED_CATALYST
    assert "REVERSE" in m.reason
    assert "1 candidate(s) weighed and none cleared 0.25" in m.reason
    assert "dividend_change" in m.reason


def test_an_empty_candidate_list_is_not_read_as_a_no_news_move():
    """Nothing offered is not the same finding as everything rejected. The
    reversal tendency belongs to moves whose news was looked at and found
    wanting; a name the collector held nothing about has not been looked at."""
    m = attach(big_idio_move(), [])
    assert m.verdict is Verdict.NO_IDENTIFIED_CATALYST
    assert "no candidate cause was offered to weigh" in m.reason
    assert "not a rejection" in m.reason
    assert "REVERSE" not in m.reason
    assert "sigma" in m.reason


def test_close_candidates_all_stay_shown():
    m = big_idio_move()
    cands = score_candidates(
        m,
        [ev("e1", EventType.REGULATORY_ACTION), ev("e2", EventType.LITIGATION)],
        build_table(),
        {"e1": 1, "e2": 1},
        "XKLS",
        source_kind={"e1": "exchange", "e2": "exchange"},
    )
    m = attach(m, cands)
    if len(cands) > 1 and cands[1].score >= cands[0].score * 0.8:
        assert "false precision" in m.reason


def test_magnitude_is_neutral_when_no_base_rate_exists():
    assert magnitude_plausibility(0.05, None) == 0.5


def test_candidates_are_ranked_by_score():
    cands = score_candidates(
        big_idio_move(),
        [
            ev("e1", EventType.EARNINGS_RESULT, surprise=SurpriseBucket.BIG_BEAT),
            ev("e2", EventType.DIVIDEND_CHANGE),
            ev("e3", EventType.INSIDER_SELL),
        ],
        build_table(),
        {"e1": 1, "e2": 1, "e3": 2},
        "XKLS",
    )
    assert [c.score for c in cands] == sorted((c.score for c in cands), reverse=True)
