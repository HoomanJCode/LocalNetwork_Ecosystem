"""Cache manager — key generation, validation, TTL management."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Dict, Optional

log = logging.getLogger("localnetwork.proxy.cache.manager")

CACHEABLE_STATUSES = {200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501}
CACHEABLE_METHODS = {"GET", "HEAD"}


class CacheManager:
    """Manages cache keys, validation, and cache-control logic."""

    def __init__(
        self,
        storage: Any = None,
        default_ttl: int = 300,
    ) -> None:
        self.storage = storage
        self.default_ttl = default_ttl

    def get_cache_key(self, method: str, path: str, vary_headers: Dict[str, str] = None) -> str:
        """Generate a deterministic cache key.

        Uses SHA-256 over method + path + normalized vary headers.
        """
        parts = [method.upper(), path]
        if vary_headers:
            for k in sorted(vary_headers):
                parts.append(f"{k}:{vary_headers[k]}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()

    def is_cacheable(self, method: str, status: int, response_headers: Dict[str, str]) -> bool:
        """Determine if a response should be cached."""
        if method.upper() not in CACHEABLE_METHODS:
            return False
        if status not in CACHEABLE_STATUSES:
            return False

        cache_control = response_headers.get("cache-control", "").lower()
        if "no-store" in cache_control or "no-cache" in cache_control:
            return False
        if "private" in cache_control:
            return False

        return True

    def get_ttl(self, response_headers: Dict[str, str]) -> int:
        """Extract TTL from response headers or return default."""
        cache_control = response_headers.get("cache-control", "").lower()
        if "max-age=" in cache_control:
            try:
                parts = cache_control.split("max-age=")[1].split(",")[0].strip()
                return int(parts)
            except (IndexError, ValueError):
                pass

        if "s-maxage=" in cache_control:
            try:
                parts = cache_control.split("s-maxage=")[1].split(",")[0].strip()
                return int(parts)
            except (IndexError, ValueError):
                pass

        expires = response_headers.get("expires", "")
        if expires:
            try:
                from email.utils import parsedate_to_datetime
                expiry = parsedate_to_datetime(expires)
                ttl = int((expiry.timestamp() - time.time()))
                return max(0, ttl)
            except (ValueError, TypeError):
                pass

        return self.default_ttl

    def is_fresh(self, key: str) -> bool:
        """Check if a cached entry exists and is not expired."""
        if self.storage is None:
            return False
        result = self.storage.retrieve(key)
        return result is not None

    def is_stale(self, key: str) -> bool:
        """Check if a cached entry exists but is expired."""
        if self.storage is None:
            return False
        return self.storage.retrieve(key) is None and key in getattr(
            self.storage, "_entries", {}
        )


__all__ = ["CacheManager", "CACHEABLE_STATUSES", "CACHEABLE_METHODS"]
