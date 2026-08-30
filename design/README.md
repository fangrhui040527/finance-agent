# Design — 12 screens, as artboards

The frontend as designed. **None of it is implemented**: `ui/render.py` is 273
lines of terminal rendering, and no screen here exists as running code. These
are the specification, not the product.

Published as a canvas at
https://claude.ai/code/artifact/7e6f9647-c4a4-47ee-9fe1-3f085eb713ef

| Artboard | Screen |
|---|---|
| `Main` | the daily brief and what changed |
| `WhyItMoved` | attribution: market, sector, style, currency, unexplained |
| `Prices` | annotated chart with event markers |
| `Thesis` | the memo, its breakers, and the red team against it |
| `Portfolio` | concentration, heat, effective bets |
| `Sizing` | the five caps and which one binds |
| `Predictions` | the forward record and calibration |
| `Trace` | prompts, rail decisions, dropped claims |
| `Learn` | the curriculum and its prerequisite order |
| `WorldMonitor` | where news came from, and what is live |
| `Agents` | all 16 agents, drill into one for its full state |
| `Settings` | markets, budgets, feeds, keys |

## How they were built

`.dc.html` Design Component files: `{{handlebars}}`, `<sc-for>`, `<sc-if>`,
`data-props`. `gen.py` plus `b1.py`–`b9.py` generate them; `_css.txt` holds the
shared tokens, lifted verbatim from `docs/user-guide.html` so the design cannot
drift from the documentation's palette (`--accent:#0F5C63`, Newsreader /
Public Sans / JetBrains Mono).

Regenerate with `python design/gen.py`.

## Why they are in the repository

They lived outside it, in a scratch directory, for the whole of their existence.
An ephemeral container would have taken them and there was no second copy. A
design nobody can find is a design that gets redrawn.
