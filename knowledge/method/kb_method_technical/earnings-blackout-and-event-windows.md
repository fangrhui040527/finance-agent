---
title: "Earnings blackout and event windows: when price description is unreliable"
as_of: 2026-09-06
licence: own
base_rate: {n: 0, unvalidated: true}
refs:
  - {title: "Ball and Brown (1968), An Empirical Evaluation of Accounting Income Numbers, Journal of Accounting Research", url: "https://doi.org/10.2307/2490232", licence: link_only}
  - {title: "MacKinlay (1997), Event Studies in Economics and Finance", url: "https://www.jstor.org/stable/2729691", licence: link_only}
---
1. The window
The sessions immediately before and after a scheduled announcement, results above all, are dominated by the announcement. Volatility rises into the date and jumps on it; technical descriptions built on the sessions around it describe the event, not the trend. This system's technical agent treats the three sessions around a known earnings date as a blackout for pattern description.

2. Event windows for attribution
An event study measures the abnormal return in a window around the event after removing the market and sector components, against the distribution of such returns in the past. That is how a base rate for "results miss of this size" is built, and it is the same decomposition the why-did-it-move engine runs on a single day.

3. Scheduled versus unscheduled
Scheduled events have a date in the calendar and a base rate; unscheduled ones do not, and their surprise content is higher. The fact book stores both with announced-at and effective-at dates kept separate, because a dividend announced today and paid next month moves the price today.

4. Base rate
Post-announcement drift, the tendency of prices to continue moving in the direction of an earnings surprise for weeks, is one of the better-documented anomalies in the literature, and even so no base rate for it is claimed here until it is measured on this market and this cap band.

5. References
Ball and Brown for the original evidence that earnings announcements move prices with a lag; MacKinlay for the event-study method.
