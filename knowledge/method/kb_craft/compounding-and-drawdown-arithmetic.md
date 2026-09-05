---
title: "Compounding, and why a loss costs more than the gain that undoes it"
as_of: 2026-09-06
licence: own
concepts: [compounding]
refs:
  - {title: "Kelly (1956), A New Interpretation of Information Rate, Bell System Technical Journal", url: "https://doi.org/10.1002/j.1538-7305.1956.tb03809.x", licence: link_only}
  - {title: "Bernstein, The Four Pillars of Investing, chapter on the arithmetic of loss", url: "https://en.wikipedia.org/wiki/William_J._Bernstein", licence: link_only}
---
1. Returns multiply, they do not add
A sequence of returns compounds: the ending value is the product of (1 + r) for each period, not the sum of the r's. Two consequences follow that most people get wrong the first time. Order does not matter for the final value of a lump sum, so a bad year first or last ends in the same place. But the size of losses matters more than the size of gains, because a loss shrinks the base that the next gain works on.

2. The recovery table
Down 10 percent needs up 11.1 percent to recover. Down 20 percent needs 25 percent. Down 33 percent needs 50 percent. Down 40 percent needs 66.7 percent. Down 50 percent needs 100 percent. Down 75 percent needs 300 percent. The formula is recovery = 1 / (1 - loss) - 1. Down 50 percent and then up 50 percent leaves 25 percent lost. The asymmetry is not a market quirk; it is arithmetic, and it is the reason position sizing and stops exist.

3. Volatility drag
A stream of returns with the same arithmetic average but higher variance compounds to less. The geometric mean is approximately the arithmetic mean minus half the variance. A position that goes plus 30 percent and minus 30 percent in alternate years averages zero arithmetically and loses about 4.5 percent a year geometrically. This is why the sizing engine in this system targets volatility rather than a fixed fraction of capital: the same expected return with less variance compounds to more.

4. The cost of interruption
Compounding also needs time and continuity. A forced sale at the bottom of a drawdown, because of a margin call, a cash need, or a risk limit, converts a temporary loss into a permanent one and removes the base that later returns would have grown. The emergency-fund and investable-capital waterfall in this system exists so that the paper book, and later a real one, is never the source of cash in a bad month.

5. References
Kelly's 1956 paper is the origin of growth-optimal sizing and of the intuition that overbetting destroys geometric growth. Bernstein's chapter is a readable treatment of loss arithmetic for a retail investor.
