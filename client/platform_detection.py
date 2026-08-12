"""Platform capability detection.

Determines what the current machine can do so the client can pick the right
feature set and explain degradations to the user (DESIGN.md §4.2).

Degradation rules (task 5.1):

* ``tun_available == False``  → TUN mode disabled; service exposure only
* ``has_root == False``       → privileged features (gateway, port < 1024) off
* ``is_termux == True``       → TUN permanently disabled
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import sys
from dataclasses import dataclass, field

log = logging.getLogger("localnetwork.client")

TERMUX_PREFIX = "/data/data/com.termux/files/usr"
LINUX_TUN_DEVICE = "/dev/net/tun"


@dataclass
class PlatformCapabilities:
    """What the current platform supports."""

    os_name: str
    has_root: bool = False
    tun_available: bool = False
    raw_sockets: bool = False
    privileged_ports: bool = False
    is_termux: bool = False
    python_version: str = field(default_factory=lambda: sys.version.split()[0])

    # ---- derived degradation flags ------------------------------------------
    @property
    def tun_mode_enabled(self) -> bool:
        """TUN mode requires an available interface and no Termux."""
        return self.tun_available and not self.is_termux

    @property
    def gateway_mode_enabled(self) -> bool:
        """Gateway mode additionally requires root for IP forwarding."""
        return self.tun_mode_enabled and self.has_root

    @property
    def privileged_features_enabled(self) -> bool:
        return self.has_root

    def to_dict(self) -> dict:
        return {
            "os": self.os_name,
            "has_root": self.has_root,
            "tun_available": self.tun_available,
            "tun_mode_enabled": self.tun_mode_enabled,
            "raw_sockets": self.raw_sockets,
            "privileged_ports": self.privileged_ports,
            "is_termux": self.is_termux,
            "python_version": self.python_version,
        }


# ---------------------------------------------------------------------------
# Individual probes (testable in isolation)
# ---------------------------------------------------------------------------
def detect_os() -> str:
    return platform.system()


def detect_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return bool(prefix) and TERMUX_PREFIX in prefix


def detect_root() -> bool:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (ImportError, AttributeError, OSError):
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


def detect_tun(os_name: str, is_termux: bool) -> bool:
    """Check whether a TUN interface can be created.

    Termux always reports False (no TUN support in the app sandbox).
    """
    if is_termux:
        return False
    if os_name == "Linux":
        try:
            with open(LINUX_TUN_DEVICE, "r", encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            return False
    if os_name == "Darwin":
        # utun interfaces are generally available on macOS
        return True
    if os_name == "Windows":
        # WinTun driver presence: check the wintun DLL typically bundled
        # next to the executable; a truthful probe happens at open() time.
        return _windows_wintun_present()
    return False


def _windows_wintun_present() -> bool:
    candidates = [
        os.path.join(os.path.dirname(sys.executable), "wintun.dll"),
        os.path.join(os.getcwd(), "wintun.dll"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return True
    # Windows 10+ includes the wintun driver service in many installs; fall
    # back to optimistic detection only if we cannot verify.
    return False


def detect_raw_sockets(os_name: str, is_termux: bool) -> bool:
    if is_termux or os_name != "Linux":
        return False
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        sock.close()
        return True
    except (OSError, AttributeError):
        return False


def detect_privileged_ports(has_root: bool) -> bool:
    """True when the process may bind ports < 1024."""
    if has_root:
        return True
    if sys.platform.startswith("win"):
        return True  # Windows has no unprivileged-port restriction
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 80))
        probe.close()
        return True
    except PermissionError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Aggregate detection
# ---------------------------------------------------------------------------
def detect_platform() -> PlatformCapabilities:
    """Run every probe and return the combined result."""
    os_name = detect_os()
    is_termux = detect_termux()
    has_root = detect_root()
    return PlatformCapabilities(
        os_name=os_name,
        has_root=has_root,
        tun_available=detect_tun(os_name, is_termux),
        raw_sockets=detect_raw_sockets(os_name, is_termux),
        privileged_ports=detect_privileged_ports(has_root),
        is_termux=is_termux,
    )


def print_capabilities(caps: PlatformCapabilities) -> str:
    """Pretty-print capabilities; returns the rendered text as well."""
    lines = ["Platform capabilities:", f"  OS:                 {caps.os_name}"]
    lines.append(f"  Python:             {caps.python_version}")
    lines.append(f"  Termux:             {'yes' if caps.is_termux else 'no'}")
    lines.append(f"  Root/admin:         {'yes' if caps.has_root else 'no'}")
    lines.append(f"  TUN interface:      {'yes' if caps.tun_available else 'no'}")
    lines.append(f"  Raw sockets:        {'yes' if caps.raw_sockets else 'no'}")
    lines.append(
        f"  Privileged ports:   {'yes' if caps.privileged_ports else 'no'}"
    )
    lines.append(f"  TUN mode enabled:   {'yes' if caps.tun_mode_enabled else 'no'}")
    lines.append(
        f"  Gateway mode:       {'yes' if caps.gateway_mode_enabled else 'no'}"
    )

    # Degradation notes
    notes = []
    if caps.is_termux:
        notes.append("Termux detected: TUN mode is permanently disabled.")
    elif not caps.tun_available:
        notes.append("No TUN interface available: running in service-exposure mode.")
    if not caps.has_root:
        notes.append("Running without root/admin: privileged features are off.")
    for note in notes:
        lines.append(f"  ! {note}")
    text = "\n".join(lines)
    print(text)
    return text


__all__ = [
    "TERMUX_PREFIX",
    "LINUX_TUN_DEVICE",
    "PlatformCapabilities",
    "detect_os",
    "detect_termux",
    "detect_root",
    "detect_tun",
    "detect_raw_sockets",
    "detect_privileged_ports",
    "detect_platform",
    "print_capabilities",
]
