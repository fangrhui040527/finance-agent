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
