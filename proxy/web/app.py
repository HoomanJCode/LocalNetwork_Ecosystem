"""Proxy web admin panel (DESIGN.md §6, Phase 22).

Access: ``http://localhost:54010`` (default).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from aiohttp import web

log = logging.getLogger("localnetwork.proxy.web")

APP_START_TIME = time.time()


def create_app(
    upstream_pool: Any = None,
    health_monitor: Any = None,
    cache_manager: Any = None,
    status_collector: Any = None,
) -> web.Application:
    """Create the proxy admin web application."""

    app = web.Application()

    app["upstream_pool"] = upstream_pool
    app["health_monitor"] = health_monitor
    app["cache_manager"] = cache_manager
    app["status_collector"] = status_collector
    app["start_time"] = APP_START_TIME

    # Jinja2
    import jinja2

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app["jinja_env"] = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=jinja2.select_autoescape(["html"]),
    )

    # Routes
    app.router.add_get("/", dashboard)
    app.router.add_get("/upstreams", upstreams_page)
    app.router.add_get("/cache", cache_page)
    app.router.add_get("/config", config_page)
    app.router.add_get("/logs", logs_page)
    app.router.add_get("/api/dashboard", api_dashboard)

    return app


def _render(request: web.Request, template_name: str, **context) -> web.Response:
    env = request.app["jinja_env"]
    template = env.get_template(template_name)
    html = template.render(**context, active_page=template_name.replace(".html", ""))
    return web.Response(text=html, content_type="text/html")


async def dashboard(request: web.Request) -> web.Response:
    return _render(request, "dashboard.html", panel_name="Proxy Admin")


async def upstreams_page(request: web.Request) -> web.Response:
    return _render(request, "upstream.html", panel_name="Proxy Admin")


async def cache_page(request: web.Request) -> web.Response:
    return _render(request, "cache.html", panel_name="Proxy Admin")


async def config_page(request: web.Request) -> web.Response:
    return _render(request, "config.html", panel_name="Proxy Admin")


async def logs_page(request: web.Request) -> web.Response:
    return _render(request, "logs.html", panel_name="Proxy Admin")


async def api_dashboard(request: web.Request) -> web.Response:
    sc = request.app.get("status_collector")
    return web.json_response(sc.get_stats() if sc else {})
