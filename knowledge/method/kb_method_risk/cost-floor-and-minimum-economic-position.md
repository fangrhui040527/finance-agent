---
title: "The cost floor: the position below which the trade cannot pay for itself"
as_of: 2026-09-06
licence: own
refs:
  - {title: "Bursa Malaysia, Transaction costs: brokerage, clearing fee and stamp duty", url: "https://www.bursamalaysia.com/trade/trading_resources/equities/transaction_costs", licence: link_only}
  - {title: "moomoo MY, Fee schedule", url: "https://www.moomoo.com/my/support/topic4_1262", licence: link_only}
---
1. The arithmetic
A round trip costs brokerage twice, clearing fees, stamp duty on Bursa, the bid-ask spread, and for a US name the currency spread each way. Expressed in basis points of the position, the total falls as the position grows because some fees are fixed minimums. Below some size the round-trip cost exceeds the expected gain from the thesis, and the trade has negative expected value before the market has moved.

2. The floor in this system
The cost floor is computed per market from the account's own fee card, not the venue's headline schedule, because a minimum brokerage of a few ringgit dominates small orders. A position whose expected edge, at the stance's horizon, does not cover the round-trip cost with margin is refused with the floor named. The floor does not soften under repeated asking; the stress suite checks that.

3. Why this matters more for a small book
A USD 1,000 paper book, or a small real one, is in the region where minimum fees are a large fraction of the position. The fundable set of names, those on which a minimum economic position fits inside the concentration cap, is smaller than the watchlist, and the paper book's status page lists it explicitly.

4. Spread as a cost
The quoted spread is paid on entry and exit; for an illiquid Bursa name it can exceed the brokerage. The liquidity cap and the cost floor are two views of the same constraint: a name that cannot be traded without paying away the edge is not investable at this size, whatever its valuation.

5. References
Bursa's published transaction-cost schedule; the broker's own fee card, which is the one the engine reads.
