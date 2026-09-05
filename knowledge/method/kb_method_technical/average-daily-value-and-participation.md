---
title: "Average daily traded value and participation: the liquidity numbers that bound a position"
as_of: 2026-09-06
licence: own
base_rate: {n: 0, unvalidated: true}
refs:
  - {title: "Almgren, Thum, Hauptmann and Li (2005), Direct Estimation of Equity Market Impact, Risk", url: "https://www.risk.net/", licence: link_only}
  - {title: "Bursa Malaysia, Trading rules and board lots", url: "https://www.bursamalaysia.com/regulation/rules_of_bursa_malaysia", licence: link_only}
---
1. The measure
Average daily traded value over twenty sessions is the liquidity figure this system uses. Volume in shares is misleading across price levels; value in the trading currency compares names. On Bursa the board lot of 100 shares and the tick size set a floor on what a small order can do.

2. Participation
A position built over one session at 5 percent of that day's value moves the price against itself by an amount that grows roughly with the square root of the participation rate. The sizing engine caps participation at a stated fraction of average daily value; a position that would need more than that to enter is capped, and one that could not be exited within a few sessions at that rate is a red-team challenge on the thesis.

3. Liquidity is regime-dependent
Value traded falls in stress at exactly the moment holders want to exit, and Bursa mid-caps can go from adequately liquid to untradeable in a week. The twenty-session average is a fair-weather figure; the liquidity cap uses it with a haircut, and the stress test assumes a fraction of it.

4. Base rate
No pattern is claimed on volume. Volume spikes accompany both breakouts and breakdowns; without a measured base rate on this system's own history the technical agent reports the figure and nothing more.

5. References
Almgren and co-authors for the empirical impact model; Bursa's rules for board lots and tick sizes.
