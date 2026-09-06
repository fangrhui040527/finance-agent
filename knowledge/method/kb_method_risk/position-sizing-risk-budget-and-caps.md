---
title: "Position sizing: a risk budget divided by the distance to being wrong, then five caps"
as_of: 2026-09-06
licence: own
refs:
  - {title: "Grinold and Kahn, Active Portfolio Management (2nd ed.), on risk budgeting", url: "https://en.wikipedia.org/wiki/Active_Portfolio_Management", licence: link_only}
  - {title: "Securities Commission Malaysia, Guidelines on Investor Protection and Risk Disclosure", url: "https://www.sc.com.my/", licence: link_only}
---
1. The first calculation
Risk per trade as a fraction of capital, divided by the fractional distance from the entry to the price at which the thesis is falsified, gives the position as a fraction of capital. At 0.75 percent risk and a stop 10 percent away, the position is 7.5 percent of capital. The stop comes from the thesis, not from the desired position size; sizing backward from a desired position to a convenient stop is the error the order of operations prevents.

2. The five caps
Risk budget, from the calculation above. Kelly, once fifty graded decisions exist to estimate an edge. Concentration, the single-name limit. Liquidity, a fraction of average daily traded value so the position can be built and exited. Cost floor, the minimum size at which fees and spread do not consume the expected edge; below it the trade cannot pay for itself and is refused. The smallest of the applicable caps binds and is named.

3. Volatility scaling
A target volatility for the book scales every position by the ratio of the target to the position's own volatility, so a volatile name gets a smaller weight at the same risk. The target here is 20 percent annualised; in a high-volatility regime positions shrink automatically.

4. Whole lots and currency
Bursa trades in board lots of 100; the sized position is rounded down to whole lots, and if the rounded position is zero the answer is no position. A US name is sized in dollars and converted at the official rate written down that day; the currency exposure counts toward the non-base-currency limit.

5. A refusal is an answer
When the binding cap yields a position below the cost floor or below one lot, the tool reports no position and the reason. It does not soften the inputs to produce a number; the stress suite checks that repeated asking does not move the floor.

6. References
Grinold and Kahn for risk budgeting; the Securities Commission's guidelines for the disclosure frame a Malaysian retail investor operates under.
