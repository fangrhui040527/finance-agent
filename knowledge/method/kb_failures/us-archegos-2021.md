---
title: "Archegos (United States, 2021): leverage hidden in swaps, concentrated in a handful of names"
as_of: 2026-09-06
licence: own
patterns: [hidden_leverage, single_customer_concentration]
case: {name: Archegos Capital Management, country: US, year: 2021, outcome: "forced liquidation of tens of billions in positions, counterparties lost about USD 10 billion, founder convicted 2024"}
refs:
  - {title: "Credit Suisse, Report on Archegos Capital Management (Paul Weiss special committee, July 2021)", url: "https://www.credit-suisse.com/about-us/en/reports-research/archegos-info-kit.html", licence: link_only}
  - {title: "US Department of Justice, Archegos founder convicted (July 2024)", url: "https://www.justice.gov/usao-sdny", licence: open}
---
1. What the numbers showed beforehand
A family office holding highly concentrated positions in a few media and technology stocks through total return swaps with several banks, so that no single bank saw the whole exposure and no public filing disclosed it. Leverage across the positions was several times capital. Each bank's risk view saw a fraction of the concentration.

2. What happened
In March 2021 one of the positions fell sharply after a share issue, margin calls arrived from several banks at once, the family office could not meet them, and the banks liquidated the positions into a falling market. Some counterparties lost billions; the stocks involved fell by half.

3. Pattern
Leverage hidden by structure; concentration hidden by spreading it across counterparties; and the single-customer problem seen from the banks' side, where one client's positions were large enough to move the collateral they were secured on.

4. The check in this system that would have caught it
For the holder: the concentration and effective-number-of-bets limits, which this book enforces mechanically. For an observer: unusual price behaviour and block trading in a cluster of names with no common fundamental story is a signal that a single holder is behind the moves. The lesson for a small book is that leverage and concentration compound each other, and neither is visible until both fail.

5. References
The Credit Suisse special committee report and the Department of Justice's public statements.
