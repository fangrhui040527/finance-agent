"""The collector seam for structured data, shaped like the news feed seam.

`knowledge/feeds/adapter.FeedAdapter` turns a source into Articles. A
`Collector` turns one into Observations, EventRecords, SeriesPoints and
Documents (`knowledge/facts.py`) - and, for the providers that also carry
news, Articles as well. Same rules, restated because they matter more for
numbers than for prose:

  * **A source that cannot be read RAISES.** `SourceError`, never an empty
    Pull. An empty Pull means "nothing new", and the day the vendor is down
    must not be recorded as a day nothing happened.
  * **A missing key is a SKIP, not a failure.** `KeyMissing` is caught by the
    sweep and recorded as `skipped` with the variable name, so the operator
    reads "FINNHUB_API_KEY is not set" rather than a red job.
  * **A plan boundary is a SKIP too.** Free tiers return 402/403 or a polite
    JSON error for premium endpoints. `PlanExcluded` records which endpoint
    the plan does not include; the rest of the pull still lands.
  * **Everything is typed on the way in.** A vendor's float becomes a Decimal
    or is dropped at the seam; a date that does not parse drops its row, never
    the pull. Nothing downstream sees raw JSON.

Transport reuses `core.net.retry.with_retry` (honours Retry-After, retries only
what waiting fixes) and a per-source `CircuitBreaker`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

from core.net.breaker import CircuitBreaker, CircuitOpen
from core.net.retry import with_retry
from knowledge.facts import Document, EventRecord, Observation, SeriesPoint
from knowledge.news.features import Article

USER_AGENT = "finplanet-analyst-mind/0.1 (personal research)"

#: Every environment variable a collector reads, in one place. `.env.example`
#: documents these and tests/test_config.py refuses a documented key nothing
#: reads - the class of defect where a key sets nothing and reads as configured.
PROVIDER_KEYS: tuple[str, ...] = (
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "ALPHAVANTAGE_API_KEY",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
    # Optional: FinMind answers 300 requests an hour without it and 600 with.
    "FINMIND_TOKEN",
    # Optional: EODHD statements, 2 names a day on the free plan; the collector skips without it.
    "EODHD_API_KEY",
)


def configured_keys() -> dict[str, bool]:
    """Which provider variables are set, without revealing their values."""
    return {name: bool(os.environ.get(name, "").strip()) for name in PROVIDER_KEYS}


class SourceError(RuntimeError):
    """The source could not be read, or answered with something unusable."""


class KeyMissing(SourceError):
    """The environment variable holding this source's key is not set."""


class PlanExcluded(SourceError):
    """The key works, but the plan does not include this endpoint."""


@dataclass
class Pull:
    """What one collector produced. Empty lists are 'nothing new', not failure."""

    observations: list[Observation] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    series: list[SeriesPoint] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    #: Endpoints skipped inside an otherwise successful pull, with the reason.
    notes: list[str] = field(default_factory=list)
    requests: int = 0

    @property
    def fetched(self) -> int:
        return (
            len(self.observations)
            + len(self.events)
            + len(self.series)
            + len(self.documents)
            + len(self.articles)
        )

    def extend(self, other: Pull) -> None:
        self.observations += other.observations
        self.events += other.events
        self.series += other.series
        self.documents += other.documents
        self.articles += other.articles
        self.notes += other.notes
        self.requests += other.requests

    def __str__(self) -> str:
        parts = []
        for label, rows in (
            ("observations", self.observations),
            ("events", self.events),
            ("series points", self.series),
            ("documents", self.documents),
            ("articles", self.articles),
        ):
            if rows:
                parts.append(f"{len(rows)} {label}")
        return ", ".join(parts) or "nothing new"


class Collector(ABC):
    """One structured source. Subclass, implement `collect`, inherit transport."""

    name: str = "abstract"
    key_env: str | None = None
    TIMEOUT = 30
    RETRY_BASE_SECONDS = 1.0

    def __init__(
        self,
        opener: Callable | None = None,
        sleep: Callable | None = None,
        key: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._opener = opener
        self._sleep = sleep
        self._key = key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._breaker = CircuitBreaker(self.name)
        self.requests = 0

    # -- keys ------------------------------------------------------------------

    @property
    def key(self) -> str:
        if self._key:
            return self._key
        if self.key_env is None:
            return ""
        value = os.environ.get(self.key_env, "").strip()
        if not value:
            raise KeyMissing(
                f"{self.name} needs {self.key_env}, which is not set. Add it to .env or as a "
                f"GitHub Actions secret of the same name; until then this source is skipped."
            )
        return value

    def today(self) -> date:
        return self._clock().date()

    # -- transport ---------------------------------------------------------------

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        """GET, retry what waiting fixes, decode JSON. Raises on anything else."""
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        opener = self._opener or urllib.request.urlopen
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})

        def _transport() -> bytes:
            with opener(req, timeout=self.TIMEOUT) as resp:
                return resp.read()

        self.requests += 1
        try:
            self._breaker.before_call()
            if self._sleep is not None:
                body = with_retry(_transport, base=self.RETRY_BASE_SECONDS, sleep=self._sleep)
            else:
                body = with_retry(_transport, base=self.RETRY_BASE_SECONDS)
        except CircuitOpen as e:
            raise SourceError(str(e)) from e
        except urllib.error.HTTPError as e:
            if e.code in (401,):
                raise SourceError(f"{self.name}: the key was refused (HTTP 401)") from e
            if e.code in (402, 403):
                raise PlanExcluded(
                    f"{self.name}: HTTP {e.code} for {_redact(url)} - the plan does not include it"
                ) from e
            self._breaker.record_failure(e)
            raise SourceError(f"{self.name} fetch failed: HTTP {e.code} for {_redact(url)}") from e
        except (urllib.error.URLError, OSError) as e:
            self._breaker.record_failure(e)
            raise SourceError(f"{self.name} fetch failed: {e}") from e
        self._breaker.record_success()

        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        stripped = text.lstrip()
        if stripped[:1] not in ("{", "["):
            raise SourceError(f"{self.name} returned non-JSON: {stripped[:160]!r}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise SourceError(f"{self.name} returned malformed JSON: {stripped[:160]!r}") from e

    # -- the contract --------------------------------------------------------------

    @abstractmethod
    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        """Everything new since `since` for these instruments. Raises, never []."""


def _redact(url: str) -> str:
    """A URL safe to put in a log line: the key parameter is blanked."""
    import re

    return re.sub(r"(apikey|token|api_key)=[^&]+", r"\1=***", url, flags=re.IGNORECASE)


def local_code(instrument_id: str) -> str:
    """`XNAS:AAPL` -> `AAPL`, `MYX:1155` -> `1155`."""
    return instrument_id.split(":", 1)[1] if ":" in instrument_id else instrument_id


def parse_date(raw) -> date | None:
    """A vendor date in any of the shapes seen so far, or None."""
    if raw in (None, "", 0):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_datetime(raw) -> datetime | None:
    """Like parse_date, but keeps the time when the vendor gave one. UTC."""
    if raw in (None, "", 0):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt).replace(
                tzinfo=UTC
            )
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        d = parse_date(s)
        return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None
