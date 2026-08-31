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


def create_app() -> FastAPI:
    from core.logging import configure as configure_logging

    configure_logging()

    app = FastAPI(
        title="FinPlanet - the Analyst Mind",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # the schema surface is web/schemas.py, reviewed, not generated
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # A browser form cannot set a custom header; a hostile page therefore
        # cannot POST here even though we listen on localhost.
        if request.method == "POST":
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
