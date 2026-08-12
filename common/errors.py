"""User-facing error messages with plain language descriptions.

DESIGN.md §10.2: Maps technical exceptions to user-friendly messages with
suggestions. Supports terminal (colored) and web (JSON) output formats.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional


class Severity(enum.Enum):
    """Error severity levels."""

    INFO = "info"       # ℹ️
    SUCCESS = "success"  # ✅
    WARNING = "warning"  # ⚠️
    ERROR = "error"      # ❌
    CRITICAL = "critical"  # 🔴


# Icon mappings for terminal output
_SEVERITY_ICONS = {
    Severity.INFO: "ℹ️",
    Severity.SUCCESS: "✅",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "❌",
    Severity.CRITICAL: "🔴",
}


@dataclass
class UserFacingError(Exception):
    """An error meant to be displayed to the end user.

    Attributes:
        title: Short one-line summary.
        plain_description: Plain-language explanation of what happened.
        suggestions: List of actionable suggestions to fix the issue.
        severity: How serious this is.
    """

    title: str
    plain_description: str
    suggestions: List[str] = field(default_factory=list)
    severity: Severity = Severity.ERROR

    def __str__(self) -> str:
        return self.format_for_terminal()

    def format_for_terminal(self) -> str:
        """Return a colored terminal-friendly message."""
        icon = _SEVERITY_ICONS.get(self.severity, "❓")
        lines = [f"{icon}  {self.title}"]
        if self.plain_description:
            lines.append(f"   {self.plain_description}")
        for suggestion in self.suggestions:
            lines.append(f"   💡 {suggestion}")
        return "\n".join(lines)

    def format_for_web(self) -> dict:
        """Return a JSON-serializable dict for web display."""
        return {
            "title": self.title,
            "description": self.plain_description,
            "suggestions": self.suggestions,
            "severity": self.severity.value,
        }


# ---------------------------------------------------------------------------
# Error catalog
# ---------------------------------------------------------------------------
class ConnectionRefused(UserFacingError):
    """Cannot reach the mediation server."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(
            title=f"Cannot connect to {host}:{port}",
            plain_description=(
                "The LocalNetwork mediation server is not reachable. "
                "Check that the server is running and your network allows "
                "outbound connections on this port."
            ),
            suggestions=[
                "Make sure the server is running: localnetwork-server",
                "Check your firewall settings",
                "Verify the server address with: localnetwork-cli status",
            ],
            severity=Severity.ERROR,
        )


class AuthFailed(UserFacingError):
    """Authentication with the server failed."""

    def __init__(self, reason: str = "") -> None:
        extra = f": {reason}" if reason else ""
        super().__init__(
            title=f"Authentication failed{extra}",
            plain_description=(
                "Your client identity could not be verified by the server. "
                "This may happen if your keys were regenerated or if someone "
                "is trying to impersonate you."
            ),
            suggestions=[
                "If you regenerated your keys, re-register with the server",
                "Check that your identity files exist in ~/.localnetwork/",
                "Contact the server administrator if the issue persists",
            ],
            severity=Severity.ERROR,
        )


class TunnelFailed(UserFacingError):
    """P2P tunnel could not be established."""

    def __init__(self, peer_name: str = "peer") -> None:
        super().__init__(
            title=f"Could not establish direct connection to {peer_name}",
            plain_description=(
                "A direct peer-to-peer connection could not be established. "
                "This is often caused by restrictive NAT or firewalls. "
                "The connection will use a relay instead (slightly slower)."
            ),
            suggestions=[
                "Enable UPnP on your router for better connectivity",
                "Use a relay server if direct connections are blocked",
                "Try connecting from a different network",
            ],
            severity=Severity.WARNING,
        )


class PortInUse(UserFacingError):
    """A requested port is already in use."""

    def __init__(self, port: int, service: str = "") -> None:
        label = f" for {service}" if service else ""
        super().__init__(
            title=f"Port {port} is already in use{label}",
            plain_description=(
                f"Another program is already listening on port {port}. "
                "You can use a different port or stop the other program."
            ),
            suggestions=[
                f"Try a different port: lnet expose --port {port + 1}",
                f"Find what's using port {port}: lsof -i :{port}",
                "Use auto port selection to pick a free port automatically",
            ],
            severity=Severity.WARNING,
        )


class ConfigInvalid(UserFacingError):
    """Configuration is invalid."""

    def __init__(self, detail: str = "") -> None:
        extra = f": {detail}" if detail else ""
        super().__init__(
            title=f"Invalid configuration{extra}",
            plain_description=(
                "Your LocalNetwork configuration contains errors. "
                "Check the config file and fix the issues listed below."
            ),
            suggestions=[
                "Check ~/.localnetwork/config.yaml for syntax errors",
                "Run the setup wizard to regenerate your config",
                "See the documentation for valid configuration options",
            ],
            severity=Severity.ERROR,
        )


class PlatformUnsupported(UserFacingError):
    """Feature not available on this platform."""

    def __init__(self, feature: str, platform: str = "") -> None:
        super().__init__(
            title=f"{feature} is not supported on this platform",
            plain_description=(
                f"The '{feature}' feature requires specific OS support "
                f"that is not available{f' on {platform}' if platform else ''}. "
                "Consider using an alternative approach."
            ),
            suggestions=[
                "Use service exposure mode instead of TUN mode",
                "Run on Linux for full TUN interface support",
                "See platform-specific setup instructions in the README",
            ],
            severity=Severity.WARNING,
        )


class FirewallBlock(UserFacingError):
    """A firewall is blocking the connection."""

    def __init__(self, port: int = 0) -> None:
        port_info = f" on port {port}" if port else ""
        super().__init__(
            title=f"Connection blocked by firewall{port_info}",
            plain_description=(
                "A firewall appears to be blocking the connection. "
                "This is common on corporate networks or restrictive ISPs."
            ),
            suggestions=[
                "Check your firewall settings and allow LocalNetwork",
                "Try using a different port (some ISPs block common ports)",
                "Connect via a VPN to bypass restrictive firewalls",
            ],
            severity=Severity.ERROR,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
ERROR_CATALOG = {
    "ConnectionRefused": ConnectionRefused,
    "AuthFailed": AuthFailed,
    "TunnelFailed": TunnelFailed,
    "PortInUse": PortInUse,
    "ConfigInvalid": ConfigInvalid,
    "PlatformUnsupported": PlatformUnsupported,
    "FirewallBlock": FirewallBlock,
}

__all__ = [
    "Severity",
    "UserFacingError",
    "ConnectionRefused",
    "AuthFailed",
    "TunnelFailed",
    "PortInUse",
    "ConfigInvalid",
    "PlatformUnsupported",
    "FirewallBlock",
    "ERROR_CATALOG",
]
