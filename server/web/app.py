"""Server web admin panel — aiohttp application.

DESIGN.md §6.1: Web dashboard for monitoring and configuring the mediation
server. Runs alongside the server in the same process.

Access: ``http://<server-host>:54001`` (default).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from aiohttp import web

from server.web.auth import (
    auth_middleware,
    get_admin_credentials,
    login_handler,
    login_page,
    logout_handler,
)

log = logging.getLogger("localnetwork.server.web")

APP_START_TIME = time.time()


def create_app(
    client_registry: Any = None,
    network_manager: Any = None,
    relay_forwarder: Any = None,
) -> web.Application:
    """Create and configure the server admin web application.

    Args:
        client_registry: The server's ClientRegistry instance.
        network_manager: The server's NetworkManager instance.
        relay_forwarder: The server's RelayForwarder instance.
    """
    app = web.Application(middlewares=[auth_middleware])

    # Store references for route handlers
    app["client_registry"] = client_registry
    app["network_manager"] = network_manager
    app["relay_forwarder"] = relay_forwarder
    app["admin_credentials"] = get_admin_credentials()
    app["start_time"] = APP_START_TIME

    # Jinja2 setup
    import jinja2

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    app["jinja_env"] = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=jinja2.select_autoescape(["html"]),
    )

    # Routes
    app.router.add_get("/", dashboard)
    app.router.add_get("/clients", clients_page)
    app.router.add_get("/clients/{client_id}", client_detail)
    app.router.add_get("/networks", networks_page)
    app.router.add_get("/networks/{network_id}", network_detail)
    app.router.add_get("/relay", relay_page)
    app.router.add_get("/config", config_page)
    app.router.add_post("/config", config_save)
    app.router.add_get("/logs", logs_page)
    app.router.add_get("/access", access_page)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_handler)
    app.router.add_get("/logout", logout_handler)

    # API endpoints
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/clients", api_clients)
    app.router.add_get("/api/networks", api_networks)
    app.router.add_get("/api/relay", api_relay)
    app.router.add_get("/api/logs/stream", api_logs_stream)

    # Static files
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.router.add_static("/static/", static_dir)

    return app


# ---- Helper ----------------------------------------------------------------
def _render(request: web.Request, template_name: str, **context) -> web.Response:
    """Render a Jinja2 template."""
    env = request.app["jinja_env"]
    template = env.get_template(template_name)
    html = template.render(**context, active_page=template_name.replace(".html", ""))
    return web.Response(text=html, content_type="text/html")


def _uptime() -> str:
    """Human-readable uptime string."""
    seconds = int(time.time() - APP_START_TIME)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


# ---- Pages ----------------------------------------------------------------
async def dashboard(request: web.Request) -> web.Response:
    return _render(request, "dashboard.html", panel_name="Server Admin", uptime=_uptime())


async def clients_page(request: web.Request) -> web.Response:
    registry = request.app.get("client_registry")
    clients = []
    if registry is not None:
        for cid, info in registry.list_all().items():
            clients.append({
                "id": cid,
                "status": "online" if info.get("online") else "offline",
                "connected_since": info.get("connected_since", "—"),
                "networks": len(info.get("networks", [])),
            })
    return _render(request, "clients.html", panel_name="Server Admin", clients=clients)


async def client_detail(request: web.Request) -> web.Response:
    cid = request.match_info["client_id"]
    registry = request.app.get("client_registry")
    client = None
    if registry is not None:
        client = registry.get(cid)
    return _render(request, "client_detail.html", panel_name="Server Admin", client=client, client_id=cid)


async def networks_page(request: web.Request) -> web.Response:
    nm = request.app.get("network_manager")
    networks = []
    if nm is not None:
        for net in nm.list_networks():
            networks.append({
                "id": net.get("network_id", ""),
                "name": net.get("name", ""),
                "owner": net.get("owner_id", ""),
                "topology": net.get("topology", "mesh"),
                "members": net.get("member_count", 0),
            })
    return _render(request, "networks.html", panel_name="Server Admin", networks=networks)


async def network_detail(request: web.Request) -> web.Response:
    nid = request.match_info["network_id"]
    return _render(request, "network_detail.html", panel_name="Server Admin", network_id=nid)


async def relay_page(request: web.Request) -> web.Response:
    return _render(request, "relay.html", panel_name="Server Admin")


async def config_page(request: web.Request) -> web.Response:
    return _render(request, "config.html", panel_name="Server Admin")


async def config_save(request: web.Request) -> web.Response:
    raise web.HTTPFound("/config")


async def logs_page(request: web.Request) -> web.Response:
    return _render(request, "logs.html", panel_name="Server Admin")


async def access_page(request: web.Request) -> web.Response:
    return _render(request, "access.html", panel_name="Server Admin")


# ---- API endpoints ---------------------------------------------------------
async def api_dashboard(request: web.Request) -> web.Response:
    registry = request.app.get("client_registry")
    nm = request.app.get("network_manager")
    return web.json_response({
        "uptime": _uptime(),
        "clients_online": len(registry.list_online()) if registry else 0,
        "clients_total": len(registry.list_all()) if registry else 0,
        "networks": len(nm.list_networks()) if nm else 0,
    })


async def api_clients(request: web.Request) -> web.Response:
    registry = request.app.get("client_registry")
    if registry is None:
        return web.json_response([])
    return web.json_response(list(registry.list_all().values()))


async def api_networks(request: web.Request) -> web.Response:
    nm = request.app.get("network_manager")
    if nm is None:
        return web.json_response([])
    return web.json_response(list(nm.list_networks()))


async def api_relay(request: web.Request) -> web.Response:
    return web.json_response({"paths": [], "bytes_relayed": 0})


async def api_logs_stream(request: web.Request) -> web.StreamResponse:
    """SSE endpoint for live log tail."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    import asyncio

    try:
        while True:
            await response.write(f"data: {json.dumps({'ts': time.time(), 'msg': 'heartbeat'})}\n\n".encode())
            await asyncio.sleep(10)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response
