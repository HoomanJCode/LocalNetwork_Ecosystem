"""Mediation server entry point and connection handling.

The server is a single asyncio process. Each client gets a persistent TCP
connection carrying length-prefixed JSON control messages. The lifecycle is:

    REGISTER ──► AUTH_CHALLENGE ──► AUTH_RESPONSE ──► command loop
                                                        │
                                CREATE_NETWORK, JOIN_NETWORK, LEAVE_NETWORK,
                                LIST_NETWORKS, REQUEST_PEER_CONN, HEARTBEAT, …
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Dict, Optional

from common import constants
from common.messages import (
    AuthChallenge,
    AuthResult,
    ErrorMessage,
    HeartbeatAck,
    Message,
    NetworkCreated,
    NetworkJoined,
    NetworkLeft,
    NetworkList,
    NetworkPeers,
    PeerEndpoints,
    PeerOffline,
    PeerOnline,
    ServiceAdded,
    ServiceRemoved,
    make_message,
)
from server import protocol as proto
from server.auth import AuthSession
from server.config import ServerConfig
from server.network_manager import NetworkManager
from server.registry import ClientRegistry

try:  # RelayForwarder arrives in Phase 4; keep the server runnable without it
    from server.relay import RelayForwarder
    _RELAY_AVAILABLE = True
except ImportError:
    RelayForwarder = None  # type: ignore[assignment,misc]
    _RELAY_AVAILABLE = False

log = logging.getLogger("localnetwork.server")


class MediationServer:
    """Asyncio TCP server mediating control traffic between clients."""

    def __init__(self, config: Optional[ServerConfig] = None) -> None:
        self.config = config or ServerConfig.from_env()
        self.config.validate()
        self.registry = ClientRegistry()
        self.networks = NetworkManager(self.registry)
        self.auth = AuthSession(
            self.registry,
            ttl=self.config.auth_challenge_ttl,
            max_attempts=self.config.auth_max_attempts,
        )
        self.relay = (
            RelayForwarder(self)
            if _RELAY_AVAILABLE and RelayForwarder is not None
            else None
        )

        self._writers: Dict[str, asyncio.StreamWriter] = {}
        self._authenticated: Dict[str, bool] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._prune_task: Optional[asyncio.Task] = None
        self._expiry_task: Optional[asyncio.Task] = None
        self._shutting_down = False
        self.started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Bind, listen, and run until cancelled."""
        import time

        self.started_at = time.time()
        self._server = await asyncio.start_server(
            self.handle_client,
            host=self.config.host,
            port=self.config.port,
            limit=constants.MAX_MESSAGE_SIZE + 4,
        )
        addr = self._server.sockets[0].getsockname()
        log.info(
            "mediation server listening on %s:%s (max_clients=%s)",
            addr[0],
            addr[1],
            self.config.max_clients,
        )
        self._prune_task = asyncio.create_task(self._prune_loop())
        self._expiry_task = asyncio.create_task(self._auth_expiry_loop())

        async with self._server:
            await self._server.serve_forever()

    async def shutdown(self) -> None:
        """Gracefully stop accepting, close all client connections."""
        self._shutting_down = True
        if self._prune_task:
            self._prune_task.cancel()
        if self._expiry_task:
            self._expiry_task.cancel()
        for writer in list(self._writers.values()):
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        log.info("mediation server stopped")

    async def _prune_loop(self) -> None:
        """Periodically mark silent clients offline and notify their peers."""
        interval = max(5, self.config.heartbeat_timeout // 3)
        while True:
            await asyncio.sleep(interval)
            stale = self.registry.prune_stale(self.config.heartbeat_timeout)
            for client_id in stale:
                log.warning("client %s timed out (no heartbeat)", client_id)
                await self._handle_disconnect(client_id)

    async def _auth_expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(constants.AUTH_CHALLENGE_TTL)
            self.auth.expire_stale()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Per-connection coroutine: register → authenticate → command loop."""
        peer = writer.get_extra_info("peername")
        client_id: Optional[str] = None
        registered = False
        authenticated = False

        try:
            while not reader.at_eof():
                data = await reader.readexactly(4)
                length = int.from_bytes(data, "big")
                if length > constants.MAX_MESSAGE_SIZE:
                    log.warning("oversized message (%d bytes) from %s", length, peer)
                    break
                body = await reader.readexactly(length)
                # deserialize expects the full length-prefixed buffer
                msg = proto.parse_message(data + body)

                if not registered:
                    if msg.type != constants.MSG_REGISTER:
                        await self._send_error(
                            writer, "REGISTER_REQUIRED", "register first"
                        )
                        continue
                    client_id = await self._on_register(writer, msg, peer)
                    registered = client_id is not None
                    continue

                if not authenticated:
                    if msg.type != constants.MSG_AUTH_RESPONSE:
                        await self._send_error(
                            writer, "AUTH_REQUIRED", "authenticate first"
                        )
                        continue
                    ok, detail = await self._on_auth(writer, client_id, msg)
                    authenticated = ok
                    if not ok:
                        await self._send_error(writer, "AUTH_FAILED", detail)
                    continue

                # Command loop
                if not await self._dispatch(client_id, writer, msg):
                    break  # fatal error for this connection
                if self.relay is not None:
                    # Deliver any relayed frames queued for this client
                    await self.relay.deliver_relayed(client_id, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:  # never let one client kill the server
            log.warning("connection error from %s: %r", peer, exc)
        finally:
            if client_id and authenticated:
                await self._handle_disconnect(client_id)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _on_register(
        self,
        writer: asyncio.StreamWriter,
        msg: Message,
        peer: tuple,
    ) -> Optional[str]:
        try:
            client_id, public_key = proto.validate_register(msg.payload)
        except proto.ProtocolError as exc:
            await self._send_error(writer, "INVALID_REGISTER", str(exc))
            return None

        if len(self._writers) >= self.config.max_clients and (
            client_id not in self._writers
        ):
            await self._send_error(writer, "SERVER_FULL", "server is at capacity")
            return None
        if self.registry.is_banned(client_id):
            await self._send_error(writer, "BANNED", "client is banned")
            return None

        record = self.registry.register(client_id, public_key)
        # Replace any previous connection for the same identity
        old_writer = self._writers.get(client_id)
        if old_writer is not None and old_writer is not writer:
            old_writer.close()

        endpoint = (peer[0], peer[1])
        self.registry.update_endpoint(client_id, endpoint)
        self.registry.set_online(client_id)
        self._writers[client_id] = writer
        self._authenticated[client_id] = False

        challenge = self.auth.issue(client_id)
        await self._send(
            writer,
            make_message(
                AuthChallenge,
                challenge=challenge.challenge,
                client_id=challenge.client_id,
            ),
        )
        log.info("client %s registered from %s", client_id, endpoint)
        return client_id

    async def _on_auth(
        self, writer: asyncio.StreamWriter, client_id: str, msg: Message
    ) -> tuple[bool, str]:
        try:
            challenge_hex, signature_hex = proto.validate_auth_response(msg.payload)
        except proto.ProtocolError as exc:
            return False, str(exc)
        ok, detail = self.auth.verify(client_id, challenge_hex, signature_hex)
        if ok:
            self._authenticated[client_id] = True
            await self._send(
                writer,
                make_message(AuthResult, ok=True, message="authenticated"),
            )
            log.info("client %s authenticated", client_id)
            await self._announce_peer_online(client_id)
        return ok, detail

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------
    async def _dispatch(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> bool:
        """Route an authenticated command. Returns False on fatal errors."""
        try:
            if msg.type == constants.MSG_HEARTBEAT:
                self.registry.heartbeat(client_id)
                await self._send(
                    writer,
                    make_message(
                        HeartbeatAck, ts=msg.payload.get("ts", 0.0)
                    ),
                )
            elif msg.type == constants.MSG_CREATE_NETWORK:
                await self._on_create_network(client_id, writer, msg)
            elif msg.type == constants.MSG_JOIN_NETWORK:
                await self._on_join_network(client_id, writer, msg)
            elif msg.type == constants.MSG_LEAVE_NETWORK:
                await self._on_leave_network(client_id, writer, msg)
            elif msg.type == constants.MSG_LIST_NETWORKS:
                await self._on_list_networks(client_id, writer)
            elif msg.type == constants.MSG_REQUEST_PEER_CONN:
                await self._on_request_peer_conn(client_id, writer, msg)
            elif msg.type == constants.MSG_EXPOSE_SERVICE:
                await self._on_expose_service(client_id, writer, msg)
            elif msg.type == constants.MSG_UNEXPOSE_SERVICE:
                await self._on_unexpose_service(client_id, writer, msg)
            elif msg.type == constants.MSG_RELAY_REQUEST:
                await self._on_relay_request(client_id, writer, msg)
            else:
                await self._send_error(
                    writer, "UNKNOWN_MESSAGE", f"unhandled message type {msg.type}"
                )
        except proto.ProtocolError as exc:
            await self._send_error(writer, "INVALID_MESSAGE", str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("error dispatching %s from %s", msg.type, client_id)
            await self._send_error(writer, "INTERNAL", f"internal error: {exc}")
        return True

    async def _on_create_network(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        name, password, topology = proto.validate_create_network(msg.payload)
        record = self.networks.create(name, password, client_id, topology)
        await self._send(
            writer,
            make_message(
                NetworkCreated,
                network_id=record.network_id,
                name=record.name,
                owner_id=client_id,
                topology=record.topology,
            ),
        )
        log.info("client %s created network %s (%s)", client_id, record.network_id, name)
        # The new network may have pre-existing members (owner re-joining) —
        # announce the creator as online to them.
        await self._announce_peer_online(client_id)

    async def _on_join_network(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        network_id, password = proto.validate_join_network(msg.payload)
        if self.networks.is_banned(network_id):
            await self._send_error(writer, "NETWORK_BANNED", "network is banned")
            return
        record = self.networks.get(network_id)
        if record is None:
            await self._send_error(writer, "NETWORK_NOT_FOUND", "no such network")
            return
        if not self.networks.join(network_id, client_id, password):
            await self._send_error(writer, "WRONG_PASSWORD", "join rejected")
            return
        await self._send(
            writer,
            make_message(
                NetworkJoined,
                network_id=network_id,
                name=record.name,
            ),
        )
        log.info("client %s joined network %s", client_id, network_id)
        await self._announce_peer_online(client_id)

    async def _on_leave_network(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        network_id = proto.validate_network_id(msg.payload)
        if self.networks.leave(network_id, client_id):
            await self._send(
                writer, make_message(NetworkLeft, network_id=network_id)
            )
            await self._announce_peer_offline(client_id, network_id)
        else:
            await self._send_error(
                writer, "NOT_MEMBER", "you are not a member of this network"
            )

    async def _on_list_networks(
        self, client_id: str, writer: asyncio.StreamWriter
    ) -> None:
        networks = self.networks.list_for_client(client_id)
        await self._send(
            writer,
            make_message(
                NetworkList,
                networks=[record.to_dict() for record in networks],
            ),
        )

    async def _on_request_peer_conn(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        peer_id = proto.validate_peer_id(msg.payload)
        # Find a shared network between requester and target
        shared_network = None
        for network_id in self.registry.networks_for(client_id):
            if self.networks.is_member(network_id, peer_id):
                shared_network = network_id
                break
        if shared_network is None:
            await self._send_error(
                writer, "NO_SHARED_NETWORK", "no shared network with that peer"
            )
            return
        endpoints = self.networks.shared_endpoints_for(
            shared_network, client_id, peer_id
        )
        if not endpoints:
            await self._send_error(
                writer, "PEER_UNAVAILABLE", "peer is offline or endpoints hidden"
            )
            return
        await self._send(
            writer,
            make_message(
                PeerEndpoints,
                peer_id=peer_id,
                endpoints=[list(ep) for ep in endpoints],
            ),
        )

    # ---- Service exposure hooks (Phase 14) ----------------------------------
    async def _on_expose_service(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        from server.network_manager import ServiceRecord  # noqa: F401  (registered in phase 14)

        # Forward to the service registry if available
        if hasattr(self.networks, "expose_service"):
            name, protocol, local_host, local_port = proto.validate_expose_service(
                msg.payload
            )
            networks = self.networks.list_for_client(client_id)
            if not networks:
                await self._send_error(
                    writer, "NO_NETWORK", "join a network before exposing a service"
                )
                return
            network_id = msg.payload.get("network_id") or networks[0].network_id
            record = self.networks.expose_service(
                network_id, client_id, name, protocol, local_host, local_port
            )
            await self._send(
                writer,
                make_message(
                    constants.MSG_SERVICE_EXPOSED,
                    service_id=record.service_id,
                    name=record.name,
                    protocol=record.protocol,
                    local_port=record.local_port,
                ),
            )
            await self._broadcast_service(
                network_id,
                ServiceAdded,
                service=record.to_dict(),
            )
        else:
            await self._send_error(
                writer, "NOT_IMPLEMENTED", "service exposure not available yet"
            )

    async def _on_unexpose_service(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        if hasattr(self.networks, "unexpose_service"):
            service_id = proto.validate_service_id(msg.payload)
            network_id = self.networks.unexpose_service_owner(
                service_id, client_id
            )
            if network_id is None:
                await self._send_error(
                    writer, "SERVICE_NOT_FOUND", "service not found"
                )
                return
            await self._broadcast_service(
                network_id, ServiceRemoved, service_id=service_id
            )
        else:
            await self._send_error(
                writer, "NOT_IMPLEMENTED", "service exposure not available yet"
            )

    async def _broadcast_service(self, network_id: str, msg_cls, **payload) -> None:
        """Push a service event to every online member of a network."""
        for member in self.registry.members_in_networks([network_id]):
            member_writer = self._writers.get(member.client_id)
            if member_writer is not None:
                await self._send(member_writer, make_message(msg_cls, **payload))

    # ---- Relay hook (Phase 4) -------------------------------------------------
    async def _on_relay_request(
        self, client_id: str, writer: asyncio.StreamWriter, msg: Message
    ) -> None:
        if self.relay is None:
            await self._send_error(
                writer, "RELAY_DISABLED", "relay unavailable (relay module missing)"
            )
            return
        peer_id = proto.validate_peer_id(msg.payload)
        await self.relay.handle_relay_request(client_id, peer_id, writer)

    # ------------------------------------------------------------------
    # Peer notifications
    # ------------------------------------------------------------------
    async def _announce_peer_online(self, client_id: str) -> None:
        """Tell other members of every network the client belongs to."""
        for network_id in self.registry.networks_for(client_id):
            for member in self.registry.members_in_networks([network_id]):
                if member.client_id == client_id:
                    continue
                member_writer = self._writers.get(member.client_id)
                if member_writer is not None:
                    await self._send(
                        member_writer,
                        make_message(
                            PeerOnline,
                            network_id=network_id,
                            peer_id=client_id,
                        ),
                    )

    async def _announce_peer_offline(self, client_id: str, network_id: str) -> None:
        for member in self.registry.members_in_networks([network_id]):
            if member.client_id == client_id:
                continue
            member_writer = self._writers.get(member.client_id)
            if member_writer is not None:
                await self._send(
                    member_writer,
                    make_message(
                        PeerOffline, network_id=network_id, peer_id=client_id
                    ),
                )

    async def _handle_disconnect(self, client_id: str) -> None:
        """Mark a client offline and notify peers in all its networks."""
        self._writers.pop(client_id, None)
        self._authenticated.pop(client_id, None)
        networks = self.registry.networks_for(client_id)
        self.registry.unregister(client_id)
        if self.relay is not None:
            self.relay.drop_client(client_id)
        for network_id in networks:
            await self._announce_peer_offline(client_id, network_id)
        log.info("client %s disconnected", client_id)

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _send(writer: asyncio.StreamWriter, msg: Message) -> None:
        writer.write(proto.build_message(msg))
        await writer.drain()

    @staticmethod
    async def _send_error(
        writer: asyncio.StreamWriter, code: str, message: str
    ) -> None:
        await MediationServer._send(
            writer, make_message(ErrorMessage, code=code, message=message)
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localnetwork-server",
        description="LocalNetwork Ecosystem mediation server",
    )
    parser.add_argument("--host", default=None, help="bind address")
    parser.add_argument("--port", type=int, default=None, help="TCP port")
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help="admin panel port (0 disables)",
    )
    parser.add_argument("--max-clients", type=int, default=None)
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if v is not None}
    config = ServerConfig.from_env(**overrides)
    config.validate()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    server = MediationServer(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(server.shutdown()))
        except (NotImplementedError, RuntimeError):
            pass  # Windows: signal handlers fall back to KeyboardInterrupt

    try:
        loop.run_until_complete(server.start())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            loop.run_until_complete(server.shutdown())
        except Exception:
            pass
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
