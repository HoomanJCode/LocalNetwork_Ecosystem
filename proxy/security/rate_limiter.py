"""Sliding window rate limiter (DESIGN.md Phase 20).

Per-IP request rate limiting with burst support.
Returns HTTP 429 with Retry-After header on exceed.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict

log = logging.getLogger("localnetwork.proxy.rate_limit")


@dataclass
class RateWindow:
    """Tracks request count for one client in the current window."""

    count: int = 0
    window_start: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Per-IP sliding window rate limiter."""

    def __init__(self, zone_size: int = 10000) -> None:
        self._zone_size = zone_size
        self._windows: Dict[str, RateWindow] = {}
        self._bursts: Dict[str, float] = {}  # key → burst expiry

    def allow(self, key: str, rate: float, burst: float = 0) -> bool:
        """Check if a request should be allowed.

        Args:
            key: Client identifier (typically client IP).
            rate: Allowed requests per second.
            burst: Maximum burst size (extra requests allowed in a spike).

        Returns:
            True if the request is within limits.
        """
        if rate <= 0:
            return True  # No limit

        now = time.monotonic()
        window = self._windows.get(key)

        # Create or reset expired window
        if window is None or (now - window.window_start) > 1.0:
            window = RateWindow()
            self._windows[key] = window

        # Check burst credit
        window.count += 1
        effective_limit = rate + max(0, burst)

        if window.count <= effective_limit:
            return True

        # Exceeded — check if within burst window
        burst_expiry = self._bursts.get(key, 0)
        if burst > 0 and now < burst_expiry:
            return True

        return False

    def set_burst(self, key: str, burst_duration: float = 1.0) -> None:
        """Grant burst allowance for a key."""
        self._bursts[key] = time.monotonic() + burst_duration

    def cleanup(self) -> None:
        """Remove expired entries to keep memory bounded."""
        now = time.monotonic()
        for key in list(self._windows):
            if now - self._windows[key].window_start > 10.0:
                del self._windows[key]
        for key in list(self._bursts):
            if now > self._bursts[key]:
                del self._bursts[key]
        # Enforce zone size
        if len(self._windows) > self._zone_size:
            excess = len(self._windows) - self._zone_size
            for key in list(self._windows)[:excess]:
                del self._windows[key]


__all__ = ["RateLimiter", "RateWindow"]
