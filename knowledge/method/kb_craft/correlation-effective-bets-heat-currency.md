---
title: "Correlation, effective bets, portfolio heat, and the currency you did not choose"
as_of: 2026-09-06
licence: own
concepts: [correlation, effective_bets, portfolio_heat, currency]
refs:
  - {title: "Markowitz (1952), Portfolio Selection, Journal of Finance 7(1)", url: "https://doi.org/10.2307/2975974", licence: link_only}
  - {title: "Meucci (2009), Managing Diversification, Risk Magazine (effective number of bets)", url: "https://ssrn.com/abstract=1358533", licence: link_only}
  - {title: "Bank Negara Malaysia, Foreign exchange policy notices", url: "https://www.bnm.gov.my/fep", licence: link_only}
---
1. Correlation is the hidden concentration
Six positions in six Bursa banks are one position in Malaysian credit. Diversification counts independent sources of return, not tickers. Correlations are unstable and rise in stress, which is when diversification is needed, so the correlations to plan for are the stressed ones, not the calm-period averages. The risk engine in this system clusters holdings by correlation and caps the weight of any cluster.

2. Effective number of bets
The effective number of bets is a single figure for how many independent positions a portfolio really holds, derived from the covariance structure rather than the count. A twelve-name portfolio can have an effective number of three. The floor in this system is five effective bets with a hard floor of three; a book below the floor is concentrated whatever its name count says, and new entries that lower it further are refused.

3. Portfolio heat
Heat is the sum, across open positions, of the loss each would take if its stop were hit, expressed as a fraction of capital. It is the amount at risk if everything goes wrong at once, which in a correlated book is not a remote scenario. A heat limit bounds the total, so adding a position when the book is already at its limit means reducing another. The limit here is 6 percent of capital.

4. Currency is a position you did not choose
A ringgit-based investor holding Nasdaq names holds a US dollar position of the same size, whether or not they wanted one. A 5 percent move in the ringgit against the dollar is a 5 percent move in the value of those holdings in the unit that pays the bills. The exposure is legitimate, but it must be counted: the non-base-currency limit in this system is half the book, the official rate is written down daily, and the attribution engine reports the currency leg of every foreign holding's return separately from the local-currency leg.

5. In this system
Concentration, cluster, country, currency and heat limits are checked together on every sizing and rebalance, and the binding one is named. The stress test applies stated shocks to the book rather than assuming calm-period correlations hold.

6. References
Markowitz for the origin of covariance-based diversification; Meucci for the effective-number-of-bets measure; Bank Negara for the rules that govern ringgit conversion for a Malaysian resident.
