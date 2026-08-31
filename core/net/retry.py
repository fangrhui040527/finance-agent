"""Bounded retry with jitter, for the two keyless feeds.

The Anthropic path retries inside its backend; Stooq and GDELT had none, so a
single dropped packet read as an outage. Rules, matching what the live QA pass
pinned for the model backend:

  * Retry ONLY what waiting can fix: connection errors and 408/425/429/5xx/529.
    400/401/403/404 are answered questions - retrying them burns quota and time.
  * **Honour Retry-After** when the server says when, capped at 60s. The server
    knows; exponential guessing over the top of it is rude and slower.
  * Exponential backoff with jitter otherwise, so parallel legs do not retry in
    lockstep against the same free endpoint.
  * The last error is raised, never masked. A retry loop that swallows the
    final failure turns an outage into silence.
"""

from __future__ import annotations

import random
import time
import urllib.error
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
RETRY_AFTER_CAP_S = 60.0


def retry_after_seconds(error: urllib.error.HTTPError) -> float | None:
    """The server's own wait, in seconds, capped - or None if it named none."""
    raw = (error.headers or {}).get("Retry-After") if error.headers is not None else None
    if raw is None:
        return None
    try:
        return min(float(raw), RETRY_AFTER_CAP_S)
    except ValueError:
        return None  # an HTTP-date Retry-After is legal; treat as unnamed


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base: float = 0.5,
    cap: float = 8.0,
    jitter: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    retryable: Callable[[BaseException], bool] | None = None,
) -> T:
    """Run `fn`, retrying transient transport failures. Raises the last error.

    `retryable` may veto a retry for errors that look transient but are not -
    the Stooq HTML wall arrives as a 200 and a deterministic refusal; retrying
    it just burns time.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in RETRY_STATUSES or (retryable and not retryable(e)):
                raise
            if attempt == attempts:
                raise
            named = retry_after_seconds(e)
            delay = named if named is not None else min(cap, base * 2 ** (attempt - 1))
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last = e
            if retryable and not retryable(e):
                raise
            if attempt == attempts:
                raise
            delay = min(cap, base * 2 ** (attempt - 1))
        if jitter:
            delay += random.uniform(0, delay * 0.1)
        sleep(delay)
    raise last if last is not None else RuntimeError("retry loop exited without a result")
