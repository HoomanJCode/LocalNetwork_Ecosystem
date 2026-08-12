"""Friendly logging with dual output.

DESIGN.md §10.4: Machine logs (JSON) + human logs (colored terminal).
Technical details hidden unless `--verbose`.

Human format:: ``[time] [icon] Plain language message``

Examples::

    12:34:56 ✅ Connected to "My Network" — 3 peers online
    12:35:01 ⚠️ Direct connection to Alice failed — using relay
    12:35:10 ℹ️ Bob shared a new service: Minecraft (port 25565)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Icons for common events
# ---------------------------------------------------------------------------
EVENT_ICONS = {
    # Connection
    "connected": "🔗",
    "disconnected": "🔌",
    "reconnecting": "🔄",
    # Peers
    "peer_online": "👋",
    "peer_offline": "👋",
    # Tunnels
    "tunnel_established": "🔒",
    "tunnel_failed": "⚠️",
    "tunnel_closed": "🔒",
    # Services
    "service_exposed": "📤",
    "service_removed": "📤",
    "service_mapped": "📥",
    # Networks
    "network_created": "🌐",
    "network_joined": "🌐",
    "network_left": "🌐",
    # General
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
    "success": "✅",
    "startup": "🚀",
    "shutdown": "🛑",
}


class FriendlyFormatter(logging.Formatter):
    """Human-readable log formatter with icons and plain language."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__()
        self.verbose = verbose

    def format(self, record: logging.LogRecord) -> str:
        timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        icon = EVENT_ICONS.get(record.levelname.lower(), "")

        # Extract a friendly message if the record has one
        friendly = getattr(record, "friendly", None)
        if friendly:
            return f"{timestamp} {icon} {friendly}"

        # Verbose mode: show full detail
        if self.verbose:
            name = record.name.split(".")[-1]
            return f"{timestamp} {icon} [{name}] {record.getMessage()}"

        # Default: hide debug, show info/warning/error compactly
        if record.levelno <= logging.DEBUG:
            return ""  # skip debug in non-verbose mode
        return f"{timestamp} {icon} {record.getMessage()}"


class JSONFormatter(logging.Formatter):
    """Machine-readable JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include extra fields
        for key in ("friendly", "peer_id", "network_id", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Convenience setup
# ---------------------------------------------------------------------------
def setup_logging(
    level: str = "INFO",
    verbose: bool = False,
    json_output: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Configure the root logger with friendly and/or JSON output.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        verbose: Show technical details and debug messages.
        json_output: Emit JSON instead of human-friendly format.
        log_file: Also write logs to this file path.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    if json_output:
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(FriendlyFormatter(verbose=verbose))
    root.addHandler(handler)

    if log_file:
        try:
            fh = logging.FileHandler(log_file)
            fh.setFormatter(JSONFormatter())
            root.addHandler(fh)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Friendly log helpers
# ---------------------------------------------------------------------------
def log_friendly(
    logger: logging.Logger,
    level: int,
    friendly: str,
    **extra,
) -> None:
    """Log a user-friendly message alongside the technical detail.

    Args:
        logger: The logger to use.
        level: Log level (e.g., ``logging.INFO``).
        friendly: Plain-language message for the user.
        **extra: Additional fields for the JSON formatter.
    """
    logger.log(level, friendly, extra={"friendly": friendly, **extra})


__all__ = [
    "EVENT_ICONS",
    "FriendlyFormatter",
    "JSONFormatter",
    "setup_logging",
    "log_friendly",
]
