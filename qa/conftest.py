"""Fixtures and markers for the two-phase QA suite.

The suite is separate from `tests/` on purpose. `tests/` is the product's own
suite: 1,073 tests that must pass with no network and no keys, and nothing here
is allowed to change that contract. `qa/` is the adapted testing strategy - phase
1 keyless, phase 2 live - and its phase-2 half deliberately does need a key.

Selecting a phase:

    python -m pytest qa/phase1                  # keyless, always runnable
    QA_LIVE=1 python -m pytest qa/phase2        # live, needs ANTHROPIC_API_KEY

Phase-2 tests skip rather than fail when the key is absent, so the whole suite
stays green in CI. `QA_LIVE=1` is a second, deliberate opt-in: a key present in
the environment for some other reason should not silently start spending money.

Scope: everything except `mcp_server/`. The MCP surface is out of scope for this
suite by decision; nothing here imports or drives it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa._support.cheap import CHEAP_MODEL, api_key, cheap_models  # noqa: E402
from qa._support.cost import Meter  # noqa: E402

ARTIFACTS = ROOT / "qa" / "artifacts"
PY = sys.executable
NOW = datetime.now(timezone.utc)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: sends a real request to the Anthropic API")
    config.addinivalue_line("markers", "network: needs the public internet, but no key")
    config.addinivalue_line("markers", "phase1: keyless structural validation")
    config.addinivalue_line("markers", "phase2: live-key integration")
    config.addinivalue_line("markers", "slow: takes more than a few seconds")


def pytest_collection_modifyitems(config, items):
    """Tag by directory, and skip the live half unless it was asked for."""
    live_enabled = os.environ.get("QA_LIVE", "").strip() not in ("", "0", "false", "no")
    have_key = bool(api_key())

    if live_enabled and have_key:
        skip = None
    elif not have_key:
        skip = pytest.mark.skip(reason="no ANTHROPIC_API_KEY: phase 2 needs a live key")
    else:
        skip = pytest.mark.skip(reason="set QA_LIVE=1 to opt in to live, billable calls")

    for item in items:
        path = str(item.fspath)
        if f"{os.sep}phase2{os.sep}" in path:
            item.add_marker(pytest.mark.phase2)
            item.add_marker(pytest.mark.live)
            if skip is not None:
                item.add_marker(skip)
        elif f"{os.sep}phase1{os.sep}" in path:
            item.add_marker(pytest.mark.phase1)


# -- cost and live transport ---------------------------------------------------

@pytest.fixture(scope="session")
def meter():
    """One cost meter for the whole live session, written out at the end."""
    m = Meter()
    yield m
    if m.calls:
        m.write(ARTIFACTS / "live-cost.json")


@pytest.fixture(scope="session")
def live(meter):
    """A metered live client pinned to the cheap model."""
    from qa._support.live import LiveClient

    key = api_key()
    if not key:
        pytest.skip("no ANTHROPIC_API_KEY")
    return LiveClient(key=key, model=CHEAP_MODEL, meter=meter)


@pytest.fixture
def cheap(monkeypatch):
    """Every Messages tier resolves to Haiku - model AND billing - via the
    product's own `FINPLANET_CHEAP` cap (promoted there from this suite's old
    model-table pin, which changed the model only and over-billed by design)."""
    monkeypatch.setenv("FINPLANET_CHEAP", "1")
    return CHEAP_MODEL


@pytest.fixture
def live_calls():
    """`LiveResult`s captured from calls that went through the PRODUCT."""
    return []


@pytest.fixture
def product_backend(meter, cheap, live_calls):
    """The product's own `AnthropicBackend`, on the real network, metered.

    The SDK backend's test seam is `client=`; `metered_client` wraps a real
    `anthropic.Anthropic` so every product-path request is budget-guarded
    before it is sent and priced after it returns. Small `max_tokens` on
    purpose: every phase-2 prompt asks for a word or two.
    """
    from core.llm.backends import AnthropicBackend
    from qa._support.live import metered_client

    key = api_key()
    if not key:
        pytest.skip("no ANTHROPIC_API_KEY")
    return AnthropicBackend(max_tokens=64,
                            client=metered_client(meter, live_calls, key=key))


# -- environments --------------------------------------------------------------

@pytest.fixture
def keyed_env(monkeypatch):
    """A syntactically plausible key that is never sent anywhere.

    Phase 1 constructs `AnthropicBackend` many times. It refuses to build without
    a key, and every one of those tests drives a fake opener, so the value here
    only has to exist.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-phase1-not-a-real-key")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    return "sk-ant-phase1-not-a-real-key"


@pytest.fixture
def no_key_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)


@pytest.fixture(scope="session")
def artifacts():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / ".gitignore").write_text("*\n", encoding="utf-8")
    return ARTIFACTS


# -- subprocess entry points ---------------------------------------------------

def scrubbed_env(tmp_path: Path, *, key: str | None = None) -> dict:
    """An environment for running the product as a real process.

    No key unless one is passed explicitly, so a keyless run genuinely gets the
    EchoBackend; UTF-8 forced so Windows consoles cannot fail a test on a box
    glyph; the trace directory pointed into the test's own tmp dir so nothing a
    QA run does lands in the repository's `debug/`.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("LLM_BACKEND", None)
    env.pop("FINPLANET_CHEAP", None)
    # The CLI loads `.env` itself now (core/env.py), so popping variables from
    # the parent is no longer enough to make a child keyless - the child would
    # read the operator's populated file and quietly stop being the scenario
    # under test. This is the loader's own documented opt-out.
    env["FINPLANET_NO_DOTENV"] = "1"
    if key:
        env["ANTHROPIC_API_KEY"] = key
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["FINPLANET_DEBUG_DIR"] = str(tmp_path / "debug")
    # And the same for config: a child process started in the repo root would
    # find an operator's `config.local.toml` and stop being the scenario under
    # test. Deleting variables cannot help here - the file is the input.
    env["FINPLANET_CONFIG"] = str(ROOT / "config.toml")
    return env


@pytest.fixture
def venue_only_env(tmp_path):
    """`env_extra` that selects NO broker, for cases whose subject is a VENUE.

    config.toml ships with `broker = "moomoo_my"` because that is the account
    this installation runs against. A case asserting Bursa's own schedule and
    Bursa's own 60 bps floor is asking a different question, and must pin a
    config that answers it.
    """
    shipped = (ROOT / "config.toml").read_text()
    kept = [ln for ln in shipped.splitlines() if not ln.startswith("broker =")]
    cfg = tmp_path / "venue_only.toml"
    cfg.write_text("\n".join(kept))
    return {"FINPLANET_CONFIG": str(cfg)}


@pytest.fixture
def run_cli(tmp_path):
    """`run_cli(["ask.py", "plan", ...])` -> CompletedProcess, keyless by default."""

    def run(args: list[str], *, key: str | None = None, timeout: int = 300,
            env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = scrubbed_env(tmp_path, key=key)
        env.update(env_extra or {})
        return subprocess.run(
            [PY, *args], cwd=str(ROOT), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )

    return run


# -- the product, in-process, on the REAL registry ----------------------------

@pytest.fixture(scope="session")
def registry():
    from core.registry.loader import load

    return load(ROOT / "agents" / "registry.yaml")


@pytest.fixture
def real_ctx(registry):
    """An `AgentContext` whose allowlist is derived from `agents/registry.yaml`.

    `verify.py` and the unit tests pass hand-written allowlists with invented
    agent ids; that is how a missing `llm_complete` grant survived every test.
    Nothing in `qa/` is allowed to do that.
    """
    from agents.base import AgentContext
    from core.guardrails.defaults import default_engine
    from knowledge.retrieval.pipeline import Router

    ownership = {aid: set(spec.knowledge) for aid, spec in registry.agents.items()}
    return AgentContext(router=Router(ownership), engine=default_engine(registry.allowlist()),
                        now=NOW)


# -- network -------------------------------------------------------------------

def reachable(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def stooq():
    """Reachable, AND actually serving CSV.

    On 2026-08-31 stooq.com answered every non-browser client - the product's
    User-Agent, a Chrome User-Agent and curl alike - with an HTML page reading
    "this site requires javascript to verify your browser". The product turned
    that into `PriceFeedError` rather than an empty series, which is the
    behaviour it promises; but it means the only wired price source is blocked
    upstream. That is reported as an expected failure with the reason attached,
    not as a broken test and not as a pass.
    """
    if not reachable("stooq.com"):
        pytest.skip("stooq.com unreachable from this machine")
    from core.market.feed import PriceFeedError, StooqFeed

    try:
        StooqFeed().fetch("XNAS:SPY")
    except PriceFeedError as e:
        if "javascript" in str(e).lower() or "<!doctype html" in str(e).lower():
            pytest.xfail("stooq.com serves a JavaScript browser-verification page to "
                         "non-browser clients; the price feed is blocked upstream")
        raise


@pytest.fixture(scope="session")
def gdelt():
    if not reachable("api.gdeltproject.org"):
        pytest.skip("api.gdeltproject.org unreachable from this machine "
                    "(443; set GDELT_DOC_API=http://... if only 80 is open)")


@pytest.fixture(scope="session")
def prices_online():
    """At least one source in the default chain answers with real bars.

    The chain exists because stooq.com walled itself off on 2026-08-31; a run
    where Yahoo is also unreachable has no price path to test, and that is an
    expected failure with both reasons attached, not a broken test.
    """
    from core.market.feed import PriceFeedError, default_feed

    feed = default_feed()
    try:
        feed.fetch("XNAS:SPY")
    except PriceFeedError as e:
        pytest.xfail(f"no price source reachable from this machine: {e}")
    return feed.source_used
