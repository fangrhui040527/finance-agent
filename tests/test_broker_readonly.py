"""The account feed may look. It may not touch.

`tests/test_no_execution_anywhere.py` greps the WHOLE repository for a handful
of well-known execution verbs. That is the right net for "nobody bolted a
broker client on", and it is the wrong net for this package: moomoo's own
instruction methods are not called `place_order` in the sense that grep means,
and a future contributor could add `ctx.modify_order(...)` here without
tripping it.

So this file is the specific guard for the one package that holds a live
broker connection. It reads the source and fails if an instruction-shaped call
appears - which is a structural test, like the global one, rather than a
behavioural test that a mock could satisfy while the real thing did otherwise.

THE STRONGEST GUARANTEE IS NOT IN OUR CODE AT ALL
-------------------------------------------------
moomoo's gateway refuses every money-moving instruction until it is unlocked
with the trading password. `core/broker/moomoo.py` never performs that unlock
and takes no password argument. So the connection this system opens is refused
by moomoo's server if it ever tried to trade - enforced on their side, where a
bug in ours cannot reach it. `test_the_module_never_unlocks_trading` is what
keeps that true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "core" / "broker"

#: moomoo's own money-moving surface, read from futu-api 10.10.7008's
#: `futu/trade/open_trade_context.py`. Not a guess at what such a method might
#: be called: these are the real names on the real class this code connects to.
INSTRUCTION_METHODS = (
    "place_order",
    "modify_order",
    "cancel_all_order",
    "unlock_trade",
)

SOURCES = sorted(PKG.glob("*.py"))


def test_the_package_has_sources_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert SOURCES, f"no sources found under {PKG}"


@pytest.mark.parametrize("method", INSTRUCTION_METHODS)
def test_no_instruction_method_is_ever_called(method: str):
    call = f".{method}("
    offenders = [p.name for p in SOURCES if call in p.read_text(encoding="utf-8")]
    assert not offenders, (
        f"{offenders} calls {method}(). This package reads an account and may "
        f"never instruct one. If this is deliberate, it is a decision for the "
        f"account holder, not a passing edit - and it forfeits the guarantee "
        f"that moomoo's own gateway refuses this connection's instructions."
    )


def test_the_module_never_unlocks_trading():
    """The load-bearing one.

    Without an unlock, moomoo's gateway rejects instructions regardless of what
    this code does. That makes the read-only property enforced OUTSIDE this
    repository, which is worth more than any assertion inside it.
    """
    source = (PKG / "moomoo.py").read_text(encoding="utf-8")
    assert ".unlock_trade(" not in source
    assert "password" not in source.lower().replace("takes no password", "").replace(
        "trading password", ""
    ), "moomoo.py may mention a password only to say it never handles one"


def test_the_feed_takes_no_password():
    """A credential has no reason to exist in this repository: the operator
    logs into OpenD themselves and OpenD holds the session. A password
    parameter appearing here would mean that stopped being true."""
    import inspect

    from core.broker.moomoo import MoomooAccountFeed

    params = set(inspect.signature(MoomooAccountFeed.__init__).parameters)
    forbidden = {"password", "password_md5", "pwd", "secret", "token", "api_key"}
    assert not (params & forbidden), f"MoomooAccountFeed takes a credential: {params & forbidden}"


def test_the_public_surface_is_one_read_method():
    """A capability that does not exist cannot be called by mistake, cannot be
    reached by an injected prompt, and cannot be added by accident."""
    from core.broker.account import AccountFeed

    public = {n for n in dir(AccountFeed) if not n.startswith("_")}
    assert public == {"snapshot"}, f"AccountFeed grew a surface beyond reading: {public}"
