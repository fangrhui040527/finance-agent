"""The web app factory. Loopback-only, same engines, same refusals.

Ground rules, none negotiable:

  * **The model contributes no numbers here either.** Every figure on every
    screen is produced by the same tested engines the CLI and MCP server use;
    endpoint `text` is byte-identical to the MCP tool's output for the same
    inputs, and the parity tests hold that equality.
  * **Loopback only.** `web/serve.py` binds 127.0.0.1 and nothing here does
    auth, because there is nothing to authenticate on a single user's own
    machine - but a cross-site form post from a hostile page IS in scope, so
    every POST requires the `X-Requested-With: FinPlanet` header a form
    cannot send.
  * **No transaction UI.** The no-execution grep covers web/static like every
    other directory.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 1_000_000
REQUIRED_POST_HEADER = ("x-requested-with", "FinPlanet")


#: The names this app answers to. `testserver` is the Host Starlette's test
#: client sends; a browser sends it only if the user's own resolver maps that
#: name, which no rebinding page controls.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})


def _origin_host(origin: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(origin).hostname or "").lower()


def _refused(status: int, reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "text": "",
            "data": None,
            "refusal": {"reason": reason},
            "disclaimer": "",
        },
    )


def create_app() -> FastAPI:
    from core.env import load as load_dotenv
    from core.logging import configure as configure_logging

    load_dotenv()
    configure_logging()

    app = FastAPI(
        title="FinPlanet - the Analyst Mind",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # the schema surface is web/schemas.py, reviewed, not generated
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # THE HOST FIRST. Binding to 127.0.0.1 keeps other machines out, not
        # other ORIGINS: a DNS-rebinding page re-points its own name at
        # 127.0.0.1, becomes same-origin with this app, reads every GET (the
        # capital plan, the traces) and can set the custom header below too.
        # Its requests still carry ITS name in Host, so a Host that is not this
        # machine is refused before anything else runs.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ALLOWED_HOSTS:
            return _refused(403, f"REFUSED: Host {host or '(none)'} is not this machine")
        # A browser form cannot set a custom header; a hostile page therefore
        # cannot POST here even though we listen on localhost.
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and _origin_host(origin) not in ALLOWED_HOSTS:
                return _refused(403, f"REFUSED: a POST from {origin} is cross-site")
            name, want = REQUIRED_POST_HEADER
            if request.headers.get(name) != want:
                return JSONResponse(
                    status_code=403,
                    content={
                        "ok": False,
                        "text": "",
                        "data": None,
                        "refusal": {
                            "reason": "REFUSED: POST requires the X-Requested-With: "
                            "FinPlanet header (cross-site form posts cannot send it)"
                        },
                        "disclaimer": "",
                    },
                )
            length = request.headers.get("content-length")
            if length and int(length) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "ok": False,
                        "text": "",
                        "data": None,
                        "refusal": {"reason": "REFUSED: body over 1 MB"},
                        "disclaimer": "",
                    },
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"
        )
        return response

    from web.api import router

    app.include_router(router, prefix="/api")

    from pathlib import Path

    static = Path(__file__).parent / "static"
    if static.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(static), html=True), name="static")

    return app
