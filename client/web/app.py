"""Client web admin panel — aiohttp application.

DESIGN.md §6.2: Local web dashboard for managing the client — networks, peers,
tunnels, services, and settings. Bound to 127.0.0.1 only.

Access: ``http://localhost:54002`` (default).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from aiohttp import web

log = logging.getLogger("localnetwork.client.web")

APP_START_TIME = time.time()


def create_app(
    daemon: Any = None,
    control_channel: Any = None,
    tunnel_manager: Any = None,
    nat_traversal: Any = None,
) -> web.Application:
    """Create and configure the client admin web application.

    Args:
        daemon: The ClientDaemon instance.
        control_channel: The ControlChannel instance.
        tunnel_manager: The TunnelManager instance.
        nat_traversal: The NatTraversal instance.
    """
    app = web.Application()

    # Store references
    app["daemon"] = daemon
    app["control_channel"] = control_channel
    app["tunnel_manager"] = tunnel_manager
    app["nat_traversal"] = nat_traversal
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
    app.router.add_get("/networks", networks_page)
    app.router.add_get("/peers", peers_page)
    app.router.add_get("/peers/{peer_id}", peer_detail)
    app.router.add_get("/services", services_page)
    app.router.add_get("/config", config_page)
    app.router.add_post("/config", config_save)
    app.router.add_get("/logs", logs_page)
    app.router.add_get("/nat-diag", nat_diag_page)

    # API endpoints
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/peers", api_peers)
    app.router.add_get("/api/tunnels", api_tunnels)
    app.router.add_get("/api/logs/stream", api_logs_stream)

    return app


def _render(request: web.Request, template_name: str, **context) -> web.Response:
    env = request.app["jinja_env"]
    template = env.get_template(template_name)
    html = template.render(**context, active_page=template_name.replace(".html", ""))
    return web.Response(text=html, content_type="text/html")


def _uptime() -> str:
    seconds = int(time.time() - APP_START_TIME)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


# ---- Pages ----------------------------------------------------------------
async def dashboard(request: web.Request) -> web.Response:
    ch = request.app.get("control_channel")
    tm = request.app.get("tunnel_manager")
    daemon = request.app.get("daemon")

    status = "disconnected"
    virtual_ip = "—"
    if ch is not None and ch.authenticated:
        status = "connected"
    if daemon is not None:
        vip = daemon.config.virtual_ip
        if vip:
            virtual_ip = vip

    tunnels_active = len(tm.list_tunnels()) if tm else 0

    return _render(
        request,
        "dashboard.html",
        panel_name="Client Admin",
        uptime=_uptime(),
        status=status,
        virtual_ip=virtual_ip,
        tunnels_active=tunnels_active,
    )


async def networks_page(request: web.Request) -> web.Response:
    return _render(request, "networks.html", panel_name="Client Admin")


async def peers_page(request: web.Request) -> web.Response:
    tm = request.app.get("tunnel_manager")
    peers = []
    if tm is not None:
        for t in tm.list_tunnels():
            peers.append({
                "peer_id": t.peer_id,
                "peer_ip": t.peer_ip,
                "state": t.state.value,
                "last_rx": f"{time.time() - t.last_rx:.0f}s ago" if t.last_rx else "—",
            })
    return _render(request, "peers.html", panel_name="Client Admin", peers=peers)


async def peer_detail(request: web.Request) -> web.Response:
    pid = request.match_info["peer_id"]
    return _render(request, "peer_detail.html", panel_name="Client Admin", peer_id=pid)


async def services_page(request: web.Request) -> web.Response:
    return _render(request, "services.html", panel_name="Client Admin")


async def config_page(request: web.Request) -> web.Response:
    return _render(request, "config.html", panel_name="Client Admin")


async def config_save(request: web.Request) -> web.Response:
    raise web.HTTPFound("/config")


async def logs_page(request: web.Request) -> web.Response:
    return _render(request, "logs.html", panel_name="Client Admin")


async def nat_diag_page(request: web.Request) -> web.Response:
    nat = request.app.get("nat_traversal")
    nat_type = "Unknown"
    if nat is not None:
        try:
            nt = nat.determine_nat_type()
            nat_type = nt.value
        except Exception:
            pass
    return _render(request, "nat_diag.html", panel_name="Client Admin", nat_type=nat_type)


# ---- API endpoints ---------------------------------------------------------
async def api_dashboard(request: web.Request) -> web.Response:
    ch = request.app.get("control_channel")
    tm = request.app.get("tunnel_manager")
    return web.json_response({
        "connected": ch.authenticated if ch else False,
        "tunnels_active": len(tm.list_tunnels()) if tm else 0,
        "uptime": _uptime(),
    })


async def api_peers(request: web.Request) -> web.Response:
    tm = request.app.get("tunnel_manager")
    if tm is None:
        return web.json_response([])
    return web.json_response([
        {
            "peer_id": t.peer_id,
            "peer_ip": t.peer_ip,
            "state": t.state.value,
        }
        for t in tm.list_tunnels()
    ])


async def api_tunnels(request: web.Request) -> web.Response:
    tm = request.app.get("tunnel_manager")
    if tm is None:
        return web.json_response([])
    return web.json_response([
        {
            "peer_id": t.peer_id,
            "state": t.state.value,
            "tx_seq": t.tx_seq,
            "rx_seq": t.rx_seq,
        }
        for t in tm.list_tunnels()
    ])


async def api_logs_stream(request: web.Request) -> web.StreamResponse:
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
            await response.write(
                f"data: {json.dumps({'ts': time.time(), 'msg': 'heartbeat'})}\n\n".encode()
            )
            await asyncio.sleep(10)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response
