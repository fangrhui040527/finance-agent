"""Keep a key out of anything the stores write.

A vendor can echo the caller's key back in a refusal. Alpha Vantage's quota
notice did on 2026-09-28 ("We have detected your API key as ..."), and the
sweep stored that sentence word for word: in `data/corpus.db`, in
`data/facts.db` and in that day's digest, all three committed to a public
repository. `_redact` in `knowledge/sources/base.py` already blanked the key
in a URL; nothing blanked it in prose.

`scrub` is the one place free text is cleaned before a store keeps it. It
removes, in order: the value of every secret-named environment variable that
is set (so a key is caught whatever sentence it arrives in), a key passed as a
URL parameter, and a key-shaped token that follows the words "API key".
"""

from __future__ import annotations

import os
import re

#: Environment variables whose values are secrets, matched by suffix so a key
#: added later is covered without editing a list.
SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")

#: Shorter values are not treated as secrets: replacing a two-letter value
#: everywhere it occurs would mangle the text and protect nothing.
MIN_SECRET_CHARS = 8

MASK = "***"

_URL_PARAM = re.compile(r"(apikey|api_key|token)=[^&\s]+", re.IGNORECASE)

#: "API key as ABCD...", "api key: ABCD...", "apikey=ABCD...". The token must
#: be eight or more key characters, so "the API key ... 25 requests per day"
#: and "check ALPHAVANTAGE_API_KEY)" are left alone.
_KEY_PHRASE = re.compile(
    r"(\bapi[ _-]?key(?:\s+(?:is|as))?\s*[:=]?\s*)[A-Za-z0-9][A-Za-z0-9_.\-]{7,}",
    re.IGNORECASE,
)


def secret_values(environ: dict[str, str] | None = None) -> list[str]:
    """The secret values set in the environment, longest first, so a key that
    contains another is masked whole."""
    env = os.environ if environ is None else environ
    found = {
        value
        for name, value in env.items()
        if name.upper().endswith(SECRET_SUFFIXES) and len(value) >= MIN_SECRET_CHARS
    }
    return sorted(found, key=len, reverse=True)


def scrub(text: str, environ: dict[str, str] | None = None) -> str:
    """`text` with every key it carries replaced by `***`."""
    if not text:
        return text
    for value in secret_values(environ):
        text = text.replace(value, MASK)
    text = _URL_PARAM.sub(rf"\1={MASK}", text)
    return _KEY_PHRASE.sub(rf"\1{MASK}", text)


__all__ = ["MASK", "SECRET_SUFFIXES", "scrub", "secret_values"]
