"""Live-phase instruments: a raw-wire client, and a metered SDK client.

`LiveClient` is deliberately still urllib: it is the instrument that measures
what the API DOES, independent of whichever SDK the product sits on, so a claim
like "the alias resolves to this snapshot" or "a 401 costs nothing" is checked
against the wire rather than against the vendor's own library.

`metered_client` is the product-path half: the SDK backend takes `client=` (any
object with `.messages.create/.stream`), and this wrapper delegates to a REAL
`anthropic.Anthropic` while budget-guarding every request before it is sent and
pricing every response after it returns. It replaced `metered_opener` when the
product moved off urllib.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from qa._support.cheap import CHEAP_MODEL, api_key

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


@dataclass
class LiveResult:
    """Everything a backend might throw away, kept for assertions."""

    status: int
    headers: dict
    payload: dict
    request_body: dict
    elapsed_ms: float = 0.0
    error_body: str = ""

    @property
    def text(self) -> str:
        return "".join(
            b.get("text", "")
            for b in self.payload.get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"
        )

    @property
    def usage(self) -> dict:
        return self.payload.get("usage") or {}

    @property
    def blocks(self) -> list:
        return self.payload.get("content") or []

    def header(self, name: str) -> str | None:
        lower = {str(k).lower(): v for k, v in self.headers.items()}
        return lower.get(name.lower())


def _decode(raw: bytes) -> tuple[dict, str]:
    detail = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = {}
    return (payload if isinstance(payload, dict) else {}), detail


@dataclass
class LiveClient:
    """One POST, all headers kept, never retried.

    Retry behaviour is a property of the code under test, not of this client, so
    this one deliberately does not retry: a 429 comes back as a `LiveResult` with
    `status == 429` and its headers intact, for a test to assert on.
    """

    #: repr=False is not cosmetic. pytest prints every fixture's repr into a
    #: failure report, and a failure report gets pasted into issues.
    key: str = field(default_factory=lambda: api_key() or "", repr=False)
    model: str = CHEAP_MODEL
    timeout: int = 90
    meter: Any = None

    def call(
        self,
        prompt: str | list,
        *,
        system: Any = None,
        max_tokens: int = 64,
        model: str | None = None,
        extra_headers: dict | None = None,
        **body_extra,
    ) -> LiveResult:
        if not self.key:
            raise RuntimeError("no ANTHROPIC_API_KEY; this is a live-phase call")
        if self.meter is not None:
            self.meter.guard()          # refuse BEFORE spending, never after

        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": prompt}]
        )
        body: dict = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            body["system"] = system
        body.update(body_extra)

        headers = {
            "x-api-key": self.key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        headers.update(extra_headers or {})

        req = urllib.request.Request(
            URL, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                payload, _ = _decode(raw)
                result = LiveResult(
                    status=resp.status,
                    headers=dict(resp.headers),
                    payload=payload,
                    request_body=body,
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )
        except urllib.error.HTTPError as e:
            raw = e.read()
            payload, detail = _decode(raw)
            result = LiveResult(
                status=e.code,
                headers=dict(e.headers or {}),
                payload=payload,
                request_body=body,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                error_body=detail,
            )
        if self.meter is not None:
            self.meter.record(result)
        return result


class _MeteredMessages:
    """The `.messages` surface the product's backend drives, budgeted and priced."""

    def __init__(self, inner, meter, record: list | None) -> None:
        self._inner = inner
        self._meter = meter
        self._record = record

    def _account(self, kwargs: dict, message, t0: float) -> None:
        payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else {}
        result = LiveResult(
            status=200,
            headers={},
            payload=payload,
            request_body=dict(kwargs),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
        if self._meter is not None:
            self._meter.record(result)
        if self._record is not None:
            self._record.append(result)

    def _account_error(self, kwargs: dict, exc, t0: float) -> None:
        status = getattr(exc, "status_code", 0) or 0
        result = LiveResult(
            status=int(status),
            headers={},
            payload={},
            request_body=dict(kwargs),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error_body=str(exc),
        )
        if self._meter is not None:
            self._meter.record(result)
        if self._record is not None:
            self._record.append(result)

    def create(self, **kwargs):
        if self._meter is not None:
            self._meter.guard()
        t0 = time.perf_counter()
        try:
            message = self._inner.messages.create(**kwargs)
        except Exception as e:
            self._account_error(kwargs, e, t0)
            raise
        self._account(kwargs, message, t0)
        return message

    def stream(self, **kwargs):
        if self._meter is not None:
            self._meter.guard()
        outer = self

        class _Managed:
            def __enter__(managed):
                managed._t0 = time.perf_counter()
                managed._mgr = outer._inner.messages.stream(**kwargs)
                managed._stream = managed._mgr.__enter__()
                return managed

            def __exit__(managed, *exc):
                return managed._mgr.__exit__(*exc)

            def get_final_message(managed):
                message = managed._stream.get_final_message()
                outer._account(kwargs, message, managed._t0)
                return message

        return _Managed()


class MeteredAnthropic:
    """A real `anthropic.Anthropic`, wrapped: guard before, price after."""

    def __init__(self, meter=None, record: list | None = None, key: str | None = None) -> None:
        import anthropic

        inner = anthropic.Anthropic(api_key=key or api_key(), max_retries=0, timeout=90.0)
        self.messages = _MeteredMessages(inner, meter, record)


def metered_client(meter=None, record: list | None = None, key: str | None = None) -> MeteredAnthropic:
    """For `AnthropicBackend(client=...)`: the live network, metered."""
    return MeteredAnthropic(meter=meter, record=record, key=key)
