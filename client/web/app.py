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

# Feature state registry (lives across requests)
_feature_states: dict[str, dict] = {
    "tun": {
        "name": "TUN Mode",
        "icon": "🌐",
        "description": "Virtual LAN interface for IP routing between peers. Creates a TUN device and assigns a virtual IP in the 25.0.0.0/8 subnet.",
        "running": False,
        "config": {
            "virtual_ip": "25.1.0.1",
            "subnet": "25.0.0.0/8",
            "mtu": 1400,
        },
        "requires": ["root", "tun_available"],
    },
    "service_exposure": {
        "name": "Service Exposure",
        "icon": "📦",
        "description": "Expose local TCP/UDP services to other peers on the network. Map remote services to local ports for consumption.",
        "running": False,
        "config": {
            "exposed_services": [],
            "mapped_services": [],
        },
        "requires": [],
    },
    "nat_traversal": {
        "name": "NAT Traversal",
        "icon": "🔍",
        "description": "Detect NAT type and punch UDP holes for direct peer-to-peer connections. Supports STUN-based detection.",
        "running": False,
        "config": {
            "nat_type": "Unknown",
            "stun_server": "stun.l.google.com:19302",
        },
        "requires": [],
    },
    "connection": {
        "name": "VPN Connection",
        "icon": "🔗",
        "description": "Connect to the mediation server, authenticate, and join networks. Heartbeat keep-alive and auto-reconnect.",
        "running": False,
        "config": {
            "server_host": "localhost",
            "server_port": 54000,
            "heartbeat_interval": 30,
            "reconnect": True,
        },
        "requires": [],
    },
    "topology": {
        "name": "Network Topology",
        "icon": "🕸️",
        "description": "Choose the network topology: Mesh (all-to-all), Hub-and-Spoke (star), or Gateway (bridge to physical LAN).",
        "running": False,
        "config": {
            "mode": "mesh",
            "hub_id": "",
            "gateway_subnet": "",
        },
        "requires": [],
    },
}


def _get_feature_states() -> dict:
    """Return a summary of all feature states."""
    return {
        key: {"name": v["name"], "icon": v["icon"], "description": v["description"],
               "running": v["running"], "config": v["config"], "requires": v["requires"]}
        for key, v in _feature_states.items()
    }


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

    # Static files — serve common/web_static design system
    common_static = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "common", "web_static")
    )
    app.router.add_static("/static/", common_static)

    # Feature routes
    app.router.add_get("/features", features_page)

    # API endpoints
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/peers", api_peers)
    app.router.add_get("/api/tunnels", api_tunnels)
    app.router.add_get("/api/features", api_features)
    app.router.add_post("/api/features/{feature_id}/start", api_feature_start)
    app.router.add_post("/api/features/{feature_id}/stop", api_feature_stop)
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
        # Sync feature states from daemon
        _feature_states["connection"]["running"] = True
        _feature_states["connection"]["config"]["server_host"] = \
            daemon.config.server_host if daemon else "localhost"
    if daemon is not None:
        vip = daemon.config.virtual_ip
        if vip:
            virtual_ip = vip
        if daemon.tun is not None:
            _feature_states["tun"]["running"] = True

    tunnels_active = len(tm.list_tunnels()) if tm else 0
    features = _get_feature_states()
    running_count = sum(1 for f in features.values() if f["running"])

    return _render(
        request,
        "dashboard.html",
        panel_name="Client Admin",
        uptime=_uptime(),
        status=status,
        virtual_ip=virtual_ip,
        tunnels_active=tunnels_active,
        features=features,
        running_count=running_count,
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


# ---- Feature page ---------------------------------------------------------
async def features_page(request: web.Request) -> web.Response:
    """Full page showing all features with config panels."""
    return _render(
        request,
        "features.html",
        panel_name="Client Admin",
        features=_get_feature_states(),
    )


# ---- Feature API -----------------------------------------------------------
async def api_features(request: web.Request) -> web.Response:
    """Return all feature states."""
    return web.json_response(_get_feature_states())


async def api_feature_start(request: web.Request) -> web.Response:
    """Start a feature by ID."""
    feature_id = request.match_info["feature_id"]
    if feature_id not in _feature_states:
        raise web.HTTPNotFound(text=json.dumps({"error": "unknown feature"}))

    try:
        data = await request.json()
    except Exception:
        data = {}

    # Update config from request
    for key, val in data.get("config", {}).items():
        if key in _feature_states[feature_id]["config"]:
            _feature_states[feature_id]["config"][key] = val

    _feature_states[feature_id]["running"] = True

    # Attempt to actually start the feature via daemon
    daemon = request.app.get("daemon")
    message = "started"

    if feature_id == "tun" and daemon is not None:
        try:
            await daemon._setup_tun()
            message = "TUN interface created"
        except Exception as exc:
            _feature_states[feature_id]["running"] = False
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
    elif feature_id == "connection" and daemon is not None:
        try:
            # Trigger reconnection
            daemon.request_shutdown()
            message = "Reconnecting…"
        except Exception as exc:
            _feature_states[feature_id]["running"] = False
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
    elif feature_id == "nat_traversal":
        nat = request.app.get("nat_traversal")
        if nat is not None:
            try:
                nat_type = nat.determine_nat_type()
                _feature_states[feature_id]["config"]["nat_type"] = nat_type.value
                message = f"NAT type: {nat_type.value}"
            except Exception as exc:
                message = f"Detection failed: {exc}"

    return web.json_response({
        "ok": True,
        "message": message,
        "feature": _get_feature_states()[feature_id],
    })


async def api_feature_stop(request: web.Request) -> web.Response:
    """Stop a feature by ID."""
    feature_id = request.match_info["feature_id"]
    if feature_id not in _feature_states:
        raise web.HTTPNotFound(text=json.dumps({"error": "unknown feature"}))

    _feature_states[feature_id]["running"] = False
    daemon = request.app.get("daemon")
    message = "stopped"

    if feature_id == "tun" and daemon is not None and daemon.tun is not None:
        try:
            daemon.tun.close()
            daemon.tun = None
            message = "TUN interface removed"
        except Exception as exc:
            message = f"Warning: {exc}"

    return web.json_response({
        "ok": True,
        "message": message,
        "feature": _get_feature_states()[feature_id],
    })


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
