"""Sidecar telemetry.

docs/13 section 5 P0, from tools/skill_usage.py at hermes-agent 652f5d74 (MIT):
operational counters live beside the content, never inside it. Writing usage
stats into an authored file creates conflict pressure on content nobody wants
merged, and mixes what the agent did with what a human wrote.

Atomic write via tempfile + os.replace. Every bump is best-effort: a broken
sidecar never breaks the call it was counting.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SIDECAR_NAME = ".usage.json"


def _path(collection_dir: Path) -> Path:
    return Path(collection_dir) / SIDECAR_NAME


def load(collection_dir: Path) -> dict[str, Any]:
    try:
        return json.loads(_path(collection_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except OSError:
        log.debug("sidecar unreadable in %s", collection_dir, exc_info=True)
        return {}


def _atomic_write(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


#: Directories whose bump already failed once this process - warn once, not per call.
_warned: set[str] = set()
#: Failures since process start, per directory, surfaced through stats().
_failures: dict[str, int] = {}


def bump(collection_dir: Path, key: str, field: str = "retrieved") -> None:
    """Count a retrieval. Never raises - but a failure is WARNED and counted,
    because a counter that silently stops counting reads as "nothing was ever
    retrieved", which is a lie with consequences for pruning decisions."""
    try:
        data = load(collection_dir)
        rec = data.setdefault(key, {})
        rec[field] = int(rec.get(field, 0)) + 1
        rec["last_used_at"] = datetime.now(UTC).isoformat()
        _atomic_write(_path(collection_dir), data)
    except Exception:
        tag = str(collection_dir)
        _failures[tag] = _failures.get(tag, 0) + 1
        if tag not in _warned:
            _warned.add(tag)
            log.warning(
                "sidecar bump failed for %s/%s (suppressing further warnings "
                "for this directory; failure count in stats())",
                collection_dir,
                key,
                exc_info=True,
            )
        else:
            log.debug("sidecar bump failed for %s/%s", collection_dir, key, exc_info=True)


def stats(collection_dir: Path, key: str) -> dict[str, Any]:
    out = load(collection_dir).get(key, {})
    failed = _failures.get(str(collection_dir))
    if failed:
        out = {**out, "bump_failures_this_process": failed}
    return out
