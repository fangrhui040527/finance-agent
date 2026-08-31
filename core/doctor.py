"""Preflight: is this installation able to do what the user is about to ask?

Every check answers with a `CheckResult` naming its impact, so "n of m ready"
comes with WHICH capability is missing rather than a bare failure count. A
critical failure exits non-zero; a degraded-but-usable state does not.

Offline by default from CI (`--offline` skips the two network probes); the
probes never retry - one packet each is the whole point.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    impact: str
    critical: bool = False


def run_checks(offline: bool = False) -> list[CheckResult]:
    out: list[CheckResult] = []

    # -- config and bounds
    try:
        from core.config import load

        cfg = load()
        out.append(CheckResult("config", OK, f"loaded from {cfg.source}", "settings"))
    except Exception as e:
        out.append(CheckResult("config", FAIL, str(e), "everything", critical=True))
        return out  # nothing below is meaningful without config

    # -- registry and eval ratchet
    try:
        from core.registry.loader import load as load_registry

        reg = load_registry("agents/registry.yaml")
        out.append(
            CheckResult(
                "registry", OK, f"{len(reg.agents)} agents, {len(reg.knowledge)} stores", "routing"
            )
        )
    except Exception as e:
        out.append(CheckResult("registry", FAIL, str(e), "every agent", critical=True))

    # -- stores: writable, and still append-only
    for name, path, impact in (
        ("provenance", Path("data/provenance.db"), "cost accounting and budget checks"),
        ("learning", Path("data/learning.db"), "predictions and calibration"),
    ):
        if not path.exists():
            out.append(CheckResult(name, WARN, f"{path} absent (created on first write)", impact))
            continue
        try:
            conn = sqlite3.connect(path)
            try:
                tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                triggers = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
                ).fetchone()[0]
            finally:
                conn.close()
            if triggers == 0:
                out.append(
                    CheckResult(
                        name,
                        FAIL,
                        f"{path}: no append-only triggers present",
                        impact,
                        critical=True,
                    )
                )
            else:
                out.append(
                    CheckResult(name, OK, f"{len(tables)} tables, {triggers} triggers", impact)
                )
        except sqlite3.DatabaseError as e:
            out.append(CheckResult(name, FAIL, f"{path}: {e}", impact, critical=True))

    graph = Path("data/graph.db")
    out.append(
        CheckResult(
            "graph",
            OK if graph.exists() else WARN,
            str(graph) if graph.exists() else "data/graph.db absent - run `make graph`",
            "multi-hop exposure claims",
        )
    )

    # -- model backend
    from core.llm.backends import backend_from_env

    backend, reason = backend_from_env()
    kind = type(backend).__name__
    out.append(
        CheckResult(
            "backend",
            OK if kind != "EchoBackend" else WARN,
            reason,
            "narrative output (numbers are engine-computed either way)",
        )
    )
    cap = os.environ.get("FINPLANET_CHEAP", "")
    out.append(
        CheckResult(
            "spend-cap",
            OK if cap else WARN,
            "FINPLANET_CHEAP=1: every tier resolves to the cheapest model"
            if cap
            else "FINPLANET_CHEAP unset - live calls bill at each tier's own rate",
            "spend",
        )
    )

    # -- debug retention
    debug = Path(os.environ.get("FINPLANET_DEBUG_DIR", "debug"))
    if debug.is_dir():
        runs = [p for p in debug.iterdir() if p.is_dir()]
        size_mb = sum(f.stat().st_size for f in debug.rglob("*") if f.is_file()) / 1e6
        status = WARN if size_mb > 200 else OK
        out.append(
            CheckResult(
                "traces",
                status,
                f"{len(runs)} runs, {size_mb:.0f} MB (verbatim prompts; pruned on next run)",
                "disk and privacy",
            )
        )

    # -- network probes (one packet each, never retried)
    if offline:
        out.append(CheckResult("stooq", SKIP, "--offline", "price history"))
        out.append(CheckResult("gdelt", SKIP, "--offline", "news"))
    else:
        import urllib.error
        import urllib.request

        for name, url, impact in (
            ("stooq", "https://stooq.com/q/d/l/?s=spy.us&i=d", "price history"),
            (
                "gdelt",
                os.environ.get("GDELT_DOC_API", "https://api.gdeltproject.org/api/v2/doc/doc")
                + "?query=domainis:reuters.com&mode=artlist&format=json&maxrecords=1&timespan=15min",
                "news",
            ),
        ):
            req = urllib.request.Request(
                url, headers={"User-Agent": "finplanet-analyst-mind/0.1 (doctor)"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read(200)
                walled = body.lstrip()[:1] == b"<" and name == "stooq"
                out.append(
                    CheckResult(
                        name,
                        WARN if walled else OK,
                        "answers, but with an HTML browser check - the chain will use Yahoo"
                        if walled
                        else "reachable",
                        impact,
                    )
                )
            except (urllib.error.URLError, OSError) as e:
                out.append(CheckResult(name, WARN, f"unreachable: {e}", impact))

    return out


def render(results: list[CheckResult]) -> str:
    icon = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", SKIP: " skip "}
    lines = ["PREFLIGHT", "-" * 66]
    for r in results:
        lines.append(f"[{icon[r.status]}] {r.name:<12} {r.message}")
        if r.status in (WARN, FAIL):
            lines.append(f"           affects: {r.impact}")
    ready = sum(1 for r in results if r.status == OK)
    lines.append("-" * 66)
    lines.append(f"{ready}/{len(results)} ready")
    return "\n".join(lines)
