"""One pasted secret, six keys: the workflow-side parser must find each by
label or by shape, guess nothing, and print no value where a log would see it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "keys_from_blob.py"
spec = importlib.util.spec_from_file_location("keys_from_blob", SCRIPT)
assert spec is not None and spec.loader is not None
blob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blob)

# Synthetic keys in each provider's shape. None is real.
FINNHUB = "c1abcdef01qtj63p8580c1abcdef01qtj63p858g"  # 40 lowercase alphanumerics
FMP = "aBy2D8zf9ueVQK2MMsSwkrT3fLuREBk4"  # 32 mixed-case alphanumerics
AV = "5J9AHRK0RZFZGEBF"  # 16 uppercase alphanumerics
FRED = "db0264da0829925fab2d5357f6a20670"  # 32 lowercase hex
GROQ = "gsk_" + "Ylw8h9R3PvrwxYuotQm5WGdyb3FYbgpikCOctB3Nlts4QHyNt5Ox"  # gsk_ + 52
EODHD = "1a2b3c4d5e6f7a.12345678"  # hex, a dot, a short hex suffix


def test_a_dotenv_layout_is_read_by_label():
    text = (
        f"FINNHUB_API_KEY={FINNHUB}\nFMP_API_KEY={FMP}\nALPHAVANTAGE_API_KEY={AV}\n"
        f"FRED_API_KEY={FRED}\nGROQ_API_KEY={GROQ}\n"
    )
    assert blob.recognise(text) == {
        "FINNHUB_API_KEY": FINNHUB,
        "FMP_API_KEY": FMP,
        "ALPHAVANTAGE_API_KEY": AV,
        "FRED_API_KEY": FRED,
        "GROQ_API_KEY": GROQ,
    }


def test_a_chat_style_paste_with_misspelt_labels_is_read_by_label():
    text = (
        f"finhub api = {FINNHUB}\n\nFMP api= {FMP}\n\nALPHAVANTAGE api = {AV}\n\n"
        f"FREDAPI KEY = {FRED}\n\ngroq_api key = {GROQ}\n"
    )
    got = blob.recognise(text)
    assert got["FINNHUB_API_KEY"] == FINNHUB and got["FMP_API_KEY"] == FMP
    assert got["ALPHAVANTAGE_API_KEY"] == AV and got["FRED_API_KEY"] == FRED
    assert got["GROQ_API_KEY"] == GROQ


def test_bare_tokens_with_no_labels_are_read_by_shape():
    text = f"{GROQ} {FRED}\n{FMP}\n{FINNHUB}\n{AV}\n"
    assert blob.recognise(text) == {
        "FINNHUB_API_KEY": FINNHUB,
        "FMP_API_KEY": FMP,
        "ALPHAVANTAGE_API_KEY": AV,
        "FRED_API_KEY": FRED,
        "GROQ_API_KEY": GROQ,
    }


def test_a_label_wins_over_a_shape_and_the_rest_still_resolves():
    # A FRED key labelled as FMP is taken as FMP because the operator said so.
    text = f"fmp: {FRED}\n{GROQ}\n"
    got = blob.recognise(text)
    assert got["FMP_API_KEY"] == FRED and got["GROQ_API_KEY"] == GROQ
    assert "FRED_API_KEY" not in got


def test_an_ambiguous_shape_is_left_unassigned_rather_than_guessed():
    other = "ef0264da0829925fab2d5357f6a20671"  # a second 32-hex token
    got = blob.recognise(f"{FRED}\n{other}\n")
    assert "FRED_API_KEY" not in got and "FMP_API_KEY" not in got


def test_variable_names_in_the_blob_are_never_taken_as_values():
    got = blob.recognise("FINNHUB_API_KEY=\nGROQ_API_KEY=\n")
    assert got == {}


def test_main_prints_name_equals_value_lines_and_names_only_on_stderr(monkeypatch, capsys):
    monkeypatch.setenv("ALL_SECRET", f"groq {GROQ}\nfred {FRED}\n")
    assert blob.main(["keys_from_blob.py", "ALL_SECRET"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [f"FRED_API_KEY={FRED}", f"GROQ_API_KEY={GROQ}"]
    assert "recognised FRED_API_KEY, GROQ_API_KEY" in err
    assert "not found: FINNHUB_API_KEY, FMP_API_KEY, ALPHAVANTAGE_API_KEY" in err
    assert GROQ not in err and FRED not in err


def test_an_empty_blob_prints_nothing_and_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("ALL_SECRET", raising=False)
    assert blob.main(["keys_from_blob.py"]) == 0
    out, err = capsys.readouterr()
    assert out == "" and "empty or unset" in err


@pytest.mark.parametrize("name", ["FINNHUB_API_KEY", "FRED_API_KEY", "GROQ_API_KEY"])
def test_every_expected_name_is_known(name):
    assert name in blob.NAMES


# --- the dotted shape ---------------------------------------------------------
#
# EODHD was in both tables from the day the parser was written and could never
# fire: the token pattern had no dot in it, so a key of this shape was split
# into two fragments under the sixteen-character floor and nothing reached the
# rules. Every test token above is one the old pattern already matched, which is
# exactly why no test caught it.


def test_an_eodhd_key_is_recognised_by_its_shape_alone():
    assert blob.recognise(EODHD) == {"EODHD_API_KEY": EODHD}


def test_an_eodhd_key_is_recognised_by_its_label():
    assert blob.recognise(f"eodhd = {EODHD}") == {"EODHD_API_KEY": EODHD}
    assert blob.recognise(f"EOD Historical Data: {EODHD}") == {"EODHD_API_KEY": EODHD}


def test_a_hostname_beside_the_label_is_not_mistaken_for_a_key():
    """The reason the dotted alternative demands ten hex characters before the
    dot: an operator pasting a note with a link must not have the link read as
    their key."""
    assert blob.recognise("eodhd api key: see https://eodhd.com/cp/settings") == {}
    assert blob.recognise(f"finnhub.com login, key {FINNHUB}") == {"FINNHUB_API_KEY": FINNHUB}


def test_all_six_keys_survive_one_paste_together():
    text = (
        f"FINNHUB_API_KEY={FINNHUB}\nFMP_API_KEY={FMP}\nALPHAVANTAGE_API_KEY={AV}\n"
        f"FRED_API_KEY={FRED}\nGROQ_API_KEY={GROQ}\nEODHD_API_KEY={EODHD}\n"
    )
    assert blob.recognise(text) == {
        "FINNHUB_API_KEY": FINNHUB,
        "FMP_API_KEY": FMP,
        "ALPHAVANTAGE_API_KEY": AV,
        "FRED_API_KEY": FRED,
        "GROQ_API_KEY": GROQ,
        "EODHD_API_KEY": EODHD,
    }


def test_the_dotted_token_does_not_disturb_the_other_shapes():
    """Adding an alternative to the token pattern must not let a dotted run be
    claimed as one of the five undotted keys."""
    got = blob.recognise(f"{EODHD}\n{FRED}\n{FINNHUB}\n")
    assert got == {"EODHD_API_KEY": EODHD, "FRED_API_KEY": FRED, "FINNHUB_API_KEY": FINNHUB}
