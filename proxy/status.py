"""Stub status endpoint for monitoring (DESIGN.md §7, Phase 21).

Exposes ``GET /proxy-status`` with connection stats, upstream states, and more.
"""

from __future__ import annotations

import time
from typing import Any


class StatusCollector:
    """Collects and reports proxy runtime statistics."""

    def __init__(self, start_time: float | None = None) -> None:
        self._start_time = start_time or time.time()
        self._accepted: int = 0
        self._handled: int = 0
        self._active: int = 0
        self._requests: int = 0

    def increment_accepted(self) -> None:
        self._accepted += 1
        self._active += 1

    def decrement_active(self) -> None:
        self._active = max(0, self._active - 1)

    def increment_handled(self) -> None:
        self._handled += 1
        self._requests += 1

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def get_stats(self) -> dict:
        """Return a snapshot of current stats."""
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "active_connections": self._active,
            "accepted_total": self._accepted,
            "handled_total": self._handled,
            "requests_total": self._requests,
        }


__all__ = ["StatusCollector"]
