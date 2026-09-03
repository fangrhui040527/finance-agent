"""P1-P4: the transport, and the assumptions it lets you stop making.

The blueprint's Portability pillar is written for a browser talking SSE through
a reverse proxy: silent EventSource drops, `X-Accel-Buffering`, mobile network
switches. Three of its four items do not apply here, and saying so precisely is
worth more than pretending to test them - an architecture that later grows an
SSE endpoint has to revisit exactly these.

What this system does have is two transports with real portability hazards of
their own: a newline-delimited JSON-RPC pipe, and a CLI whose entrypoints must
behave the same on the Windows box this is developed on as on the Linux box CI
runs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from audit._support.scorecard import Check

ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict:
    import os

    return {**os.environ, "FINPLANET_NO_DOTENV": "1", "PYTHONPATH": str(ROOT)}


def p1_the_transport_is_not_sse() -> Check:
    """State the architecture, so the n/a items below are reasoned, not skipped."""
    c = Check(
        "P1",
        "Portability",
        1,
        "The web transport is request/response, not SSE",
        "establishes which streaming hazards apply at all",
    )
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "web").rglob("*.py"))
    streaming = [
        m for m in ("StreamingResponse", "EventSourceResponse", "text/event-stream") if m in src
    ]
    if streaming:
        return c.failed(
            f"the web layer streams ({', '.join(streaming)}), so the SSE items below "
            f"are live and this audit does not yet cover them"
        )
    c.evidence = "whole JSON responses only; no SSE endpoint exists to buffer or drop"
    return c.ok()


def p2_proxy_buffering() -> Check:
    c = Check(
        "P2",
        "Portability",
        1,
        "Reverse-proxy buffering is disabled for streamed responses",
        "X-Accel-Buffering: no, so a proxy cannot hold a stream back",
    )
    return c.not_applicable(
        "nothing streams: every response is a complete JSON body, which a proxy may "
        "buffer freely with no user-visible effect. This becomes live the moment a "
        "streaming endpoint is added"
    )


def p3_abort_cascade() -> Check:
    c = Check(
        "P3",
        "Reliability",
        1,
        "A cancelled request severs the upstream call",
        "AbortController cascade: a user hitting stop must not leave a call billing",
    )
    return c.not_applicable(
        "no client-side cancel exists to cascade: the web API answers in one shot and "
        "the CLI is a foreground process the user can interrupt directly. The upstream "
        "call is bounded instead by the backend's 120s timeout and its 3-attempt ceiling"
    )


def p4_the_wire_is_newline_delimited() -> Check:
    """One JSON object per line, and nothing else on that stream, ever."""
    c = Check(
        "P4",
        "Portability",
        1,
        "The MCP wire is one JSON object per line and nothing else",
        "a stray print on stdout corrupts the protocol for every client",
        blocking=False,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_server.server", "--selftest"],
        capture_output=True,
        cwd=ROOT,
        env=_env(),
    )
    if proc.returncode != 0:
        return c.failed(f"the selftest exited {proc.returncode}")
    if proc.stdout.strip():
        return c.failed(
            f"the selftest wrote {len(proc.stdout)} bytes to stdout; the human report "
            f"belongs on stderr because stdout is the protocol"
        )

    # And drive a real handshake to prove each frame is one parseable line.
    req = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "market_info", "arguments": {"market": "XKLS"}},
        },
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_server.server"],
        input=("\n".join(json.dumps(r) for r in req) + "\n").encode(),
        capture_output=True,
        cwd=ROOT,
        env=_env(),
    )
    raw = proc.stdout.decode("utf-8")
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    if len(lines) != len(req):
        return c.failed(f"{len(req)} requests produced {len(lines)} frames")
    trailing_cr = 0
    for ln in lines:
        body = ln.rstrip("\r")
        if "\r" in body:
            return c.failed(
                "a frame carries a carriage return INSIDE the JSON; that is "
                "corruption, not a line-ending convention"
            )
        if ln != body:
            trailing_cr += 1
        try:
            json.loads(ln)
        except json.JSONDecodeError as e:
            return c.failed(f"a frame is not parseable JSON: {e}")
    if trailing_cr:
        return c.failed(
            f"{trailing_cr} of {len(lines)} frames end CR-LF rather than LF. JSON treats "
            f"the trailing CR as whitespace so a newline-splitting client still parses it, "
            f"but the stdio convention is a bare LF and a client comparing raw bytes keeps "
            f"a stray CR on every frame. Windows text-mode stdout translates the newline "
            f"(mcp_server/protocol.py, _write)"
        )
    c.evidence = f"{len(lines)} frames, one bare-LF line each, no stray stdout output"
    return c.ok()


def p5_entrypoint_parity() -> Check:
    """The Windows batch file and the Makefile must offer the same system."""
    c = Check(
        "P5",
        "Portability",
        1,
        "run.bat and the Makefile expose the same commands",
        "transferability across execution environments, which here means both OSes",
    )
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    bat_raw = (ROOT / "run.bat").read_bytes()
    bat = bat_raw.decode("utf-8", errors="replace").lower()
    targets = {m for m in re.findall(r"^([a-z]+):", make, re.M)} - {"lint"}
    missing = sorted(t for t in targets if f'"{t}"' not in bat)
    if missing:
        return c.failed(f"run.bat is missing: {', '.join(missing)}")
    if b"\r\n" not in bat_raw:
        return c.failed("run.bat has LF endings; label jumps break on some Windows shells")
    if re.search(rb"[^\r]\n", bat_raw):
        return c.failed("run.bat mixes LF and CRLF endings")
    c.evidence = f"{len(targets)} make targets all reachable from run.bat, CRLF intact"
    return c.ok()


CHECKS = (
    p1_the_transport_is_not_sse,
    p2_proxy_buffering,
    p3_abort_cascade,
    p4_the_wire_is_newline_delimited,
    p5_entrypoint_parity,
)
