"""Keep-alive manager for P2P tunnels.

Runs as a background asyncio task; sends KEEPALIVE frames on every tunnel and
prunes connections that have gone silent.

Lifecycle::

    every 10s               every 30s                every 60s
    ─────────               ─────────                ─────────
    send KEEPALIVE   →      mark SUSPECT      →      close tunnel
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from client.nat_traversal import PunchState
from common.constants import KEEPALIVE_INTERVAL, TUNNEL_STALE_TIMEOUT

log = logging.getLogger("localnetwork.client.keepalive")

KEEPALIVE_SUSPECT_TIMEOUT = 30.0  # mark tunnel as suspect


class KeepAliveManager:
    """Periodically sends KEEPALIVE frames and prunes stale tunnels."""

    def __init__(
        self,
        manager: Any,  # TunnelManager
        interval: float = KEEPALIVE_INTERVAL,
        suspect_timeout: float = KEEPALIVE_SUSPECT_TIMEOUT,
        stale_timeout: float = TUNNEL_STALE_TIMEOUT,
    ) -> None:
        self._manager = manager
        self.interval = interval
        self.suspect_timeout = suspect_timeout
        self.stale_timeout = stale_timeout
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._suspect_peers: set = set()

    async def run(self) -> None:
        """Run the keep-alive loop (never returns until cancelled)."""
        self._running = True
        while self._running:
            await asyncio.sleep(self.interval)
            try:
                self._tick()
            except Exception as exc:
                log.debug("keepalive tick error: %r", exc)

    def _tick(self) -> None:
        """One keep-alive cycle."""
        now = time.monotonic()
        for tunnel in self._manager.list_tunnels():
            if tunnel.state != PunchState.CONNECTED:
                continue
            # Send keepalive
            self._manager.send_keepalive(tunnel)

            # Check staleness
            if tunnel.last_rx > 0:
                since_rx = now - tunnel.last_rx
                if since_rx > self.stale_timeout:
                    log.warning(
                        "tunnel to %s is stale (%.0fs silent), closing",
                        tunnel.peer_id,
                        since_rx,
                    )
                    self._manager.close_tunnel(tunnel)
                    self._suspect_peers.discard(tunnel.peer_id)
                elif since_rx > self.suspect_timeout:
                    if tunnel.peer_id not in self._suspect_peers:
                        log.info(
                            "tunnel to %s is suspect (%.0fs silent)",
                            tunnel.peer_id,
                            since_rx,
                        )
                        self._suspect_peers.add(tunnel.peer_id)
                else:
                    # Back to healthy
                    self._suspect_peers.discard(tunnel.peer_id)

    @property
    def suspect_peers(self) -> frozenset:
        """Peer IDs currently marked as suspect."""
        return frozenset(self._suspect_peers)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def wait_closed(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass


__all__ = ["KeepAliveManager", "KEEPALIVE_SUSPECT_TIMEOUT"]
