---
title: "Cost of capital: the discount rate is built, not chosen"
as_of: 2026-09-06
licence: own
archetypes: [bank, utility, cyclical, software, semis, chemicals, hospital, gaming, holding, reit, pre_profit]
refs:
  - {title: "Damodaran, Country Default Spreads and Risk Premiums (July 2026 update)", url: "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html", licence: attributed}
  - {title: "Damodaran, Betas by Sector (US)", url: "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html", licence: attributed}
  - {title: "Sharpe (1964), Capital Asset Prices, Journal of Finance 19(3)", url: "https://doi.org/10.2307/2977928", licence: link_only}
---
1. Four inputs, each with a source
The cost of equity under the capital asset pricing model is the risk-free rate plus beta times the equity risk premium, plus a country risk premium where the business earns its cash in a market whose sovereign carries default risk. Each input has a source, and a discount rate stated without its inputs is a number picked to make the answer come out. This system reads the risk-free rate from the stored ten-year Treasury series as of the valuation date, the premiums from the dated cost-of-capital table, and beta from the stored vendor figure or, failing that, the industry average.

2. The premiums, as of July 2026
Damodaran's July 2026 update uses a mature-market equity risk premium of 4.17 percent and a United States premium of 4.45 percent; country premiums are the sovereign default spread scaled by an equity volatility multiplier of 1.5545. Those figures are copied into the table with their date, and the table flags itself stale after about thirteen months, because the January and July updates move them.

3. Beta: measured, borrowed, or unlevered and relevered
A company beta estimated from its own returns is noisy; five years of monthly data give a standard error of a few tenths. An industry average unlevered beta, relevered for the company's own debt ratio, is usually the more stable estimate. The table carries industry unlevered betas for the sectors in the book; a beta taken from a vendor is labelled as such, and a beta borrowed from the industry is labelled as such, so the memo shows where the number came from.

4. Cost of debt and the weights
The pre-tax cost of debt is the risk-free rate plus a default spread. When a company's interest expense is stored, its interest coverage maps to a synthetic rating and the rating to a spread; when it is not, the caveat says so. The after-tax cost uses the effective tax rate when the statements give one and the statutory rate otherwise. Weights are market values: market capitalisation for equity and book debt as the usual proxy for debt.

5. Sanity
A cost of equity below the risk-free rate plus half the premium, or a cost of equity below the cost of debt, is an input error, and the valuation sanity checks refuse the scenario rather than discounting at it. A Malaysian name discounted at a United States rate with no country premium is understating its risk; the table's country row exists to stop that.

6. References
Damodaran's country and beta tables for the figures; Sharpe for the model.
