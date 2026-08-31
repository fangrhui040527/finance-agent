"""`.env` is loaded, the real environment still wins, and tests stay keyless.

The defect this pins: backends.py refused with "Set it in .env", .env.example
documented the file, .gitignore protected it - and no code read it. An
operator who did the documented thing got the echo stub.
"""

from __future__ import annotations

import pytest

from core.env import load, parse


def test_parse_handles_comments_quotes_exports_and_junk():
    got = parse(
        "\n".join(
            [
                "# a comment",
                "",
                "ANTHROPIC_API_KEY=sk-ant-not-real",
                'QUOTED="with spaces"',
                "SINGLE='also quoted'",
                "export EXPORTED=yes",
                "not a variable line",
                "BAD-NAME=skipped",
            ]
        )
    )
    assert got["ANTHROPIC_API_KEY"] == "sk-ant-not-real"
    assert got["QUOTED"] == "with spaces"
    assert got["SINGLE"] == "also quoted"
    assert got["EXPORTED"] == "yes"
    assert "BAD-NAME" not in got


def test_load_sets_variables_from_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\nLLM_BACKEND=anthropic\n", encoding="utf-8")
    monkeypatch.delenv("FINPLANET_NO_DOTENV", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    applied = load(env)

    import os

    assert sorted(applied) == ["ANTHROPIC_API_KEY", "LLM_BACKEND"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-file"


def test_the_real_environment_outranks_the_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_BACKEND=anthropic\n", encoding="utf-8")
    monkeypatch.delenv("FINPLANET_NO_DOTENV", raising=False)
    monkeypatch.setenv("LLM_BACKEND", "echo")

    assert load(env) == []  # nothing applied

    import os

    assert os.environ["LLM_BACKEND"] == "echo"


def test_no_dotenv_skips_the_file_entirely(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-should-not-load\n", encoding="utf-8")
    monkeypatch.setenv("FINPLANET_NO_DOTENV", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert load(env) == []

    import os

    assert "ANTHROPIC_API_KEY" not in os.environ


def test_a_missing_or_unreadable_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("FINPLANET_NO_DOTENV", raising=False)
    assert load(tmp_path / "nope.env") == []
    assert load(tmp_path) == []  # a directory, not a file


def test_the_suite_itself_is_opted_out():
    """The autouse fixture must set this, or a developer's populated .env
    would reach every test that calls an entrypoint's main()."""
    import os

    assert os.environ.get("FINPLANET_NO_DOTENV") == "1"


def test_every_entrypoint_loads_it():
    from pathlib import Path

    for f in (
        "ask.py",
        "predict.py",
        "verify.py",
        "trace_run.py",
        "mcp_server/server.py",
        "web/serve.py",
        "web/app.py",
    ):
        text = Path(f).read_text(encoding="utf-8")
        assert "core.env import load" in text, f


def test_the_refusal_message_names_a_route_that_works(monkeypatch):
    from core.llm.backends import AnthropicBackend, AuthError

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError) as exc:
        AnthropicBackend()
    assert "every entrypoint loads that file" in str(exc.value)
