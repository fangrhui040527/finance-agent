---
title: "Restatements, revisions, and why every figure needs a known-at date"
as_of: 2026-09-06
licence: own
concepts: [restatement]
refs:
  - {title: "IAS 8, Accounting Policies, Changes in Accounting Estimates and Errors", url: "https://www.ifrs.org/issued-standards/list-of-standards/ias-8-accounting-policies-changes-in-accounting-estimates-and-errors/", licence: link_only}
  - {title: "Lopez de Prado, Advances in Financial Machine Learning (2018), on point-in-time data and look-ahead bias", url: "https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086", licence: link_only}
  - {title: "SEC Financial Reporting Manual, Topic 13 on restatements", url: "https://www.sec.gov/corpfin/cf-manual", licence: link_only}
---
1. Two kinds of change to a number
A revision updates an estimate with new information; macro series do this constantly, and a quarterly GDP print is revised for years. A restatement corrects an error or a misapplied policy in a published financial statement, and it is a disclosure event in its own right, because it says the earlier figure was wrong when it was published. Both mean that the number an analyst saw on a given day is not the number in today's database.

2. Look-ahead bias
Any backtest or historical judgement that uses the final revised figure for a past date is using information that did not exist on that date. The error flatters every strategy that reacts to fundamentals, because the revised figures are cleaner than the originals were. The defence is a point-in-time store: every figure kept with the date it became knowable, and every historical question answered from what was knowable then.

3. Restatements as evidence
A restatement that lowers past revenue or profit is one of the strongest signals in the failure library. It often follows an auditor change, a segment reorganisation, or a period of receivables growing faster than sales. Its arrival should reopen the earnings-quality question for every year it touches, and a thesis built on the restated years is a thesis built on numbers that were not true.

4. In this system
The fact book is append-only. A figure for a period is stored with `known_at` equal to its filing date; a later, different figure for the same period is a new row, not an overwrite, so the store shows both what was first reported and what it became. Backtests and the agents read through a point-in-time view that hides rows filed after the date in question.

5. References
IAS 8 for the accounting definition of an error correction; Lopez de Prado for the treatment of point-in-time data in research; the SEC manual for how US restatements are disclosed.
