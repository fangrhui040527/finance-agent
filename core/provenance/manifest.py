"""A methodology hash for one run.

The ledger already hashes each PROMPT; nothing hashed the METHOD - which
prompts, which registry, which tool surface, which library versions. Two runs
a month apart could disagree and nothing could say whether the world changed
or the system did. `RunManifest` answers that with one comparable hash.

Excluded on purpose: run_id, timestamps, and anything else that differs
between two runs of identical code - the hash must be equal exactly when the
methodology is equal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _package_versions() -> dict[str, str]:
    from importlib import metadata

    out: dict[str, str] = {}
    for pkg in ("pydantic", "pyyaml", "anthropic", "fastapi", "uvicorn"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = "absent"
    return out


@dataclass(frozen=True)
class RunManifest:
    """What the system WAS when it ran. Content-addressed, diffable."""

    system_prompt_hashes: dict[str, str]
    registry_hash: str
    tools_hash: str
    package_versions: dict[str, str] = field(default_factory=dict)

    @property
    def manifest_hash(self) -> str:
        payload = json.dumps(
            {
                "system_prompts": dict(sorted(self.system_prompt_hashes.items())),
                "registry": self.registry_hash,
                "tools": self.tools_hash,
                "packages": dict(sorted(self.package_versions.items())),
            },
            sort_keys=True,
        )
        return _sha(payload)

    def as_dict(self) -> dict:
        return {
            "manifest_hash": self.manifest_hash,
            "system_prompt_hashes": dict(sorted(self.system_prompt_hashes.items())),
            "registry_hash": self.registry_hash,
            "tools_hash": self.tools_hash,
            "package_versions": dict(sorted(self.package_versions.items())),
        }

    def diff(self, other: RunManifest) -> list[str]:
        """Human-readable list of what changed between two methodologies."""
        out: list[str] = []
        if self.registry_hash != other.registry_hash:
            out.append("registry changed")
        if self.tools_hash != other.tools_hash:
            out.append("tool surface changed")
        mine, theirs = self.system_prompt_hashes, other.system_prompt_hashes
        for agent in sorted(set(mine) | set(theirs)):
            a, b = mine.get(agent), theirs.get(agent)
            if a != b:
                what = "added" if a is None else "removed" if b is None else "changed"
                out.append(f"system prompt {what}: {agent}")
        for pkg in sorted(set(self.package_versions) | set(other.package_versions)):
            a = self.package_versions.get(pkg)
            b = other.package_versions.get(pkg)
            if a != b:
                out.append(f"package {pkg}: {a} -> {b}")
        return out


def current(registry_path: str = "agents/registry.yaml") -> RunManifest:
    """The manifest for the code as imported right now."""
    from agents.learning.reflection import A15Reflection
    from mcp_server.server import S

    prompts = {"a15_reflection": _sha(A15Reflection.SYSTEM)}
    registry_file = Path(registry_path)
    registry_hash = (
        _sha(registry_file.read_text(encoding="utf-8")) if registry_file.exists() else "absent"
    )
    tools_hash = _sha(json.dumps(sorted(S.tools), sort_keys=True))
    return RunManifest(
        system_prompt_hashes=prompts,
        registry_hash=registry_hash,
        tools_hash=tools_hash,
        package_versions=_package_versions(),
    )


def write(directory: Path, manifest: RunManifest) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path
