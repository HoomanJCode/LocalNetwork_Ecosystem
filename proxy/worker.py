"""Reverse proxy worker process (DESIGN.md §7.2).

Each worker runs an asyncio event loop, accepts connections from the shared
listen sockets, and spawns Connection coroutines for each client.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Dict

from proxy.config import ProxyConfig

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
        self._connections = 0

    def run(self) -> None:
        """Start the worker's event loop and accept connections."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()

    async def _serve(self) -> None:
        """Accept connections from all listen sockets."""
        log.info("worker %d starting", self.worker_id)

        # Create asyncio servers for each listen socket
        servers = []
        for port, sock in self._listen_sockets.items():
            server = await asyncio.get_running_loop().create_server(
                lambda: _ConnectionProtocol(self),
                sock=sock,
            )
            servers.append(server)

        log.info("worker %d ready (pid=%d)", self.worker_id, __import__("os").getpid())

        # Keep running until interrupted
        try:
            await asyncio.Future()  # Wait forever
        except asyncio.CancelledError:
            pass
        finally:
            for server in servers:
                server.close()
            log.info("worker %d stopped", self.worker_id)


class _ConnectionProtocol(asyncio.Protocol):
    """Minimal protocol that handles incoming connections."""

    def __init__(self, worker: WorkerProcess) -> None:
        self.worker = worker
        self.transport: asyncio.Transport | None = None
        self._buffer = b""

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.transport = transport
        self.worker._connections += 1
        peername = transport.get_extra_info("peername", ("?", 0))
        log.debug("worker %d: connection from %s:%d", self.worker.worker_id, *peername)

    def data_received(self, data: bytes) -> None:
        """Process incoming HTTP data."""
        # For now, echo basic response
        self._buffer += data
        if b"\r\n\r\n" in self._buffer:
            self._handle_request()

    def _handle_request(self) -> None:
        """Minimal HTTP request handler."""
        import time

        body = (
            "<html><body><h1>LocalNetwork Proxy</h1>"
            f"<p>Worker {self.worker.worker_id}</p>"
            "<p>The reverse proxy is running.</p>"
            "</body></html>"
        )
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            f"Server: LocalNetworkProxy/0.1.0\r\n"
            "\r\n"
            f"{body}"
        )
        if self.transport:
            self.transport.write(response.encode())
            self.transport.close()

    def connection_lost(self, exc: Exception | None) -> None:
        self.worker._connections -= 1
