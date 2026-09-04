# The paper journal

One page per calendar day about the paper book (docs/22-PAPER-BOOK.md): what it
held, what changed, whether the decision or the market did it, what it got
wrong, and what tomorrow's target book is. Written by the nightly routine from
`knowledge/paper/YYYY-MM-DD.pack.md`, which `ask.py paper pack` prepares.

Three readers: you, the reflection agent (these pages are indexed into
`kb_lessons` beside the feedback pages), and the routine itself, which reads
the last three pages before it decides.

## Files

- `YYYY-MM-DD.md` - the page, tracked in git.
- `YYYY-MM-DD.json` - the same figures as data, tracked in git. Schema below.
- `YYYY-MM-DD.pack.md` - the pack the page was written from; regenerable, gitignored.
- `README.md` (this contract) and `TEMPLATE.md`. Nothing else lives here; anything not
  named as a date is not indexed.

## The rules the page is held to

1. **Every number comes from the pack, with its source.** Equity, weights, fees, returns
   and the control's figures are copied; nothing is estimated. A figure the pack does not
   carry is not on the page.
2. **Components before narrative.** The attribution table says whether a held name's move
   was market or idiosyncratic. A story is offered only for the idiosyncratic part, and
   only when the verdict warrants a hunt.
3. **A mistake is a falsifiable sentence.** "Entered NVDA at a 25% weight the day before
   a market-wide fall" is a mistake; "should have waited" is not. Each mistake names the
   decision (its date and prediction id) and the evidence.
4. **Bands, never verbs.** No "buy", "sell", "should". The page records targets, exposure
   and reasons.
5. **Lessons are candidates.** A `lessons` entry is a one-line, general, falsifiable
   statement. It becomes a real lesson only when the reflection agent's gate promotes it;
   nothing here promotes itself.
6. **The decision is recorded by the CLI, never by the page.** The page says what target
   book was recorded (or refused) and why; `ask.py paper decide` is the only writer, and
   its refusals are copied verbatim.

## JSON schema

```json
{
  "day": "2026-09-08",
  "written_at": "2026-09-08T22:41:00Z",
  "phase": "observe",
  "equity_usd": 1000.0,
  "cash_usd": 1000.0,
  "control_equity_usd": 1000.0,
  "drawdown": 0.0,
  "halted": false,
  "positions": [
    {"instrument_id": "MYX:5183", "units": 100, "avg_cost": "4.16", "close": "4.20",
     "value_usd": 104.0, "weight": 0.104, "pnl_open_usd": 0.99}
  ],
  "changes": [
    {"action": "open", "instrument_id": "MYX:5183", "units": 100, "price": "4.17",
     "fee_usd": 1.12, "fx_spread_usd": 0.52, "cash_usd": -104.63, "prediction_id": "paper-..."}
  ],
  "caps": [{"cap": "invested", "value": "10.40%", "limit": "40%", "breached": false}],
  "attribution": {"market": 0.0021, "idiosyncratic": -0.0034},
  "decision": {
    "recorded": true,
    "weights": {"MYX:5183": 0.11, "XNAS:NVDA": 0.22},
    "thesis": "...",
    "refusals": []
  },
  "mistakes": ["one falsifiable sentence, with the prediction id it concerns"],
  "lessons": ["one-line, general enough to apply to another name"]
}
```
