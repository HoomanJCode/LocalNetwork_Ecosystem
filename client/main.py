"""Client entry points.

Provides:

* ``localnetwork-client`` — the VPN client daemon (identity + control channel
  + TUN interface + P2P tunnels + topology management, with graceful shutdown).
* ``localnetwork-cli`` — a management CLI for network operations
  (``create``, ``join``, ``leave``, ``list``, ``status``, ``info``).
* ``localnetwork-server`` entry is provided by :mod:`server.main`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import struct
import sys
from typing import Any, Dict, Optional

from client import identity
from client.config import ClientConfig
from client.control_channel import ControlChannel, ControlChannelError
from client.keepalive import KeepAliveManager
from client.nat_traversal import NatTraversal, PunchState
from client.platform_detection import print_capabilities
from client.topology import (
    TopologyManager,
    create_topology_manager,
)
from client.tunnel_manager import PeerTunnel, TunnelManager
from common.constants import (
    DEFAULT_TOPOLOGY,
    FRAME_DATA,
    MSG_PEER_OFFLINE,
    MSG_PEER_ONLINE,
    MSG_RELAY_FRAME,
    VIRTUAL_SUBNET,
)

log = logging.getLogger("localnetwork.client")

__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Client daemon
# ---------------------------------------------------------------------------
class ClientDaemon:
    """Full-featured VPN client daemon.

    Ties together:
    * Identity (RSA key pair)
    * Control channel to mediation server
    * P2P tunnel manager with NAT traversal
    * TUN virtual interface (platform-dependent)
    * Topology managers per network
    * IP routing (virtual IP → peer ID mapping)
    """

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.client_id: str = ""
        self.private_key = None
        self.public_key = None
        self.public_pem: str = ""

        # Core components
        self.channel: Optional[ControlChannel] = None
        self.tunnels: Optional[TunnelManager] = None
        self.keepalive: Optional[KeepAliveManager] = None
        self.tun: Any = None  # TunInterface (set if TUN mode enabled)

        # Per-network state
        self._topologies: Dict[str, TopologyManager] = {}  # network_id → TopologyManager
        self._network_ips: Dict[str, str] = {}  # network_id → our virtual IP
        self._ip_to_peer: Dict[str, str] = {}  # virtual_ip → peer_id
        self._peer_to_ip: Dict[str, str] = {}  # peer_id → virtual_ip
        self._peer_networks: Dict[str, str] = {}  # peer_id → network_id

        # Background tasks
        self._event_task: Optional[asyncio.Task] = None
        self._tun_read_task: Optional[asyncio.Task] = None
        self._tunnel_recv_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    # ---- Identity -----------------------------------------------------------
    def ensure_identity(self) -> str:
        """Load or generate the client identity; returns the client_id."""
        try:
            self.private_key, self.public_key = identity.load_identity(
                self.config.identity_dir
            )
        except identity.IdentityError:
            log.info("no identity found — generating a new RSA-2048 key pair")
            self.private_key, self.public_key = identity.generate_identity()
            identity.save_identity(
                self.private_key, self.public_key, path=self.config.identity_dir
            )
        log.info(
            "identity ready (fingerprint %s)",
            identity.public_key_fingerprint(self.public_key)[:20] + "…",
        )
        return identity.client_id_for_public_key(self.public_key)

    # ---- Main run loop ------------------------------------------------------
    async def run(self) -> None:
        """Connect, authenticate, set up TUN/tunnels, and keep the daemon alive."""
        from cryptography.hazmat.primitives import serialization

        self.client_id = self.ensure_identity()
        self.public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

        # 1. Control channel
        self.channel = ControlChannel(
            host=self.config.server_host,
            port=self.config.server_port,
            heartbeat_interval=self.config.heartbeat_interval,
            reconnect_base_delay=self.config.reconnect_base_delay,
            reconnect_max_delay=self.config.reconnect_max_delay,
            client_id=self.client_id,
        )
        await self.channel.connect()
        await self.channel.authenticate(self.private_key, self.public_pem)
        log.info(
            "connected to server %s:%s",
            self.config.server_host,
            self.config.server_port,
        )

        # 2. Tunnel manager
        nat = NatTraversal()
        self.tunnels = TunnelManager(nat=nat)
        self.tunnels.inject_control(self.channel)

        # 3. Keepalive
        self.keepalive = KeepAliveManager(self.tunnels)

        # 4. TUN interface (if platform supports it)
        if self.config.tun_mode_active:
            await self._setup_tun()

        # 5. Start background tasks
        self._event_task = asyncio.create_task(self._handle_events())
        self.keepalive.start()
        if self.tun is not None:
            self._tun_read_task = asyncio.create_task(self._tun_read_loop())
            self._tunnel_recv_task = asyncio.create_task(self._tunnel_recv_loop())

        log.info("client daemon running (tun=%s)", self.config.tun_mode_active)

        # Start web admin panel if configured
        web_task = None
        if self.config.web_port and self.config.web_port > 0:
            web_task = asyncio.create_task(self._start_web_panel())

        try:
            await self._shutdown.wait()
        finally:
            if web_task:
                web_task.cancel()

        # Graceful shutdown
        await self._shutdown_daemon()

    async def _start_web_panel(self) -> None:
        """Start the admin web panel alongside the daemon."""
        try:
            from client.web.app import create_app
            from aiohttp import web

            app = create_app(
                daemon=self,
                control_channel=self.channel,
                tunnel_manager=self.tunnels,
            )
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", self.config.web_port)
            await site.start()
            log.info("web admin panel listening on 127.0.0.1:%s", self.config.web_port)

            while True:
                await asyncio.sleep(3600)
        except ImportError:
            log.debug("web panel skipped: aiohttp not installed")
        except Exception as exc:
            log.warning("web panel failed to start: %r", exc)

    # ---- TUN setup ----------------------------------------------------------
    async def _setup_tun(self) -> None:
        """Create and configure the TUN virtual interface."""
        from client.tun_interface import create_tun_interface

        vip = self.config.virtual_ip or "25.1.0.1"
        try:
            self.tun = create_tun_interface()
            self.tun.open(ip=vip)
            self.tun.add_route(subnet=VIRTUAL_SUBNET, device=self.tun.name)
            self._ip_to_peer[vip] = self.client_id
            self._peer_to_ip[self.client_id] = vip
            log.info("TUN interface %s is up (ip=%s)", self.tun.name, vip)
        except OSError as exc:
            log.warning("TUN setup failed: %s — falling back to service-only mode", exc)
            self.tun = None

    # ---- Event handling -----------------------------------------------------
    async def _handle_events(self) -> None:
        """Main event loop: consume push events from the control channel."""
        if self.channel is None:
            return
        async for event in self.channel.listen_events():
            try:
                await self._dispatch_event(event)
            except Exception as exc:
                log.warning("error handling event %s: %r", event.type, exc)

    async def _dispatch_event(self, event: Any) -> None:
        """Route an event to the appropriate handler."""
        event_type = event.type
        payload = event.payload

        if event_type == MSG_PEER_ONLINE:
            await self._on_peer_online(
                payload.get("peer_id", ""),
                payload.get("network_id", ""),
                payload.get("peer_ip", ""),
            )
        elif event_type == MSG_PEER_OFFLINE:
            await self._on_peer_offline(
                payload.get("peer_id", ""),
                payload.get("network_id", ""),
            )
        elif event_type == MSG_RELAY_FRAME:
            await self._on_relay_frame(payload)
        else:
            log.debug("event: %s %s", event_type, payload)

    async def _on_peer_online(
        self, peer_id: str, network_id: str, peer_ip: str
    ) -> None:
        """Handle a peer coming online: register IP, create tunnel if topology says so."""
        if not peer_id or peer_id == self.client_id:
            return

        log.info("peer online: %s (ip=%s, network=%s)", peer_id, peer_ip, network_id)
        self._peer_to_ip[peer_id] = peer_ip or ""
        if peer_ip:
            self._ip_to_peer[peer_ip] = peer_id
        self._peer_networks[peer_id] = network_id

        # Ensure a topology manager exists for this network
        if network_id not in self._topologies:
            self._topologies[network_id] = create_topology_manager(
                DEFAULT_TOPOLOGY
            )

        topo = self._topologies[network_id]
        if await topo.on_peer_online(peer_id, peer_ip, network_id):
            await self._create_tunnel_to_peer(peer_id, peer_ip)

    async def _on_peer_offline(self, peer_id: str, network_id: str) -> None:
        """Handle a peer going offline: remove from routing, close tunnel."""
        if not peer_id:
            return
        log.info("peer offline: %s (network=%s)", peer_id, network_id)

        peer_ip = self._peer_to_ip.pop(peer_id, "")
        self._ip_to_peer.pop(peer_ip, None)
        self._peer_networks.pop(peer_id, None)

        if self.tunnels is not None:
            tunnel = self.tunnels.get_tunnel(peer_id)
            if tunnel is not None:
                self.tunnels.close_tunnel(tunnel)

        if network_id in self._topologies:
            await self._topologies[network_id].on_peer_offline(peer_id, network_id)

    async def _on_relay_frame(self, payload: dict) -> None:
        """Handle a relay frame from the control channel."""
        if self.tunnels is None:
            return
        src_id = payload.get("src_id", "")
        frame_b64 = payload.get("frame_b64", "")
        if not frame_b64:
            return
        import base64

        raw = base64.b64decode(frame_b64)
        tunnel = self.tunnels.get_tunnel(src_id)
        if tunnel is not None and self.tun is not None:
            try:
                self.tun.write(raw)
            except OSError:
                pass

    # ---- Tunnel creation -----------------------------------------------------
    async def _create_tunnel_to_peer(self, peer_id: str, peer_ip: str) -> None:
        """Request peer endpoints and create a P2P tunnel."""
        if self.tunnels is None or self.channel is None:
            return
        if self.tunnels.get_tunnel(peer_id) is not None:
            return  # Already have a tunnel

        try:
            endpoints = await self.channel.request_peer_endpoints(peer_id)
            if not endpoints:
                log.debug("no endpoints for peer %s", peer_id)
                return
            await self.tunnels.create_tunnel(peer_id, peer_ip, endpoints)
        except Exception as exc:
            log.debug("tunnel creation to %s failed: %r", peer_id, exc)

    # ---- TUN ↔ Tunnel bridging -----------------------------------------------
    async def _tun_read_loop(self) -> None:
        """Read IP packets from TUN and forward them through P2P tunnels."""
        if self.tun is None:
            return
        loop = asyncio.get_running_loop()
        while not self._shutdown.is_set():
            try:
                packet = await loop.run_in_executor(None, self.tun.read)
            except OSError:
                await asyncio.sleep(0.01)
                continue

            # ARP handling: respond to ARP requests for our IPs (gateway mode)
            if _is_arp_for_us(packet, list(self._ip_to_peer.keys())):
                reply = _build_arp_reply_for_tun(packet, self._ip_to_peer)
                if reply:
                    try:
                        self.tun.write(reply)
                    except OSError:
                        pass
                continue

            # Extract destination IP from the raw IP packet
            dst_ip = _extract_dst_ip_from_packet(packet)
            if dst_ip is None:
                continue

            # Route to the peer that owns this IP
            peer_id = self._ip_to_peer.get(dst_ip)
            if peer_id is None:
                continue

            tunnel = self.tunnels.get_tunnel(peer_id) if self.tunnels else None
            if tunnel is not None:
                self.tunnels.send_data(tunnel, packet)

    async def _tunnel_recv_loop(self) -> None:
        """Receive decrypted packets from P2P tunnels and inject them into TUN."""
        if self.tun is None or self.tunnels is None:
            return
        async for _peer_id, packet in self.tunnels.recv_loop():
            if self._shutdown.is_set():
                break
            try:
                self.tun.write(packet)
            except OSError:
                pass

    # ---- Shutdown -----------------------------------------------------------
    async def _shutdown_daemon(self) -> None:
        """Gracefully stop all components."""
        log.info("shutting down client daemon…")

        # Stop background tasks
        for task in (self._event_task, self._tun_read_task, self._tunnel_recv_task):
            if task is not None:
                task.cancel()

        # Stop keepalive
        if self.keepalive is not None:
            self.keepalive.stop()

        # Shut down tunnels
        if self.tunnels is not None:
            self.tunnels.shutdown()

        # Close TUN and remove routes
        if self.tun is not None:
            self.tun.remove_route(subnet=VIRTUAL_SUBNET, device=self.tun.name)
            self.tun.close()

        # Close control channel
        if self.channel is not None:
            await self.channel.close()

        log.info("client daemon stopped")

    def request_shutdown(self) -> None:
        self._shutdown.set()


# ---------------------------------------------------------------------------
# IP/ARP helpers (used by TUN bridging)
# ---------------------------------------------------------------------------
def _extract_dst_ip_from_packet(packet: bytes) -> Optional[str]:
    """Extract the destination IPv4 address from a raw IP packet."""
    import socket as _socket

    if len(packet) < 20:
        return None
    version_ihl = packet[0]
    if (version_ihl >> 4) != 4:
        return None
    dst = packet[16:20]
    try:
        return _socket.inet_ntoa(dst)
    except OSError:
        return None


def _extract_src_ip_from_packet(packet: bytes) -> Optional[str]:
    """Extract the source IPv4 address from a raw IP packet."""
    import socket as _socket

    if len(packet) < 20:
        return None
    version_ihl = packet[0]
    if (version_ihl >> 4) != 4:
        return None
    src = packet[12:16]
    try:
        return _socket.inet_ntoa(src)
    except OSError:
        return None


def _parse_arp(packet: bytes) -> Optional[tuple]:
    """Parse an ARP packet, raw (28 bytes) or Ethernet-framed.

    Returns:
        ``(opcode, sender_mac, sender_ip, target_mac, target_ip)`` where MACs
        are 6-byte and IPs are 4-byte raw values, or ``None`` if the packet is
        not a valid IPv4-over-Ethernet ARP frame.
    """
    if not packet:
        return None

    # Ethernet-framed ARP (EtherType 0x0806) — e.g. TAP / raw-socket captures.
    if len(packet) >= 14 and packet[12:14] == b"\x08\x06":
        arp = packet[14:]
    # Raw ARP (no Ethernet header) — some TUN configurations.
    elif (
        len(packet) >= 28
        and packet[0:2] == b"\x00\x01"
        and packet[2:4] == b"\x08\x00"
    ):
        arp = packet
    else:
        return None

    if len(arp) < 28:
        return None

    htype = int.from_bytes(arp[0:2], "big")
    ptype = int.from_bytes(arp[2:4], "big")
    hlen = arp[4]
    plen = arp[5]
    opcode = int.from_bytes(arp[6:8], "big")
    if htype != 1 or ptype != 0x0800 or hlen != 6 or plen != 4:
        return None

    sha = arp[8:14]   # sender hardware (MAC)
    spa = arp[14:18]  # sender protocol (IPv4)
    tha = arp[18:24]  # target hardware (MAC)
    tpa = arp[24:28]  # target protocol (IPv4)
    return opcode, sha, spa, tha, tpa


def _mac_for_ip(ip: str) -> bytes:
    """Derive a stable locally-administered MAC from a virtual IP.

    TUN devices have no hardware address, but gateway ARP proxying needs a
    source MAC for replies. We derive a deterministic unicast MAC (locally
    administered, the second-least-significant bit of the first octet is set)
    from the IP octets.
    """
    parts = [int(p) for p in ip.split(".")]
    if len(parts) != 4:
        return bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00])
    return bytes([0x02, parts[1], parts[2], parts[3], parts[0], 0x00])


def _is_arp_for_us(packet: bytes, our_ips: list) -> bool:
    """Return True if ``packet`` is an ARP request for one of our virtual IPs.

    Handles both raw and Ethernet-framed ARP. On a plain TUN device the kernel
    answers ARP itself, so non-ARP (IP) packets simply return False; this
    helper exists for gateway-mode ARP proxying.
    """
    parsed = _parse_arp(packet)
    if parsed is None:
        return False
    opcode, _sha, _spa, _tha, tpa = parsed
    if opcode != 1:  # ARP request only
        return False
    import socket as _socket

    try:
        target_ip = _socket.inet_ntoa(tpa)
    except OSError:
        return False
    return target_ip in our_ips


def _build_arp_reply_for_tun(packet: bytes, ip_to_peer: dict) -> Optional[bytes]:
    """Build an ARP reply for a request targeting one of our virtual IPs.

    Returns:
        A raw 28-byte ARP reply (no Ethernet header, suitable for TUN writes),
        or ``None`` if the packet is not an ARP request for one of our IPs.
    """
    parsed = _parse_arp(packet)
    if parsed is None:
        return None
    opcode, sha, spa, _tha, tpa = parsed
    if opcode != 1:
        return None

    import socket as _socket

    try:
        target_ip = _socket.inet_ntoa(tpa)
    except OSError:
        return None
    if target_ip not in ip_to_peer:
        return None

    our_mac = _mac_for_ip(target_ip)
    # htype=1, ptype=0x0800, hlen=6, plen=4, opcode=2 (reply)
    return struct.pack(
        "!HHBBH6s4s6s4s",
        1, 0x0800, 6, 4, 2,
        our_mac, tpa,  # sha = our MAC, spa = the requested (our) IP
        sha, spa,      # tha = requester MAC, tpa = requester IP
    )


def _daemonize() -> None:
    """Fork the process to background (Unix daemon)."""
    import os as _os

    if _os.fork() > 0:
        _os._exit(0)
    _os.setsid()
    if _os.fork() > 0:
        _os._exit(0)
    _os.chdir("/")
    _os.umask(0o022)
    # Write PID file
    pid_dir = _os.path.expanduser("~/.localnetwork")
    _os.makedirs(pid_dir, exist_ok=True)
    pid_file = _os.path.join(pid_dir, "client.pid")
    with open(pid_file, "w") as f:
        f.write(str(_os.getpid()))
    # Redirect stdio to /dev/null
    devnull = _os.open(_os.devnull, _os.O_RDWR)
    _os.dup2(devnull, 0)
    _os.dup2(devnull, 1)
    _os.dup2(devnull, 2)
    _os.close(devnull)


# ---------------------------------------------------------------------------
# localnetwork-client
# ---------------------------------------------------------------------------
def build_client_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localnetwork-client",
        description="LocalNetwork Ecosystem VPN client",
    )
    parser.add_argument("--server", default=None, help="server host:port")
    parser.add_argument("--identity-dir", default=None, help="key storage directory")
    parser.add_argument("--virtual-ip", default=None, help="request a virtual IP")
    parser.add_argument("--tun", action="store_true", default=None,
                        help="enable TUN mode (virtual LAN interface)")
    parser.add_argument("--no-tun", action="store_true",
                        help="disable TUN mode even if available")
    parser.add_argument("--web-port", type=int, default=None, help="admin panel port")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--detect-platform", action="store_true",
                        help="print platform capabilities and exit")
    parser.add_argument("--daemon", action="store_true",
                        help="fork to background (PID file in ~/.localnetwork/)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def client_main(argv: Optional[list] = None) -> int:
    args = build_client_parser().parse_args(argv)

    if args.detect_platform:
        from client.platform_detection import detect_platform

        print_capabilities(detect_platform())
        return 0

    overrides = {}
    if args.server and ":" in args.server:
        host, _, port = args.server.rpartition(":")
        overrides["server_host"] = host
        overrides["server_port"] = int(port)
    elif args.server:
        overrides["server_host"] = args.server
    if args.identity_dir:
        overrides["identity_dir"] = args.identity_dir
    if args.virtual_ip:
        overrides["virtual_ip"] = args.virtual_ip
        overrides["request_virtual_ip"] = True
    if args.web_port is not None:
        overrides["web_port"] = args.web_port
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.tun:
        overrides["tun_enabled"] = True
    if args.no_tun:
        overrides["tun_enabled"] = False

    config = ClientConfig.from_env(**overrides)

    if args.daemon:
        _daemonize()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    daemon = ClientDaemon(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    except ControlChannelError as exc:
        log.error("connection failed: %s", exc)
        return 1
    finally:
        loop.close()
    return 0


# ---------------------------------------------------------------------------
# localnetwork-cli
# ---------------------------------------------------------------------------
def _make_session(args) -> tuple[ClientConfig, ControlChannel]:
    config = ClientConfig.from_env(
        server_host=args.host, server_port=args.port
    )
    return config, ControlChannel(host=config.server_host, port=config.server_port)


def _run_cli_command(args, coro_factory):
    """Connect, authenticate, run a command, then close."""
    from cryptography.hazmat.primitives import serialization

    config, channel = _make_session(args)

    async def inner():
        private_key, public_key = identity.load_identity(config.identity_dir)
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        await channel.connect()
        await channel.authenticate(private_key, public_pem)
        try:
            result = await coro_factory(channel)
            return result
        finally:
            await channel.close()

    return asyncio.run(inner())


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localnetwork-cli",
        description="LocalNetwork Ecosystem management CLI",
    )
    parser.add_argument("--host", default="localhost", help="server host")
    parser.add_argument("--port", type=int, default=54000, help="server port")
    parser.add_argument("--identity-dir", default=None, help="identity directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="create a new virtual network")
    p_create.add_argument("name")
    p_create.add_argument("--password", default="")
    p_create.add_argument("--topology", default="mesh",
                          choices=["mesh", "hub", "gateway"])
    p_create.add_argument("--virtual-ip")

    p_join = sub.add_parser("join", help="join an existing network")
    p_join.add_argument("network")
    p_join.add_argument("--password", default="")

    p_leave = sub.add_parser("leave", help="leave a network")
    p_leave.add_argument("network")

    sub.add_parser("list", help="list networks you belong to")
    sub.add_parser("status", help="show connection status")

    p_info = sub.add_parser("info", help="show details about a network")
    p_info.add_argument("network")

    sub.add_parser("version", help="show version")
    return parser


def cli_main(argv: Optional[list] = None) -> int:
    args = build_cli_parser().parse_args(argv)
    topology_map = {"hub": "hub_and_spoke", "gateway": "gateway"}

    try:
        if args.command == "create":
            topology = topology_map.get(args.topology, args.topology)

            def do(channel):
                return channel.create_network(args.name, args.password, topology)

            result = _run_cli_command(args, do)
            network_id = result.get("network_id", result) if isinstance(result, dict) else result
            invite_code = result.get("invite_code", "") if isinstance(result, dict) else ""
            print(f"Created network {args.name!r}")
            print(f"  Network ID:  {network_id}")
            if invite_code:
                print(f"  Invite code: {invite_code}")
            print("Share the invite code (or network ID) and password with friends.")

        elif args.command == "join":
            def do(channel):
                return channel.join_network(args.network, args.password)

            virtual_ip = _run_cli_command(args, do)
            print(f"Joined network {args.network}")
            if virtual_ip:
                print(f"Virtual IP: {virtual_ip}")

        elif args.command == "leave":
            def do(channel):
                return channel.leave_network(args.network)

            _run_cli_command(args, do)
            print(f"Left network {args.network}")

        elif args.command == "list":
            def do(channel):
                return channel.list_networks()

            networks = _run_cli_command(args, do)
            if not networks:
                print("You are not a member of any network.")
            for network in networks:
                print(
                    f"  {network['name']:<24} {network['network_id']}  "
                    f"topology={network['topology']}  "
                    f"members={network['member_count']}"
                )

        elif args.command == "status":
            from client.platform_detection import detect_platform

            caps = detect_platform()
            print(f"Server:            {args.host}:{args.port}")
            print(f"TUN mode:          {'enabled' if caps.tun_mode_enabled else 'disabled'}")
            print("Networks:")
            networks = _run_cli_command(args, lambda ch: ch.list_networks())
            for network in networks:
                print(
                    f"  {network['name']:<24} {network['network_id']}  "
                    f"members={network['member_count']}"
                )

        elif args.command == "info":
            def do(channel):
                return channel.list_networks()

            networks = _run_cli_command(args, do)
            match = [n for n in networks if n["network_id"] == args.network
                     or n["name"] == args.network]
            if not match:
                print(f"Network {args.network!r} not found.")
                return 1
            network = match[0]
            print(f"Name:      {network['name']}")
            print(f"ID:        {network['network_id']}")
            print(f"Owner:     {network['owner_id']}")
            print(f"Topology:  {network['topology']}")
            print(f"Members:   {network['member_count']}")

        elif args.command == "version":
            print(f"localnetwork-cli {__version__}")
        return 0
    except identity.IdentityError as exc:
        print(f"Identity error: {exc}", file=sys.stderr)
        return 1
    except ControlChannelError as exc:
        print(f"Server error: {exc}", file=sys.stderr)
        return 1
    except (ConnectionRefusedError, OSError) as exc:
        print(f"Cannot reach server at {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(client_main())
