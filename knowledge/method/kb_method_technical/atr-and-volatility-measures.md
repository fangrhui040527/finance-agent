---
title: "Average true range and realised volatility: measuring dispersion, not predicting it"
as_of: 2026-09-06
licence: own
base_rate: {n: 0, unvalidated: true}
refs:
  - {title: "Wilder (1978), New Concepts in Technical Trading Systems", url: "https://en.wikipedia.org/wiki/Average_true_range", licence: link_only}
  - {title: "Andersen, Bollerslev, Diebold and Labys (2003), Modeling and Forecasting Realized Volatility, Econometrica", url: "https://doi.org/10.1111/1468-0262.00418", licence: link_only}
---
1. Two measures of the same thing
Realised volatility is the standard deviation of daily returns over a window, annualised by the square root of 252. The average true range is the mean over a window of the day's range including any gap from the previous close, in price units. Both describe how much the price has been moving; the first is used for sizing in return terms, the second for placing stops in price terms.

2. What they are for
A stop that sits inside one average true range of the entry is likely to be hit by noise, not by information. A position sized so that one average true range equals the risk budget per trade is a position whose ordinary day does not breach the budget. Volatility targeting scales positions down when dispersion rises so that the book's risk stays roughly constant in stress.

3. What they are not for
Neither predicts direction. Volatility clusters, so the recent window forecasts the near-term level of dispersion better than the long-run average does, and that is the extent of the forecasting content. A rising ATR is a statement about uncertainty, not about which way the uncertainty resolves.

4. Base rate
No base rate is claimed for any pattern built on these measures. The field on this note records n equals zero and unvalidated, and the technical agent cannot cite a pattern as evidence until a base rate has been measured on this system's own price history.

5. References
Wilder for the average true range; Andersen and co-authors for realised volatility and its forecastability.
