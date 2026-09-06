---
title: "Expected value, sizing, Kelly and ruin: the arithmetic of staying in the game"
as_of: 2026-09-06
licence: own
concepts: [expected_value, position_sizing, kelly, ruin]
refs:
  - {title: "Kelly (1956), A New Interpretation of Information Rate", url: "https://doi.org/10.1002/j.1538-7305.1956.tb03809.x", licence: link_only}
  - {title: "Thorp (2006), The Kelly Criterion in Blackjack, Sports Betting and the Stock Market", url: "https://www.eecs.harvard.edu/cs286r/courses/fall12/papers/Thorpe_KellyCriterion2007.pdf", licence: link_only}
  - {title: "MacLean, Thorp and Ziemba (2011), The Kelly Capital Growth Investment Criterion", url: "https://doi.org/10.1142/7598", licence: link_only}
---
1. Expected value is not enough
A bet with positive expected value can still bankrupt the person who takes it too large. Expected value says whether a repeated bet is worth taking; sizing says how much of it. Edge is expected value per unit risked, and it is small in liquid markets, uncertain, and estimated from the same noisy data the thesis came from. Treating an estimated edge as a known one is the first step toward ruin.

2. Sizing bounds the loss, not the position
The question is never how many shares; it is how much can be lost if the thesis is wrong, and the answer is set before the entry. Risk per trade as a fraction of capital, divided by the distance to the point at which the thesis is falsified, gives the position. A wider stop means a smaller position for the same risk. The stop is not a prediction of where the price will go; it is the price at which the reason for owning the stock no longer holds.

3. Kelly, and why a fraction of it
The Kelly fraction maximises the long-run growth rate of capital for a bet with known odds and edge. Full Kelly is violently volatile: it accepts drawdowns of 50 percent as the price of maximum growth, and it is only optimal if the edge estimate is exactly right. An overestimate of edge moves the bet past the optimum, where growth falls and can turn negative. Half Kelly gives up a quarter of the growth for half the variance; a quarter Kelly is the common practice for estimated edges. This system's Kelly cap activates only after fifty graded decisions, because before that the edge estimate has no standing.

4. Risk of ruin and path dependence
Ruin is not only a zero balance. It is any drawdown from which the capital cannot recover within the investor's horizon, or that forces a sale at the bottom. The probability of hitting a drawdown threshold rises steeply with position size and with correlation between positions. The recovery arithmetic from the compounding note is why: each additional 10 percent of drawdown costs more than the last to recover. Path matters; two sequences with the same average return and different orderings of losses end at different places for anyone adding or withdrawing capital.

5. In this system
Five caps bound every sized position: risk budget, Kelly (when eligible), single-name concentration, liquidity participation and a cost floor below which the trade cannot pay for its own frictions. The binding cap is named in every sizing output, and a refusal is an answer.

6. References
Kelly for the criterion; Thorp for its application to markets; MacLean, Thorp and Ziemba for the collected theory and the case for fractional Kelly.
