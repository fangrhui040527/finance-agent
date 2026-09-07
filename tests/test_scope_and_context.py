"""Phase D: two defects that were live in every session.

`Intent.SCREEN` was a label with two per-INSTRUMENT agents behind it and no
instrument, so "find me stocks" ran a1 and a2 against nothing. And
`AgentContext.holdings`/`.watchlist` were never populated by either live
surface, so the news escalation gate could not match an article to anything
the user owned however carefully config.toml was filled in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.supervisor import PLAYBOOK, A0Supervisor, Intent

BASE = Path("config.toml").read_text(encoding="utf-8")


def _ctx():
    from datetime import UTC, datetime

    from agents.base import AgentContext
    from core.guardrails.defaults import default_engine
    from core.registry.loader import load as load_registry
    from knowledge.retrieval.pipeline import Router

    engine = default_engine(load_registry("agents/registry.yaml").allowlist())
    return AgentContext(router=Router({}), engine=engine, now=datetime.now(UTC))


def _cfg(tmp_path, holdings: str = "", watchlist: str = "") -> Path:
    s = BASE
    if holdings:
        s = re.sub(r"^holdings = .*$", f"holdings = {holdings}", s, count=1, flags=re.M)
    if watchlist:
        s = re.sub(r"^watchlist = .*$", f"watchlist = {watchlist}", s, count=1, flags=re.M)
    p = tmp_path / "config.toml"
    p.write_text(s, encoding="utf-8")
    return p


# --- the screen boundary ------------------------------------------------------------


def test_screen_routes_to_no_agents_at_all():
    """a1 and a2 are per-instrument. A screen has no instrument."""
    assert PLAYBOOK[Intent.SCREEN] == ()


@pytest.mark.parametrize(
    "question",
    [
        "find me stocks that look cheap",
        "screen for undervalued companies",
        "give me a list of banks worth buying",
    ],
)
def test_a_screen_is_refused_with_the_boundary_and_the_next_step(question):
    plan = A0Supervisor(_ctx()).plan(question, None, ())
    assert plan.refusal is not None
    assert "does not screen for stocks" in plan.refusal.reason
    assert "allocate" in plan.refusal.what_would_help
    assert plan.agents == ()


def test_the_refusal_survives_an_instrument_being_supplied():
    """Naming a ticker does not turn a screen into an analysis of that ticker."""
    plan = A0Supervisor(_ctx()).plan("find me stocks like MYX:1155", None, ("MYX:1155",))
    assert plan.refusal is not None
    assert "does not screen" in plan.refusal.reason


def test_the_cli_refuses_a_screen(capsys):
    import ask

    assert ask.main(["plan", "find me stocks that look cheap"]) == 2
    assert "does not screen for stocks" in capsys.readouterr().out


# --- holdings and watchlist reach the context ---------------------------------------


def test_the_cli_context_carries_the_book_and_the_watchlist(monkeypatch, tmp_path):
    import ask
    import core.config as C

    p = _cfg(tmp_path, holdings='["MYX:1155"]', watchlist='["XNAS:NVDA"]')
    real = C.load
    monkeypatch.setattr(ask, "load_config", lambda path=None: real(p))
    ctx = ask.context()
    assert ctx.holdings == {"MYX:1155"}
    assert ctx.watchlist == {"XNAS:NVDA"}


def test_the_mcp_context_carries_them_too(monkeypatch, tmp_path):
    import core.config as C
    from mcp_server import tools as T

    p = _cfg(tmp_path, holdings='[{ id = "MYX:1155", units = 100 }]', watchlist='["XNAS:NVDA"]')
    real = C.load
    monkeypatch.setattr(T, "load_config", lambda path=None: real(p))
    ctx = T.context()
    assert ctx.holdings == {"MYX:1155"}
    assert ctx.watchlist == {"XNAS:NVDA"}


def test_a_broken_config_does_not_take_out_every_command(monkeypatch, tmp_path):
    """A settings file that fails to parse is reported by the config commands.
    It must not make an unrelated question unanswerable."""
    from core.config import ConfigError
    from mcp_server import tools as T

    def boom(path=None):
        raise ConfigError("bad file")

    monkeypatch.setattr(T, "load_config", boom)
    ctx = T.context()
    assert ctx.holdings == set() and ctx.watchlist == set()


def test_escalation_can_now_fire_on_a_watched_name(monkeypatch, tmp_path):
    """The gate this was blocking: an article about a watched name must reach
    the model, and one about an unrelated name must not."""
    import core.config as C
    from knowledge.news.features import LexiconExtractor, should_escalate
    from mcp_server import tools as T

    p = _cfg(tmp_path, holdings='["MYX:1155"]', watchlist='["XNAS:NVDA"]')
    real = C.load
    monkeypatch.setattr(T, "load_config", lambda path=None: real(p))
    ctx = T.context()

    # A real extraction, not a stub with one attribute on it: the gate reads
    # more of the feature vector than relevance now, and a stub that answers
    # only the question the test happens to know about stops testing the gate.
    f = LexiconExtractor().extract("Nvidia profit rose sharply on data-centre demand", ["Nvidia"])

    assert should_escalate(f, ["XNAS:NVDA"], ctx.holdings, ctx.watchlist)
    assert should_escalate(f, ["MYX:1155"], ctx.holdings, ctx.watchlist)
    assert not should_escalate(f, ["MYX:9999"], ctx.holdings, ctx.watchlist)


def test_an_empty_config_escalates_nothing_rather_than_everything(monkeypatch, tmp_path):
    """An empty book must escalate NOTHING, not everything.

    Builds its own empty config rather than reading the shipped one. It used to
    rely on config.toml listing neither, which made it a test of a settings file
    that is meant to change - it broke the day a watchlist was filled in, which
    is the one day it should have kept passing.
    """
    import core.config as C
    from knowledge.news.features import LexiconExtractor, should_escalate
    from mcp_server import tools as T

    p = _cfg(tmp_path, holdings="[]", watchlist="[]")
    real = C.load
    monkeypatch.setattr(T, "load_config", lambda path=None: real(p))
    ctx = T.context()
    assert ctx.holdings == set() and ctx.watchlist == set()

    f = LexiconExtractor().extract("Maybank profit rose on wider margins", ["Maybank"])
    assert not should_escalate(f, ["MYX:1155"], ctx.holdings, ctx.watchlist)
