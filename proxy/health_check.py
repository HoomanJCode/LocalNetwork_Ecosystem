"""Passive health checks for upstream servers (DESIGN.md §7, Phase 19).

Monitors upstream connection failures. After N consecutive failures within
a time window, marks the server as unavailable. After fail_timeout seconds,
attempts a single probe; on success, restores availability.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("localnetwork.proxy.health")


@dataclass
class ServerHealth:
    """Health state for one upstream server."""

    host: str
    port: int
    failures: int = 0
    last_failure: float = 0.0
    unavailable: bool = False
    unavailable_since: float = 0.0
    total_failures: int = 0


class HealthMonitor:
    """Tracks health of upstream servers based on connection failures."""

    def __init__(self, max_failures: int = 3, fail_timeout: float = 10.0) -> None:
        self.max_failures = max_failures
        self.fail_timeout = fail_timeout
        self._servers: Dict[str, ServerHealth] = {}

    def _key(self, host: str, port: int) -> str:
        return f"{host}:{port}"

    def record_failure(self, host: str, port: int) -> None:
        """Record a connection failure for a server."""
        key = self._key(host, port)
        health = self._servers.get(key)
        if health is None:
            health = ServerHealth(host=host, port=port)
            self._servers[key] = health

        now = time.monotonic()
        # Reset failure count if outside the time window
        if now - health.last_failure > self.fail_timeout:
            health.failures = 0

        health.failures += 1
        health.total_failures += 1
        health.last_failure = now

        if health.failures >= self.max_failures:
            health.unavailable = True
            health.unavailable_since = now
            log.warning("server %s marked unavailable after %d failures", key, health.failures)

    def record_success(self, host: str, port: int) -> None:
        """Record a successful connection."""
        key = self._key(host, port)
        health = self._servers.get(key)
        if health is None:
            return
        health.failures = 0
        health.unavailable = False

    def is_available(self, host: str, port: int) -> bool:
        """Check if a server is currently available.

        Automatically retries servers that have been unavailable for
        longer than fail_timeout.
        """
        key = self._key(host, port)
        health = self._servers.get(key)
        if health is None:
            return True
        if not health.unavailable:
            return True

        # Retry after fail_timeout
        if time.monotonic() - health.unavailable_since > self.fail_timeout:
            health.unavailable = False
            health.failures = 0
            log.info("server %s retried after fail_timeout", key)
            return True
        return False

    def get_health(self, host: str, port: int) -> Optional[ServerHealth]:
        """Get health info for a server."""
        return self._servers.get(self._key(host, port))

    def list_all(self) -> List[ServerHealth]:
        """List all tracked servers."""
        return list(self._servers.values())


__all__ = ["ServerHealth", "HealthMonitor"]
