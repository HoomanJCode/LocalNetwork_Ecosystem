"""Reverse proxy worker process (DESIGN.md §7.2).

Each worker runs an asyncio event loop, accepts connections from the shared
listen sockets, and handles each client with the :class:`proxy.connection.Connection`
HTTP state machine (routing, load balancing, health checks, compression,
access logging, and status tracking).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Dict, List, Optional

from proxy.compression import GzipCompressor
from proxy.config import ProxyConfig
from proxy.connection import Connection
from proxy.health_check import HealthMonitor
from proxy.load_balancer import create_balancer
from proxy.logging import AccessLogger
from proxy.status import StatusCollector

log = logging.getLogger("localnetwork.proxy.worker")


class WorkerProcess:
    """Async worker that handles client connections."""

    def __init__(
        self,
        worker_id: int,
        config: ProxyConfig,
        listen_sockets: Dict[int, socket.socket],
    ) -> None:
        self.worker_id = worker_id
        self.config = config
        self._listen_sockets = listen_sockets

        # Build the runtime components for this worker from config.
        self.upstreams = {u.name: u for u in config.upstreams}
        self.balancers = {
            u.name: create_balancer(u.algorithm) for u in config.upstreams
        }
        self.health_monitor = HealthMonitor()
        self.compressor = (
            GzipCompressor(
                level=config.gzip_level, min_length=config.gzip_min_length
            )
            if config.gzip_enabled
            else None
        )
        self.access_logger = (
            AccessLogger(config.access_log, config.log_format)
            if config.access_log
            else None
        )
        self.status_collector = StatusCollector()

    def run(self) -> None:
        """Start the worker's event loop and accept connections."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle one client connection with the HTTP engine."""
        connection = Connection(
            reader,
            writer,
            self.config,
            self.upstreams,
            self.balancers,
            self.health_monitor,
            self.compressor,
            self.access_logger,
            self.status_collector,
        )
        await connection.handle()

    async def _serve(self) -> None:
        """Accept connections from all listen sockets."""
        log.info("worker %d starting", self.worker_id)

        if self.access_logger is not None:
            await self.access_logger.start()

        # Create asyncio servers for each listen socket
        servers: List[asyncio.AbstractServer] = []
        for _port, sock in self._listen_sockets.items():
            server = await asyncio.start_server(self.handle_connection, sock=sock)
            servers.append(server)

        log.info("worker %d ready (pid=%d)", self.worker_id, os.getpid())

        # Keep running until interrupted
        try:
            await asyncio.Future()  # Wait forever
        except asyncio.CancelledError:
            pass
        finally:
            for server in servers:
                server.close()
                await server.wait_closed()
            if self.access_logger is not None:
                await self.access_logger.stop()
            log.info("worker %d stopped", self.worker_id)


__all__ = ["WorkerProcess"]
