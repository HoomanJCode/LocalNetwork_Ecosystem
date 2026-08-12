"""TCP/UDP stream proxying (DESIGN.md Phase 21).

Routes raw TCP and UDP connections to backend servers using the same
load balancing algorithms as HTTP. No HTTP-level processing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Tuple

log = logging.getLogger("localnetwork.proxy.stream")


class StreamProxy:
    """TCP/UDP stream proxy handler."""

    def __init__(
        self,
        upstream_pool: Any = None,
        balancers: dict = None,
        config: Any = None,
    ) -> None:
        self.upstream_pool = upstream_pool
        self.balancers = balancers or {}
        self.config = config

    async def handle_tcp(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_name: str,
    ) -> None:
        """Proxy a TCP stream to a backend server.

        Opens a connection to the upstream, then bidirectionally pipes
        data between client and backend.
        """
        balancer = self.balancers.get(upstream_name)
        upstream_cfg = None
        if self.config:
            for up in getattr(self.config, "upstreams", []):
                if getattr(up, "name", "") == upstream_name:
                    upstream_cfg = up
                    break

        if balancer is None or upstream_cfg is None:
            client_writer.close()
            return

        servers = getattr(upstream_cfg, "servers", [])
        peername = client_writer.get_extra_info("peername")
        client_ip = peername[0] if peername else "127.0.0.1"

        server = balancer.select(servers, client_ip=client_ip)
        if server is None:
            client_writer.close()
            return

        host = getattr(server, "host", "")
        port = getattr(server, "port", 80)

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10.0
            )
        except (OSError, asyncio.TimeoutError):
            client_writer.close()
            return

        async def pipe(src_reader, dst_writer) -> None:
            try:
                while True:
                    data = await src_reader.read(8192)
                    if not data:
                        break
                    dst_writer.write(data)
                    await dst_writer.drain()
            except (OSError, asyncio.CancelledError):
                pass

        try:
            await asyncio.gather(
                pipe(client_reader, upstream_writer),
                pipe(upstream_reader, client_writer),
            )
        except asyncio.CancelledError:
            pass
        finally:
            try:
                upstream_writer.close()
            except OSError:
                pass
            try:
                client_writer.close()
            except OSError:
                pass

    async def handle_udp(
        self,
        data: bytes,
        client_addr: Tuple[str, int],
        upstream_name: str,
    ) -> Optional[bytes]:
        """Proxy a UDP datagram to a backend server.

        Returns the upstream response, or None on failure.
        """
        import socket

        balancer = self.balancers.get(upstream_name)
        upstream_cfg = None
        if self.config:
            for up in getattr(self.config, "upstreams", []):
                if getattr(up, "name", "") == upstream_name:
                    upstream_cfg = up
                    break

        if balancer is None or upstream_cfg is None:
            return None

        servers = getattr(upstream_cfg, "servers", [])
        server = balancer.select(servers, client_ip=client_addr[0])
        if server is None:
            return None

        host = getattr(server, "host", "")
        port = getattr(server, "port", 80)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5.0)
        try:
            sock.sendto(data, (host, port))
            response, _ = sock.recvfrom(65535)
            return response
        except OSError:
            return None
        finally:
            sock.close()


__all__ = ["StreamProxy"]
