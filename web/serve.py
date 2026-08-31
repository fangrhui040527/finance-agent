"""`python -m web.serve` - the app on loopback, and nowhere else."""

from __future__ import annotations

HOST = "127.0.0.1"
PORT = 8765


def main() -> int:
    import uvicorn

    from core.logging import configure

    configure()
    from web.app import create_app

    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
