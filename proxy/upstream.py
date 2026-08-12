"""Upstream backend pool management (DESIGN.md Phase 18).

Manages connections to backend servers with keep-alive pooling,
per-server tracking (active connections, failure count), and
load balancer integration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("localnetwork.proxy.upstream")


@dataclass
class PooledConnection:
    """A reusable keep-alive connection to a backend server."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    host: str
    port: int
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False


class UpstreamPool:
    """Manages a pool of upstream backend connections.

    Features:
    * Connection keep-alive: idle connections reused across requests
    * Per-server tracking: active connections, failure count
    * Configurable max idle connections per server
    * Automatic idle connection cleanup
    """

    def __init__(self, max_idle_per_server: int = 32, idle_timeout: float = 60.0) -> None:
        self.max_idle_per_server = max_idle_per_server
        self.idle_timeout = idle_timeout
        self._pools: Dict[str, List[PooledConnection]] = {}  # "host:port" → [conns]
        self._server_stats: Dict[str, dict] = {}

    def _key(self, host: str, port: int) -> str:
        return f"{host}:{port}"

    async def get_connection(
        self, host: str, port: int, timeout: float = 5.0
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Get a connection to a backend server.

        Returns a pooled keep-alive connection if available, otherwise
        opens a new one.
        """
        key = self._key(host, port)
        now = time.time()

        # Try to find an idle pooled connection
        pool = self._pools.get(key, [])
        for conn in pool:
            if not conn.in_use and not self._is_stale(conn, now):
                conn.in_use = True
                conn.last_used = now
                return conn.reader, conn.writer

        # Open a new connection
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        conn = PooledConnection(reader=reader, writer=writer, host=host, port=port)
        conn.in_use = True
        if key not in self._pools:
            self._pools[key] = []
        self._pools[key].append(conn)

        return reader, writer

    def release(self, host: str, port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Return a connection to the pool for reuse."""
        key = self._key(host, port)
        pool = self._pools.get(key, [])
        for conn in pool:
            if conn.writer is writer:
                conn.in_use = False
                conn.last_used = time.time()
                return
        # Connection not tracked — close it
        try:
            writer.close()
        except OSError:
            pass

    def close_connection(self, host: str, port: int, writer: asyncio.StreamWriter) -> None:
        """Remove and close a specific connection."""
        key = self._key(host, port)
        pool = self._pools.get(key, [])
        for conn in list(pool):
            if conn.writer is writer:
                pool.remove(conn)
                try:
                    conn.writer.close()
                except OSError:
                    pass
                return

    def prune_idle(self) -> int:
        """Close idle connections that exceed the idle timeout.

        Returns the number of connections pruned.
        """
        now = time.time()
        pruned = 0
        for key, pool in list(self._pools.items()):
            for conn in list(pool):
                if not conn.in_use and self._is_stale(conn, now):
                    pool.remove(conn)
                    try:
                        conn.writer.close()
                    except OSError:
                        pass
                    pruned += 1
            if not pool:
                del self._pools[key]
        return pruned

    def _is_stale(self, conn: PooledConnection, now: float) -> bool:
        return (now - conn.last_used) > self.idle_timeout

    @property
    def active_connections(self) -> int:
        """Count of connections currently in use."""
        count = 0
        for pool in self._pools.values():
            count += sum(1 for c in pool if c.in_use)
        return count

    @property
    def idle_connections(self) -> int:
        """Count of idle connections in the pool."""
        count = 0
        for pool in self._pools.values():
            count += sum(1 for c in pool if not c.in_use)
        return count

    def shutdown(self) -> None:
        """Close all pooled connections."""
        for pool in self._pools.values():
            for conn in pool:
                try:
                    conn.writer.close()
                except OSError:
                    pass
        self._pools.clear()


__all__ = ["PooledConnection", "UpstreamPool"]
