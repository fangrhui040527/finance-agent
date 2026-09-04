"""Shared test doubles. One definition each, instead of one per test file.

Two properties every fixture here upholds:

  * **Offline and keyless.** Nothing opens a socket. The `keyless_env` fixture
    below is autouse so a developer's populated `.env` can never turn a unit
    test into a billable call - the dev box holds a real key, CI does not, and
    a test that passes on only one of them is measuring the environment.
  * **Injectable, not patched.** Every network seam in the product takes an
    `opener` argument. The doubles here are plain callables handed to that
    seam; no monkeypatching of `urllib` internals, so a refactor that stops
    honouring the seam fails loudly rather than silently bypassing the fake.
"""

from __future__ import annotations

import io
import urllib.error
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --- environment --------------------------------------------------------------


@pytest.fixture(autouse=True)
def keyless_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sees a real key or a backend override unless it sets one."""
    from core.llm.providers import env_vars

    for name in (
        "ANTHROPIC_API_KEY",
        "LLM_BACKEND",
        "LLM_BACKEND_REASON",
        "LLM_BACKEND_BALANCED",
        "LLM_BACKEND_CHEAP",
        "FINPLANET_CHEAP",
        "FINPLANET_DEBUG_DIR",
        # Every free-provider key and override (docs/21): a GROQ_API_KEY in a
        # developer's shell would otherwise turn "no key -> echo" tests into
        # "no key -> groq" on that one machine.
        *env_vars(),
    ):
        monkeypatch.delenv(name, raising=False)
    # Belt and braces: entrypoints load .env now, so deleting the variables is
    # not enough - a main() called inside a test would put them straight back.
    monkeypatch.setenv("FINPLANET_NO_DOTENV", "1")


@pytest.fixture(autouse=True)
def shipped_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test reads the config.toml in this repository, never the operator's.

    `config.local.toml` shadows `config.toml` by design - it is where a real
    financial position lives, and it is gitignored. That is right for running
    the product and wrong for testing it: with one present, four tests that
    assert behaviour "on the shipped config" read someone's actual holdings
    instead and fail. Green on a machine that has never been configured, red on
    the machine that actually uses the tool.

    Same argument as `keyless_env` one fixture above, applied to the other file
    a developer's box has and CI does not.
    """
    monkeypatch.setenv("FINPLANET_CONFIG", str(ROOT / "config.toml"))


@pytest.fixture(scope="session", autouse=True)
def shipped_config_session():
    """The same pin, one scope up, because the fixture above cannot reach far enough.

    `shipped_config` is function-scoped, so a module- or session-scoped fixture
    is built BEFORE it applies and reads the operator's `config.local.toml`
    anyway. `test_graph_surfaces.db` is module-scoped and does exactly that: it
    builds a graph from the real book, while every build inside a test body
    builds from the shipped one, and the diff between them is reported as a
    change to the graph.

    That is the precise failure the fixture above was written to stop, arriving
    through a door it does not cover - and it only appears on a machine whose
    watchlist is filled in, which the product instructs the operator to do.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv("FINPLANET_CONFIG", str(ROOT / "config.toml"))
    yield
    mp.undo()


# --- urllib doubles -----------------------------------------------------------


class FakeResponse:
    """Stands in for the object urllib.request.urlopen returns."""

    def __init__(
        self, body: str | bytes, headers: dict[str, str] | None = None, status: int = 200
    ) -> None:
        self._body = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._body if n is None or n < 0 else self._body[:n]

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def http_error(
    status: int,
    body: str = '{"error":{"message":"nope"}}',
    headers: dict[str, str] | None = None,
    url: str = "https://example.invalid/",
) -> urllib.error.HTTPError:
    """A ready-to-raise HTTPError with a readable body and optional headers."""
    return urllib.error.HTTPError(
        url, status, f"http {status}", headers or {}, io.BytesIO(body.encode())
    )


def opener_for(body: str, capture: list | None = None) -> Callable:
    """An opener that returns the same body every time and records requests."""

    def open_(req, timeout=None):
        if capture is not None:
            capture.append(req)
        return FakeResponse(body)

    return open_


def scripted_opener(steps: Iterable[str | BaseException], capture: list | None = None) -> Callable:
    """An opener that plays `steps` in order: a str is returned as a body, an
    exception instance is raised. Exhausting the script is an error - a test
    that makes more calls than it scripted has found a retry it did not expect."""
    queue = list(steps)

    def open_(req, timeout=None):
        if capture is not None:
            capture.append(req)
        if not queue:
            raise AssertionError("scripted opener exhausted: more requests than steps")
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return FakeResponse(step)

    return open_


# --- stores and engines -------------------------------------------------------


@pytest.fixture
def tmp_ledger(tmp_path: Path):
    from core.provenance.ledger import ProvenanceLedger

    return ProvenanceLedger(tmp_path / "provenance.db")


@pytest.fixture
def tmp_learning_store(tmp_path: Path):
    from agents.learning.store import LearningStore

    return LearningStore(tmp_path / "learning.db")


@pytest.fixture(scope="session")
def registry():
    from core.registry.loader import load

    return load(str(ROOT / "agents" / "registry.yaml"))


@pytest.fixture
def registry_engine(registry):
    """The REAL registry-derived allowlist, not a hand-written one - the only
    kind that catches a missing grant in agents/registry.yaml."""
    from core.guardrails.defaults import default_engine

    return default_engine(registry.allowlist())


# --- the paper book ------------------------------------------------------------------
#
# A deterministic price world for the nine names and both proxies, weekdays
# only, so the paper book's arithmetic can be asserted to the cent without the
# cache (whose contents change every day the collector runs).

PAPER_PRICES = {
    "MYX:1155": 10.50,
    "MYX:5347": 13.60,
    "MYX:5183": 4.16,
    "MYX:5225": 7.98,
    "MYX:8869": 7.96,
    "MYX:3182": 2.03,
    "XNAS:NVDA": 224.0,
    "XNAS:AAPL": 325.0,
    "XNAS:MSFT": 497.0,
    "MYX:0820EA": 1.82,
    "XNAS:SPY": 765.0,
}


class SyntheticFeed:
    """Bars with a tiny drift and a deterministic wobble; `shocks` adds a
    return on one (name, day). `fetch` follows core.market.feed.PriceFeed."""

    name = "synthetic"
    source_used = "synthetic"

    def __init__(self, first, last, prices=None, drift=0.0002, wobble=0.004, shocks=None):
        import math
        from datetime import timedelta

        from core.market.prices import Bar

        self.shocks = dict(shocks or {})
        self.series: dict = {}
        days = []
        d = first
        while d <= last:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        for iid, p0 in (prices or PAPER_PRICES).items():
            close = float(p0)
            k = sum(map(ord, iid))
            bars = []
            for i, day in enumerate(days):
                r = drift + wobble * math.sin(i * 0.37 + k) + self.shocks.get((iid, day), 0.0)
                open_ = close * (1 + r * 0.3)
                close = close * (1 + r)
                bars.append(
                    Bar(
                        day, open_, max(open_, close) * 1.002, min(open_, close) * 0.998, close, 1e6
                    )
                )
            self.series[iid] = bars

    def fetch(self, instrument_id, start=None, end=None):
        from core.market.feed import PriceFeedError
        from core.market.prices import PriceSeries

        bars = self.series.get(instrument_id)
        if bars is None:
            raise PriceFeedError(f"{instrument_id}: not in the synthetic world")
        sel = [
            b for b in bars if (start is None or b.day >= start) and (end is None or b.day <= end)
        ]
        if not sel:
            raise PriceFeedError(f"{instrument_id}: no bars in the window")
        return PriceSeries(instrument_id, sel)


@pytest.fixture
def paper_env(tmp_path: Path):
    """A config with the paper start pinned to a Monday, a synthetic feed, a
    config-fallback FX rate of 4.0, a fresh ledger and a fresh prediction log."""
    from dataclasses import replace
    from datetime import date, timedelta
    from decimal import Decimal
    from types import SimpleNamespace

    from agents.learning.store import LearningStore
    from core.config import load
    from engines.paper.fx import UsdMyr
    from engines.paper.store import PaperStore

    start = date(2026, 3, 2)
    cfg = load(ROOT / "config.toml")
    cfg = replace(
        cfg,
        paper=replace(cfg.paper, start_date=start, database=str(tmp_path / "paper.db")),
        database=str(tmp_path / "learning.db"),
    )
    store = PaperStore(tmp_path / "paper.db")
    store.init_books(cfg.paper, start)
    learning = LearningStore(tmp_path / "learning.db")
    env = SimpleNamespace(
        cfg=cfg,
        feed=SyntheticFeed(date(2025, 9, 1), date(2026, 7, 31)),
        fx=UsdMyr(None, Decimal("4.0"), Decimal("0.005")),
        store=store,
        learning=learning,
        start=start,
        tmp=tmp_path,
        week=lambda n, weekday=0: start + timedelta(days=7 * (n - 1) + weekday),
    )
    yield env
    store.close()
    learning.close()
