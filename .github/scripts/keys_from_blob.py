"""Recognise the collectors' API keys inside one pasted blob.

An operator who keeps every key in a single repository secret (one value,
five keys, any layout) has done something reasonable that the workflows did
not expect: they read `secrets.FINNHUB_API_KEY` and friends by name. This
script closes that gap. It reads the blob from the environment variable named
on the command line (default `ALL_SECRET`) and prints one `NAME=value` line per
key it recognises, for the calling step to mask and export.

Two ways to recognise a key, in this order:

1. A label on the same line ("finnhub", "fmp", "alpha vantage", "fred", "eodhd",
   "groq", in any case, with or without "api"/"key"/"="), followed by a token.
   This is how a `.env` file looks and how a message that says
   "finhub api = ..." looks.
2. The token's shape, for tokens no label claimed: `gsk_` + 40 or more
   characters is Groq; 32 lowercase hex characters is FRED; 32 mixed
   alphanumerics is FMP; 40 lowercase alphanumerics is Finnhub; 16 uppercase
   alphanumerics is Alpha Vantage. A shape that fits two names, or a name
   that two tokens fit, is left unassigned rather than guessed.

Nothing is printed for a name it is not sure about, and no value is ever
written to stderr: the summary there names the names.
"""

from __future__ import annotations

import os
import re
import sys

LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FINNHUB_API_KEY", re.compile(r"fin+hub", re.I)),
    ("FMP_API_KEY", re.compile(r"\bfmp\b|financial\s*modeling", re.I)),
    ("ALPHAVANTAGE_API_KEY", re.compile(r"alpha\s*_?vantage|\balpha\b|\bav\b", re.I)),
    ("FRED_API_KEY", re.compile(r"\bfred\b", re.I)),
    ("GROQ_API_KEY", re.compile(r"\bgroq\b", re.I)),
    ("EODHD_API_KEY", re.compile(r"\beod\s*hd\b|\beodhd\b|eod\s*historical", re.I)),
)

SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GROQ_API_KEY", re.compile(r"^gsk_[A-Za-z0-9]{40,}$")),
    ("FRED_API_KEY", re.compile(r"^[0-9a-f]{32}$")),
    ("FMP_API_KEY", re.compile(r"^(?=.*[A-Z])[A-Za-z0-9]{32}$")),
    ("FINNHUB_API_KEY", re.compile(r"^[a-z0-9]{40}$")),
    ("ALPHAVANTAGE_API_KEY", re.compile(r"^[A-Z0-9]{16}$")),
    # EODHD tokens end in a dot and a short suffix (e.g. ".12345678"); labelled use is the safe path.
    ("EODHD_API_KEY", re.compile(r"^[0-9]{6,}\.[0-9a-f]{8}$")),
)

TOKEN = re.compile(r"[A-Za-z0-9_\-]{16,}")
NAMES = tuple(name for name, _ in LABELS)


def recognise(blob: str) -> dict[str, str]:
    """The keys the blob carries, by their environment-variable name."""
    found: dict[str, str] = {}
    claimed: set[str] = set()

    for line in blob.splitlines():
        for name, label in LABELS:
            if name in found:
                continue
            m = label.search(line)
            if m is None:
                continue
            tail = line[m.end() :]
            tok = TOKEN.search(tail)
            if tok is None or tok.group(0).upper() in NAMES:
                continue
            value = tok.group(0)
            found[name] = value
            claimed.add(value)
            break

    unclaimed = [t for t in TOKEN.findall(blob) if t not in claimed and t.upper() not in NAMES]
    for name, shape in SHAPES:
        if name in found:
            continue
        fits = [t for t in unclaimed if shape.match(t)]
        if len(fits) == 1:
            found[name] = fits[0]
            unclaimed.remove(fits[0])
    return found


def main(argv: list[str]) -> int:
    var = argv[1] if len(argv) > 1 else "ALL_SECRET"
    blob = os.environ.get(var, "")
    if not blob.strip():
        sys.stderr.write(f"{var} is empty or unset\n")
        return 0
    found = recognise(blob)
    for name in NAMES:
        if name in found:
            sys.stdout.write(f"{name}={found[name]}\n")
    missing = [n for n in NAMES if n not in found]
    sys.stderr.write(
        f"{var}: recognised {', '.join(n for n in NAMES if n in found) or 'nothing'}"
        + (f"; not found: {', '.join(missing)}" if missing else "")
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
