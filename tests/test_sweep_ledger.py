"""The sweep ledger: a missing or refused key is never `ok`, and it turns the run red.

2026-10-27/28: the ALL_SECRET blob was overwritten, FINNHUB/FRED/ALPHAVANTAGE
keys vanished and FMP's key was refused with HTTP 401. Every collect run
concluded `success`, the no-key rows were written `ok`, and one of them
resolved alphavantage_news's open `sweep_silence` alert. These pin the four
parts of the fix: the statuses, the monitor, the expected-row check and the
workflow that reads the ledger after its commit. Offline throughout.
"""

from __future__ import annotations

import io
import urllib.error
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from knowledge import ledger
from knowledge.corpus import AUTH_FAILED, FAILED, OK, SKIPPED_NO_KEY, Corpus
from knowledge.facts import FactBook
from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.base import (
    Collector,
    KeyMissing,
    KeyRejected,
    PlanExcluded,
    Pull,
    SourceError,
)
from knowledge.sources.fred import FredCollector
from knowledge.sweep import run_sweep

NOW = datetime(2026, 10, 27, 22, 43, tzinfo=UTC)
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "collect.yml"


class Canned(Collector):
    key_env = None

    def __init__(self, name="fred", pull=None, raise_=None, **kw):
        self.name = name
        super().__init__(**kw)
        self._pull = pull or Pull()
        self._raise = raise_

    def collect(self, since, instruments=(), slot="all"):
        if self._raise:
            raise self._raise
        return self._pull


class Refused(Collector):
    """A collector whose vendor answers HTTP 401, through the real transport."""

    name = "fred"
    key_env = None

    def collect(self, since, instruments=(), slot="all"):
        self.get_json("https://vendor.invalid/series")
        return Pull()


def _refusing_opener(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))  # type: ignore[arg-type]


class Cfg:
    holdings: tuple = ()
    watchlist: tuple = ("XNAS:NVDA",)
    languages: tuple = ()

    def __init__(self, sources, corpus_db, facts_db):
        self.sources = sources
        self.corpus_db = corpus_db
        self.facts_db = facts_db


def _sweep(tmp_path, collector, slot="us_preopen", at=NOW, source="fred"):
    corpus_db, facts_db = str(tmp_path / "corpus.db"), str(tmp_path / "facts.db")
    return run_sweep(
        Cfg((source,), corpus_db, facts_db),
        slot,
        corpus_path=corpus_db,
        facts_path=facts_db,
        link_graph=False,
        collector_for=lambda name, **kw: collector,
        entity_index={},
        clock=lambda: at,
        log=lambda m: None,
        force=True,
    )


# --- 1. statuses ---------------------------------------------------------------------


def test_a_missing_key_is_skipped_no_key_and_moves_no_watermark(tmp_path):
    report = _sweep(
        tmp_path, Canned(raise_=KeyMissing("fred needs FRED_API_KEY, which is not set."))
    )
    (r,) = report.results
    assert r.status == SKIPPED_NO_KEY and "FRED_API_KEY" in r.line()
    assert report.exit_code == 0  # the sweep's own code is unchanged; the ledger judges keys
    with Corpus(str(tmp_path / "corpus.db")) as c:
        (row,) = c.sweeps()
        assert row["status"] == SKIPPED_NO_KEY != OK
        assert c.last_success("fred") is None
        assert not c.slot_runs(NOW - timedelta(days=1), NOW + timedelta(hours=1), successful=True)


def test_a_401_is_auth_failed_not_failed(tmp_path):
    report = _sweep(tmp_path, Refused(opener=_refusing_opener, sleep=lambda s: None))
    (r,) = report.results
    assert r.status == AUTH_FAILED and "the key was refused" in r.detail
    assert report.exit_code == 3  # it was FAILED before, and still exits 3
    with Corpus(str(tmp_path / "corpus.db")) as c:
        assert c.sweeps()[0]["status"] == AUTH_FAILED and c.last_success("fred") is None
        assert c.counts()["failed_sweeps"] == 1
    with FactBook(str(tmp_path / "facts.db")) as f:
        assert f.pulls()[0]["status"] == AUTH_FAILED
        # Counted as a failure in both stores, so the two never disagree.
        assert f.counts()["failed_pulls"] == 1


def test_a_refusal_a_collector_wrapped_is_still_auth_failed(tmp_path):
    """fmp on 2026-10-28: 'every endpoint failed. First: XNAS:NVDA _grades: fmp:
    the key was refused (HTTP 401)' - a plain SourceError, the type lost."""
    wrapped = SourceError(
        "every endpoint failed. First: XNAS:NVDA _grades: fmp: the key was refused (HTTP 401)"
    )
    (r,) = _sweep(tmp_path, Canned(raise_=wrapped)).results
    assert r.status == AUTH_FAILED


class _Reply:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fred_400(message: str):
    body = (
        '{"error_code":400,"error_message":"Bad Request.  The value for variable api_key '
        + message
        + '"}'
    ).encode()

    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))  # type: ignore[arg-type]

    return opener


@pytest.mark.parametrize(
    "message", ["is not registered.", "is not a 32 character alpha-numeric lower-case string."]
)
def test_fred_answers_a_bad_key_with_400_and_it_is_auth_failed(tmp_path, message):
    """FRED refuses a revoked or wrong key with HTTP 400, not 401."""
    fred = FredCollector(
        series={"DFF": "x"}, key="k" * 32, opener=_fred_400(message), sleep=lambda s: None
    )
    with pytest.raises(KeyRejected, match="the key was refused"):
        fred.get_json("https://api.stlouisfed.org/fred/series/observations")
    (r,) = _sweep(tmp_path, fred).results
    assert r.status == AUTH_FAILED and "k" * 32 not in r.detail


def test_fred_a_400_that_is_not_about_the_key_is_still_failed(tmp_path):
    def opener(req, timeout=None):
        body = io.BytesIO(b'{"error_message":"Bad Request.  Series does not exist."}')
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, body)  # type: ignore[arg-type]

    fred = FredCollector(series={"DFF": "x"}, key="k", opener=opener, sleep=lambda s: None)
    (r,) = _sweep(tmp_path, fred).results
    assert r.status == FAILED


def test_fred_a_key_error_in_a_200_payload_is_auth_failed(tmp_path):
    body = b'{"error_code":400,"error_message":"The value for variable api_key is not registered."}'
    fred = FredCollector(
        series={"DFF": "x"}, key="k", opener=lambda req, timeout=None: _Reply(body)
    )
    (r,) = _sweep(tmp_path, fred).results
    assert r.status == AUTH_FAILED and "not registered" in r.detail


@pytest.mark.parametrize(
    "payload",
    [
        b'{"Information": "The **demo** API key is for demo purposes only. '
        b'Please claim your free API key."}',
        b'{"Error Message": "the parameter apikey is invalid or missing."}',
    ],
)
def test_alphavantage_refuses_a_key_with_a_200_and_it_is_auth_failed(tmp_path, payload):
    av = AlphaVantageNews(key="k", opener=lambda req, timeout=None: _Reply(payload))
    (r,) = _sweep(tmp_path, av, slot="us_close", source="alphavantage_news").results
    assert r.status == AUTH_FAILED and "rejected the key" in r.detail


def test_alphavantage_a_spent_quota_is_still_failed(tmp_path):
    body = b'{"Information": "Our standard API rate limit is 25 requests per day."}'
    av = AlphaVantageNews(key="k", opener=lambda req, timeout=None: _Reply(body))
    (r,) = _sweep(tmp_path, av, slot="us_close", source="alphavantage_news").results
    assert r.status == FAILED


def test_an_ordinary_failure_and_a_plan_skip_are_unchanged(tmp_path):
    (r,) = _sweep(tmp_path / "a", Canned(raise_=SourceError("fred fetch failed: HTTP 500"))).results
    assert r.status == "failed"
    plan = PlanExcluded("fred: HTTP 403 - the plan does not include it")
    (r,) = _sweep(tmp_path / "b", Canned(raise_=plan)).results
    assert r.status == "skipped"
    with Corpus(str(tmp_path / "b" / "corpus.db")) as c:
        row = c.sweeps()[0]
        # A skip by design still reads as the dispatch it was, for sweep_silence.
        assert row["status"] == OK and row["detail"].startswith("skipped:")


# --- 2. the monitor ------------------------------------------------------------------


def _monitor_cfg(tmp_path, sources):
    from core.config import load as load_config

    real = load_config()
    return replace(
        real,
        corpus_db=str(tmp_path / "corpus.db"),
        facts_db=str(tmp_path / "no-facts.db"),
        paper=replace(real.paper, database=str(tmp_path / "no-paper.db")),
        alert_sweep_silence_hours=30,
        sources=sources,
    )


def _check(tmp_path, cfg, now):
    from core.monitor import check

    return check(
        cfg,
        db=str(tmp_path / "ledger.db"),
        alerts_db=str(tmp_path / "alerts.db"),
        debug_root=str(tmp_path / "no-debug"),
        now=now,
        feedback_root=str(tmp_path / "no-pages"),
        price_cache=str(tmp_path / "no-cache.db"),
    )


def test_a_no_key_run_does_not_close_a_silence_alert_and_opens_its_own(tmp_path):
    cfg = _monitor_cfg(tmp_path, ("alphavantage_news",))
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep("r0", "alphavantage_news", NOW, OK, at=NOW - timedelta(hours=309))
    first = _check(tmp_path, cfg, NOW - timedelta(minutes=1))
    assert "sweep_silence" in [a.rule for a in first.opened]

    # The 2026-10-27 22:43 row: the key had vanished.
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep(
            "r1",
            "alphavantage_news",
            NOW,
            SKIPPED_NO_KEY,
            at=NOW,
            slot="us_close",
            detail="skipped: alphavantage_news needs ALPHAVANTAGE_API_KEY, which is not set.",
        )
    second = _check(tmp_path, cfg, NOW + timedelta(minutes=1))
    assert "sweep_silence" not in second.resolved
    assert "sweep_silence" in [a.rule for a in second.still_open]
    keys = [a for a in second.opened if a.rule == "source_keys"]
    assert keys and "alphavantage_news (skipped_no_key)" in keys[0].title
    assert "ALPHAVANTAGE_API_KEY" in keys[0].detail


def test_the_key_alert_resolves_when_the_source_reads_again(tmp_path):
    cfg = _monitor_cfg(tmp_path, ("fmp",))
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep("r1", "fmp", NOW, AUTH_FAILED, at=NOW, detail="fmp: the key was refused")
    assert [a.rule for a in _check(tmp_path, cfg, NOW).opened if a.rule == "source_keys"]
    later = NOW + timedelta(hours=12)
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep("r2", "fmp", later, OK, at=later, fetched=3968)
    assert "source_keys" in _check(tmp_path, cfg, later).resolved


def test_a_skip_by_design_or_a_failure_does_not_resolve_the_key_alert(tmp_path):
    """eodhd: us_close's 401 opens the alert, then bursa_close raises PlanExcluded
    before any request ('ok', 'skipped: ...'). That is not the key coming back,
    and neither is a network failure; only a run that reads resolves it."""
    cfg = _monitor_cfg(tmp_path, ("eodhd",))
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep("r1", "eodhd", NOW, AUTH_FAILED, at=NOW, detail="eodhd: the key was refused")
    assert [a for a in _check(tmp_path, cfg, NOW).opened if a.rule == "source_keys"]
    later = NOW + timedelta(hours=11)
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep(
            "r2",
            "eodhd",
            later,
            OK,
            at=later,
            slot="bursa_close",
            detail="skipped: no name in this slot is on the free plan",
        )
    after_skip = _check(tmp_path, cfg, later)
    assert "source_keys" not in after_skip.resolved
    assert "source_keys" in [a.rule for a in after_skip.still_open]
    later += timedelta(hours=12)
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep(
            "r3", "eodhd", later, FAILED, at=later, detail="eodhd fetch failed: timed out"
        )
        verdict = c.key_verdict("eodhd")
        assert verdict is not None and verdict["status"] == AUTH_FAILED
    assert "source_keys" not in _check(tmp_path, cfg, later).resolved
    later += timedelta(hours=12)
    with Corpus(cfg.corpus_db) as c:
        c.record_sweep("r4", "eodhd", later, OK, at=later, fetched=2)
    assert "source_keys" in _check(tmp_path, cfg, later).resolved


# --- 3. expected rows ----------------------------------------------------------------


#: Five minutes after each slot's firing (core.monitor.SLOT_TIMES).
_RUN_AT = {"bursa_close": (9, 25), "us_preopen": (12, 35)}


def _firing(day: date, slot: str = "bursa_close") -> datetime:
    hour, minute = _RUN_AT[slot]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _bursa_days(last: date, n: int) -> list[date]:
    """`n` Bursa weekdays ending at `last`, newest first, skipping weekends."""
    out, day = [], last
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out


def _history(path, source, counts_newest_first, days, slot="bursa_close"):
    with Corpus(path) as c:
        for n, day in zip(counts_newest_first, days):
            at = _firing(day, slot)
            c.record_sweep(f"r{day}", source, at, OK, at=at, slot=slot, fetched=n)


def _every_day(slot, day):
    return True


def test_two_firings_running_under_a_fifth_of_the_median_is_a_shortfall(tmp_path):
    path = str(tmp_path / "corpus.db")
    days = _bursa_days(date(2026, 10, 27), 22)
    _history(path, "fmt_business", [0, 9] + [50] * 20, days)
    with Corpus(path) as c:
        (s,) = ledger.row_shortfalls(c, "bursa_close", ["fmt_business"], _every_day)
    assert s.recent == (0, 9) and s.median == 50 and s.history == 20
    assert "fmt_business in bursa_close" in s.line()


def test_one_thin_firing_is_not_a_shortfall(tmp_path):
    path = str(tmp_path / "corpus.db")
    _history(path, "fmt_business", [0, 41] + [50] * 20, _bursa_days(date(2026, 10, 27), 22))
    with Corpus(path) as c:
        assert not ledger.row_shortfalls(c, "bursa_close", ["fmt_business"], _every_day)


def test_a_sparse_or_new_source_is_not_judged(tmp_path):
    path = str(tmp_path / "corpus.db")
    days = _bursa_days(date(2026, 10, 27), 22)
    _history(path, "edgar", [0, 0] + [1] * 20, days)  # median 1: empty is ordinary
    _history(path, "nst_business", [0, 0, 150, 150], days)  # two firings of history
    with Corpus(path) as c:
        assert not ledger.row_shortfalls(c, "bursa_close", ["edgar", "nst_business"], _every_day)


def test_a_forced_rerun_inside_a_firing_does_not_count(tmp_path):
    """The firing counts its best run, the one that read the whole window; a re-run an
    hour later over an hour's window is not the source running dry."""
    path = str(tmp_path / "corpus.db")
    days = _bursa_days(date(2026, 10, 27), 22)
    _history(path, "google_news", [40] * 22, days)
    with Corpus(path) as c:
        for day in days[:2]:
            rerun = _firing(day) + timedelta(hours=5)
            c.record_sweep("x", "google_news", rerun, OK, at=rerun, slot="bursa_close", fetched=0)
        assert not ledger.row_shortfalls(c, "bursa_close", ["google_news"], _every_day)


def test_a_forced_rerun_that_reads_after_a_repair_clears_the_shortfall(tmp_path):
    """The firing counts its best run: a `force` dispatch after the adapter was
    fixed clears the shortfall now, not at the next scheduled firing."""
    path = str(tmp_path / "corpus.db")
    days = _bursa_days(date(2026, 10, 27), 22)
    _history(path, "fmt_business", [0, 0] + [50] * 20, days)
    with Corpus(path) as c:
        assert ledger.row_shortfalls(c, "bursa_close", ["fmt_business"], _every_day)
        rerun = _firing(days[0]) + timedelta(hours=3)
        c.record_sweep("y", "fmt_business", rerun, OK, at=rerun, slot="bursa_close", fetched=50)
        assert not ledger.row_shortfalls(c, "bursa_close", ["fmt_business"], _every_day)


def test_a_bursa_holiday_is_left_out_of_the_streak(tmp_path):
    """2026-11-09 is Deepavali on the XKLS calendar (markets/holidays.py). A thin
    firing that day and a thin one the next are not two in a row: the holiday
    is not a trading day, so the streak reaches back to the Friday before."""
    assert not ledger.market_session("bursa_close", date(2026, 11, 9))
    path = str(tmp_path / "corpus.db")
    days = _bursa_days(date(2026, 11, 10), 23)  # Tue 11-10, Mon 11-09 (holiday), Fri 11-06...
    assert days[1] == date(2026, 11, 9)
    _history(path, "fmt_business", [2, 0] + [50] * 21, days)
    with Corpus(path) as c:
        assert not ledger.row_shortfalls(c, "bursa_close", ["fmt_business"])
        # Judged on every day instead, the same rows are a shortfall.
        assert ledger.row_shortfalls(c, "bursa_close", ["fmt_business"], _every_day)


def test_the_monitor_raises_expected_rows(tmp_path):
    from core.monitor import evaluate

    cfg = _monitor_cfg(tmp_path, ("fmt_business",))
    _history(cfg.corpus_db, "fmt_business", [0, 1] + [50] * 20, _bursa_days(date(2026, 10, 27), 22))
    alerts = evaluate(
        cfg,
        db=str(tmp_path / "ledger.db"),
        debug_root=str(tmp_path / "no-debug"),
        now=_firing(date(2026, 10, 27)) + timedelta(hours=1),
        feedback_root=str(tmp_path / "no-pages"),
        price_cache=str(tmp_path / "no-cache.db"),
    )
    rows = [a for a in alerts if a.rule == "expected_rows"]
    assert rows and rows[0].evidence["shortfalls"] == ["fmt_business/bursa_close"]


# --- 4. the ledger and its verdict ---------------------------------------------------


def test_the_ledger_names_the_source_and_why_and_never_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FMP_API_KEY", "sk-live-0123456789abcdef")
    report = _sweep(
        tmp_path,
        Canned(
            raise_=SourceError("fmp: the key was refused (HTTP 401) for sk-live-0123456789abcdef")
        ),
    )
    path = tmp_path / "ledger.json"
    ledger.write(path, ledger.build(report))
    text = path.read_text(encoding="utf-8")
    assert "sk-live-0123456789abcdef" not in text and AUTH_FAILED in text

    assert ledger.main(["check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "fred: auth_failed" in out and "sk-live" not in out

    assert ledger.main(["summary", str(path)]) == 0
    table = capsys.readouterr().out
    assert "| fred | **auth_failed** |" in table and "fails after its commit" in table

    assert ledger.main(["text", str(path)]) == 0
    assert "fred auth_failed" in capsys.readouterr().out


def test_a_clean_run_passes_and_no_ledger_is_not_a_verdict(tmp_path, capsys):
    report = _sweep(tmp_path, Canned(pull=Pull()))
    path = tmp_path / "ledger.json"
    ledger.write(path, ledger.build(report))
    assert ledger.main(["check", str(path)]) == 0
    assert ledger.main(["check", str(tmp_path / "missing.json")]) == 0
    assert "did not reach its end" in ledger.summary_markdown(None)
    assert ledger.main(["bogus", str(path)]) == 2


def test_a_shortfall_in_the_ledger_fails_the_run(tmp_path):
    report = _sweep(tmp_path, Canned(pull=Pull()))
    short = ledger.RowShortfall("fmt_business", "bursa_close", (0, 1), 50.0, 20)
    found = ledger.problems(ledger.build(report, [short]))
    assert found == ["expected rows: " + short.line()]


def test_ask_sweep_writes_the_ledger_with_the_slot_check(tmp_path):
    import ask

    corpus_db = str(tmp_path / "corpus.db")
    _history(corpus_db, "fred", [0] + [480] * 20, _bursa_days(date(2026, 10, 27), 21), "us_preopen")
    report = _sweep(tmp_path, Canned(pull=Pull()), at=datetime(2026, 10, 28, 12, 35, tzinfo=UTC))
    path = tmp_path / "out" / "ledger.json"
    ask._write_sweep_ledger(report, str(path), corpus_db)
    written = ledger.read(path)
    assert written is not None
    assert [s["source"] for s in written["row_shortfalls"]] == ["fred"]


# --- 5. collect.yml ------------------------------------------------------------------


def _steps() -> list[dict]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["sweep"]["steps"]


def test_the_sweep_writes_the_ledger_and_shows_it_on_the_summary_page():
    sweep = next(s for s in _steps() if s.get("name") == "Sweep")
    run = sweep["run"]
    assert '--ledger "$RUNNER_TEMP/sweep-ledger.json"' in run
    # The exit code is read straight after the sweep, before anything else runs.
    assert run.index("ask.py sweep") < run.index('echo "code=$?"') < run.index("knowledge.ledger")
    assert (
        'knowledge.ledger summary "$RUNNER_TEMP/sweep-ledger.json" >> "$GITHUB_STEP_SUMMARY"' in run
    )


def test_the_ledger_fails_the_run_only_after_the_commit():
    steps = _steps()
    names = [s.get("name", "") for s in steps]
    verdict = names.index("Fail if a key is missing or refused, or a source ran short")
    assert names.index("Commit what was collected") < verdict
    step = steps[verdict]
    assert "knowledge.ledger check" in step["run"]
    assert "!cancelled()" in step["if"] and "steps.sweep.outputs.code" in step["if"]
    assert "continue-on-error" not in step


@pytest.mark.parametrize("which", ["start", "end"])
def test_the_dead_man_pings_are_optional_bounded_and_never_echo_the_url(which):
    steps = _steps()
    name = f"Dead-man switch, {which}"
    step = next(s for s in steps if s.get("name") == name)
    # First and last: the start before anything can hang, the end after every verdict.
    assert steps.index(step) == (0 if which == "start" else len(steps) - 1)
    assert step["env"]["HEALTHCHECKS_PING_URL"] == "${{ secrets.HEALTHCHECKS_PING_URL }}"
    run = step["run"]
    assert 'if [ -z "$HEALTHCHECKS_PING_URL" ]; then exit 0; fi' in run
    assert "curl -fsS -m 10 --retry 3" in run
    assert run.rstrip().endswith("exit 0")
    for line in run.splitlines():
        if "echo" in line:
            assert "HEALTHCHECKS_PING_URL" not in line, line
    if which == "start":
        assert '"${HEALTHCHECKS_PING_URL%/}/start"' in run
    else:
        assert step["if"] == "always()" and step["env"]["JOB_STATUS"] == "${{ job.status }}"
        assert 'suffix="/fail"' in run and '"$JOB_STATUS" = "success"' in run
        assert "knowledge.ledger text" in run and "--data-binary" in run
