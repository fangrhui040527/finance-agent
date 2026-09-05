---
title: "The Kelly fraction under estimation error: why a quarter, and why not before fifty decisions"
as_of: 2026-09-06
licence: own
refs:
  - {title: "MacLean, Thorp and Ziemba (2011), The Kelly Capital Growth Investment Criterion", url: "https://doi.org/10.1142/7598", licence: link_only}
  - {title: "Baker and McHale (2013), Optimal Betting Under Parameter Uncertainty, Decision Analysis", url: "https://doi.org/10.1287/deca.2013.0271", licence: link_only}
---
1. The criterion with known odds
For a bet that pays b to 1 with probability p of winning, the Kelly fraction is p minus (1 minus p) divided by b. It maximises the expected logarithm of wealth, which is the long-run growth rate. Bet more and growth falls; bet twice Kelly and expected growth is zero; beyond that it is negative.

2. The criterion with estimated odds
In markets p and b are estimates from a small, noisy sample, and estimation error is asymmetric in its effect: overestimating the edge pushes the bet past the optimum, where the growth curve falls steeply. Baker and McHale show the optimal fraction under parameter uncertainty is well below full Kelly, and the more uncertain the estimate the smaller the fraction. Fractional Kelly, a half or a quarter, trades a modest reduction in growth for a large reduction in variance and in the probability of ruin from a wrong estimate.

3. Why the count matters
An edge estimated from ten decisions has a confidence interval that includes zero. The Kelly cap in this system does not activate until fifty graded decisions exist, and even then applies a quarter fraction. Before that, the risk budget and the other caps size the position, and the journal accumulates the record the estimate will eventually rest on.

4. Kelly and the drawdown you can live with
Full Kelly implies a 50 percent probability of a 50 percent drawdown at some point. A quarter Kelly reduces that to a drawdown most investors can hold through without a forced exit. The relevant constraint is not mathematical; it is whether the investor will still be in the game when the edge pays.

5. References
MacLean, Thorp and Ziemba for the theory and the case for fractions; Baker and McHale for the treatment of estimation error.
