"""TUN virtual network interface — platform abstraction layer.

Provides:

* :class:`TunInterface` — abstract base defining the interface contract.
* :class:`LinuxTunInterface` — Linux ``/dev/net/tun`` via ``fcntl.ioctl``.
* :class:`DarwinTunInterface` — macOS ``utun`` via ``SYSPROTO_CONTROL`` socket.
* :class:`WindowsTunInterface` — Windows wintun adapter (stub; requires driver).
* :func:`create_tun_interface` — factory that picks the right implementation.

Also includes routing helpers (:func:`add_route`, :func:`remove_route`) for
the ``25.0.0.0/8`` subnet on Linux.

Design: DESIGN.md §4.5.
"""

from __future__ import annotations

import abc
import ctypes
import fcntl
import logging
import os
import struct
import subprocess
import sys
from typing import Optional

log = logging.getLogger("localnetwork.client.tun")

# ---- Linux TUN ioctl constants ----------------------------------------------
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
SIOCSIFADDR = 0x8916
SIOCSIFNETMASK = 0x891C
SIOCSIFMTU = 0x8922
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1

# Struct for ifreq (interface request) — Linux-specific
# struct ifreq { char ifr_name[16]; union { struct sockaddr ifr_addr; ... } }
_IFNAMSIZ = 16

# ---- macOS utun constants ---------------------------------------------------
# AF_SYSTEM / SYSPROTO_CONTROL
AF_SYSTEM = 32
SYSPROTO_CONTROL = 2
UTUN_OPT_IFNAME = 2
UTUN_CONTROL_NAME = "com.apple.net.utun_control"

log = logging.getLogger("localnetwork.client.tun")


# =============================================================================
# Abstract base
# =============================================================================
class TunInterface(abc.ABC):
    """Abstract TUN virtual interface.

    All platform implementations must implement :meth:`open`, :meth:`read`,
    :meth:`write`, :meth:`close`, and :meth:`get_ip`.
    """

    def __init__(self) -> None:
        self._fd: Optional[int] = None
        self._ip: str = ""
        self._netmask: str = "255.0.0.0"
        self._mtu: int = 1400
        self._name: str = ""

    @property
    def name(self) -> str:
        return self._name

    @property
    def ip(self) -> str:
        return self._ip

    @abc.abstractmethod
    def open(self, ip: str, netmask: str = "255.0.0.0", mtu: int = 1400) -> None:
        """Create and configure the virtual TUN interface.

        Args:
            ip: Virtual IP address to assign (from the 25.0.0.0/8 range).
            netmask: Subnet mask.
            mtu: Maximum transmission unit (default 1400).
        """

    @abc.abstractmethod
    def read(self) -> bytes:
        """Read one raw IP packet from the TUN interface.

        Returns:
            Raw IP packet bytes.

        Raises:
            OSError: If the interface is not open or read fails.
        """

    @abc.abstractmethod
    def write(self, data: bytes) -> int:
        """Inject one raw IP packet into the TUN interface.

        Args:
            data: Raw IP packet bytes.

        Returns:
            Number of bytes written.

        Raises:
            OSError: If the interface is not open or write fails.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Close the TUN interface and release resources."""

    def get_ip(self) -> str:
        """Return the assigned virtual IP address."""
        return self._ip

    # ---- Routing helpers ----------------------------------------------------
    @staticmethod
    def add_route(subnet: str = "25.0.0.0/8", device: str = "") -> bool:
        """Add a route for *subnet* via the TUN device (Linux only).

        Uses ``ip route add`` for reliability across distributions.

        Returns:
            True on success, False otherwise.
        """
        if sys.platform != "linux":
            log.debug("route setup skipped: not Linux")
            return False
        if not device:
            log.warning("cannot add route: no device name")
            return False
        try:
            subprocess.run(
                ["ip", "route", "add", subnet, "dev", device],
                check=True,
                capture_output=True,
                text=True,
            )
            log.info("added route %s dev %s", subnet, device)
            return True
        except subprocess.CalledProcessError as exc:
            # Route might already exist — not a fatal error
            if "File exists" in exc.stderr or "EEXIST" in exc.stderr:
                log.debug("route %s dev %s already exists", subnet, device)
                return True
            log.warning("failed to add route %s dev %s: %s", subnet, device, exc.stderr.strip())
            return False

    @staticmethod
    def remove_route(subnet: str = "25.0.0.0/8", device: str = "") -> bool:
        """Remove the route for *subnet* via the TUN device (Linux only).

        Returns:
            True on success, False otherwise.
        """
        if sys.platform != "linux":
            return False
        if not device:
            return False
        try:
            subprocess.run(
                ["ip", "route", "del", subnet, "dev", device],
                check=True,
                capture_output=True,
                text=True,
            )
            log.info("removed route %s dev %s", subnet, device)
            return True
        except subprocess.CalledProcessError as exc:
            log.debug("failed to remove route: %s", exc.stderr.strip())
            return False


# =============================================================================
# Linux TUN (/dev/net/tun)
# =============================================================================
class LinuxTunInterface(TunInterface):
    """Linux TUN interface via ``/dev/net/tun`` and ``fcntl.ioctl``."""

    TUN_DEVICE = "/dev/net/tun"

    def open(self, ip: str, netmask: str = "255.0.0.0", mtu: int = 1400) -> None:
        """Create a Linux TUN interface.

        Opens ``/dev/net/tun``, configures IP/netmask/MTU via ioctls, and
        brings the interface up.
        """
        self._ip = ip
        self._netmask = netmask
        self._mtu = mtu

        # Open the TUN clone device
        try:
            fd = os.open(self.TUN_DEVICE, os.O_RDWR)
        except OSError as exc:
            raise OSError(f"cannot open {self.TUN_DEVICE}: {exc}") from exc

        # Create the TUN interface with NO_PI (no packet info prefix)
        ifr = struct.pack(f"{_IFNAMSIZ}sH", b"ln%d", IFF_TUN | IFF_NO_PI)
        try:
            result = fcntl.ioctl(fd, TUNSETIFF, ifr)
        except OSError as exc:
            os.close(fd)
            raise OSError(f"TUNSETIFF ioctl failed: {exc}") from exc

        # Extract the actual interface name (kernel may have changed %d to a number)
        self._name = result[: _IFNAMSIZ].rstrip(b"\x00").decode("ascii")
        self._fd = fd

        # Create a temporary socket for SIOC* ioctls on the interface
        try:
            self._configure_iface(ip, netmask, mtu)
        except OSError as exc:
            os.close(fd)
            self._fd = None
            raise OSError(f"iface configuration failed: {exc}") from exc

        log.info(
            "TUN interface %s created: ip=%s netmask=%s mtu=%d",
            self._name,
            ip,
            netmask,
            mtu,
        )

    def _configure_iface(self, ip: str, netmask: str, mtu: int) -> None:
        """Configure IP address, netmask, MTU and bring the interface up."""
        import socket as _socket

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)

        try:
            # Set IP address
            ifr = struct.pack(
                f"{_IFNAMSIZ}sH4s8x",
                self._name.encode("ascii")[: _IFNAMSIZ - 1],
                _socket.AF_INET,
                _socket.inet_aton(ip),
            )
            fcntl.ioctl(sock.fileno(), SIOCSIFADDR, ifr)

            # Set netmask
            ifr = struct.pack(
                f"{_IFNAMSIZ}sH4s8x",
                self._name.encode("ascii")[: _IFNAMSIZ - 1],
                _socket.AF_INET,
                _socket.inet_aton(netmask),
            )
            fcntl.ioctl(sock.fileno(), SIOCSIFNETMASK, ifr)

            # Set MTU
            ifr = struct.pack(
                f"{_IFNAMSIZ}si",
                self._name.encode("ascii")[: _IFNAMSIZ - 1],
                mtu,
            )
            fcntl.ioctl(sock.fileno(), SIOCSIFMTU, ifr)

            # Bring interface up (get flags, set IFF_UP, set flags)
            ifr = struct.pack(
                f"{_IFNAMSIZ}sH",
                self._name.encode("ascii")[: _IFNAMSIZ - 1],
                0,
            )
            flags_data = fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, ifr)
            flags = struct.unpack("H", flags_data[16:18])[0]
            flags |= IFF_UP
            ifr = struct.pack(
                f"{_IFNAMSIZ}sH",
                self._name.encode("ascii")[: _IFNAMSIZ - 1],
                flags,
            )
            fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, ifr)

        finally:
            sock.close()

    def read(self) -> bytes:
        """Read one IP packet from the TUN interface (blocking)."""
        if self._fd is None:
            raise OSError("TUN interface not open")
        return os.read(self._fd, self._mtu + 64)  # Headroom for frame overhead

    def write(self, data: bytes) -> int:
        """Inject one IP packet into the TUN interface."""
        if self._fd is None:
            raise OSError("TUN interface not open")
        return os.write(self._fd, data)

    def close(self) -> None:
        """Close the TUN interface file descriptor."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            log.info("TUN interface %s closed", self._name)


# =============================================================================
# macOS utun
# =============================================================================
class DarwinTunInterface(TunInterface):
    """macOS TUN interface via ``utun`` control socket."""

    def open(self, ip: str, netmask: str = "255.0.0.0", mtu: int = 1400) -> None:
        """Create a macOS utun interface.

        Opens a ``SYSPROTO_CONTROL`` socket and requests a utun device.
        Then uses ``ifconfig`` to assign the IP and bring the interface up.
        """
        import socket as _socket

        self._ip = ip
        self._netmask = netmask
        self._mtu = mtu

        # Open the utun control socket
        try:
            sock = _socket.socket(AF_SYSTEM, _socket.SOCK_DGRAM, SYSPROTO_CONTROL)
        except OSError as exc:
            raise OSError(f"cannot create utun control socket: {exc}") from exc

        # Find the utun control ID
        ctl_info = _build_ctl_info(UTUN_CONTROL_NAME)
        try:
            sock.setsockopt(SYSPROTO_CONTROL, 2, ctl_info)  # 2 = CTLIOCGINFO
        except OSError as exc:
            sock.close()
            raise OSError(f"cannot find utun control: {exc}") from exc

        # Connect to utun
        try:
            sock.connect((UTUN_OPT_IFNAME, 0))
        except OSError as exc:
            sock.close()
            raise OSError(f"cannot connect to utun: {exc}") from exc

        # Get the assigned interface name
        self._name = sock.getsockname()[1]  # type: ignore[index]
        self._fd = sock.fileno()  # We keep the socket as our fd

        # Configure via ifconfig
        try:
            subprocess.run(
                ["ifconfig", self._name, "inet", ip, netmask, ip, "mtu", str(mtu), "up"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            sock.close()
            self._fd = None
            raise OSError(f"ifconfig failed: {exc.stderr.strip()}") from exc

        log.info(
            "TUN interface %s created: ip=%s netmask=%s mtu=%d",
            self._name,
            ip,
            netmask,
            mtu,
        )

    def read(self) -> bytes:
        """Read one IP packet from the utun interface."""
        import socket as _socket

        if self._fd is None:
            raise OSError("TUN interface not open")
        sock = _socket.fromfd(self._fd, AF_SYSTEM, _socket.SOCK_DGRAM)
        data = sock.recv(self._mtu + 64)
        # utun prepends a 4-byte address family header — strip it
        if len(data) > 4:
            return data[4:]
        return data

    def write(self, data: bytes) -> int:
        """Inject one IP packet into the utun interface."""
        import socket as _socket

        if self._fd is None:
            raise OSError("TUN interface not open")
        sock = _socket.fromfd(self._fd, AF_SYSTEM, _socket.SOCK_DGRAM)
        # utun expects a 4-byte address family header (AF_INET = 2)
        header = struct.pack("!I", 2)  # AF_INET
        return sock.send(header + data)

    def close(self) -> None:
        """Close the utun socket."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


def _build_ctl_info(name: str) -> bytes:
    """Build a ctl_info struct for macOS SYSPROTO_CONTROL.

    struct ctl_info { u_int32_t ctl_id; char ctl_name[96]; };
    """
    name_bytes = name.encode("ascii")[:95]
    # 4 bytes ctl_id (0) + 96 bytes ctl_name (padded)
    return struct.pack("I96s", 0, name_bytes.ljust(96, b"\x00"))


# =============================================================================
# Windows (wintun stub)
# =============================================================================
class WindowsTunInterface(TunInterface):
    """Windows TUN interface stub.

    Full Windows support requires the wintun driver. This stub provides
    the API contract but will raise :class:`NotImplementedError` on open.
    Real implementation would use ``ctypes`` to call the wintun DLL or
    the ``pywintun`` package.
    """

    def open(self, ip: str, netmask: str = "255.0.0.0", mtu: int = 1400) -> None:
        """Stub: Windows TUN is not yet implemented."""
        raise NotImplementedError(
            "Windows TUN support requires the wintun driver. "
            "Install wintun and re-run, or use service-exposure mode instead."
        )

    def read(self) -> bytes:
        if self._fd is None:
            raise OSError("TUN interface not open")
        raise NotImplementedError("Windows TUN not implemented")

    def write(self, data: bytes) -> int:
        if self._fd is None:
            raise OSError("TUN interface not open")
        raise NotImplementedError("Windows TUN not implemented")

    def close(self) -> None:
        self._fd = None


# =============================================================================
# Factory
# =============================================================================
def create_tun_interface() -> TunInterface:
    """Create the appropriate :class:`TunInterface` for the current platform.

    Returns:
        A platform-specific TUN interface instance.

    Raises:
        NotImplementedError: On unsupported platforms.
    """
    if sys.platform == "linux":
        return LinuxTunInterface()
    if sys.platform == "darwin":
        return DarwinTunInterface()
    if sys.platform == "win32":
        return WindowsTunInterface()
    raise NotImplementedError(f"unsupported platform: {sys.platform}")


# ---- IP packet helpers ------------------------------------------------------
def extract_dst_ip(raw_packet: bytes) -> Optional[str]:
    """Extract the destination IPv4 address from a raw IP packet.

    Returns:
        IPv4 address string, or ``None`` on parse failure.
    """
    import socket as _socket

    if len(raw_packet) < 20:
        return None
    version_ihl = raw_packet[0]
    if (version_ihl >> 4) != 4:
        return None  # Not IPv4
    dst = raw_packet[16:20]
    try:
        return _socket.inet_ntoa(dst)
    except OSError:
        return None


def is_arp_request(raw_packet: bytes) -> bool:
    """Check if a raw packet is an ARP request for us to handle."""
    # ARP over Ethernet: 14-byte Ethernet header + ARP
    if len(raw_packet) < 42:
        return False
    # For TUN, packets come as pure IP (no Ethernet header).
    # TUN devices don't natively carry ARP; this helper exists for
    # gateway mode where ARP might be injected.
    return False


def build_arp_reply(
    target_ip: str, target_mac: str, sender_ip: str, sender_mac: str
) -> bytes:
    """Build a raw ARP reply packet.

    Used by gateway mode for ARP proxying.
    """
    import struct as _struct

    # Ethernet header
    eth = (
        _struct.pack("!6B", *[int(b, 16) for b in sender_mac.split(":")])
        + _struct.pack("!6B", *[int(b, 16) for b in target_mac.split(":")])
        + _struct.pack("!H", 0x0806)  # ARP
    )
    # ARP reply
    import socket as _socket

    arp = (
        _struct.pack("!HHBB", 1, 0x0800, 6, 4)  # Ethernet, IPv4
        + _struct.pack("!H", 2)  # ARP reply
        + _struct.pack("!6B", *[int(b, 16) for b in sender_mac.split(":")])
        + _socket.inet_aton(sender_ip)
        + _struct.pack("!6B", *[int(b, 16) for b in target_mac.split(":")])
        + _socket.inet_aton(target_ip)
    )
    return eth + arp


__all__ = [
    "TunInterface",
    "LinuxTunInterface",
    "DarwinTunInterface",
    "WindowsTunInterface",
    "create_tun_interface",
    "extract_dst_ip",
    "is_arp_request",
    "build_arp_reply",
]
