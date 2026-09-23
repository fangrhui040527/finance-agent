"""P6b: the static shell - offline, local-only, display-data-safe."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.app import create_app

STATIC = Path("web/static")
SCREENS = [
    "main",
    "why",
    "prices",
    "sizing",
    "portfolio",
    "thesis",
    "predictions",
    "paper",
    "trace",
    "learn",
    "world",
    "agents",
    "settings",
]


def test_every_routed_screen_module_exists():
    registry = (STATIC / "screens" / "registry.js").read_text(encoding="utf-8")
    for name in SCREENS:
        assert f"/screens/{name}.js" in registry, f"{name} missing from the registry"
        assert (STATIC / "screens" / f"{name}.js").exists(), f"{name}.js missing"


def test_index_references_only_local_assets():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for scheme in ("http://", "https://"):
        assert scheme not in html, "the shell must load with the network cable pulled"


def test_no_screen_builds_markup_from_display_data():
    """Tool and model text is untrusted display data. textContent only; the one
    innerHTML in the codebase is the icon table, whose strings are ours."""
    offenders = []
    for js in STATIC.rglob("*.js"):
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            if "innerHTML" not in line:
                continue
            if line.strip().startswith(("*", "//", "/*")):
                continue  # prose about the rule, not use of it
            offenders.append(f"{js.as_posix()}:{i}:{line.strip()[:60]}")
    assert len(offenders) == 1, offenders
    assert "app.js" in offenders[0] and "ICONS" in offenders[0], offenders


def test_tokens_css_stays_in_sync_with_the_design_source():
    from design.export_css import exported_css

    generated = (STATIC / "tokens.css").read_text(encoding="utf-8")
    assert generated == exported_css(), "run `python -m design.export_css`"
    assert "fonts.googleapis.com" not in generated  # offline-first


def test_the_app_serves_shell_and_assets():
    client = TestClient(create_app())
    index = client.get("/")
    assert index.status_code == 200
    assert "FinPlanet" in index.text
    for asset in ("/tokens.css", "/app.css", "/app.js", "/components.js", "/screens/registry.js"):
        resp = client.get(asset)
        assert resp.status_code == 200, asset


@pytest.mark.parametrize("name", SCREENS)
def test_each_screen_fetches_through_the_shared_helper(name):
    """Screens go through api()/renderEnvelope - no raw fetch() of their own,
    so the refusal card and the POST header cannot be skipped by one screen."""
    text = (STATIC / "screens" / f"{name}.js").read_text(encoding="utf-8")
    assert "fetch(" not in text, f"{name}.js bypasses the shared api() helper"


# --- the screens must actually parse -------------------------------------------


@pytest.mark.parametrize("name", SCREENS)
def test_every_screen_is_valid_javascript(name):
    """A syntax error in a screen module fails silently: the import rejects, the
    panel never renders, and the server keeps returning 200 for the file. The
    Portfolio screen shipped a literal newline inside a string this way."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the browser is the only other parser")
    src = (STATIC / "screens" / f"{name}.js").read_bytes()
    proc = subprocess.run(
        [node, "--input-type=module", "--check"],
        input=src,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()[:400]


# --- the capital panels reach the API they document ----------------------------


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("/capital", "how much may be invested at all"),
        ("/allocate", "splitting that across nominated names"),
        ("/rebalance", "what changes against the book"),
    ],
)
def test_the_portfolio_screen_calls_the_money_endpoints(path, why):
    js = (STATIC / "screens" / "portfolio.js").read_text(encoding="utf-8")
    assert f'api("{path}"' in js, f"the Portfolio screen does not ask {path} - {why}"


def test_the_portfolio_screen_states_that_it_does_not_choose_names():
    """The screen is where a user is about to ask it to pick something."""
    js = (STATIC / "screens" / "portfolio.js").read_text(encoding="utf-8")
    assert "does not choose the names" in js


def test_the_book_cannot_be_typed_into_the_rebalance_panel():
    """A book the user never stated is not their book: holdings come from
    config.toml, and the screen says so rather than offering a field."""
    js = (STATIC / "screens" / "portfolio.js").read_text(encoding="utf-8")
    assert "account.holdings in config.toml" in js
    assert "holdings" not in js.split("rebalance", 1)[1].split("api(")[0].replace(
        "account.holdings in config.toml", ""
    )
