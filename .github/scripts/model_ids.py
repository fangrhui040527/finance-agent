"""The model ids in an OpenAI-style ``/models`` reply, one per line.

Used by ``free-backend-probe.yml``: the reply from each keyed provider's
``/models`` endpoint is piped in on stdin, and only the sorted ids come out.
Free lineups rotate, and when a default model in ``core/llm/providers.py``
retires the backend answers 404 with the variable to set; this list makes the
replacement a lookup rather than a guess.

Two reply shapes are read: ``{"data": [{"id": ...}]}`` (Groq, OpenRouter,
Mistral, NVIDIA) and ``{"models": [{"name": ...}]}`` (Gemini's OpenAI surface).
A reply with no ids prints a short, bounded note instead: the vendor's own
error message when there is one, never the raw body, so a refusal is readable
and nothing sensitive can reach the log.
"""

from __future__ import annotations

import json
import sys

EXCERPT = 200


def ids_in(text: str) -> list[str]:
    """Sorted, de-duplicated model ids; empty when the reply carries none."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    rows = data.get("data") or data.get("models") or []
    if not isinstance(rows, list):
        return []
    found: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            ident = row.get("id") or row.get("name")
            if ident:
                found.add(str(ident))
    return sorted(found)


def explain(text: str) -> str:
    """Why a reply had no ids, in one bounded line."""
    if not text.strip():
        return "(empty reply)"
    try:
        data = json.loads(text)
    except ValueError:
        return "(the reply is not JSON)"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return f"(refused: {str(error['message'])[:EXCERPT]})"
    if isinstance(error, str):
        return f"(refused: {error[:EXCERPT]})"
    return "(no model ids in the reply)"


def main() -> int:
    text = sys.stdin.read()
    ids = ids_in(text)
    if ids:
        sys.stdout.write("\n".join(ids) + "\n")
    else:
        sys.stdout.write(explain(text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
