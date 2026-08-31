"""Regenerate every artboard, deterministically, from the repo root or anywhere.

`exec(open("gen.py").read())` was the old mechanism: whatever file named gen.py
sat in the CURRENT DIRECTORY was executed. Run from the wrong place, it ran the
wrong code. Imports resolve relative to this package instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> int:
    import importlib

    for n in range(1, 10):
        importlib.import_module(f"b{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
