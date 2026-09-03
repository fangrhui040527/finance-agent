"""Load `.env` into the environment, because the product told the operator to.

`core/llm/backends.py` refuses with "Set it in .env", `.env.example` documents
the file, and `.gitignore` protects it - and nothing read it. An operator who
did exactly the documented thing got the echo stub and a message telling them
to do what they had already done. The stub said so honestly, which is the only
reason this was a nuisance rather than a disaster; it is still a lie in the
instructions, so the instructions are now true.

Three rules:

  * **The real environment always wins.** A variable already set is never
    overwritten - an exported key, a CI secret, or a deliberate
    `LLM_BACKEND=echo` for one command outranks a file.
  * **`FINPLANET_NO_DOTENV=1` skips it entirely.** The test suite sets this,
    so a developer's populated `.env` can never turn a unit test into a
    billable call - the same guarantee `tests/conftest.py` already enforces
    by deleting the variables.
  * **A malformed line is skipped, not guessed at.** No shell expansion, no
    command substitution: this is a key-value file, not a script.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_NAME = ".env"


def parse(text: str) -> dict[str, str]:
    """`KEY=value` lines to a dict. Comments, blanks and junk are skipped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or not key.replace("_", "").isalnum():
            continue  # not an environment variable name; skip rather than guess
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load(path: str | Path | None = None, override: bool = False) -> list[str]:
    """Load the file into os.environ. Returns the NAMES it set (never values)."""
    if os.environ.get("FINPLANET_NO_DOTENV", "").strip() in ("1", "true", "yes"):
        return []
    p = Path(path) if path is not None else Path(DEFAULT_NAME)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []  # an unreadable .env is not worth failing a command over
    applied: list[str] = []
    for key, value in parse(text).items():
        if not override and os.environ.get(key):
            continue  # the real environment outranks the file
        os.environ[key] = value
        applied.append(key)
    return applied
