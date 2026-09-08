"""The DBnomics probe's verdict logic, exercised without the network.

The probe can only ever run on a runner - the development environment has no
route to db.nomics.world - so its logic has to be right the first time it is
dispatched. What it decides is the whole point of it: RETIRED and FROZEN look
identical in a single series' response and have opposite fixes, and the
difference is read entirely from the siblings.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "dbnomics_probe.py"
spec = importlib.util.spec_from_file_location("dbnomics_probe", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


TODAY = datetime.now(UTC).date()


def _period(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).strftime("%Y-%m")


# --- period parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("2026-07-01", (2026, 7, 1)),
        ("2026-07", (2026, 7, 1)),
        ("2026", (2026, 1, 1)),
        ("2026-Q2", (2026, 4, 1)),
        ("2026-Q4", (2026, 10, 1)),
    ],
)
def test_every_period_shape_dbnomics_publishes_parses(raw, expect):
    day = probe.as_day(raw)
    assert day is not None, raw
    assert (day.year, day.month, day.day) == expect


def test_a_period_it_cannot_read_is_none_rather_than_a_wrong_date():
    """A guessed date here would turn an unreadable period into a confident
    age, which is the one output nobody could check."""
    assert probe.as_day("") is None
    assert probe.as_day("not-a-period") is None


def test_the_newest_observation_is_the_last_one():
    assert probe.newest_period({"period": ["2025-01", "2025-02", "2025-03"]}) == "2025-03"
    assert probe.newest_period({"period_start_day": ["2025-01-01"]}) == "2025-01-01"
    assert probe.newest_period({}) == ""


# --- the verdicts -----------------------------------------------------------


def _run(monkeypatch, capsys, *, ours: str, siblings_best: str, n_siblings: int = 12):
    """Drive main() with one configured series and a stubbed API.

    Reads real stdout rather than intercepting `print`: the script writes with
    `sys.stdout.write`, and a test that patches `print` passes while asserting
    against an empty string.
    """
    one = probe.SERIES[0]
    monkeypatch.setattr(probe, "SERIES", (one,))
    monkeypatch.setattr(probe, "fetch_one", lambda key: {"period": [ours]} if ours else None)
    monkeypatch.setattr(probe, "dataset_freshness", lambda p, d: (siblings_best, n_siblings))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert probe.main() == 0
    return capsys.readouterr().out


def test_a_fresh_series_reads_CURRENT(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, ours=_period(20), siblings_best=_period(20))
    assert "**CURRENT**" in out


def test_a_stale_series_whose_siblings_are_fresh_reads_RETIRED(monkeypatch, capsys):
    """Our code stopped and the dataset did not: the replacement exists."""
    out = _run(monkeypatch, capsys, ours="2025-06", siblings_best=_period(15))
    assert "**RETIRED**" in out
    assert "this code did not" in out


def test_a_stale_series_whose_siblings_are_equally_stale_reads_FROZEN(monkeypatch, capsys):
    """The code is right and the upstream stopped - editing SERIES fixes nothing."""
    out = _run(monkeypatch, capsys, ours="2025-06", siblings_best="2025-06")
    assert "**FROZEN**" in out
    assert "the dataset stopped" in out


def test_a_code_dbnomics_does_not_know_reads_NO_SERIES(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, ours="", siblings_best="2025-06")
    assert "**NO SERIES**" in out


def test_a_fetch_that_raises_is_reported_not_propagated(monkeypatch, capsys):
    """A probe that dies on series 3 tells you nothing about series 4 to 15."""
    one = probe.SERIES[0]
    monkeypatch.setattr(probe, "SERIES", (one,))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def boom(key):
        raise TimeoutError("upstream said no")

    monkeypatch.setattr(probe, "fetch_one", boom)
    assert probe.main() == 0
    assert "FETCH FAILED" in capsys.readouterr().out


def test_every_configured_series_appears_in_the_table(monkeypatch, capsys):
    """The probe reports on the real SERIES list, so a series added to the
    collector cannot be silently left unprobed."""
    monkeypatch.setattr(probe, "fetch_one", lambda key: {"period": ["2025-06"]})
    monkeypatch.setattr(probe, "dataset_freshness", lambda p, d: ("2025-06", 10))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    probe.main()
    out = capsys.readouterr().out
    for spec in probe.SERIES:
        assert f"`{spec.series_id}`" in out, spec.series_id


# --- the transport, which the stubs above deliberately skip ------------------
#
# A wrong response-shape assumption is the failure most likely to survive to
# the runner, because every test that stubs `fetch_one` steps over it.


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._p = payload

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(captured: list[str], payload: dict):
    import json as _json

    def opener(req, timeout=None):
        captured.append(req.full_url)
        return _Resp(_json.dumps(payload).encode())

    return opener


DOC = {
    "series": {
        "docs": [
            {
                "provider_code": "IMF",
                "dataset_code": "PCPS",
                "series_code": "M.W00.PPOIL.USD",
                "period": ["2025-05", "2025-06"],
                "value": [900.0, 910.0],
            }
        ]
    }
}


def test_fetch_one_reads_the_documented_response_shape(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(probe.urllib.request, "urlopen", _fake_urlopen(seen, DOC))
    doc = probe.fetch_one("IMF/PCPS/M.W00.PPOIL.USD")
    assert doc is not None
    assert probe.newest_period(doc) == "2025-06"


def test_the_request_asks_for_observations_and_names_the_series(monkeypatch):
    """`observations=1` is DBnomics' include-observations FLAG, not a count of
    one. Sending 0, or omitting it, returns metadata with no periods at all and
    every verdict becomes NO SERIES."""
    seen: list[str] = []
    monkeypatch.setattr(probe.urllib.request, "urlopen", _fake_urlopen(seen, DOC))
    probe.fetch_one("IMF/PCPS/M.W00.PPOIL.USD")
    assert "observations=1" in seen[0]
    assert "series_ids=IMF%2FPCPS%2FM.W00.PPOIL.USD" in seen[0]


def test_an_empty_docs_array_is_no_series_not_a_crash(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        probe.urllib.request, "urlopen", _fake_urlopen(seen, {"series": {"docs": []}})
    )
    assert probe.fetch_one("IMF/PCPS/NOPE") is None


def test_dataset_freshness_returns_the_newest_sibling_and_how_many_it_read(monkeypatch):
    payload = {
        "series": {
            "docs": [
                {"period": ["2025-01", "2025-06"]},
                {"period": ["2025-01", "2026-08"]},
                {"period": ["2024-01"]},
            ]
        }
    }
    seen: list[str] = []
    monkeypatch.setattr(probe.urllib.request, "urlopen", _fake_urlopen(seen, payload))
    best, n = probe.dataset_freshness("IMF", "PCPS")
    assert best == "2026-08"
    assert n == 3
    assert "provider_code=IMF" in seen[0] and "dataset_code=PCPS" in seen[0]


def test_the_report_is_pure_ascii_so_a_C_locale_runner_can_print_it():
    """`capsys` captures into an in-memory buffer, so every test above passes
    against a report that the real stdout cannot encode. This one caught a
    U+00B7 in the heading that raised UnicodeEncodeError on an ASCII stdout -
    which on a once-a-quarter manual probe means a wasted dispatch and no
    answer."""
    text = SCRIPT.read_text(encoding="utf-8")
    offenders = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if not ln.isascii()]
    assert not offenders, f"non-ascii in the probe: {offenders}"
    # and prove the encode itself, not just the source
    for spec in probe.SERIES:
        spec.series_id.encode("ascii")
        spec.key.encode("ascii")
