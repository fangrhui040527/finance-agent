"""A per-host circuit breaker for free endpoints with no SLA.

When GDELT or a price source starts failing, every further call in the same
run pays the full timeout to learn the same fact. After `failures` consecutive
failures the breaker OPENS and calls fail fast with the cached reason until
`cooldown_s` has passed, when one probe is allowed through.

What does NOT trip it: `NoData` and `SymbolUnmappable` - those are answers
about coverage, not about the host's health, and tripping on them would let
one unknown ticker take a healthy source down for everything else.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitOpen(RuntimeError):
    """The breaker is open; the host was failing and the cooldown has not passed."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failures: int = 5,
        cooldown_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.threshold = failures
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._consecutive = 0
        self._opened_at: float | None = None
        self._last_error = ""

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self.cooldown_s:
            return False  # half-open: the next call is the probe
        return True

    def before_call(self) -> None:
        if self.is_open:
            remaining = self.cooldown_s - (self._clock() - (self._opened_at or 0.0))
            raise CircuitOpen(
                f"{self.name} breaker open after {self._consecutive} consecutive failures "
                f"(last: {self._last_error}); retry in {remaining:.0f}s"
            )

    def record_success(self) -> None:
        self._consecutive = 0
        self._opened_at = None
        self._last_error = ""

    def record_failure(self, error: BaseException) -> None:
        self._consecutive += 1
        self._last_error = f"{type(error).__name__}: {error}"
        if self._consecutive >= self.threshold:
            self._opened_at = self._clock()
