"""In-memory cache metadata index (DESIGN.md Phase 20).

Fast lookup without disk I/O. Rebuilt on startup by scanning the cache directory.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

log = logging.getLogger("localnetwork.proxy.cache.metadata")


@dataclass
class EntryMeta:
    """In-memory metadata for one cache entry."""

    filepath: str
    key: str
    size: int
    status: int
    created_at: float
    last_access: float
    ttl: int


class MetadataIndex:
    """In-memory index of cache entries for fast lookup."""

    def __init__(self) -> None:
        self._entries: Dict[str, EntryMeta] = {}

    def add(self, key: str, filepath: str, size: int, status: int, ttl: int) -> None:
        import time

        self._entries[key] = EntryMeta(
            filepath=filepath,
            key=key,
            size=size,
            status=status,
            created_at=time.time(),
            last_access=time.time(),
            ttl=ttl,
        )

    def get(self, key: str) -> Optional[EntryMeta]:
        """Look up metadata. Updates last_access on hit."""
        entry = self._entries.get(key)
        if entry:
            import time
            entry.last_access = time.time()
        return entry

    def remove(self, key: str) -> None:
        self._entries.pop(key, None)

    def list_all(self) -> list:
        return list(self._entries.values())

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self._entries.values())

    @property
    def count(self) -> int:
        return len(self._entries)

    def rebuild_from_disk(self, cache_path: str) -> None:
        """Scan the cache directory and rebuild the index."""
        import glob

        self._entries.clear()
        pattern = os.path.join(cache_path, "*", "*")
        for filepath in glob.glob(pattern):
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "rb") as f:
                    meta_len = int.from_bytes(f.read(4), "big")
                    meta = json.loads(f.read(meta_len))
                self._entries[meta["key"]] = EntryMeta(
                    filepath=filepath,
                    key=meta["key"],
                    size=os.path.getsize(filepath),
                    status=meta.get("status", 200),
                    created_at=meta.get("created_at", 0),
                    last_access=os.path.getatime(filepath),
                    ttl=meta.get("ttl", 300),
                )
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        log.info("rebuilt cache index: %d entries", len(self._entries))


__all__ = ["EntryMeta", "MetadataIndex"]
