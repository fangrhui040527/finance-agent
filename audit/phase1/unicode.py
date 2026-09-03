"""U1-U3: multi-byte text, split at the worst possible byte.

The blueprint's first hazard: a UTF-8 character whose bytes land in two
different network chunks. A decoder constructed per chunk emits U+FFFD for the
half it cannot finish and then again for the orphan half - two replacement
characters where one emoji was, and no way to recover them downstream.

This system has three places where that can happen, and they are not the
browser:

  * **The MCP wire.** JSON-RPC frames go to stdout as text. If a frame is
    written with a non-UTF-8 codepage underneath it, the client receives
    mojibake or the write raises.
  * **The console.** Model prose, news headlines in any language, and
    instrument names are printed to a Windows console that defaults to cp1252.
  * **A streamed completion.** Text arrives as deltas; a delta boundary is a
    byte boundary the model did not choose.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from audit._support.scorecard import Check

ROOT = Path(__file__).resolve().parents[2]

#: One character from each hazard class the blueprint names, plus the one this
#: repository actually hit in production (the em dash on cp1252).
HAZARD = "MYR 4,705.88 — 马来西亚银行 🧪 naïve café ✓ ₹ ¥ 한국"


def u1_mcp_wire_is_ascii_safe() -> Check:
    """A JSON-RPC frame must survive a console that cannot spell it."""
    c = Check(
        "U1",
        "Usability",
        1,
        "MCP frames survive a non-UTF-8 console",
        "TextDecoder({stream:true}) equivalent: no character is corrupted in transit",
    )
    from mcp_server.protocol import _write

    buf = io.StringIO()
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"text": HAZARD}]}}
    _write(buf, payload)
    frame = buf.getvalue()

    # The frame must be pure ASCII on the wire - json.dumps escapes every
    # non-ASCII codepoint - so a cp1252 stdout cannot mangle what it cannot see.
    try:
        frame.encode("ascii")
    except UnicodeEncodeError as e:
        return c.failed(
            f"the frame carries raw non-ASCII ({e.reason}); a cp1252 console will "
            f"corrupt it or the write will raise"
        )
    back = json.loads(frame)
    if back["result"]["content"][0]["text"] != HAZARD:
        return c.failed("the round trip did not return the original text")
    if "\n" in frame.rstrip("\n"):
        return c.failed("a frame contains an embedded newline; the wire is newline-delimited")
    c.evidence = f"{len(frame)} ASCII bytes, round-trips to {len(HAZARD)} characters"
    return c.ok()


def u2_bytes_split_mid_character() -> Check:
    """The blueprint's byte-stream simulator, run for real.

    Two things have to hold, and they are different claims:

      * A decoder fed 3-byte chunks must reassemble a 4-byte emoji. The
        naive per-chunk decoder runs alongside to prove this payload CAN
        be corrupted - a check that cannot fail proves nothing.
      * The MCP wire must not carry the hazard at all. `json.dumps`
        escapes every non-ASCII codepoint, so the frame is ASCII and no
        chunk boundary can land inside a character. That is a stronger
        guarantee than decoding carefully, and worth stating as a reason
        rather than leaving it to look like luck.
    """
    c = Check(
        "U2",
        "Usability",
        1,
        "A split character survives, and the wire cannot split one",
        "TextDecoder({stream:true}) equivalent: no U+FFFD across a boundary",
    )
    import codecs

    raw = HAZARD.encode("utf-8")
    naive = "".join(raw[i : i + 3].decode("utf-8", errors="replace") for i in range(0, len(raw), 3))
    if chr(0xFFFD) not in naive:
        return c.failed(
            "a per-chunk decoder did not corrupt this payload, so the check "
            "cannot tell a correct decoder from a lucky one"
        )

    inc = codecs.getincrementaldecoder("utf-8")()
    assembled = "".join(inc.decode(raw[i : i + 3]) for i in range(0, len(raw), 3))
    assembled += inc.decode(b"", final=True)
    if assembled != HAZARD:
        return c.failed("incremental decoding did not reassemble the text")

    from mcp_server.protocol import _write

    buf = io.StringIO()
    _write(buf, {"jsonrpc": "2.0", "id": 7, "result": {"text": HAZARD}})
    frame = buf.getvalue()
    if any(ord(ch) > 127 for ch in frame):
        return c.failed(
            "the wire frame carries multi-byte characters, so a chunk boundary "
            "CAN land inside one and the decoder becomes load-bearing"
        )
    if json.loads(frame)["result"]["text"] != HAZARD:
        return c.failed("the escaped frame does not decode back to the original")
    c.evidence = (
        f"naive decode corrupts {naive.count(chr(0xFFFD))} characters, "
        f"incremental decode corrupts none, and the wire is pure ASCII"
    )
    return c.ok()


def u3_console_holds_utf8() -> Check:
    """The console the CLI prints to must not be the weakest link."""
    c = Check(
        "U3",
        "Portability",
        1,
        "The console is forced to UTF-8 whatever the codepage",
        "em dashes and CJK reach the user unmangled on a cp1252 Windows console",
    )
    script = (
        "import sys; from core.logging import use_utf8_streams; "
        "use_utf8_streams(); "
        f"sys.stdout.write({HAZARD!r})"
    )
    # PYTHONIOENCODING=cp1252 is the closest reproducible stand-in for a
    # Windows console codepage inside a piped subprocess.
    env = {**_env(), "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, cwd=ROOT, env=env)
    if proc.returncode != 0:
        return c.failed(f"printing hazard text under cp1252 raised: {proc.stderr.decode()[:160]}")
    got = proc.stdout.decode("utf-8", errors="replace")
    if got != HAZARD:
        lost = sum(1 for a, b in zip(got, HAZARD, strict=False) if a != b)
        return c.failed(
            f"cp1252 console mangled the output ({lost} characters differ); got {got[:40]!r}"
        )
    c.evidence = "cp1252 subprocess emitted the em dash, CJK, Hangul and the emoji intact"
    return c.ok()


def _env() -> dict:
    import os

    return {**os.environ, "FINPLANET_NO_DOTENV": "1", "PYTHONPATH": str(ROOT)}


CHECKS = (u1_mcp_wire_is_ascii_safe, u2_bytes_split_mid_character, u3_console_holds_utf8)
