"""Tests for gateway-mode ARP proxying helpers (client.main)."""

from __future__ import annotations

import socket
import struct

from client.main import (
    _build_arp_reply_for_tun,
    _is_arp_for_us,
    _mac_for_ip,
    _parse_arp,
)


def _arp_request(
    target_ip: str,
    sender_ip: str = "25.2.0.1",
    sender_mac: bytes = b"\xaa\xbb\xcc\xdd\xee\xff",
) -> bytes:
    """Build a raw 28-byte ARP request (no Ethernet header)."""
    return struct.pack(
        "!HHBBH6s4s6s4s",
        1, 0x0800, 6, 4, 1,  # htype, ptype, hlen, plen, opcode=1 (request)
        sender_mac, socket.inet_aton(sender_ip),
        b"\x00" * 6, socket.inet_aton(target_ip),
    )


def _ethernet_frame(arp_payload: bytes) -> bytes:
    """Wrap a raw ARP payload in an Ethernet frame (EtherType 0x0806)."""
    return b"\xff" * 6 + b"\xaa\xbb\xcc\xdd\xee\xff" + b"\x08\x06" + arp_payload


class TestParseArp:
    def test_parse_raw_arp_request(self):
        parsed = _parse_arp(_arp_request("25.1.0.1"))
        assert parsed is not None
        opcode, sha, spa, tha, tpa = parsed
        assert opcode == 1
        assert sha == b"\xaa\xbb\xcc\xdd\xee\xff"
        assert spa == socket.inet_aton("25.2.0.1")
        assert tha == b"\x00" * 6
        assert tpa == socket.inet_aton("25.1.0.1")

    def test_parse_ethernet_framed_arp(self):
        parsed = _parse_arp(_ethernet_frame(_arp_request("25.1.0.1")))
        assert parsed is not None
        opcode, _sha, _spa, _tha, tpa = parsed
        assert opcode == 1
        assert tpa == socket.inet_aton("25.1.0.1")

    def test_non_arp_returns_none(self):
        assert _parse_arp(b"") is None
        assert _parse_arp(b"garbage-not-arp-at-all") is None
        ip_pkt = (
            bytes([0x45, 0x00, 0x00, 0x14, 0, 0, 0, 0, 64, 17, 0, 0])
            + socket.inet_aton("25.1.0.1")
            + socket.inet_aton("25.1.0.2")
        )
        assert _parse_arp(ip_pkt) is None


class TestIsArpForUs:
    def test_request_for_our_ip(self):
        assert _is_arp_for_us(_arp_request("25.1.0.1"), ["25.1.0.1", "25.1.0.2"])

    def test_request_for_other_ip(self):
        assert not _is_arp_for_us(_arp_request("25.9.9.9"), ["25.1.0.1"])

    def test_arp_reply_is_not_a_request(self):
        reply = struct.pack(
            "!HHBBH6s4s6s4s",
            1, 0x0800, 6, 4, 2,  # opcode 2 = reply
            b"\xaa\xbb\xcc\xdd\xee\xff", socket.inet_aton("25.1.0.1"),
            b"\x11\x22\x33\x44\x55\x66", socket.inet_aton("25.2.0.1"),
        )
        assert not _is_arp_for_us(reply, ["25.1.0.1"])

    def test_non_arp_packet(self):
        assert not _is_arp_for_us(b"hello", ["25.1.0.1"])


class TestBuildArpReply:
    def test_build_reply(self):
        pkt = _arp_request("25.1.0.1")
        reply = _build_arp_reply_for_tun(pkt, {"25.1.0.1": "peer-a"})
        assert reply is not None
        opcode, sha, spa, tha, tpa = _parse_arp(reply)
        assert opcode == 2  # reply
        assert spa == socket.inet_aton("25.1.0.1")  # our IP
        assert tha == b"\xaa\xbb\xcc\xdd\xee\xff"  # requester MAC
        assert tpa == socket.inet_aton("25.2.0.1")  # requester IP
        assert sha == _mac_for_ip("25.1.0.1")  # our derived MAC

    def test_no_reply_for_other_ip(self):
        pkt = _arp_request("25.9.9.9")
        assert _build_arp_reply_for_tun(pkt, {"25.1.0.1": "peer-a"}) is None

    def test_no_reply_for_non_arp(self):
        assert _build_arp_reply_for_tun(b"junk", {"25.1.0.1": "peer-a"}) is None


class TestMacForIp:
    def test_deterministic_and_distinct(self):
        assert _mac_for_ip("25.1.0.1") == _mac_for_ip("25.1.0.1")
        assert _mac_for_ip("25.1.0.1") != _mac_for_ip("25.1.0.2")

    def test_locally_administered_unicast(self):
        mac = _mac_for_ip("25.1.0.1")
        assert len(mac) == 6
        assert mac[0] == 0x02  # locally administered, unicast

    def test_invalid_ip_fallback(self):
        assert _mac_for_ip("not-an-ip") == bytes([0x02, 0, 0, 0, 0, 0])
