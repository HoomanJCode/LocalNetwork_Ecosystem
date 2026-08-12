"""Mediation server configuration.

Values are read (in order of precedence) from:

1. Explicit constructor kwargs (used by tests and programmatic embedding)
2. Environment variables (``LNSERVER_*``)
3. A ``.env`` file in the current directory (via python-dotenv)
4. Built-in defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common.constants import (
    HEARTBEAT_TIMEOUT,
    SERVER_DEFAULT_HOST,
    SERVER_DEFAULT_PORT,
    SERVER_DEFAULT_WEB_PORT,
)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a declared dependency

    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


ENV_PREFIX = "LNSERVER"


@dataclass
class ServerConfig:
    """Runtime configuration for the mediation server."""

    host: str = SERVER_DEFAULT_HOST
    port: int = SERVER_DEFAULT_PORT
    web_port: int = SERVER_DEFAULT_WEB_PORT
    max_clients: int = 256
    heartbeat_timeout: int = HEARTBEAT_TIMEOUT
    auth_challenge_ttl: int = 60
    auth_max_attempts: int = 5
    log_level: str = "INFO"
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None
    data_dir: str = "~/.localnetwork"
    # Internal registry/network state passed in by MediationServer
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls, **overrides) -> "ServerConfig":
        """Build a config from environment variables plus explicit overrides.

        Recognized env vars (prefixed ``LNSERVER_``):

        * ``LNSERVER_HOST``
        * ``LNSERVER_PORT``
        * ``LNSERVER_WEB_PORT``
        * ``LNSERVER_MAX_CLIENTS``
        * ``LNSERVER_HEARTBEAT_TIMEOUT``
        * ``LNSERVER_LOG_LEVEL``
        * ``LNSERVER_ADMIN_USER``
        * ``LNSERVER_ADMIN_PASS``
        """
        load_dotenv()

        def _env_int(name: str) -> Optional[int]:
            raw = os.getenv(f"{ENV_PREFIX}_{name}")
            if raw is None or raw.strip() == "":
                return None
            return int(raw)

        values = {
            "host": os.getenv(f"{ENV_PREFIX}_HOST"),
            "port": _env_int("PORT"),
            "web_port": _env_int("WEB_PORT"),
            "max_clients": _env_int("MAX_CLIENTS"),
            "heartbeat_timeout": _env_int("HEARTBEAT_TIMEOUT"),
            "log_level": os.getenv(f"{ENV_PREFIX}_LOG_LEVEL"),
            "admin_user": os.getenv(f"{ENV_PREFIX}_ADMIN_USER"),
            "admin_password": os.getenv(f"{ENV_PREFIX}_ADMIN_PASS"),
        }
        values.update(overrides)
        # Coerce int fields, ignore None values
        int_fields = {"port", "web_port", "max_clients", "heartbeat_timeout"}
        kwargs = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in int_fields:
                value = int(value)
            kwargs[key] = value
        return cls(**kwargs)

    def validate(self) -> None:
        """Raise ValueError on invalid configuration."""
        if not (0 < self.port < 65536):
            raise ValueError(f"invalid port: {self.port}")
        if self.web_port is not None and not (0 <= self.web_port < 65536):
            raise ValueError(f"invalid web port: {self.web_port}")
        if self.max_clients < 1:
            raise ValueError(f"max_clients must be >= 1, got {self.max_clients}")
        if self.heartbeat_timeout < 1:
            raise ValueError(
                f"heartbeat_timeout must be >= 1, got {self.heartbeat_timeout}"
            )
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"invalid log level: {self.log_level}")

    @property
    def data_path(self) -> Path:
        """Expand and ensure the data directory exists."""
        path = Path(os.path.expanduser(self.data_dir))
        path.mkdir(parents=True, exist_ok=True)
        return path


__all__ = ["ServerConfig", "ENV_PREFIX"]
