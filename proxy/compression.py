"""Gzip response compression (DESIGN.md §7, Phase 20)."""

from __future__ import annotations

import zlib

COMPRESSIBLE_TYPES = {
    "text/html",
    "text/css",
    "text/plain",
    "text/xml",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
}


class GzipCompressor:
    """Compresses HTTP response bodies with gzip."""

    def __init__(self, level: int = 6, min_length: int = 256) -> None:
        self.level = level
        self.min_length = min_length

    def should_compress(self, content_type: str, accept_encoding: str = "") -> bool:
        """Check if compression should be applied."""
        if not accept_encoding or "gzip" not in accept_encoding.lower():
            return False
        if content_type and content_type.split(";")[0].strip() not in COMPRESSIBLE_TYPES:
            return False
        return True

    def compress(self, data: bytes) -> bytes:
        """Compress data with gzip.

        Returns the raw compressed bytes (no gzip wrapper headers).
        """
        if len(data) < self.min_length:
            return data
        compressor = zlib.compressobj(self.level, zlib.DEFLATED, 31)
        return compressor.compress(data) + compressor.flush()


__all__ = ["GzipCompressor", "COMPRESSIBLE_TYPES"]
