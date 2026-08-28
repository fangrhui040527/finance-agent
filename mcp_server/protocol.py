"""MCP stdio transport: JSON-RPC 2.0 over stdin/stdout, no dependencies.

Why hand-rolled rather than the `mcp` SDK. This repository's hard constraint is
that everything runs and is tested with no network and no keys, on two runtime
dependencies. MCP's stdio transport is line-delimited JSON-RPC - small enough
that implementing it keeps CI dependency-free, which matters more here than the
convenience of a client library. The same reasoning that put urllib in
`knowledge/feeds/adapter.py` instead of an SDK.

The seam is `Tool` and `Server.dispatch`: if this is ever swapped for the
official SDK, the tool functions do not change.

One property is load-bearing and is tested: **a tool that raises returns a
JSON-RPC error, and a tool that refuses returns a REFUSAL as content**. The two
are different. A guardrail denial is an answer - the correct one - and must reach
the model as text it can reason about, not as a transport error it may retry.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC error codes, plus the one MCP adds.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """The call could not be attempted: bad arguments, unknown instrument.

    Distinct from a REFUSAL, which is a successful answer meaning "no".
    """


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., str]

    def as_json(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.schema}


@dataclass
class Server:
    name: str
    version: str
    instructions: str = ""
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(self, name: str, description: str, schema: dict):
        def register(fn):
            self.tools[name] = Tool(name, description, schema, fn)
            return fn
        return register

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, req: dict) -> dict | None:
        """One request -> one response, or None for a notification."""
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        # A notification has no id and takes no response. Answering one is a
        # protocol violation that some clients treat as a fatal error.
        if rid is None and isinstance(method, str) and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                result = self._initialize()
            elif method == "tools/list":
                result = {"tools": [t.as_json() for t in self.tools.values()]}
            elif method == "tools/call":
                result = self._call(params)
            elif method == "ping":
                result = {}
            else:
                return _error(rid, METHOD_NOT_FOUND, f"unknown method {method!r}")
        except ToolError as e:
            return _error(rid, INVALID_PARAMS, str(e))
        except RecursionError:
            # Caught by name before the generic handler: formatting a traceback
            # for a recursion error can itself recurse.
            return _error(rid, INVALID_PARAMS, "arguments are nested too deeply")
        except Exception as e:                       # never kill the loop
            return _error(rid, INTERNAL_ERROR,
                          f"{type(e).__name__}: {e}",
                          data={"traceback": traceback.format_exc(limit=4)})
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _initialize(self) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
            "instructions": self.instructions,
        }

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}; have {sorted(self.tools)}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise ToolError(f"arguments must be an object, got {type(args).__name__}")

        _check_required(tool, args)
        try:
            text = tool.fn(**args)
        except ToolError:
            raise
        except TypeError as e:
            # An unexpected keyword is the caller's error, not ours.
            raise ToolError(f"{name}: {e}") from e
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # -- the loop ------------------------------------------------------------

    def serve(self, stdin=None, stdout=None) -> int:
        """Read requests until EOF. Injectable streams so this is testable."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                _write(stdout, _error(None, PARSE_ERROR, f"invalid JSON: {e}"))
                continue
            except RecursionError:
                # Nesting depth is client-controlled. json.loads raises this
                # rather than JSONDecodeError, and an uncaught one ends the
                # session - a client can hang up the server with one line.
                _write(stdout, _error(None, PARSE_ERROR,
                                      "request nesting is too deep to parse"))
                continue
            if not isinstance(req, dict):
                _write(stdout, _error(None, INVALID_REQUEST, "request must be an object"))
                continue
            resp = self.dispatch(req)
            if resp is not None:
                _write(stdout, resp)
        return 0


def _check_required(tool: Tool, args: dict) -> None:
    required = tool.schema.get("required") or []
    missing = [r for r in required if r not in args]
    if missing:
        raise ToolError(f"{tool.name}: missing required argument(s) {missing}")


def _error(rid: Any, code: int, message: str, data: dict | None = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def _write(stream, payload: dict) -> None:
    stream.write(json.dumps(payload) + "\n")
    stream.flush()
