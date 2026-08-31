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


def bump(collection_dir: Path, key: str, field: str = "retrieved") -> None:
    """Count a retrieval. Never raises."""
    try:
        data = load(collection_dir)
        rec = data.setdefault(key, {})
        rec[field] = int(rec.get(field, 0)) + 1
        rec["last_used_at"] = datetime.now(UTC).isoformat()
        _atomic_write(_path(collection_dir), data)
    except Exception:
        log.debug("sidecar bump failed for %s/%s", collection_dir, key, exc_info=True)


def stats(collection_dir: Path, key: str) -> dict[str, Any]:
    return load(collection_dir).get(key, {})
