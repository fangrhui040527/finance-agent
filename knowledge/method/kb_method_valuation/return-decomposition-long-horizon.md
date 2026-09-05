---
title: "Return decomposition over a holding period: earnings growth, multiple change, yield, currency"
as_of: 2026-09-06
licence: own
archetypes: [bank, utility, cyclical, software, semis, chemicals, hospital, gaming, holding, reit]
refs:
  - {title: "Bogle (1991), Investing in the 1990s: Occam's Razor Revisited, Journal of Portfolio Management (the sources of return)", url: "https://doi.org/10.3905/jpm.1991.409336", licence: link_only}
  - {title: "Brinson, Hood and Beebower (1986), Determinants of Portfolio Performance, Financial Analysts Journal", url: "https://doi.org/10.2469/faj.v42.n4.39", licence: link_only}
---
1. Four legs of a total return
Over any holding period the return on a stock decomposes, approximately and log-additively, into growth in earnings per share, change in the multiple applied to those earnings, the shareholder yield from dividends and buybacks, and, for a foreign holding, the currency move against the investor's base. The decomposition says which of the four the investor was paid for, and which they were betting on without knowing it.

2. Why it matters for a thesis
A stock that returned 15 percent a year for five years through multiple expansion alone has not proved a business thesis; it has proved that the market changed its mind. The same return through earnings growth with a flat multiple is a business result. The forward case should be built on the legs that can be defended: earnings growth from the drivers, yield from cash generation, and an explicit view on whether the multiple is more likely to compress or expand from here.

3. The Malaysian investor's fourth leg
For a ringgit investor in Nasdaq names the currency leg has been large in some years and its sign has varied. Reporting it separately stops a currency gain from being read as a stock-picking gain, and a currency loss from being read as a thesis failure.

4. In this system
The attribution engine's long-horizon decomposition takes start and end earnings, multiples, cumulative distributions and the exchange rate and reports the four legs with a residual. The workup's return-decomposition step runs it when at least two years of earnings and multiple snapshots exist in the fact book, and marks itself unavailable otherwise.

5. References
Bogle for the earnings-growth plus multiple-change plus yield decomposition; Brinson, Hood and Beebower for portfolio-level attribution, which the Brinson engine here implements for the book as a whole.
