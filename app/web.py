"""Tiny aiohttp app: /health for Railway/Docker health checks (+ the webhook route in webhook mode)."""
from __future__ import annotations

from aiohttp import web

from app.container import Container


def build_app(container: Container) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        if await container.db.ping():
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "database_unavailable"}, status=503)

    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    return app
