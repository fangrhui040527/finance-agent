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

## 5. Setting it up — the checklist

### First, what you do NOT do

- ❌ **You do not log in from the command line.** There is no `login` command
  here and there never will be. Logging in happens in moomoo's own program.
- ❌ **You do not put your moomoo password in `.env`, `config.toml`, or
  anywhere in this repository.** `MoomooAccountFeed` takes no credential
  argument and `tests/test_broker_readonly.py` fails if one is ever added.
- ❌ **You do not give this system your trading password.** That password is
  what unlocks order placement. Never typing it here is precisely why this
  connection cannot trade (§3).

The login lives in OpenD, on your machine, under your control. This system
connects to OpenD, not to moomoo.

### The steps

**1. Enable the API on your moomoo account**

In the moomoo app or on their website, find the developer / OpenAPI section and
enable it for your account. Some markets require agreeing to separate terms.

*If you cannot find it, this is the step to ask moomoo support about — it is
their setting, not ours.*

**2. Download and install OpenD**

OpenD is moomoo's gateway program. It comes in two forms: a windowed version
and a command-line one. **Take the windowed version** — it is easier to log
into and easier to see the state of.

Get it from moomoo's own developer download page. Do not install a build of it
from anywhere else.

**3. Start OpenD and log in — this is the login step**

Open OpenD and sign in with your normal moomoo account. It will ask for the
usual second factor. OpenD holds that session; nothing else needs it.

Leave OpenD **running**. Close it and `ask.py positions` stops working, which
is the correct behaviour: no gateway, no read.

**4. Confirm OpenD is listening**

It should be on `127.0.0.1` port `11111` — the default this code uses. `127.0.0.1`
means "this machine only": OpenD is not reachable from the internet.

If OpenD shows a different port, pass it through when you construct the feed
rather than changing OpenD.

**5. Install the optional package**

```bash
uv add futu-api
```

Not installed by default: it pulls in pandas and protobuf, and this system runs
offline and keyless without them.

**6. Read the account**

```bash
python ask.py positions              # your Bursa account
python ask.py positions --market US  # your US account
```

### What each outcome means

| what you see | what it means | what to do |
|---|---|---|
| a list of holdings | it worked | paste the `holdings = [...]` line into `config.toml` |
| `holdings none. The account was read and holds nothing.` | the connection worked; the account is empty | nothing — this is correct for an unfunded account |
| `account unavailable: ... futu-api` | step 5 not done | `uv add futu-api` |
| `account unavailable: could not reach the moomoo gateway` | OpenD is not running, not logged in, or on another port | go back to step 3 |
| `no instrument mapping for broker market 'XX'` | your account holds a market this system has no mapping for | tell me the code and I will add it to `MARKET_PREFIXES` |

### If something else goes wrong

**Remember this has never run against a real gateway.** The first real run is
the real test. If moomoo returns a column name this code does not expect, the
one place to fix it is `MoomooAccountFeed._positions` in
`core/broker/moomoo.py` — it is the only method that knows moomoo's own
vocabulary. Send me the error and I will correct it.

One known possibility: OpenD can be configured to require an **encrypted**
connection, in which case the SDK needs an RSA key file
(`SysConfig.set_init_rsa_file`). This code does not set one, so if you have
turned that on in OpenD, either turn it off for local use or tell me and I will
add the option.

### Confidence

**Verified**, read from the `futu-api` 10.10.7008 package source: the default
address `127.0.0.1:11111`, the connection class, the methods and columns used,
`TrdMarket.MY` and `Currency.MYR` support, and the optional RSA setting.

**Not verified**, because moomoo's site is unreachable from the environment
this was written in: where exactly OpenD is downloaded, what its screens look
like, and whether your specific Malaysian account tier has API access enabled.
Steps 1 and 2 are therefore a description of the shape of the task, not a
click-by-click guide. If moomoo's actual flow differs, trust moomoo.

## 6. Limits

- **Reads, never writes.** You paste the holdings line yourself.
- **One market per call.** `--market MY` or `--market US`.
- **Short positions are refused**, not reported: nothing downstream models one.
- **A missing number is refused, never zeroed.** Brokers return `N/A` where a
  figure is unavailable, and a zero cost basis reads as a free position.
- **No background sync.** It runs when you run it.
