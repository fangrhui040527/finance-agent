"""Export the design tokens to the web app, dropping the network font import.

`design/_css.txt` is the single source for the palette and components - the
artboards and the docs already read it. The served copy differs in exactly one
way: the Google Fonts @import goes, because the app must render fully offline
and a CSS import is a network fetch on every load. The font stacks keep their
local fallbacks, so the page degrades to system faces instead of blocking.

`python -m design.export_css` regenerates web/static/tokens.css; a test holds
the two in sync minus that one line.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parent / "_css.txt"
DST = Path(__file__).parent.parent / "web" / "static" / "tokens.css"

HEADER = "/* Generated from design/_css.txt by design/export_css.py - do not edit. */\n"


def exported_css() -> str:
    lines = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        if "@import" in line and "fonts.googleapis.com" in line:
            continue  # offline-first: no network fetch for fonts
        lines.append(line[4:] if line.startswith("    ") else line)
    return HEADER + "\n".join(lines).strip() + "\n"


def main() -> int:
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(exported_css(), encoding="utf-8", newline="\n")
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
