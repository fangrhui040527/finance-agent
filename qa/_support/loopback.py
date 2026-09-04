"""A real listener that speaks the OpenAI `chat/completions` wire format.

The scripted opener in `qa/_support/http.py` proves what a backend does with a
response it is handed. This proves what it does on a socket: the request is
built by `urllib`, travels over TCP to 127.0.0.1, and the reply comes back with
real headers, a real `Content-Length` and, when the script says so, a
connection dropped mid-flight. Nothing here patches the product; the backend
under test is given a base URL and nothing else.

    with LoopbackProvider() as srv:
        srv.say(ok("hello"))                # one 200
        srv.say(status(429, retry_after=2)) # then a 429 with the header
        srv.drop()                          # then the connection is cut
        ... point LLM_BASE_URL at srv.base_url and call the product ...
        srv.hits[0]["body"]["model"]        # what actually crossed the wire

Every step is consumed once, in order. A request that arrives after the script
is exhausted is answered 500 with a body saying so, and recorded like any
other, so an unexpected retry shows up in `hits` rather than as a hang.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DROP = object()


def ok(
    text: str = "ok",
    *,
    prompt_tokens: int = 12,
    completion_tokens: int = 7,
    cached_tokens: int = 0,
    finish_reason: str = "stop",
    model: str = "qa-model",
    request_id: str = "chatcmpl-qa-1",
) -> tuple[int, dict, bytes]:
    """A well-formed success payload in the OpenAI shape."""
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cached_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    body = {
        "id": request_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8")


def status(
    code: int, message: str = "", *, retry_after: float | None = None, raw: bytes | None = None
) -> tuple[int, dict, bytes]:
    """An error status with an OpenAI-style error object, or a raw body."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    if raw is not None:
        headers["Content-Type"] = "text/html"
        return code, headers, raw
    body = {"error": {"message": message or f"status {code}", "type": "qa", "code": code}}
    return code, headers, json.dumps(body).encode("utf-8")


@dataclass
class _Script:
    steps: list = field(default_factory=list)
    hits: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Any

    def log_message(self, *_args) -> None:  # quiet
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        script: _Script = self.server.script
        with script.lock:
            script.hits.append(
                {"path": self.path, "headers": {k: v for k, v in self.headers.items()}, "body": body}
            )
            step = script.steps.pop(0) if script.steps else None
        if step is None:
            step = status(500, "loopback script exhausted: an unexpected request")
        if step is DROP:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        code, headers, payload = step
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("x-request-id", "qa-req-1")
        self.end_headers()
        self.wfile.write(payload)


class LoopbackProvider:
    """A `chat/completions` endpoint on 127.0.0.1, scripted per request."""

    def __init__(self) -> None:
        self._script = _Script()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.script = self._script  # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle ----------------------------------------------------------------

    def __enter__(self) -> LoopbackProvider:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- addressing ---------------------------------------------------------------

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    # -- the script ---------------------------------------------------------------

    def say(self, *steps: tuple[int, dict, bytes]) -> LoopbackProvider:
        with self._script.lock:
            self._script.steps.extend(steps)
        return self

    def drop(self) -> LoopbackProvider:
        with self._script.lock:
            self._script.steps.append(DROP)
        return self

    @property
    def hits(self) -> list[dict]:
        with self._script.lock:
            return list(self._script.hits)

    def header(self, i: int, name: str) -> str | None:
        lower = {k.lower(): v for k, v in self.hits[i]["headers"].items()}
        return lower.get(name.lower())


def closed_port() -> int:
    """A port on 127.0.0.1 that was just bound and released: nothing listens there."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
