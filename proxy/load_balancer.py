"""Load balancing algorithms (DESIGN.md §7, Phase 19).

Supported algorithms:
* ``round_robin`` — weighted round-robin.
* ``least_conn`` — server with fewest active connections.
* ``ip_hash`` — hash client IP to deterministic server.
* ``random`` — weighted random selection.
"""

from __future__ import annotations

import hashlib
import itertools
import random
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from proxy.config import UpstreamServer


class LoadBalancer(ABC):
    """Abstract load balancer."""

    @abstractmethod
    def select(self, servers: List[Any], client_ip: str = "") -> Optional[Any]:
        """Pick one server from the list.

        Args:
            servers: Available upstream servers (UpstreamServer or similar).
            client_ip: Client IP for hash-based algorithms.

        Returns:
            Selected server, or None if no server is available.
        """


class RoundRobinBalancer(LoadBalancer):
    """Weighted round-robin selection."""

    def __init__(self) -> None:
        self._index = 0

    def select(self, servers: List[Any], client_ip: str = "") -> Optional[Any]:
        available = [s for s in servers if not getattr(s, "down", False) and not getattr(s, "backup", False)]
        if not available:
            # Fall back to backup servers
            available = [s for s in servers if getattr(s, "backup", False) and not getattr(s, "down", False)]
        if not available:
            return None

        # Build weighted list
        weighted: List[Any] = []
        for server in available:
            weight = getattr(server, "weight", 1)
            for _ in range(weight):
                weighted.append(server)

        self._index = (self._index + 1) % len(weighted)
        return weighted[self._index]


class LeastConnBalancer(LoadBalancer):
    """Select the server with the fewest active connections."""

    def __init__(self) -> None:
        self._active_conns: Dict[str, int] = {}

    def select(self, servers: List[Any], client_ip: str = "") -> Optional[Any]:
        available = [s for s in servers if not getattr(s, "down", False)]
        if not available:
            return None

        def key(s):
            host = getattr(s, "host", "")
            port = getattr(s, "port", 0)
            return self._active_conns.get(f"{host}:{port}", 0) / max(getattr(s, "weight", 1), 1)

        return min(available, key=key)

    def increment(self, server: Any) -> None:
        key = f"{getattr(server, 'host', '')}:{getattr(server, 'port', 0)}"
        self._active_conns[key] = self._active_conns.get(key, 0) + 1

    def decrement(self, server: Any) -> None:
        key = f"{getattr(server, 'host', '')}:{getattr(server, 'port', 0)}"
        self._active_conns[key] = max(0, self._active_conns.get(key, 1) - 1)


class IpHashBalancer(LoadBalancer):
    """Hash client IP to deterministic server."""

    def select(self, servers: List[Any], client_ip: str = "") -> Optional[Any]:
        available = [s for s in servers if not getattr(s, "down", False)]
        if not available:
            return None
        if not client_ip:
            return available[0]

        # Hash the IP
        h = int(hashlib.md5(client_ip.encode()).hexdigest(), 16)
        total_weight = sum(getattr(s, "weight", 1) for s in available)
        if total_weight == 0:
            return None

        idx = h % total_weight
        for server in available:
            weight = getattr(server, "weight", 1)
            if idx < weight:
                return server
            idx -= weight
        return available[-1]


class RandomBalancer(LoadBalancer):
    """Weighted random selection."""

    def select(self, servers: List[Any], client_ip: str = "") -> Optional[Any]:
        available = [s for s in servers if not getattr(s, "down", False)]
        if not available:
            return None

        weights = [getattr(s, "weight", 1) for s in available]
        return random.choices(available, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_ALGORITHMS = {
    "round_robin": RoundRobinBalancer,
    "least_conn": LeastConnBalancer,
    "ip_hash": IpHashBalancer,
    "random": RandomBalancer,
}


def create_balancer(algorithm: str) -> LoadBalancer:
    """Create a load balancer by algorithm name.

    Args:
        algorithm: One of ``round_robin``, ``least_conn``, ``ip_hash``, ``random``.

    Returns:
        A :class:`LoadBalancer` instance.

    Raises:
        ValueError: For unknown algorithms.
    """
    cls = _ALGORITHMS.get(algorithm)
    if cls is None:
        raise ValueError(f"unknown load balancing algorithm: {algorithm}")
    return cls()


__all__ = [
    "LoadBalancer",
    "RoundRobinBalancer",
    "LeastConnBalancer",
    "IpHashBalancer",
    "RandomBalancer",
    "create_balancer",
]
