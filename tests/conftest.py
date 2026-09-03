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
    for name in ("ANTHROPIC_API_KEY", "LLM_BACKEND", "FINPLANET_CHEAP", "FINPLANET_DEBUG_DIR"):
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
