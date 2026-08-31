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


def configure(level: str | int | None = None, stream=None) -> None:
    """Idempotent. Call from every entrypoint; the first call wins."""
    global _CONFIGURED
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
