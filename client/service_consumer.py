"""Service consumer — map remote network services to local ports.

DESIGN.md §4.7: Clients can consume services exposed by other network members.
Each mapped service creates a local TCP or UDP listener; connections to that
local port are forwarded through the P2P tunnel to the service host.

Port mapping strategies:
* ``same`` — use the same port as the remote service (fall back to auto).
* ``auto`` — pick the first available port starting from 50000.
* ``manual`` — use an explicitly specified port.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("localnetwork.client.service_consumer")


@dataclass
class MappedService:
    """A locally-mapped remote service."""

    map_id: str
    service_id: str
    service_name: str
    protocol: str  # "tcp" or "udp"
    provider_id: str  # which peer provides this service
    local_port: int
    strategy: str = "auto"
    remote_port: Optional[int] = None  # the remote service's own port
    created_at: float = field(default_factory=time.time)


class ServiceConsumer:
    """Maps remote network services to local ports.

    Creates local TCP/UDP listeners that forward traffic through P2P tunnels
    to the peer that exposed the service.
    """

    # Port range for auto assignment
    AUTO_PORT_START = 50000
    AUTO_PORT_END = 50999

    def __init__(self, control_channel: Any = None) -> None:
        self._control = control_channel
        self._mapped: Dict[str, MappedService] = {}
        self._servers: Dict[str, asyncio.AbstractServer] = {}
        self._udp_transports: Dict[str, Any] = {}
        self._tunnel_manager: Any = None

    def inject_control(self, control: Any) -> None:
        self._control = control

    def inject_tunnel_manager(self, tm: Any) -> None:
        """Give the consumer access to the tunnel manager."""
        self._tunnel_manager = tm

    # ---- Map / unmap ---------------------------------------------------------
    async def map_service(
        self,
        service_id: str,
        provider_id: str,
        service_name: str = "",
        protocol: str = "tcp",
        local_port: Optional[int] = None,
        strategy: str = "auto",
        remote_port: Optional[int] = None,
    ) -> int:
        """Map a remote service to a local port.

        Args:
            service_id: The service's UUID.
            provider_id: The peer providing the service.
            service_name: Human-readable name.
            protocol: ``"tcp"`` or ``"udp"``.
            local_port: Desired local port (only for ``manual`` strategy).
            strategy: One of ``"same"``, ``"auto"``, ``"manual"``.
            remote_port: The port the service listens on remotely (used by the
                ``"same"`` strategy).

        Returns:
            The local port the service is mapped to.

        Raises:
            OSError: If the port cannot be bound.
            ValueError: If the strategy is unknown.
        """
        # Determine local port
        if strategy == "manual":
            if local_port is None:
                raise ValueError("manual strategy requires local_port")
            port = local_port
        elif strategy == "same":
            port = self._find_same_port(remote_port) or self._find_auto_port()
        elif strategy == "auto":
            port = self._find_auto_port()
        else:
            raise ValueError(f"unknown port strategy: {strategy}")

        map_id = str(uuid.uuid4())
        mapped = MappedService(
            map_id=map_id,
            service_id=service_id,
            service_name=service_name,
            protocol=protocol,
            provider_id=provider_id,
            local_port=port,
            strategy=strategy,
            remote_port=remote_port,
        )
        self._mapped[map_id] = mapped

        if protocol == "tcp":
            await self._start_tcp_listener(mapped)
        elif protocol == "udp":
            await self._start_udp_listener(mapped)

        # Notify server
        if self._control is not None:
            try:
                from common.messages import MapService, make_message

                msg = make_message(
                    MapService,
                    service_id=service_id,
                    local_port=port,
                    strategy=strategy,
                )
                await self._control.send_message(msg)
            except Exception as exc:
                log.debug("map notification failed: %r", exc)

        log.info(
            "mapped service %s → 127.0.0.1:%d (%s)",
            service_name or service_id[:8],
            port,
            protocol,
        )
        return port

    async def unmap_service(self, map_id: str) -> None:
        """Unmap a service and close its local listener."""
        mapped = self._mapped.pop(map_id, None)
        if mapped is None:
            return

        # Close TCP server
        server = self._servers.pop(map_id, None)
        if server is not None:
            server.close()
            await server.wait_closed()

        # Close UDP transport
        transport = self._udp_transports.pop(map_id, None)
        if transport is not None:
            transport.close()

        # Notify server
        if self._control is not None and mapped is not None:
            try:
                from common.messages import UnmapService, make_message

                msg = make_message(UnmapService, service_id=mapped.service_id)
                await self._control.send_message(msg)
            except Exception:
                pass

        log.info("unmapped service %s", mapped.service_name or mapped.service_id[:8])

    # ---- TCP listener --------------------------------------------------------
    async def _start_tcp_listener(self, mapped: MappedService) -> None:
        """Create a TCP server that forwards connections to the remote service."""

        async def handle_connection(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            stream_id = str(uuid.uuid4())
            tm = self._tunnel_manager
            if tm is None:
                writer.close()
                return

            tunnel = tm.get_tunnel(mapped.provider_id)
            if tunnel is None:
                log.debug("no tunnel to %s for service %s", mapped.provider_id, mapped.service_name)
                writer.close()
                return

            # Read from local client → send to peer
            async def forward_local_to_peer() -> None:
                try:
                    while True:
                        data = await reader.read(8192)
                        if not data:
                            break
                        _send_stream_frame(
                            tm, tunnel, mapped.service_id, stream_id, data
                        )
                except (OSError, asyncio.CancelledError):
                    pass

            # Data from peer arrives via feed_stream_data → write to local client
            queue: asyncio.Queue = asyncio.Queue()
            self._stream_queues[stream_id] = queue

            async def forward_peer_to_local() -> None:
                try:
                    while True:
                        data = await queue.get()
                        if data is None:
                            break
                        writer.write(data)
                        await writer.drain()
                except (OSError, asyncio.CancelledError):
                    pass

            try:
                await asyncio.gather(forward_local_to_peer(), forward_peer_to_local())
            except asyncio.CancelledError:
                pass
            finally:
                self._stream_queues.pop(stream_id, None)
                try:
                    writer.close()
                except OSError:
                    pass

        server = await asyncio.start_server(
            handle_connection, "127.0.0.1", mapped.local_port
        )
        self._servers[mapped.map_id] = server

    # ---- UDP listener --------------------------------------------------------
    async def _start_udp_listener(self, mapped: MappedService) -> None:
        """Create a UDP listener that forwards datagrams to the remote service."""
        loop = asyncio.get_running_loop()

        class UDPProtocol(asyncio.DatagramProtocol):
            def __init__(self, consumer, mapped, tm):
                self.consumer = consumer
                self.mapped = mapped
                self.tm = tm

            def datagram_received(self, data, addr):
                tunnel = self.tm.get_tunnel(self.mapped.provider_id) if self.tm else None
                if tunnel is None:
                    return
                stream_id = str(uuid.uuid4())
                _send_stream_frame(
                    self.tm, tunnel, self.mapped.service_id, stream_id, data
                )

        tm = self._tunnel_manager
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: UDPProtocol(self, mapped, tm),
            local_addr=("127.0.0.1", mapped.local_port),
        )
        self._udp_transports[mapped.map_id] = transport

    # ---- Port assignment -----------------------------------------------------
    def _find_auto_port(self) -> int:
        """Find the first free port in the auto range."""
        used = {m.local_port for m in self._mapped.values()}
        for port in range(self.AUTO_PORT_START, self.AUTO_PORT_END + 1):
            if port not in used and self._port_is_free(port):
                return port
        raise OSError("no free ports in auto range")

    def _find_same_port(self, remote_port: Optional[int]) -> Optional[int]:
        """Use the remote service's port locally; returns None if unavailable.

        Args:
            remote_port: The port the service listens on remotely.

        Returns:
            ``remote_port`` if it is free locally, else ``None`` (so the
            caller can fall back to the auto strategy).
        """
        if remote_port is None:
            return None
        if self._port_is_free(remote_port):
            return remote_port
        return None

    @staticmethod
    def _port_is_free(port: int) -> bool:
        """Check if a TCP port is available."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            return True
        except OSError:
            return False

    # ---- Stream data (called by tunnel dispatch) ----------------------------
    _stream_queues: Dict[str, asyncio.Queue] = {}

    def feed_stream_data(self, stream_id: str, data: bytes) -> None:
        """Feed incoming stream data to the appropriate handler."""
        queue = self._stream_queues.get(stream_id)
        if queue is not None:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ---- List ---------------------------------------------------------------
    def list_mapped(self) -> List[MappedService]:
        return list(self._mapped.values())

    # ---- Shutdown -----------------------------------------------------------
    async def shutdown(self) -> None:
        """Close all local listeners."""
        for map_id in list(self._mapped.keys()):
            await self.unmap_service(map_id)


def _send_stream_frame(
    tunnel_manager: Any,
    tunnel: Any,
    service_id: str,
    stream_id: str,
    data: bytes,
) -> None:
    """Send a stream frame through the tunnel with service/stream metadata."""
    sid = service_id.encode("ascii")[:36].ljust(36, b"\x00")
    stid = stream_id.encode("ascii")[:36].ljust(36, b"\x00")
    associated = sid + stid
    tunnel_manager.send_data(tunnel, associated + data)


__all__ = ["MappedService", "ServiceConsumer"]
