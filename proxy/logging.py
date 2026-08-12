"""Proxy logging — access and error log writers (DESIGN.md §7, Phase 21)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

log = logging.getLogger("localnetwork.proxy.logging")


class AccessLogger:
    """Async access log writer for HTTP requests."""

    def __init__(self, log_path: str = "", fmt: str = "combined") -> None:
        self.log_path = log_path
        self.fmt = fmt
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background log writer."""
        self._task = asyncio.create_task(self._writer())

    async def log(
        self,
        method: str,
        path: str,
        status: int,
        body_size: int,
        duration_ms: float,
        client_ip: str = "-",
        user_agent: str = "-",
        upstream: str = "-",
    ) -> None:
        """Queue an access log entry."""
        await self._queue.put({
            "ts": time.time(),
            "client": client_ip,
            "method": method,
            "path": path,
            "status": status,
            "size": body_size,
            "duration_ms": round(duration_ms, 2),
            "upstream": upstream,
            "ua": user_agent,
        })

    async def _writer(self) -> None:
        """Background task: drain the queue and write to disk."""
        while True:
            entry = await self._queue.get()
            line = self._format(entry)
            if self.log_path:
                try:
                    with open(self.log_path, "a") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
            else:
                log.info("access: %s", line)

    def _format(self, entry: dict) -> str:
        """Format an entry based on the configured format."""
        if self.fmt == "json":
            return json.dumps(entry, separators=(",", ":"))
        # Combined format
        return (
            f'{entry["client"]} - - '
            f'[{time.strftime("%d/%b/%Y:%H:%M:%S %z", time.localtime(entry["ts"]))}] '
            f'"{entry["method"]} {entry["path"]} HTTP/1.1" '
            f'{entry["status"]} {entry["size"]} '
            f'"{entry.get("referer", "-")}" "{entry["ua"]}" '
            f'{entry["duration_ms"]}ms upstream={entry["upstream"]}'
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()


__all__ = ["AccessLogger"]
