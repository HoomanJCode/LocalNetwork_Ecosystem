"""Client configuration.

Read from (in precedence order):

1. Explicit kwargs (tests / programmatic use)
2. ``LNCLIENT_*`` environment variables or a ``.env`` file
3. CLI arguments applied via ``apply_cli_args``
4. Built-in defaults

On load, if TUN mode is requested but the platform cannot provide it, the
client logs a warning and falls back to service-only mode.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from common.constants import (
    CLIENT_DEFAULT_WEB_PORT,
    HEARTBEAT_INTERVAL,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    SERVER_DEFAULT_PORT,
)
from client.identity import DEFAULT_IDENTITY_DIR
from client.platform_detection import PlatformCapabilities, detect_platform

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover

    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


log = logging.getLogger("localnetwork.client")

ENV_PREFIX = "LNCLIENT"


@dataclass
class ClientConfig:
    """Runtime configuration for the VPN client."""

    server_host: str = "localhost"
    server_port: int = SERVER_DEFAULT_PORT
    identity_dir: str = DEFAULT_IDENTITY_DIR
    virtual_ip: Optional[str] = None
    tun_enabled: bool = False  # explicit request; may be degraded
    tun_name: str = "ln0"
    web_port: int = CLIENT_DEFAULT_WEB_PORT
    log_level: str = "INFO"
    heartbeat_interval: float = HEARTBEAT_INTERVAL
    reconnect_base_delay: float = RECONNECT_BASE_DELAY
    reconnect_max_delay: float = RECONNECT_MAX_DELAY
    request_virtual_ip: bool = False
    capabilities: PlatformCapabilities = field(
        default_factory=detect_platform, repr=False, compare=False
    )
    # Resolved after load: whether TUN mode will actually run
    tun_mode_active: bool = False

    # ---- construction -------------------------------------------------------
    @classmethod
    def from_env(cls, **overrides) -> "ClientConfig":
        """Build from ``LNCLIENT_*`` env vars plus explicit overrides."""
        load_dotenv()

        def _env_int(name: str) -> Optional[int]:
            raw = os.getenv(f"{ENV_PREFIX}_{name}")
            if raw is None or raw.strip() == "":
                return None
            return int(raw)

        values = {
            "server_host": os.getenv(f"{ENV_PREFIX}_SERVER_HOST"),
            "server_port": _env_int("SERVER_PORT"),
            "identity_dir": os.getenv(f"{ENV_PREFIX}_IDENTITY_DIR"),
            "virtual_ip": os.getenv(f"{ENV_PREFIX}_VIRTUAL_IP"),
            "web_port": _env_int("WEB_PORT"),
            "log_level": os.getenv(f"{ENV_PREFIX}_LOG_LEVEL"),
        }
        # A combined LNCLIENT_SERVER="host:port" is also accepted (README).
        combined = os.getenv(f"{ENV_PREFIX}_SERVER")
        if combined and ":" in combined:
            host, _, port = combined.rpartition(":")
            values.setdefault("server_host", host)
            values.setdefault("server_port", int(port))

        values.update(overrides)
        int_fields = {"server_port", "web_port"}
        kwargs = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in int_fields:
                value = int(value)
            kwargs[key] = value
        config = cls(**kwargs)
        config.resolve_capabilities()
        return config

    # ---- capability resolution ------------------------------------------------
    def resolve_capabilities(self) -> None:
        """Warn and degrade when TUN was requested but is unavailable."""
        caps = self.capabilities
        if self.tun_enabled and not caps.tun_mode_enabled:
            log.warning(
                "TUN mode requested but unavailable on %s "
                "(Termux=%s, tun=%s) — falling back to service-only mode",
                caps.os_name,
                caps.is_termux,
                caps.tun_available,
            )
            self.tun_enabled = False
        self.tun_mode_active = self.tun_enabled and caps.tun_mode_enabled
        if self.request_virtual_ip and not self.virtual_ip:
            log.warning("virtual IP requested but none configured")

    def to_dict(self) -> dict:
        return {
            "server_host": self.server_host,
            "server_port": self.server_port,
            "identity_dir": self.identity_dir,
            "virtual_ip": self.virtual_ip,
            "tun_enabled": self.tun_enabled,
            "tun_mode_active": self.tun_mode_active,
            "web_port": self.web_port,
            "log_level": self.log_level,
            "capabilities": self.capabilities.to_dict(),
        }


__all__ = ["ClientConfig", "ENV_PREFIX"]
