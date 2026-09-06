---
title: "Portfolio heat and correlated stops: the loss if everything goes wrong at once"
as_of: 2026-09-06
licence: own
refs:
  - {title: "Tharp, Trade Your Way to Financial Freedom (1998), on total open risk", url: "https://en.wikipedia.org/wiki/Van_K._Tharp", licence: link_only}
  - {title: "Longin and Solnik (2001), Extreme Correlation of International Equity Markets, Journal of Finance", url: "https://doi.org/10.1111/0022-1082.00340", licence: link_only}
---
1. Definition
Heat is the sum over open positions of the loss each would take if its stop were hit, as a fraction of capital. If six positions each risk 1 percent to their stop, heat is 6 percent. It is the amount the book is exposed to if every thesis fails at once.

2. Why every stop can fire together
Correlations rise in stress. Longin and Solnik showed that the correlation of large down moves across markets is far higher than the average correlation, so the scenario in which all stops are hit in the same week is not a tail case; it is what a market-wide sell-off does to a book of long positions. Heat is the honest measure of that scenario.

3. The limit
This system caps heat at 6 percent of capital. Adding a position when the book is at the cap requires reducing another. The cap interacts with per-trade risk: at 0.75 percent per trade the cap allows eight positions, and a wider stop on one name means fewer names or smaller risk elsewhere.

4. Stops and gaps
A stop is an intention, not a guarantee; a gap through the stop realises a larger loss. The stress test applies a stated gap to every position and reports the loss beyond the stop, which is the difference between planned heat and realised heat in a bad open.

5. References
Tharp for the concept of total open risk; Longin and Solnik for stressed correlation.
