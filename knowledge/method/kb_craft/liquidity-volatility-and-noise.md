---
title: "Liquidity, volatility and noise: what price can and cannot tell you"
as_of: 2026-09-06
licence: own
concepts: [liquidity, volatility, trend_vs_noise]
refs:
  - {title: "Almgren and Chriss (2000), Optimal Execution of Portfolio Transactions, Journal of Risk", url: "https://doi.org/10.21314/JOR.2001.041", licence: link_only}
  - {title: "Wilder (1978), New Concepts in Technical Trading Systems (the Average True Range)", url: "https://en.wikipedia.org/wiki/Average_true_range", licence: link_only}
  - {title: "Fama (1970), Efficient Capital Markets: A Review of Theory and Empirical Work, Journal of Finance", url: "https://doi.org/10.2307/2325486", licence: link_only}
---
1. Liquidity is what your own order does to the price
Liquidity is not a property of a stock; it is the cost of moving a given amount of it in a given time. The practical measure is average daily traded value, and the practical rule is participation: an order that is a large fraction of a day's volume moves the price against itself. Impact grows roughly with the square root of participation, so doubling the order more than doubles nothing but costs more than proportionally in slippage. The sizing engine in this system caps participation at a fraction of average daily value for this reason, and a position that cannot be exited at the size assumed is a standing red-team challenge.

2. Volatility is a measurement, not a mood
Volatility is the dispersion of returns over a window, annualised. The average true range is the same idea in price units, useful for stops. Neither is good or bad; both are inputs to sizing, because a position's risk is its size times its volatility, and a stop that sits inside one day's normal range is a coin flip, not a decision. Volatility clusters: high-volatility days follow high-volatility days, so the recent window is more informative than the long-run average for the next few weeks.

3. Trend, mean reversion and noise
Most daily moves are noise: the sum of many small flows with no information content. Trends exist at some horizons and mean reversion at others, and the evidence for each is statistical, not visual. A chart pattern with no measured base rate is a story. This system's technical notes carry a base rate field, and a pattern whose base rate has not been measured is marked unvalidated and cannot be cited as evidence, which is the discipline that separates a description of what price did from a prediction of what it will do.

4. What price cannot tell you
Price cannot tell you why it moved. A fall on a market-wide down day says nothing about the company; the decomposition engine removes the market and sector components first, and only the residual is a candidate for a company-specific explanation. Price also cannot tell you whether the business is worth more or less than yesterday; that is the fact book's job.

5. References
Almgren and Chriss for the impact-cost model behind participation limits; Wilder for the average true range; Fama for the framing of price as an aggregation of information rather than a signal in itself.
