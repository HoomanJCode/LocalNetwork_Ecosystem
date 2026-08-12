"""Tests for client.platform_detection — capability probes and degradation."""

import platform
import sys

import pytest

from client import platform_detection as pd
from client.platform_detection import PlatformCapabilities


class TestBasicProbes:
    def test_detect_os_matches_platform(self):
        assert pd.detect_os() == platform.system()

    def test_detect_termux_positive(self, monkeypatch):
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        assert pd.detect_termux() is True

    def test_detect_termux_negative(self, monkeypatch):
        monkeypatch.delenv("PREFIX", raising=False)
        assert pd.detect_termux() is False

    def test_detect_termux_other_prefix(self, monkeypatch):
        monkeypatch.setenv("PREFIX", "/usr/local")
        assert pd.detect_termux() is False

    def test_root_detection_matches_geteuid(self):
        expected = getattr(os_geteuid_if_exists(), "is_root", False)
        assert pd.detect_root() == expected


def os_geteuid_if_exists():
    class _R:
        is_root = False
        if hasattr(__import__("os"), "geteuid"):
            is_root = __import__("os").geteuid() == 0
    return _R


class TestTun:
    def test_tun_negative_on_termux(self, monkeypatch):
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        assert pd.detect_tun("Linux", True) is False

    def test_tun_false_when_no_device(self, monkeypatch):
        monkeypatch.setattr(pd, "LINUX_TUN_DEVICE", "/nonexistent/tun")
        assert pd.detect_tun("Linux", False) is False

    def test_tun_linux_device_present(self, monkeypatch, tmp_path):
        device = tmp_path / "tun"
        device.write_text("")
        monkeypatch.setattr(pd, "LINUX_TUN_DEVICE", str(device))
        assert pd.detect_tun("Linux", False) is True

    def test_tun_macos_optimistic(self):
        assert pd.detect_tun("Darwin", False) is True

    def test_tun_unknown_os_false(self):
        assert pd.detect_tun("SomeOS", False) is False


class TestRawSockets:
    def test_raw_sockets_false_on_termux(self, monkeypatch):
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        assert pd.detect_raw_sockets("Linux", True) is False

    def test_raw_sockets_false_non_linux(self):
        assert pd.detect_raw_sockets("Windows", False) is False

    def test_raw_sockets_never_crashes(self):
        # Whatever the platform, this probe must not raise.
        pd.detect_raw_sockets(pd.detect_os(), False)


class TestPrivilegedPorts:
    def test_root_implies_privileged(self):
        assert pd.detect_privileged_ports(True) is True

    def test_windows_no_restriction(self, monkeypatch):
        if sys.platform.startswith("win"):
            assert pd.detect_privileged_ports(False) is True


class TestCapabilities:
    def test_detect_platform_returns_capabilities(self):
        caps = pd.detect_platform()
        assert isinstance(caps, PlatformCapabilities)
        assert caps.os_name == platform.system()

    def test_tun_mode_disabled_on_termux(self):
        caps = PlatformCapabilities(
            os_name="Linux", tun_available=True, is_termux=True
        )
        assert caps.tun_mode_enabled is False

    def test_tun_mode_disabled_without_interface(self):
        caps = PlatformCapabilities(os_name="Linux", tun_available=False)
        assert caps.tun_mode_enabled is False

    def test_tun_mode_enabled_when_available(self):
        caps = PlatformCapabilities(os_name="Linux", tun_available=True)
        assert caps.tun_mode_enabled is True

    def test_gateway_needs_root(self):
        assert PlatformCapabilities(
            os_name="Linux", tun_available=True, has_root=False
        ).gateway_mode_enabled is False
        assert PlatformCapabilities(
            os_name="Linux", tun_available=True, has_root=True
        ).gateway_mode_enabled is True

    def test_to_dict_contains_keys(self):
        caps = pd.detect_platform()
        data = caps.to_dict()
        for key in (
            "os", "has_root", "tun_available", "tun_mode_enabled",
            "is_termux", "python_version",
        ):
            assert key in data

    def test_print_capabilities_mentions_degradation(self, capsys):
        caps = PlatformCapabilities(
            os_name="Linux", tun_available=False, has_root=False, is_termux=True
        )
        text = pd.print_capabilities(caps)
        assert "Termux" in text
        assert "TUN mode is permanently disabled" in text
