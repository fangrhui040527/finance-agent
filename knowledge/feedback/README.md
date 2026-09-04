# knowledge/feedback — what the system concluded, one page a day

This folder is written by the nightly **feedback routine** (a scheduled Claude
Code session; `docs/20-FEEDBACK-ROUTINE.md`) and read by three things:

- **you**, the morning after, in about two minutes;
- **the reflection agent**, which retrieves these pages as `kb_lessons` — the
  one knowledge store the registry lets an agent write — so a conclusion drawn
  on Tuesday is evidence available on Friday;
- **the routine itself**, which reads the last few pages before writing the
  next, so a question left open is followed up rather than re-asked.

## The contract

One page per calendar day, `YYYY-MM-DD.md`, with a sibling `YYYY-MM-DD.json`
carrying the same conclusions as data. Nothing else lives here except this
README and `TEMPLATE.md`; anything not named as a date is not indexed.

Each page answers, per name in the book, the questions an analyst asks after
the close and keeps asking until the chain bottoms out in something a source
said:

1. **What moved.** The measured return, the market's return over the same
   bars, and the decomposition — how much was market, how much sector, how
   much unexplained. All from `ask.py pack`, never re-derived by hand.
2. **Why — and why that.** For the unexplained share only: candidate causes,
   each pointing at evidence by id (`doc_id` for a story, `source:event_id` for
   an event, a series id for a macro print). Then the next question down: why
   did that cause arise, what caused *that*, until the chain reaches a primary
   source or is honestly marked as unknown. A market-driven move gets **no**
   company story — the decomposition says so and the page says so.
3. **What would change the reading.** For each cause, one observation that
   would confirm it and one that would refute it, in terms the collector can
   check tomorrow.
4. **What to watch.** Scheduled events in the next 30 days from the fact book;
   the series whose next print matters.
5. **Data quality.** Which sources failed, were degraded or skipped, from the
   day's collection rows — and what that means for the confidence of the page.

The rules the routine is held to are the repository's own:

- **Components before narrative.** A cause is offered only for the part of a
  move the decomposition could not explain.
- **A mention is not a cause.** A story that names a company links it; only a
  story about *what happened to it* explains it. Say which.
- **No number without a source.** Every figure on the page is copied from the
  pack with its id; none is estimated.
- **Bands, never verbs.** No "buy", "sell", "should". The page explains; the
  engines decide.
- **Unknown is an answer.** "The unexplained share is 60% and no collected
  evidence accounts for it" is a complete, correct finding.

## The JSON

```json
{
  "day": "2026-09-04",
  "written_at": "2026-09-05T06:41:00Z",
  "market": {"regime": "neutral", "notes": ["..."]},
  "names": [
    {
      "instrument_id": "MYX:1155",
      "move": {"return_1d": -0.0210, "market_1d": -0.0080, "unexplained_share": 0.58},
      "causes": [
        {
          "claim": "Q2 NIM guidance cut",
          "evidence": ["google_news:https://...", "bursa_announcements:1155:33921"],
          "chain": ["NIM guidance cut", "deposit competition from digital banks", "OPR held at 2.75 while..."],
          "confidence": 0.55,
          "confirm": "peer banks' NIM commentary this week",
          "refute": "sector index fell as much on the day"
        }
      ],
      "open_questions": ["..."],
      "watch": [{"when": "2026-10-29", "what": "earnings_result"}]
    }
  ],
  "data_quality": {"failed": [], "degraded": ["gdelt"], "skipped": ["fmp: FMP_API_KEY not set"]},
  "lessons": ["one-line, falsifiable, general enough to apply to another name"]
}
```

A `lessons` entry that survives a few weeks of pages is a candidate for the
reflection agent to propose as a real lesson (`predict.py reflect`); nothing
here promotes itself.
