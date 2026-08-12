"""On-disk cache storage with LRU eviction (DESIGN.md Phase 20).

Layout: ``{cache_path}/{two-char}/{full-key-hash}``
Each entry: header block (JSON) + body (raw bytes).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional

log = logging.getLogger("localnetwork.proxy.cache.storage")


class CacheEntry:
    """One cached response."""

    def __init__(
        self,
        key: str,
        status: int,
        headers: Dict[str, str],
        body: bytes,
        ttl: int = 300,
    ) -> None:
        self.key = key
        self.status = status
        self.headers = headers
        self.body = body
        self.ttl = ttl
        self.created_at = time.time()
        self.last_access = time.time()

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    @property
    def size(self) -> int:
        return len(self.body)


class DiskCache:
    """On-disk cache with LRU eviction."""

    def __init__(self, cache_path: str = "/tmp/lnproxy-cache", max_size: int = 100 * 1024 * 1024) -> None:
        self.cache_path = cache_path
        self.max_size = max_size
        self._entries: Dict[str, CacheEntry] = {}
        self._current_size: int = 0

        os.makedirs(cache_path, exist_ok=True)

    def _entry_path(self, key: str) -> str:
        """Derive the file path for a cache key."""
        import hashlib

        h = hashlib.sha256(key.encode()).hexdigest()
        subdir = os.path.join(self.cache_path, h[:2])
        os.makedirs(subdir, exist_ok=True)
        return os.path.join(subdir, h)

    def store(
        self, key: str, status: int, headers: Dict[str, str], body: bytes, ttl: int = 300
    ) -> None:
        """Store a response in the cache."""
        entry = CacheEntry(key=key, status=status, headers=headers, body=body, ttl=ttl)

        # Evict if needed
        while self._current_size + entry.size > self.max_size and self._entries:
            self._evict_one()

        # Write to disk
        filepath = self._entry_path(key)
        try:
            with open(filepath, "wb") as f:
                meta = json.dumps({
                    "key": key,
                    "status": status,
                    "headers": headers,
                    "ttl": ttl,
                    "created_at": entry.created_at,
                }).encode()
                f.write(len(meta).to_bytes(4, "big"))
                f.write(meta)
                f.write(body)
        except OSError as exc:
            log.debug("cache write failed: %r", exc)
            return

        self._entries[key] = entry
        self._current_size += entry.size

    def retrieve(self, key: str) -> Optional[tuple]:
        """Retrieve a cached response.

        Returns:
            ``(status, headers, body)`` or None if not found/expired.
        """
        entry = self._entries.get(key)
        if entry is not None:
            if entry.expired:
                self._remove(key)
                return None
            entry.last_access = time.time()
            return entry.status, dict(entry.headers), entry.body

        # Try loading from disk
        filepath = self._entry_path(key)
        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "rb") as f:
                meta_len = int.from_bytes(f.read(4), "big")
                meta = json.loads(f.read(meta_len))
                body = f.read()

            if time.time() - meta.get("created_at", 0) > meta.get("ttl", 300):
                os.remove(filepath)
                return None

            return meta["status"], meta["headers"], body
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def purge(self, key_pattern: str = "") -> int:
        """Delete entries matching a key pattern (prefix match).

        Returns the number of entries purged.
        """
        if not key_pattern:
            count = len(self._entries)
            for key in list(self._entries):
                self._remove(key)
            return count

        count = 0
        for key in list(self._entries):
            if key.startswith(key_pattern):
                self._remove(key)
                count += 1
        return count

    def _remove(self, key: str) -> None:
        """Remove a cache entry from memory and disk."""
        entry = self._entries.pop(key, None)
        if entry:
            self._current_size = max(0, self._current_size - entry.size)
        try:
            os.remove(self._entry_path(key))
        except OSError:
            pass

    def _evict_one(self) -> None:
        """Evict the least-recently-used entry."""
        if not self._entries:
            return
        lru_key = min(self._entries, key=lambda k: self._entries[k].last_access)
        self._remove(lru_key)

    @property
    def size(self) -> int:
        return self._current_size

    @property
    def entry_count(self) -> int:
        return len(self._entries)


__all__ = ["CacheEntry", "DiskCache"]
