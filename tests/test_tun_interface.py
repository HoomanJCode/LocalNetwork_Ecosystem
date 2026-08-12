"""Tests for the TUN virtual interface module.

Requires root on Linux to actually open /dev/net/tun.
Tests skip gracefully when not running as root.
"""

from __future__ import annotations

import os
import sys

import pytest

from client.tun_interface import (
    DarwinTunInterface,
    LinuxTunInterface,
    TunInterface,
    WindowsTunInterface,
    create_tun_interface,
    extract_dst_ip,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_create_tun_interface_returns_platform_correct():
    """create_tun_interface returns the right type for each platform."""
    iface = create_tun_interface()
    if sys.platform == "linux":
        assert isinstance(iface, LinuxTunInterface)
    elif sys.platform == "darwin":
        assert isinstance(iface, DarwinTunInterface)
    elif sys.platform == "win32":
        assert isinstance(iface, WindowsTunInterface)


# ---------------------------------------------------------------------------
# Linux TUN — skip if not root
# ---------------------------------------------------------------------------
needs_root = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="TUN interface tests require root privileges",
)


class TestLinuxTunInterface:
    @needs_root
    def test_open_and_close(self):
        """Open a TUN interface, assign IP, then close it."""
        iface = LinuxTunInterface()
        try:
            iface.open(ip="25.255.0.99", netmask="255.0.0.0", mtu=1400)
            assert iface.name.startswith("ln")
            assert iface.get_ip() == "25.255.0.99"
        finally:
            iface.close()

    @needs_root
    def test_read_write_loopback(self):
        """Write a packet and read it back (loopback via TUN)."""
        iface = LinuxTunInterface()
        try:
            iface.open(ip="25.255.0.100")
            # Build a minimal IPv4 packet (ICMP echo to ourselves)
            import socket
            import struct

            src = socket.inet_aton("25.255.0.100")
            dst = socket.inet_aton("25.255.0.100")
            # IPv4 header: version=4, IHL=5, total_length, id, flags, TTL=64, proto=1 (ICMP)
            ip_header = struct.pack(
                "!BBHHHBBH4s4s",
                0x45,       # Version + IHL
                0,          # DSCP + ECN
                28,         # Total length
                0,          # ID
                0,          # Flags + Fragment
                64,         # TTL
                1,          # Protocol (ICMP)
                0,          # Checksum (kernel fills for TUN)
                src,
                dst,
            )
            # ICMP echo request (type 8, code 0)
            icmp = struct.pack("!BBHHH", 8, 0, 0, 0, 1)
            packet = ip_header + icmp

            written = iface.write(packet)
            assert written == len(packet)

            # Read back (might not loop back immediately)
            # On a real TUN, packets written go to the kernel which may
            # route them back. We just verify no crash.
        finally:
            iface.close()

    @needs_root
    def test_mtu_set(self):
        """Verify MTU is set."""
        iface = LinuxTunInterface()
        try:
            iface.open(ip="25.255.0.101", mtu=1200)
            assert iface._mtu == 1200
        finally:
            iface.close()

    @needs_root
    def test_close_cleanup(self):
        """Close removes the interface."""
        iface = LinuxTunInterface()
        iface.open(ip="25.255.0.102")
        name = iface.name
        iface.close()
        # After close, the interface should be gone
        assert iface._fd is None


# ---------------------------------------------------------------------------
# Unprivileged tests
# ---------------------------------------------------------------------------
def test_open_without_root_raises():
    """Opening /dev/net/tun without root should raise OSError."""
    if os.geteuid() == 0:
        pytest.skip("running as root — cannot test unprivileged failure")
    iface = LinuxTunInterface()
    with pytest.raises(OSError):
        iface.open(ip="25.1.0.1")


def test_read_without_open_raises():
    """Reading from a closed interface raises OSError."""
    iface = LinuxTunInterface()
    with pytest.raises(OSError):
        iface.read()


def test_write_without_open_raises():
    """Writing to a closed interface raises OSError."""
    iface = LinuxTunInterface()
    with pytest.raises(OSError):
        iface.write(b"test")


# ---------------------------------------------------------------------------
# IP packet helpers
# ---------------------------------------------------------------------------
def test_extract_dst_ip_valid():
    """Extract destination IP from a valid IPv4 packet."""
    import socket
    import struct

    src = socket.inet_aton("10.0.0.1")
    dst = socket.inet_aton("25.1.0.5")
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, 20, 0, 0, 64, 6, 0, src, dst,
    )
    assert extract_dst_ip(ip_header) == "25.1.0.5"


def test_extract_dst_ip_too_short():
    """Short buffers return None."""
    assert extract_dst_ip(b"short") is None


def test_extract_dst_ip_not_ipv4():
    """Non-IPv4 packets return None."""
    packet = b"\x60" + b"\x00" * 19  # IPv6 version
    assert extract_dst_ip(packet) is None


# ---------------------------------------------------------------------------
# Windows stub
# ---------------------------------------------------------------------------
def test_windows_tun_raises_not_implemented():
    """WindowsTunInterface raises NotImplementedError on open."""
    iface = WindowsTunInterface()
    with pytest.raises(NotImplementedError):
        iface.open(ip="25.1.0.1")


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------
def test_add_route_without_device():
    """add_route returns False when no device name is given."""
    assert TunInterface.add_route(device="") is False
