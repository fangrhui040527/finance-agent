# 19 — Reading a broker account

The only input to this system that is not public. Prices, news and FX rates all
come from open sources; this one is the operator's own account.

It is **read-only, and the guarantee is not ours**. Read §3 before enabling it.

---

## 1. What it does

`ask.py positions` asks a running moomoo gateway what the account holds and
prints it, with a `holdings = [...]` line you can paste into `config.toml`.

That is the whole feature. It does not write config, does not place orders, and
does not run in the background.

**It deliberately does not write `config.toml`.** The holdings list is an input
to every concentration and rebalancing figure here. A command that rewrote it
silently would change a portfolio with no diff and no decision.

## 2. What it needs

1. **OpenD**, moomoo's gateway program, running on the same machine and logged
   in. moomoo does not expose accounts over the internet; the SDK talks to this
   local program, which holds the session.
2. **The optional package**: `uv add futu-api`. Not a dependency of this
   project — it pulls in pandas and protobuf, and this system's promise is that
   it runs offline and keyless without them.

**No password is stored anywhere in this repository.** You log into OpenD
yourself. `MoomooAccountFeed` takes no credential argument, and
`tests/test_broker_readonly.py` fails if one is ever added.

## 3. Why read-only is more than a promise

moomoo requires an explicit **unlock, with the trading password**, before their
gateway accepts any instruction that moves money. `core/broker/moomoo.py` never
performs that unlock and takes no password.

So the connection this system opens is **refused by moomoo's own server** if it
ever tried to trade. The guarantee lives outside this repository, where a bug in
our code cannot reach it. That is why this was judged safe to build at all.

Three things hold it:

| | |
|---|---|
| `tests/test_broker_readonly.py` | reads `core/broker/*.py` and fails if `place_order`, `modify_order`, `cancel_all_order` or `unlock_trade` is called — the real method names, read from futu-api 10.10.7008 |
| `tests/test_no_execution_anywhere.py` | the repo-wide scan, which `core/broker/` passes like everything else |
| `AccountFeed` | one public method, `snapshot()`. A capability that does not exist cannot be called by mistake or reached by an injected prompt |

## 4. What is verified, and what is not

**Verified** — read from the `futu-api` 10.10.7008 package source, not from
documentation or memory:

- the methods used: `position_list_query()`, `accinfo_query()`
- the columns read: `code`, `qty`, `cost_price`, `market_val`, `currency`, `cash`
- the calling convention: `(ret_code, payload)`, `RET_OK == 0`, payload is an
  error **string** on failure
- code format `MARKET.CODE` — `MY.1155`, `US.NVDA`
- Malaysia is supported: `TrdMarket.MY`, `Currency.MYR`, **real accounts only**
  (simulated MY accounts are not supported by the SDK)

**NOT verified**: this code has never spoken to a running OpenD. It could not —
the environment it was written in has no network route to moomoo and no account
to read. Every test runs against a double.

**So the first real run is the actual test.** Expect to correct something. The
place to correct it is `MoomooAccountFeed._positions`, which is the only method
that knows moomoo's column names.

## 5. Using it

```bash
uv add futu-api            # once
# start OpenD and log in
python ask.py positions              # the MY account
python ask.py positions --market US  # the US one
```

Three outcomes, deliberately distinguishable:

| what you see | what it means |
|---|---|
| a list of holdings | it worked |
| `holdings none. The account was read and holds nothing.` | the account is genuinely empty |
| `account unavailable: ...` (exit 3) | the link failed — **not** an empty account |

That last distinction is the point of the whole error design. An empty holdings
list feeds every concentration measure in the system; a dead gateway that read
as "you own nothing" would silently re-plan a portfolio around a book that does
not exist. A broken source and a quiet one are different answers — the same rule
`knowledge/feeds/adapter.py` sets for news.

## 6. Limits

- **Reads, never writes.** You paste the holdings line yourself.
- **One market per call.** `--market MY` or `--market US`.
- **Short positions are refused**, not reported: nothing downstream models one.
- **A missing number is refused, never zeroed.** Brokers return `N/A` where a
  figure is unavailable, and a zero cost basis reads as a free position.
- **No background sync.** It runs when you run it.
