"""Service exposure manager — expose local TCP/UDP services to the network.

DESIGN.md §4.7: Clients can expose specific local services to other network
members without TUN or root. Each exposed service is registered with the
mediation server and handles incoming forwarded streams from peers.

Architecture::

    Local Service ←→ ServiceExposureManager ←→ TunnelManager ←→ Peer

Stream lifecycle: open → active → closed (either side disconnects).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.constants import FRAME_FORWARDED_STREAM

log = logging.getLogger("localnetwork.client.service_exposure")


@dataclass
class ServiceRecord:
    """A service exposed by this client."""

    service_id: str
    name: str
    protocol: str  # "tcp" or "udp"
    local_host: str
    local_port: int
    created_at: float = field(default_factory=time.time)


@dataclass
class ActiveStream:
    """An active forwarded stream for a service connection."""

    stream_id: str
    service_id: str
    peer_id: str
    local_reader: Optional[asyncio.StreamReader] = None
    local_writer: Optional[asyncio.StreamWriter] = None
    udp_transport: Any = None
    created_at: float = field(default_factory=time.time)


class ServiceExposureManager:
    """Manages service exposure: register/unregister and handle incoming streams.

    Incoming forwarded streams from peers connect to the local service and
    bidirectionally pipe data between the P2P tunnel and the local socket.
    """

    def __init__(self, control_channel: Any = None) -> None:
        self._control = control_channel
        self._services: Dict[str, ServiceRecord] = {}
        self._streams: Dict[str, ActiveStream] = {}
        self._udp_sockets: Dict[str, Any] = {}  # service_id → UDP socket

    def inject_control(self, control: Any) -> None:
        """Give the manager access to the control channel."""
        self._control = control

    # ---- Expose / unexpose --------------------------------------------------
    async def expose(
        self,
        name: str,
        protocol: str,
        local_host: str = "127.0.0.1",
        local_port: int = 0,
    ) -> str:
        """Register a local service with the mediation server.

        Args:
            name: Human-readable name (e.g., "minecraft", "web").
            protocol: ``"tcp"`` or ``"udp"``.
            local_host: Where the service is running (usually 127.0.0.1).
            local_port: Port the service is listening on.

        Returns:
            The service ID (UUID).
        """
        service_id = str(uuid.uuid4())
        record = ServiceRecord(
            service_id=service_id,
            name=name,
            protocol=protocol,
            local_host=local_host,
            local_port=local_port,
        )
        self._services[service_id] = record

        if self._control is not None:
            try:
                from common.messages import ExposeService, make_message

                msg = make_message(
                    ExposeService,
                    name=name,
                    protocol=protocol,
                    local_host=local_host,
                    local_port=local_port,
                )
                await self._control.send_message(msg)
            except Exception as exc:
                log.warning("failed to register service %s: %r", name, exc)

        log.info("exposed service %s (%s:%d/%s)", name, local_host, local_port, protocol)
        return service_id

    async def unexpose(self, service_id: str) -> None:
        """Unregister a service and close all active streams."""
        record = self._services.pop(service_id, None)
        if record is None:
            return

        # Close all streams for this service
        for stream_id, stream in list(self._streams.items()):
            if stream.service_id == service_id:
                await self._close_stream(stream)

        # Close UDP socket if any
        sock = self._udp_sockets.pop(service_id, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        if self._control is not None:
            try:
                from common.messages import UnexposeService, make_message

                msg = make_message(UnexposeService, service_id=service_id)
                await self._control.send_message(msg)
            except Exception as exc:
                log.debug("failed to unregister service: %r", exc)

        log.info("unexposed service %s", record.name)

    # ---- Handle incoming streams ---------------------------------------------
    async def handle_incoming_stream(
        self,
        service_id: str,
        stream_id: str,
        peer_id: str,
        tunnel_manager: Any,
    ) -> None:
        """Accept a new forwarded stream from a peer.

        Opens a connection to the local service and starts bidirectional
        forwarding through the P2P tunnel.

        Args:
            service_id: Which service this stream targets.
            stream_id: Unique stream identifier (from peer).
            peer_id: The peer that initiated the stream.
            tunnel_manager: The TunnelManager for sending data back.
        """
        record = self._services.get(service_id)
        if record is None:
            log.debug("incoming stream for unknown service %s", service_id)
            return

        stream = ActiveStream(
            stream_id=stream_id,
            service_id=service_id,
            peer_id=peer_id,
        )
        self._streams[stream_id] = stream

        if record.protocol == "tcp":
            asyncio.create_task(
                self._handle_tcp_stream(stream, record, tunnel_manager)
            )
        elif record.protocol == "udp":
            asyncio.create_task(
                self._handle_udp_stream(stream, record, tunnel_manager)
            )

    async def _handle_tcp_stream(
        self,
        stream: ActiveStream,
        record: ServiceRecord,
        tunnel_manager: Any,
    ) -> None:
        """Handle a TCP stream: connect to local service, bidirectionally pipe."""
        try:
            reader, writer = await asyncio.open_connection(
                record.local_host, record.local_port
            )
            stream.local_reader = reader
            stream.local_writer = writer
        except OSError as exc:
            log.debug("cannot connect to %s:%d: %r", record.local_host, record.local_port, exc)
            return

        tunnel = tunnel_manager.get_tunnel(stream.peer_id)
        if tunnel is None:
            writer.close()
            return

        async def forward_from_local() -> None:
            """Read from local service → send through tunnel."""
            try:
                while True:
                    data = await reader.read(8192)
                    if not data:
                        break
                    _send_stream_frame(
                        tunnel_manager, tunnel, stream.service_id, stream.stream_id, data
                    )
            except (OSError, asyncio.CancelledError):
                pass

        async def forward_from_peer(data_queue: asyncio.Queue) -> None:
            """Read from queue (filled by tunnel recv) → write to local service."""
            try:
                while True:
                    data = await data_queue.get()
                    if data is None:
                        break
                    writer.write(data)
                    await writer.drain()
            except (OSError, asyncio.CancelledError):
                pass

        queue: asyncio.Queue = asyncio.Queue()
        self._stream_queues[stream.stream_id] = queue

        try:
            await asyncio.gather(forward_from_local(), forward_from_peer(queue))
        except asyncio.CancelledError:
            pass
        finally:
            await self._close_stream(stream)

    async def _handle_udp_stream(
        self,
        stream: ActiveStream,
        record: ServiceRecord,
        tunnel_manager: Any,
    ) -> None:
        """Handle a UDP stream: relay datagrams to/from the local UDP service."""
        import socket

        loop = asyncio.get_running_loop()

        # Create or reuse UDP socket for this service
        sock = self._udp_sockets.get(record.service_id)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", 0))
            self._udp_sockets[record.service_id] = sock

        stream.udp_transport = sock

    # ---- Stream data (called by tunnel dispatch) ----------------------------
    def feed_stream_data(self, stream_id: str, data: bytes) -> None:
        """Feed incoming stream data to the appropriate handler."""
        queue = getattr(self, "_stream_queues", {}).get(stream_id)
        if queue is not None:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ---- Internal -----------------------------------------------------------
    _stream_queues: Dict[str, asyncio.Queue] = {}

    async def _close_stream(self, stream: ActiveStream) -> None:
        """Close a stream and clean up resources."""
        stream_id = stream.stream_id
        self._streams.pop(stream_id, None)
        self._stream_queues.pop(stream_id, None)
        if stream.local_writer is not None:
            try:
                stream.local_writer.close()
            except OSError:
                pass

    def list_exposed(self) -> List[ServiceRecord]:
        """Return all currently exposed services."""
        return list(self._services.values())

    def shutdown(self) -> None:
        """Close all streams and unexpose all services."""
        for stream in list(self._streams.values()):
            asyncio.create_task(self._close_stream(stream))
        for sock in self._udp_sockets.values():
            try:
                sock.close()
            except OSError:
                pass
        self._services.clear()
        self._streams.clear()
        self._udp_sockets.clear()


def _send_stream_frame(
    tunnel_manager: Any,
    tunnel: Any,
    service_id: str,
    stream_id: str,
    data: bytes,
) -> None:
    """Send a FORWARDED_STREAM frame through a tunnel.

    The associated data (service_id + stream_id) is prepended to the
    plaintext before encryption.
    """
    import struct

    # Associated data: service_id (36 bytes UUID) + stream_id (36 bytes UUID)
    sid = service_id.encode("ascii")[:36].ljust(36, b"\x00")
    stid = stream_id.encode("ascii")[:36].ljust(36, b"\x00")
    associated = sid + stid

    # Encrypt with associated data for frame type discrimination
    payload = associated + data
    tunnel_manager.send_data(tunnel, payload)


def decode_stream_frame(data: bytes) -> tuple:
    """Decode a FORWARDED_STREAM frame's associated data and payload.

    Returns:
        ``(service_id, stream_id, payload)`` or ``("", "", data)`` on failure.
    """
    if len(data) < 72:
        return "", "", data
    service_id = data[:36].rstrip(b"\x00").decode("ascii", errors="replace")
    stream_id = data[36:72].rstrip(b"\x00").decode("ascii", errors="replace")
    payload = data[72:]
    return service_id, stream_id, payload


__all__ = [
    "ServiceRecord",
    "ActiveStream",
    "ServiceExposureManager",
    "decode_stream_frame",
]
