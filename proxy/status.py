"""Proxy runtime status collection and monitoring (DESIGN.md §7.14, Phase 21).

Tracks connection counters (accepted, handled, active) and nginx-style
connection-state categories (reading, writing, waiting), and can summarize
upstream health for the ``GET /proxy-status`` admin endpoint.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Connection-state categories tracked by the status collector.
STATE_READING = "reading"
STATE_WRITING = "writing"
STATE_WAITING = "waiting"


class StatusCollector:
    """Collects and reports proxy runtime statistics."""

    def __init__(self, start_time: float | None = None) -> None:
        self._start_time = start_time or time.time()
        self._accepted: int = 0
        self._handled: int = 0
        self._active: int = 0
        self._requests: int = 0
        self._reading: int = 0
        self._writing: int = 0
        self._waiting: int = 0

    def increment_accepted(self) -> None:
        self._accepted += 1
        self._active += 1

    def decrement_active(self) -> None:
        self._active = max(0, self._active - 1)

    def increment_handled(self) -> None:
        self._handled += 1
        self._requests += 1

    # ---- Connection-state categories ---------------------------------------
    def enter_state(self, category: Optional[str]) -> None:
        """Increment the counter for a connection-state category."""
        if category == STATE_READING:
            self._reading += 1
        elif category == STATE_WRITING:
            self._writing += 1
        elif category == STATE_WAITING:
            self._waiting += 1

    def leave_state(self, category: Optional[str]) -> None:
        """Decrement the counter for a connection-state category."""
        if category == STATE_READING:
            self._reading = max(0, self._reading - 1)
        elif category == STATE_WRITING:
            self._writing = max(0, self._writing - 1)
        elif category == STATE_WAITING:
            self._waiting = max(0, self._waiting - 1)

    def transition_state(self, old: Optional[str], new: Optional[str]) -> None:
        """Move a connection from one state category to another."""
        self.leave_state(old)
        self.enter_state(new)

    # ---- Snapshot -----------------------------------------------------------
    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of current stats (DESIGN.md §7.14 shape)."""
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "active_connections": self._active,
            "accepted_connections": self._accepted,
            "handled_connections": self._handled,
            "total_requests": self._requests,
            "reading": self._reading,
            "writing": self._writing,
            "waiting": self._waiting,
        }


def upstream_summary(
    upstreams: Optional[List[Any]] = None,
    health_monitor: Any = None,
    balancers: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Summarize upstream groups for the status endpoint (DESIGN.md §7.14).

    Args:
        upstreams: Iterable of :class:`proxy.config.UpstreamBlock`.
        health_monitor: A :class:`proxy.health_check.HealthMonitor` (or None).
        balancers: Mapping of upstream name → load balancer instance.

    Returns:
        A list of ``{"name", "servers": [{"host", "state", "active", "failures"}]}``
        dictionaries, one per upstream group.
    """
    groups: List[Dict[str, Any]] = []
    for upstream in upstreams or []:
        balancer = (balancers or {}).get(getattr(upstream, "name", ""))
        servers: List[Dict[str, Any]] = []
        for server in getattr(upstream, "servers", []):
            host = getattr(server, "host", "")
            port = getattr(server, "port", 80)
            health = None
            if health_monitor is not None:
                health = health_monitor.get_health(host, port)
            if getattr(server, "down", False):
                state = "down"
            elif health is not None and health.unavailable:
                state = "unavailable"
            else:
                state = "up"
            servers.append(
                {
                    "host": f"{host}:{port}",
                    "state": state,
                    "active": _active_connections(balancer, host, port),
                    "failures": health.total_failures if health is not None else 0,
                }
            )
        groups.append({"name": getattr(upstream, "name", ""), "servers": servers})
    return groups


def _active_connections(balancer: Any, host: str, port: int) -> int:
    """Best-effort active connection count for a server from its balancer."""
    if balancer is None:
        return 0
    conns = getattr(balancer, "_active_conns", None)
    if isinstance(conns, dict):
        return int(conns.get(f"{host}:{port}", 0))
    return 0


__all__ = [
    "StatusCollector",
    "upstream_summary",
    "STATE_READING",
    "STATE_WRITING",
    "STATE_WAITING",
]
