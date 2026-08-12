"""IP access control with CIDR matching (DESIGN.md Phase 20).

Evaluates allow/deny rules in order. First matching rule wins.
Supports IPv4 and IPv6 CIDR notation.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import List, Tuple

log = logging.getLogger("localnetwork.proxy.access")


class AccessControl:
    """Allow/deny access control for client IPs."""

    def __init__(self) -> None:
        self._rules: List[Tuple[str, str]] = []  # (action, network)

    def allow(self, network: str) -> None:
        """Add an allow rule for a network (CIDR notation)."""
        self._rules.append(("allow", network))

    def deny(self, network: str) -> None:
        """Add a deny rule for a network (CIDR notation)."""
        self._rules.append(("deny", network))

    def check(self, client_ip: str) -> bool:
        """Check if a client IP is allowed.

        Rules are evaluated in order; the first match wins.
        If no rules match, access is allowed by default.

        Returns:
            True if the IP is allowed.
        """
        if not self._rules:
            return True  # Default allow

        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            return True  # Invalid IPs pass through (don't block)

        for action, network_str in self._rules:
            try:
                network = ipaddress.ip_network(network_str, strict=False)
                if ip in network:
                    return action == "allow"
            except ValueError:
                continue

        return True  # Default allow if no rules match

    def clear(self) -> None:
        """Remove all rules."""
        self._rules.clear()

    @property
    def rules(self) -> List[Tuple[str, str]]:
        return list(self._rules)


__all__ = ["AccessControl"]
