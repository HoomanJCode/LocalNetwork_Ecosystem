"""Relay fallback for clients that cannot establish direct P2P tunnels.

The server relays **opaque** data frames between peers over their existing
control-channel TCP connections. It only inspects the outer frame header
(never the encrypted payload), preserving end-to-end encryption
(DESIGN.md §3.2).

Wire protocol: relayed frames travel inside ``RELAY_FRAME`` control messages
(base64-encoded), multiplexed on the client's control channel.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Dict, List, Optional, Set, Tuple

from common import constants
from common.messages import RelayFrame, RelayGranted, make_message

log = logging.getLogger("localnetwork.server.relay")

MAX_RELAY_QUEUE = 1024  # frames buffered per destination before backpressure


class RelayForwarder:
    """Per-destination queues of (src_id, raw_frame) relayed data."""

    def __init__(self, server) -> None:
        self.server = server
        self._queues: Dict[str, asyncio.Queue] = {}
        self._paths: Set[Tuple[str, str]] = set()
        self._bytes_relayed: Dict[Tuple[str, str], int] = {}
        self._dropped_frames: int = 0

    # ------------------------------------------------------------------
    # Path management
    # ------------------------------------------------------------------
    def register_relay_path(self, src_id: str, dst_id: str) -> bool:
        """Allocate (and enqueue-ready) relay channels for both directions.

        Returns:
            True if a **new** path was allocated, False if it already existed.
        """
        self._queue_for(dst_id)
        self._queue_for(src_id)
        pair = (src_id, dst_id)
        is_new = pair not in self._paths
        self._paths.add(pair)
        self._paths.add((dst_id, src_id))
        return is_new

    def _queue_for(self, client_id: str) -> asyncio.Queue:
        queue = self._queues.get(client_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=MAX_RELAY_QUEUE)
            self._queues[client_id] = queue
        return queue

    def has_path(self, src_id: str, dst_id: str) -> bool:
        return (src_id, dst_id) in self._paths

    # ------------------------------------------------------------------
    # Data plane
    # ------------------------------------------------------------------
    def relay_frame(self, src_id: str, dst_id: str, raw_frame: bytes) -> bool:
        """Queue a raw frame for delivery to ``dst_id``.

        Returns:
            True if queued, False if the queue is full (backpressure).
        """
        queue = self._queues.get(dst_id)
        if queue is None or not self.has_path(src_id, dst_id):
            return False
        try:
            queue.put_nowait((src_id, raw_frame))
        except asyncio.QueueFull:
            self._dropped_frames += 1
            log.warning("relay queue full for %s; dropping frame", dst_id)
            return False
        key = (src_id, dst_id)
        self._bytes_relayed[key] = self._bytes_relayed.get(key, 0) + len(raw_frame)
        return True

    def pending_frames(self, dst_id: str) -> List[Tuple[str, str]]:
        """Drain and return all queued frames for a client as ``(src, b64)``."""
        queue = self._queues.get(dst_id)
        if queue is None or queue.empty():
            return []
        out: List[Tuple[str, str]] = []
        while not queue.empty():
            src_id, raw = queue.get_nowait()
            out.append((src_id, base64.b64encode(raw).decode("ascii")))
        return out

    async def consume_frames(
        self, client_id: str
    ) -> "asyncio.AsyncIterator[Tuple[str, bytes]]":
        """Async iterator yielding ``(src_id, raw_frame)`` for a client."""
        queue = self._queues.get(client_id)
        if queue is None:
            return
        while True:
            yield await queue.get()

    # ------------------------------------------------------------------
    # Server integration
    # ------------------------------------------------------------------
    async def handle_relay_request(
        self,
        src_id: str,
        dst_id: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a RELAY_REQUEST: validate, allocate paths, grant both sides."""
        registry = self.server.registry
        src_record = registry.get(src_id)
        dst_record = registry.get(dst_id)
        if src_record is None or dst_record is None:
            await self.server._send_error(
                writer, "RELAY_FAILED", "unknown peer"
            )
            return
        if not (src_record.online and dst_record.online):
            await self.server._send_error(
                writer, "RELAY_FAILED", "peer is not online"
            )
            return
        shared = self.server.networks.list_for_client(src_id)
        if not any(dst_id in self.server.networks.members(n.network_id) for n in shared):
            await self.server._send_error(
                writer, "RELAY_FAILED", "no shared network with that peer"
            )
            return

        self.register_relay_path(src_id, dst_id)
        await self.server._send(
            writer,
            make_message(
                RelayGranted, peer_id=dst_id, path_id=f"{src_id}->{dst_id}"
            ),
        )
        # Notify the destination so it can start sending via relay too.
        dst_writer = self.server._writers.get(dst_id)
        if dst_writer is not None:
            await self.server._send(
                dst_writer,
                make_message(
                    RelayGranted, peer_id=src_id, path_id=f"{dst_id}->{src_id}"
                ),
            )
        log.info("relay path granted %s <-> %s", src_id, dst_id)

    async def deliver_relayed(self, client_id: str, writer: asyncio.StreamWriter) -> None:
        """Flush any queued relay frames onto a client's control channel."""
        for src_id, frame_b64 in self.pending_frames(client_id):
            await self.server._send(
                writer,
                make_message(
                    RelayFrame, src_id=src_id, dst_id=client_id, frame_b64=frame_b64
                ),
            )

    def drop_client(self, client_id: str) -> None:
        """Remove all queues and paths involving a disconnected client."""
        self._queues.pop(client_id, None)
        self._paths = {
            (a, b) for (a, b) in self._paths if a != client_id and b != client_id
        }
        self._bytes_relayed = {
            k: v for k, v in self._bytes_relayed.items() if client_id not in k
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "paths": len(self._paths) // 2,
            "queues": len(self._queues),
            "bytes_relayed": sum(self._bytes_relayed.values()),
            "dropped_frames": self._dropped_frames,
        }


__all__ = ["RelayForwarder", "MAX_RELAY_QUEUE"]
