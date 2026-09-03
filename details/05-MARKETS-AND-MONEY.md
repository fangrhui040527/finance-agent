# 05 — Markets and money

Eleven market adapters, eight currencies, and the boundary that keeps MYR the
unit of account.

---

## The eleven markets

Generated from `markets.registry.supported()` and each adapter's own fields.
"Minimum economic position" is `cost_floor_value()` — the smallest size whose
round-trip cost stays inside the market's floor — **in that market's own
currency**.

| MIC | Country | Currency | Tier | Regulator | Cost floor | Minimum economic position | Settle |
|---|---|---|---|---|---|---|---|
| `XKLS` | MY | MYR | 1 | Securities Commission Malaysia | 60 bps | MYR 4,706 | T+2 |
| `XNAS` | US | USD | 1 | US Securities and Exchange Commission | 5 bps | USD 1 | **T+1** |  <!-- venue schedule; see the broker note below -->
| `XSES` | SG | SGD | 2 | Monetary Authority of Singapore | 30 bps | SGD 9,091 | T+2 |
| `XHKG` | HK | HKD | 2 | Securities and Futures Commission of Hong Kong | **95 bps** | HKD 28,081 | T+2 |
| `XTKS` | JP | JPY | 2 | Financial Services Agency of Japan | 55 bps | JPY 545,455 | T+2 |
| `XLON` | GB | GBP | 2 | Financial Conduct Authority | 75 bps | GBP 7,200 | T+2 |
| `XASX` | AU | AUD | 2 | Australian Securities and Investments Commission | 25 bps | AUD 8,000 | T+2 |
| `XNSE` | IN | INR | 2 | Securities and Exchange Board of India | 35 bps | INR 31,041 | **T+1** |
| `XTAI` | TW | TWD | 2 | Financial Supervisory Commission of Taiwan | 70 bps | TWD 10,000 | T+2 |
| `XKRX` | KR | KRW | 2 | Financial Services Commission of Korea | 55 bps | KRW 500,000 | T+2 |
| `XETR` | DE | EUR | 2 | Bundesanstalt für Finanzdienstleistungsaufsicht | 25 bps | EUR 6,494 | T+2 |

**These are VENUE rows, and for this account most of them are the wrong ones.**
`markets/xnas.py` models a zero-commission US retail brokerage; `markets/xkls.py`
models Bursa's standard retail schedule. Neither is what a moomoo Malaysia
account pays, and `markets/brokers.py` now carries both real cards, read
2026-09-03 from the account's own fee schedule:

| | venue row | moomoo_my |
|---|---|---|
| XKLS minimum position | RM 4,706 (60 bps) | **RM 9,793** (40 bps) |
| XNAS minimum position | USD 1 (5 bps) | **USD 2,431** at USD 100/share (35 bps) |

The direction is counter-intuitive and worth stating: moomoo's Bursa schedule is
CHEAPER than the venue's - RM3 flat against a RM8 minimum brokerage - and its
minimum position is HIGHER. That is not a contradiction. The floor asks "is this
position large enough that fees have stopped falling", relative to each
schedule's own asymptote, and a cheaper schedule flattens sooner. In absolute
terms a RM 3,000 Bursa trade costs 55 bps round trip through moomoo and the
equivalent through the venue card costs more.

The single largest line on every one of them is **Malaysian stamp duty**, RM1
per RM1,000 - 10 bps a side, 20 round trip - which this Malaysian broker charges
on US and Hong Kong trades as well as Bursa ones. It does not fall with size
until a RM1,000,000 trade, so no floor below 20 bps is reachable on any market.
It is also the reason the US figure moved from 204.9 to 226.6 bps on a USD 100
position: nothing here knew about it until the real card was read.

Two legs on XNAS are charged per SHARE, so its minimum is a function of price -
about USD 2,431 at USD 100 a share and USD 7,704 at USD 10.

### Three entries that contradict the intuition

**Hong Kong is the most expensive market here**, at 95 bps, despite being the
most developed after the US. Retail brokerage at 0.25% is 50 bps round trip on
its own, and stamp duty is 0.1% on **both** sides with **no cap** — unlike
Bursa's MYR 1,000. Cost never falls below ~72 bps at any size. Sorting markets
by how developed they are gets the cost ranking backwards.

**Tokyo's fee table lies.** The round trip is ~40 bps, but a JPY 4,000 stock
ticks in JPY 5 — 12.5 bps of spread per tick, against ~0.5 bps on a USD 200 US
name. The floor is set above the fee asymptote because the fee asymptote is not
what a Tokyo position costs to enter and leave.

**T+1 is not a US exclusive.** XNAS has settled T+1 since May 2024, and so does
XNSE. A test that asserted India was uniquely fast was wrong.

### One-way charges

`FeeLeg.per_side` is `False` for charges a round trip pays **once**:

| Market | One-way charge | Side |
|---|---|---|
| `XLON` | Stamp Duty Reserve Tax, ~50 bps | buy only |
| `XTAI` | 0.3% transaction tax, 30 bps | sell only |
| `XKRX` | securities transaction tax | sell only |
| `XNSE` | stamp duty | buy only |

`FeeSchedule.round_trip` doubles only the legs with `per_side=True`:

```python
def round_trip(self, consideration):
    return sum(leg.charge(c) for leg in self.legs if leg.per_side) * 2 + self.one_way(c)
```

**The field was declared and never read.** Doubling everything reads as prudence
and is not: it put London's floor 50 bps too high, and an overstated floor
refuses positions that would have cleared the real one.

Taiwan's tax being sell-side also means the cost is paid on **exit**, so a
position never closed never pays it. That is a bad reason to hold, and it is
named in the code so nobody rediscovers it as a feature.

### The alias map

Instrument ids and MICs are not the same string.

| Written as | Resolves to |
|---|---|
| `MYX`, `KLSE` | `XKLS` |
| `NASDAQ` | `XNAS` |
| `SGX` | `XSES` |
| `HKEX`, `SEHK` | `XHKG` |
| `XJPX`, `TSE`, `TYO` | `XTKS` |
| `LSE` | `XLON` |
| `ASX` | `XASX` |
| `NSE` | `XNSE` |
| `TWSE` | `XTAI` |
| `KRX`, `KOSPI` | `XKRX` |
| `XETRA` | `XETR` |

**This map exists because of a real silent-wrong-answer bug.** Ids throughout
the repository say `MYX:1155`; the adapter MIC is `XKLS`. Nothing mapped between
them, so `cost_floor_bps("MYX")` missed the table and quietly returned the
30 bps *default* instead of Bursa's 60. Every Bursa position was sized against a
floor half the real one, and nothing crashed.

`XJPX` is in the map for the same reason one layer along: docs/06 wrote `XJPX`
while `core/market/feed.py` already wrote `XTKS` — the same doc-versus-code
drift, caught before it cost anything.

---

## The MYR boundary

`BASE_CURRENCY = "MYR"`. The book is in ringgit. A market is not.

### What speaks which currency

| Native to the market | Native to the book |
|---|---|
| `price` | `investable` (portfolio capital) |
| `lot_size` | `risk`, `kelly`, `concentration` caps |
| `round_trip_cost_at` and every fee minimum | every portfolio weight |
| `adv_20d`, and the `liquidity` cap from it | every number reported to the holder |
| `cost_floor` | |

These two columns met in three places with nothing naming either side. See
`07-DEFECT-LOG.md` §4 for the full account and the magnitudes; the mechanism is
below.

### The mechanism

```python
CapSet(risk, kelly, concentration, liquidity, cost_floor, currency="USD")
```

1. **`CapSet.currency` names the one currency all five caps are in.** A non-ISO
   value raises `CurrencyMismatch`. `binding()` is a `min()` across the five, so
   this is the field that makes a mixed-unit comparison unconstructable rather
   than undetectable.

2. **The market's currency is read off its adapter**, via
   `markets.registry.market_currency(mic)` — never taken as an argument. A
   caller who can pass it can pass it wrong.

3. **Sizing happens entirely in the market's currency.** That is where lots,
   ticks and fee minimums are meaningful. The portfolio converts *into* it once,
   on the way in.

4. **Only the result crosses back to MYR**, once, on the way out.
   `SizingDecision` carries `currency` and `target_value_base`.

5. **Crossing without an explicit dated rate raises.** `to_base` routes through
   `Money.convert`, so the non-positive-rate rule and the same-currency-identity
   rule are decided in one place.

```
MYR portfolio ──to_quote(rate)──▶ caps in USD ──▶ size() in USD ──to_base(rate)──▶ MYR result
                                   lots, ticks, fee minimums
```

### What the user sees

Native always; the MYR equivalent alongside only when it is a different number.

```
$ python ask.py size XNAS:NVDA --portfolio 500000 --lot 1 \
    --price 180 --stop 165 --adv 30000000000 --fx 4.20

fx        1 USD = MYR 4.2
  binding cap is concentration at USD 9,523.81 = MYR 40,000.00
  -> 52 units (USD 9,360.00 = MYR 39,312.00) in lots of 1
```

Without `--fx`, the command refuses and says why. Printing
`MYR 40,000 (MYR 40,000)` for a Bursa name would train the reader to skip the
parenthesis, so it is suppressed.

### The stress probe

`stress/run.py` sizes an 8% slice of a MYR 500,000 book on **every** registered
market and checks the answer back in MYR.

Ten of eleven land on **RM 40,000 exactly**. London refuses — at RM 40,000 an
LSE round trip is 75 bps against a 75 bps floor, so stamp duty makes an 8% slice
of this book marginally sub-economic there.

That last one is a true fact about the market, surfaced by the probe rather than
asserted by it. It is also the shape of answer this system is built to give: not
a position, and a reason.

### Where FX rates come from

They do not, yet. `core/market/prices.FxStore` takes explicit dated rates with
`add(base, quote, d, rate)` and serves them with `rate_asof()` — bisect lookup
plus an inverse-pair fallback — but **no feed populates it**. Today the rate is
supplied per call, by whoever passes `--fx` or `fx_myr_per_unit`.

`config.toml` carries `fx_myr_per_usd = 4.15` for **cost estimates only**, and
says so. Real conversions carry their own dated rate.

Wiring a rate source is listed in `10-STATUS-AND-GAPS.md`.

---

## Adding a market

One adapter class plus one registry entry. The `MarketAdapter` ABC requires
`mic`, `country`, `currency`, `tier`, `regulator`, `local_index`, a
`fee_schedule`, `lot_size()`, `tick_size()`, `settlement_days` and
`withholding()`.

`regulator` is on the ABC rather than in a side table so a market added later
cannot forget it — the same reason `country` and `currency` are there.
`REGULATED_BY` edges in the knowledge graph are `EXTRACTED` from that field.
