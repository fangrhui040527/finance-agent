"""One logging configuration, stderr only, opt-in verbosity.

Until this existed, exactly one module (`core/provenance/sidecar.py`) had a
logger and nothing configured it, so its `log.debug` went nowhere at default
level: a counter could stop counting forever with no observable signal.

Rules:

  * **stderr, never stdout.** The MCP server speaks JSON-RPC on stdout and the
    CLI prints results there; a log line on stdout is protocol corruption in
    one and noise in the other.
  * **`FINPLANET_LOG` picks the level** (DEBUG/INFO/WARNING/...); unset means
    WARNING, so a healthy run is silent and a swallowed failure is not.
  * **One line per record.** The trace (`debug/<run>/trace.jsonl`) is the rich
    channel; this one is for a human tailing a terminal.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_STREAMS_UTF8 = False


def use_utf8_streams() -> bool:
    """Make stdout/stderr UTF-8, whatever the console codepage says.

    Windows consoles default to cp1252, and this system prints text it does
    not control: model prose, news headlines in 100+ languages, instrument
    names. On cp1252 an em dash arrives as a replacement character - the
    output is quietly wrong rather than loudly broken, which is the failure
    mode this repository exists to avoid. It also matters for correctness,
    not just looks: the MCP server writes JSON-RPC to stdout, and JSON is
    UTF-8 by specification.

    `errors="replace"` on the way out, because a mangled character must never
    take down a command that had a real answer to give.
    """
    global _STREAMS_UTF8
    if _STREAMS_UTF8:
        return True
    changed = False
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # pytest's capture objects, pipes wrapped by a harness
        try:
            reconfigure(encoding="utf-8", errors="replace")
            changed = True
        except (ValueError, OSError):
            pass  # a stream that refuses is not worth failing the command over
    _STREAMS_UTF8 = changed
    return changed


def configure(level: str | int | None = None, stream=None) -> None:
    """Idempotent. Call from every entrypoint; the first call wins."""
    global _CONFIGURED
    use_utf8_streams()
    if _CONFIGURED:
        return
    if level is None:
        level = os.environ.get("FINPLANET_LOG", "WARNING").upper()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        root.setLevel(level)
    except ValueError:
        root.setLevel(logging.WARNING)
        logging.getLogger(__name__).warning("FINPLANET_LOG=%r is not a level; using WARNING", level)
    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
