"""A key a vendor echoes back never reaches a store.

On 2026-09-28 Alpha Vantage's quota notice read "We have detected your API key
as ..." and the sweep stored it verbatim in two committed databases and a
digest. These pin the scrub at the two writers that keep free text."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from knowledge.corpus import FAILED, Corpus
from knowledge.facts import FactBook
from knowledge.redact import MASK, scrub, secret_values

# Synthetic, in Alpha Vantage's shape (16 uppercase alphanumerics). Not a key.
ECHOED = "SYNTHETICECHO000"
NOTICE = (
    "alphavantage quota exhausted for today: We have detected your API key as "
    f"{ECHOED} and our standard API rate limit is 25 requests per day."
)
NOW = datetime(2026, 9, 28, 22, 45, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)


def test_a_key_the_vendor_echoes_in_prose_is_masked_without_the_environment():
    out = scrub(NOTICE, environ={})
    assert ECHOED not in out
    assert f"API key as {MASK} and" in out
    assert out.startswith("alphavantage quota exhausted for today")


def test_a_key_set_in_the_environment_is_masked_in_any_sentence():
    env = {"ALPHAVANTAGE_API_KEY": ECHOED, "FINMIND_TOKEN": "synthetictoken01", "PATH": "/usr/bin"}
    out = scrub(f"refused {ECHOED}; also synthetictoken01 in the body", environ=env)
    assert ECHOED not in out and "synthetictoken01" not in out
    assert out == f"refused {MASK}; also {MASK} in the body"


def test_a_key_in_a_url_parameter_is_masked():
    out = scrub(
        "HTTP 402 for https://x.example/stable/earnings?symbol=NVDA&apikey=abcdef123456&l=1",
        environ={},
    )
    assert "abcdef123456" not in out and f"apikey={MASK}&l=1" in out


def test_ordinary_details_pass_through_unchanged():
    for text in (
        "alphavantage rejected the key (check ALPHAVANTAGE_API_KEY)",
        "skipped: EODHD_API_KEY not set",
        "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day.",
        "named the company: 140 of 148 (lowest: Microsoft 25/28, Apple 40/44, NVIDIA 75/76)",
        "",
    ):
        assert scrub(text, environ={}) == text


def test_short_or_unrelated_environment_values_are_not_treated_as_secrets():
    env = {"X_API_KEY": "abc", "HOME": "/root", "GROQ_API_KEY": "gsk_" + "S" * 20}
    assert secret_values(env) == ["gsk_" + "S" * 20]


def test_the_corpus_sweep_row_stores_the_detail_scrubbed(tmp_path):
    with Corpus(tmp_path / "corpus.db") as corpus:
        corpus.record_sweep("r1", "alphavantage_news", SINCE, FAILED, at=NOW, detail=NOTICE)
    (detail,) = (
        sqlite3.connect(tmp_path / "corpus.db").execute("SELECT detail FROM sweeps").fetchone()
    )
    assert ECHOED not in detail and "quota exhausted for today" in detail


def test_the_fact_book_pull_row_stores_the_detail_scrubbed(tmp_path):
    with FactBook(tmp_path / "facts.db") as book:
        book.record_pull("r1", "alphavantage_news", "failed", at=NOW, detail=NOTICE)
    (detail,) = (
        sqlite3.connect(tmp_path / "facts.db").execute("SELECT detail FROM pulls").fetchone()
    )
    assert ECHOED not in detail and "quota exhausted for today" in detail
