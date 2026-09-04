"""`ask.py paper`, end to end on a scratch ledger over the committed price cache."""

from __future__ import annotations

import json

import pytest

import ask


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # The cache is the book's only source here; its contents change daily, so
    # every assertion below is an invariant, never a value.
    monkeypatch.setenv("FINPLANET_OFFLINE", "1")


def test_the_round_trip(tmp_path, capsys):
    db, ldb = str(tmp_path / "p.db"), str(tmp_path / "l.db")
    common = ["--db", db, "--learning-db", ldb]

    assert ask.main(["paper", "status", *common]) == 2
    assert "NO BOOK" in capsys.readouterr().err

    assert ask.main(["paper", "init", *common, "--start", "2026-03-02"]) == 0
    assert "opened the paper book" in capsys.readouterr().out
    assert ask.main(["paper", "init", *common, "--start", "2026-03-02"]) == 2
    assert "no reset" in capsys.readouterr().err

    assert (
        ask.main(
            [
                "paper",
                "decide",
                *common,
                "--date",
                "2026-03-01",
                "--weights",
                "MYX:5183=0.11",
                "--thesis",
                "t",
            ]
        )
        == 2
    )
    assert "[start]" in capsys.readouterr().out

    assert ask.main(["paper", "mark", *common, "--date", "2026-03-02", "--slot", "us_close"]) == 0
    out = capsys.readouterr().out
    assert "decided  equity USD  1,000.00" in out and "control  equity USD  1,000.00" in out

    assert ask.main(["paper", "status", *common, "--date", "2026-03-02", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["phase"] == "observe" and d["equity_usd"] == 1000.0 and len(d["fundable"]) == 9
    fundable = [f["instrument_id"] for f in d["fundable"] if f["fundable"]]
    assert fundable, "nothing fundable at USD 1,000 - the cache or the caps changed"

    weights = ",".join(f"{iid}=0.11" for iid in fundable[:2])
    assert ask.main(
        [
            "paper",
            "decide",
            *common,
            "--date",
            "2026-03-16",
            "--weights",
            weights,
            "--thesis",
            "t",
            "--dry-run",
        ]
    ) in (0, 2)
    first = capsys.readouterr().out
    code = ask.main(
        ["paper", "decide", *common, "--date", "2026-03-16", "--weights", weights, "--thesis", "t"]
    )
    second = capsys.readouterr().out
    if code == 0:
        assert "recorded" in second and "prediction(s) logged" in second
        assert (
            ask.main(
                [
                    "paper",
                    "decide",
                    *common,
                    "--date",
                    "2026-03-16",
                    "--weights",
                    weights,
                    "--thesis",
                    "t",
                ]
            )
            == 2
        )
        assert "[duplicate]" in capsys.readouterr().out
    else:
        assert "REFUSED" in first and "REFUSED" in second

    for day in ("2026-03-17", "2026-03-18"):
        assert ask.main(["paper", "mark", *common, "--date", day, "--slot", "bursa_close"]) == 0
        capsys.readouterr()
    assert ask.main(["paper", "status", *common, "--date", "2026-03-18", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert (
        abs(d["equity_usd"] - d["cash_usd"] - sum(p["value_usd"] for p in d["positions"])) < 0.011
    )
    assert d["control_equity_usd"] is not None

    assert (
        ask.main(
            [
                "paper",
                "pack",
                *common,
                "--date",
                "2026-03-18",
                "--write",
                "--out",
                str(tmp_path / "pages"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert (
        "# Paper pack 2026-03-18" in out
        and "## Marks" in out
        and (tmp_path / "pages" / "2026-03-18.pack.md").exists()
    )

    assert ask.main(["paper", "grade", *common, "--date", "2026-03-18"]) == 0
    assert "nothing due" in capsys.readouterr().out


def test_bad_arguments_are_refused_with_exit_2(tmp_path, capsys):
    db = str(tmp_path / "p.db")
    assert ask.main(["paper", "init", "--db", db, "--start", "yesterday"]) == 2
    assert ask.main(["paper", "init", "--db", db, "--start", "2026-03-02"]) == 0
    capsys.readouterr()
    assert (
        ask.main(
            [
                "paper",
                "decide",
                "--db",
                db,
                "--date",
                "not-a-date",
                "--weights",
                "x",
                "--thesis",
                "t",
            ]
        )
        == 2
    )
    assert (
        ask.main(
            [
                "paper",
                "decide",
                "--db",
                db,
                "--date",
                "2026-03-16",
                "--weights",
                "MYX:5183",
                "--thesis",
                "t",
            ]
        )
        == 2
    )
    assert "look like" in capsys.readouterr().err
