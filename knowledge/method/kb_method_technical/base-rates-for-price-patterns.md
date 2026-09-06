---
title: "Base rates for price patterns: how one is measured, and why none is claimed yet"
as_of: 2026-09-06
licence: own
base_rate: {n: 0, unvalidated: true}
refs:
  - {title: "Lo, Mamaysky and Wang (2000), Foundations of Technical Analysis, Journal of Finance", url: "https://doi.org/10.1111/0022-1082.00265", licence: link_only}
  - {title: "Bailey, Borwein, Lopez de Prado and Zhu (2014), Pseudo-Mathematics and Financial Charlatanism, Notices of the AMS", url: "https://www.ams.org/notices/201405/rnoti-p458.pdf", licence: attributed}
---
1. What a base rate for a pattern is
Given that a pattern has occurred, the distribution of the forward return over a stated horizon, measured on a stated market and cap band, over a stated period, against the unconditional distribution. The base rate is the whole distribution, or at least its median and spread and the sample size, not a hit rate.

2. How it is measured here
Define the pattern mechanically so it can be found by code with no discretion. Find every occurrence in the stored price bars. Remove the market and sector components of the forward return. Compare the residual distribution with the residual distribution on random dates using a purged walk-forward split so the same period is never both fit and test. Record n, the median, the interquartile range, and whether the difference survives the multiple-testing correction.

3. Why none is claimed
Bailey and co-authors show how easily a pattern can be found that "works" in sample when many are tried. Until a pattern has been through the procedure above on this system's own bars, its base rate field reads n equals zero and unvalidated, and the technical agent will describe the pattern's occurrence but cannot cite it as evidence for a direction. The field is data, so a validated pattern can be added without changing code.

4. What would change the status
A measured n of at least thirty occurrences with a residual return distribution that differs from the control after correction. The backtest harness in this system already carries purged walk-forward splits, the deflated Sharpe ratio and the probability of backtest overfitting for exactly this measurement.

5. References
Lo, Mamaysky and Wang for a rigorous attempt to test chart patterns; Bailey and co-authors for why most claimed patterns are noise.
