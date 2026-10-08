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
    #: Which model each Messages tier resolves to, the effort, the pin and the
    #: backend - the selection, never a key. A different model writes a
    #: different thesis from the same data, so it is part of the method.
    model_selection: dict[str, str] = field(default_factory=dict)

    @property
    def manifest_hash(self) -> str:
        body = {
            "system_prompts": dict(sorted(self.system_prompt_hashes.items())),
            "registry": self.registry_hash,
            "tools": self.tools_hash,
            "packages": dict(sorted(self.package_versions.items())),
        }
        if self.model_selection:
            body["models"] = dict(sorted(self.model_selection.items()))
        return _sha(json.dumps(body, sort_keys=True))

    def as_dict(self) -> dict:
        return {
            "manifest_hash": self.manifest_hash,
            "system_prompt_hashes": dict(sorted(self.system_prompt_hashes.items())),
            "registry_hash": self.registry_hash,
            "tools_hash": self.tools_hash,
            "package_versions": dict(sorted(self.package_versions.items())),
            "model_selection": dict(sorted(self.model_selection.items())),
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
        for key in sorted(set(self.model_selection) | set(other.model_selection)):
            a, b = self.model_selection.get(key), other.model_selection.get(key)
            if a != b:
                out.append(f"model selection changed: {key} {a} -> {b}")
        for pkg in sorted(set(self.package_versions) | set(other.package_versions)):
            a = self.package_versions.get(pkg)
            b = other.package_versions.get(pkg)
            if a != b:
                out.append(f"package {pkg}: {a} -> {b}")
        return out


def current(registry_path: str = "agents/registry.yaml") -> RunManifest:
    """The manifest for the code as imported right now."""
    from agents.learning.reflection import A15Reflection
    from agents.synthesis.narrate import NARRATE_SYSTEM
    from mcp_server.server import S

    # Every system prompt handed to a model. Only the reflection prompt was
    # here, so editing the a10 narrative prompt left the hash unchanged and
    # run_anatomy told the operator a changed thesis "came from the DATA, not
    # the method".
    prompts = {
        "a15_reflection": _sha(A15Reflection.SYSTEM),
        "a10_narrate": _sha(NARRATE_SYSTEM),
    }
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
        model_selection=_model_selection(),
    )


def _model_selection() -> dict[str, str]:
    """The model each tier resolves to and what chose it. Names, never secrets."""
    import os

    from core.llm.tiers import (
        MESSAGES_TIERS,
        MODEL_IDS,
        ModelSelectionError,
        effective_tier,
        pin_source,
        selected_effort,
    )

    out: dict[str, str] = {}
    try:
        for tier in MESSAGES_TIERS:
            out[f"tier:{tier.value}"] = MODEL_IDS[effective_tier(tier)]
        effort = selected_effort()
        out["effort"] = effort.value if effort is not None else ""
        out["pin"] = pin_source() or ""
    except ModelSelectionError as e:
        out["error"] = str(e)
    for var in ("LLM_BACKEND", "LLM_MODEL"):
        out[var.lower()] = os.environ.get(var, "").strip()
    return out


def write(directory: Path, manifest: RunManifest) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path
