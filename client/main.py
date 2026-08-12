"""Client entry points.

Provides:

* ``localnetwork-client`` — the VPN client daemon (identity + control channel
  + event handling, with graceful shutdown).
* ``localnetwork-cli`` — a management CLI for network operations
  (``create``, ``join``, ``leave``, ``list``, ``status``, ``info``).
* ``localnetwork-server`` entry is provided by :mod:`server.main`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from client import identity
from client.config import ClientConfig
from client.control_channel import ControlChannel, ControlChannelError
from client.platform_detection import print_capabilities

log = logging.getLogger("localnetwork.client")

__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Client daemon
# ---------------------------------------------------------------------------
class ClientDaemon:
    """Ties together identity, control channel, and platform capabilities."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.private_key = None
        self.public_key = None
        self.channel: Optional[ControlChannel] = None
        self._event_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

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

    async def run(self) -> None:
        """Connect, authenticate, and keep the daemon alive."""
        from cryptography.hazmat.primitives import serialization

        client_id = self.ensure_identity()
        public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

        self.channel = ControlChannel(
            host=self.config.server_host,
            port=self.config.server_port,
            heartbeat_interval=self.config.heartbeat_interval,
            reconnect_base_delay=self.config.reconnect_base_delay,
            reconnect_max_delay=self.config.reconnect_max_delay,
            client_id=client_id,
        )

        await self.channel.connect()
        await self.channel.authenticate(self.private_key, public_pem)
        log.info(
            "connected to server %s:%s",
            self.config.server_host,
            self.config.server_port,
        )

        self._event_task = asyncio.create_task(self._handle_events())
        await self._shutdown.wait()

        # Graceful shutdown
        await self._shutdown_daemon()

    async def _handle_events(self) -> None:
        if self.channel is None:
            return
        async for event in self.channel.listen_events():
            event_type = event.type
            payload = event.payload
            if event_type == "PEER_ONLINE":
                log.info(
                    "peer online: %s (network %s)",
                    payload.get("peer_id"),
                    payload.get("network_id"),
                )
            elif event_type == "PEER_OFFLINE":
                log.info(
                    "peer offline: %s (network %s)",
                    payload.get("peer_id"),
                    payload.get("network_id"),
                )
            elif event_type == "RELAY_FRAME":
                # Relay frames are consumed by the tunnel manager (Phase 7)
                continue
            else:
                log.debug("event: %s %s", event_type, payload)

    async def _shutdown_daemon(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
        if self.channel is not None:
            await self.channel.close()
        log.info("client daemon stopped")

    def request_shutdown(self) -> None:
        self._shutdown.set()


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
    parser.add_argument("--web-port", type=int, default=None, help="admin panel port")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--detect-platform", action="store_true",
                        help="print platform capabilities and exit")
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

    config = ClientConfig.from_env(**overrides)

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

            network_id = _run_cli_command(args, do)
            print(f"Created network {args.name!r} with id {network_id}")
            if args.password:
                print("Share the network id and password with friends.")

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
