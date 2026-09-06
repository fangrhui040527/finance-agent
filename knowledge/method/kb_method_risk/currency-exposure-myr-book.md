---
title: "Currency exposure in a ringgit book: counting the position you did not choose"
as_of: 2026-09-06
licence: own
refs:
  - {title: "Bank Negara Malaysia, Foreign Exchange Policy Notices (resident investment abroad)", url: "https://www.bnm.gov.my/fep", licence: link_only}
  - {title: "Bank Negara Malaysia, Exchange rates (daily reference rates)", url: "https://www.bnm.gov.my/exchange-rates", licence: attributed}
  - {title: "Solnik (1974), Why Not Diversify Internationally Rather Than Domestically? Financial Analysts Journal", url: "https://doi.org/10.2469/faj.v30.n4.48", licence: link_only}
---
1. The exposure
A ringgit investor holding a US-listed stock holds two positions: the stock, and the dollar. The dollar leg is the full value of the holding, and a 5 percent move in the ringgit against the dollar changes the holding's value in ringgit by 5 percent whatever the stock does. The leg is legitimate and often desirable, but it must be counted as a position.

2. Limits and measurement
The non-base-currency limit in this system is half the book. Exposure is measured at the official reference rate written down daily; the spread on conversion, half a percent each way at a retail broker, is a cost the sizing engine charges to the trade. The attribution engine reports the currency leg of a foreign holding's return separately from the local-currency return.

3. Hedging
A retail investor rarely hedges currency, and for a long-horizon equity holding the case for hedging is weak: the cost is a carry differential and the benefit is a reduction in variance that matters most over short horizons. The decision is still a decision, and the journal records it.

4. Rules a resident operates under
Bank Negara's foreign exchange policy notices govern how much a Malaysian resident may invest abroad and under what conditions, and they change. A note on the current limits belongs in the account configuration, not in the analyst's head.

5. References
Bank Negara's policy notices and reference rates; Solnik for the case for international diversification, which is the reason the exposure exists at all.
