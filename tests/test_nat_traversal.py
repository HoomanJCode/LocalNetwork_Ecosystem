"""Tests for client.nat_traversal — UDP hole punching and NAT diagnostics."""

import socket
import struct
import threading
import time
from unittest import mock

import pytest

from client.nat_traversal import (
    NatTraversal,
    NatType,
    PunchState,
    UDP_RECV_BUFFER,
    _is_frame_type,
    _parse_stun_mapped_address,
    _stun_binding_request,
    _try_extract_payload,
)
from common.constants import (
    FRAME_PUNCH,
    FRAME_PUNCH_ACK,
    HOLE_PUNCH_TIMEOUT,
    MAX_FRAME_PAYLOAD,
)
from common.frame import make_punch_ack_frame, make_punch_frame, unpack_frame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unused_udp_port() -> int:
    """Return a free UDP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _recv_until_deadline(sock: socket.socket, deadline: float) -> bytes:
    """Read from sock until deadline, or return b'' on timeout."""
    remaining = deadline - time.monotonic()
    while remaining > 0:
        try:
            sock.settimeout(remaining)
            data, _ = sock.recvfrom(UDP_RECV_BUFFER)
            return data
        except socket.timeout:
            return b""
        remaining = deadline - time.monotonic()
    return b""


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_defaults(self):
        nt = NatTraversal()
        assert nt.local_port_range == (49152, 65535)
        assert nt.punch_timeout == HOLE_PUNCH_TIMEOUT
        assert nt.punch_retries == 3

    def test_custom_values(self):
        nt = NatTraversal(
            local_port_range=(10000, 20000),
            punch_timeout=3.0,
            punch_retries=5,
        )
        assert nt.local_port_range == (10000, 20000)
        assert nt.punch_timeout == 3.0
        assert nt.punch_retries == 5

    def test_rejects_invalid_range_low_zero(self):
        with pytest.raises(ValueError, match="invalid port range"):
            NatTraversal(local_port_range=(0, 1000))

    def test_rejects_invalid_range_high_too_large(self):
        with pytest.raises(ValueError, match="invalid port range"):
            NatTraversal(local_port_range=(1, 65536))

    def test_rejects_inverted_range(self):
        with pytest.raises(ValueError, match="invalid port range"):
            NatTraversal(local_port_range=(2000, 1000))


# ---------------------------------------------------------------------------
# bind_udp_socket
# ---------------------------------------------------------------------------

class TestBindUdpSocket:
    def test_binds_successfully(self):
        nt = NatTraversal(local_port_range=(30000, 30010))
        sock = nt.bind_udp_socket("127.0.0.1")
        try:
            host, port = sock.getsockname()
            assert host == "127.0.0.1"
            assert 30000 <= port <= 30010
        finally:
            sock.close()

    def test_binds_different_ports_in_range(self):
        """Two consecutive binds should use different ports."""
        nt = NatTraversal(local_port_range=(31000, 31010))
        s1 = nt.bind_udp_socket("127.0.0.1")
        s2 = nt.bind_udp_socket("127.0.0.1")
        try:
            p1 = s1.getsockname()[1]
            p2 = s2.getsockname()[1]
            assert p1 != p2
            assert 31000 <= p1 <= 31010
            assert 31000 <= p2 <= 31010
        finally:
            s1.close()
            s2.close()

    def test_exhausted_range_raises(self):
        """A tiny range with all ports in use should raise OSError."""
        nt = NatTraversal(local_port_range=(39999, 39999))
        # Occupy the single port
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", 39999))
        try:
            with pytest.raises(OSError, match="no free UDP port"):
                nt.bind_udp_socket("127.0.0.1")
        finally:
            blocker.close()


# ---------------------------------------------------------------------------
# _is_frame_type helper
# ---------------------------------------------------------------------------

class TestIsFrameType:
    def test_matches_punch_frame(self):
        frame = make_punch_frame()
        assert _is_frame_type(frame, FRAME_PUNCH) is True

    def test_matches_punch_ack_frame(self):
        frame = make_punch_ack_frame()
        assert _is_frame_type(frame, FRAME_PUNCH_ACK) is True

    def test_wrong_type_returns_false(self):
        frame = make_punch_frame()
        assert _is_frame_type(frame, FRAME_PUNCH_ACK) is False

    def test_truncated_frame_returns_false(self):
        assert _is_frame_type(b"\x01\x02", FRAME_PUNCH) is False

    def test_garbage_returns_false(self):
        assert _is_frame_type(b"\x00" * 50, FRAME_PUNCH) is False

    def test_empty_returns_false(self):
        assert _is_frame_type(b"", FRAME_PUNCH) is False


# ---------------------------------------------------------------------------
# punch_peer — active side
# ---------------------------------------------------------------------------

class TestPunchPeer:
    def test_empty_endpoints_returns_false(self):
        nt = NatTraversal()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        try:
            success, addr, key = nt.punch_peer(sock, [])
            assert success is False
            assert addr is None
            assert key == b""
        finally:
            sock.close()

    def test_punch_peer_timeout_when_nobody_responds(self):
        """When nobody answers, punch_peer returns False after timeout."""
        nt = NatTraversal(punch_timeout=0.5, punch_retries=2)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        try:
            # Point at a port nobody is listening on
            dead_port = _unused_udp_port()
            success, addr, key = nt.punch_peer(
                sock, [("127.0.0.1", dead_port)], timeout=0.5
            )
            assert success is False
            assert addr is None
            assert key == b""
        finally:
            sock.close()

    def test_two_local_sockets_punch_succeeds(self):
        """Active punch against a passive peer on localhost."""
        nt = NatTraversal(punch_timeout=2.0, punch_retries=3)

        passive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        passive.bind(("127.0.0.1", 0))
        passive_addr = passive.getsockname()

        active = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        active.bind(("127.0.0.1", 0))

        result_holder = {"addr": None}

        def passive_side():
            result_holder["addr"] = nt.accept_punch(passive, timeout=3.0)

        thread = threading.Thread(target=passive_side, daemon=True)
        thread.start()

        try:
            success, peer_addr, peer_key = nt.punch_peer(
                active, [passive_addr], timeout=2.0
            )
            thread.join(timeout=4.0)

            assert success is True
            assert peer_addr is not None
            assert result_holder["addr"] is not None
        finally:
            active.close()
            passive.close()

    def test_punch_ack_received_returns_success(self):
        """When the active side receives a PUNCH_ACK, it returns True."""
        nt = NatTraversal(punch_timeout=2.0, punch_retries=2)

        # Simulated peer that just sends a PUNCH_ACK when it gets a PUNCH
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer_addr = peer.getsockname()

        active = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        active.bind(("127.0.0.1", 0))

        ack_sent = threading.Event()

        def peer_side():
            try:
                data, addr = peer.recvfrom(UDP_RECV_BUFFER)
                if _is_frame_type(data, FRAME_PUNCH):
                    peer.sendto(make_punch_ack_frame(), addr)
                    ack_sent.set()
            except OSError:
                pass

        thread = threading.Thread(target=peer_side, daemon=True)
        thread.start()

        try:
            success, addr, key = nt.punch_peer(active, [peer_addr], timeout=2.0)
            thread.join(timeout=3.0)

            assert success is True
            assert addr == peer_addr
            assert ack_sent.is_set()
        finally:
            active.close()
            peer.close()

    def test_receiving_punch_sends_punch_ack(self):
        """When both sides punch simultaneously, each sends PUNCH_ACK to the other."""
        nt = NatTraversal(punch_timeout=2.0, punch_retries=3)

        s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s1.bind(("127.0.0.1", 0))
        addr1 = s1.getsockname()

        s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s2.bind(("127.0.0.1", 0))
        addr2 = s2.getsockname()

        results = {}

        def side(sock, endpoints, name):
            success, addr, key = nt.punch_peer(sock, endpoints, timeout=2.0)
            results[name] = (success, addr, key)

        t1 = threading.Thread(target=side, args=(s1, [addr2], "s1"), daemon=True)
        t2 = threading.Thread(target=side, args=(s2, [addr1], "s2"), daemon=True)

        t1.start()
        t2.start()
        t1.join(timeout=4.0)
        t2.join(timeout=4.0)

        try:
            assert results.get("s1", (False, None, b""))[0] is True
            assert results.get("s2", (False, None, b""))[0] is True
        finally:
            s1.close()
            s2.close()


# ---------------------------------------------------------------------------
# accept_punch — passive side
# ---------------------------------------------------------------------------

class TestAcceptPunch:
    def test_receives_punch_returns_addr(self):
        """Passive side receives a PUNCH and returns the sender's address."""
        nt = NatTraversal()
        my_key = b"A" * 32  # 32-byte ECDH pubkey

        passive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        passive.bind(("127.0.0.1", 0))
        passive_addr = passive.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        result = {}

        def accept_side():
            result["addr"] = nt.accept_punch(passive, ecdh_pubkey=my_key, timeout=2.0)

        thread = threading.Thread(target=accept_side, daemon=True)
        thread.start()

        # Give passive a moment to start listening
        time.sleep(0.1)
        sender.sendto(make_punch_frame(), passive_addr)

        thread.join(timeout=3.0)

        try:
            # accept_punch returns ((host, port), peer_pubkey) or None
            assert result["addr"] is not None
            peer_addr, peer_pubkey = result["addr"]
            assert peer_addr == sender.getsockname()
            # Passive side should have sent back a PUNCH_ACK with our pubkey
            data, _ = sender.recvfrom(UDP_RECV_BUFFER)
            assert _is_frame_type(data, FRAME_PUNCH_ACK) is True
            # The ack should carry the passive side's key as payload
            _, payload, _ = unpack_frame(data)
            assert len(payload) == 32  # ECDH pubkey
        finally:
            passive.close()
            sender.close()

    def test_timeout_returns_none(self):
        """When nobody sends a PUNCH, accept_punch returns None."""
        nt = NatTraversal()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        try:
            result = nt.accept_punch(sock, timeout=0.3)
            assert result is None
        finally:
            sock.close()

    def test_ignores_non_punch_frames(self):
        """Non-PUNCH frames are ignored; only PUNCH triggers a response."""
        nt = NatTraversal()

        passive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        passive.bind(("127.0.0.1", 0))
        passive_addr = passive.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        result = {}

        def accept_side():
            result["addr"] = nt.accept_punch(passive, timeout=2.0)

        thread = threading.Thread(target=accept_side, daemon=True)
        thread.start()

        time.sleep(0.1)
        # Send a PUNCH_ACK first (which should be ignored by accept_punch)
        sender.sendto(make_punch_ack_frame(), passive_addr)
        time.sleep(0.2)
        # Then send a real PUNCH
        sender.sendto(make_punch_frame(), passive_addr)

        thread.join(timeout=3.0)

        try:
            assert result["addr"] is not None
            peer_addr, _ = result["addr"]
            assert peer_addr == sender.getsockname()
        finally:
            passive.close()
            sender.close()

    def test_echos_ecdh_pubkey_in_ack(self):
        """accept_punch includes the ECDH pubkey in the PUNCH_ACK payload."""
        nt = NatTraversal()
        test_key = b"ecdh-pubkey-32-bytes-xxxxxxxxxx"

        passive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        passive.bind(("127.0.0.1", 0))
        passive_addr = passive.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        result_holder = {}

        def accept_side():
            result_holder["result"] = nt.accept_punch(passive, ecdh_pubkey=test_key, timeout=2.0)

        thread = threading.Thread(target=accept_side, daemon=True)
        thread.start()

        time.sleep(0.1)
        sender.sendto(make_punch_frame(), passive_addr)
        thread.join(timeout=3.0)

        try:
            data, _ = sender.recvfrom(UDP_RECV_BUFFER)
            header, payload, _ = unpack_frame(data)
            assert header.type == FRAME_PUNCH_ACK
            assert payload == test_key
            # accept_punch also returns the peer's pubkey
            assert result_holder["result"] is not None
        finally:
            passive.close()
            sender.close()


# ---------------------------------------------------------------------------
# _send_to_all
# ---------------------------------------------------------------------------

class TestSendToAll:
    def test_sends_to_all_endpoints(self):
        nt = NatTraversal()
        r1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        r1.bind(("127.0.0.1", 0))
        r1_addr = r1.getsockname()

        r2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        r2.bind(("127.0.0.1", 0))
        r2_addr = r2.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        try:
            frame = make_punch_frame(payload=b"hello")
            nt._send_to_all(sender, frame, [r1_addr, r2_addr])

            # Both receivers should get the frame
            for sock in (r1, r2):
                sock.settimeout(1.0)
                data, _ = sock.recvfrom(UDP_RECV_BUFFER)
                assert data == frame
        finally:
            r1.close()
            r2.close()
            sender.close()

    def test_continues_on_send_error(self):
        """If one endpoint is unreachable, _send_to_all still tries the others."""
        nt = NatTraversal()
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        recv_addr = receiver.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        try:
            dead_port = _unused_udp_port()
            frame = make_punch_frame(payload=b"test")
            # Should not raise — just logs/ignores the dead endpoint
            nt._send_to_all(sender, frame, [("127.0.0.1", dead_port), recv_addr])

            receiver.settimeout(1.0)
            data, _ = receiver.recvfrom(UDP_RECV_BUFFER)
            assert data == frame
        finally:
            receiver.close()
            sender.close()


# ---------------------------------------------------------------------------
# STUN helpers
# ---------------------------------------------------------------------------

class TestStunBindingRequest:
    def test_produces_valid_header(self):
        tx_id = b"\x01" * 12
        req = _stun_binding_request(tx_id)
        assert len(req) == 20
        msg_type, msg_len, cookie, tx = struct.unpack("!HHI12s", req)
        assert msg_type == 0x0001  # Binding Request
        assert msg_len == 0
        assert cookie == 0x2112A442  # Magic cookie
        assert tx == tx_id


class TestParseStunMappedAddress:
    def test_parses_xor_mapped_address_ipv4(self):
        """Build a synthetic STUN response and verify IPv4 XOR decoding."""
        tx_id = b"\xaa" * 12
        cookie = 0x2112A442

        # Build XOR-MAPPED-ADDRESS attribute for 1.2.3.4:5678
        family = 0x01  # IPv4
        xport = 5678 ^ (cookie >> 16)  # XOR with upper 16 bits of cookie
        real_ip = b"\x01\x02\x03\x04"
        xip = bytes(b ^ m for b, m in zip(real_ip, struct.pack("!I", cookie)))

        attr_type = 0x0020  # XOR-MAPPED-ADDRESS
        attr_value = struct.pack("!BBH", 0, family, xport) + xip
        attr_len = len(attr_value)
        # 8-byte value is already 4-byte aligned; no padding needed
        attr_header = struct.pack("!HH", attr_type, attr_len)

        # STUN header: message type (Binding Success = 0x0101), length, cookie, tx
        msg_len = len(attr_header) + len(attr_value)
        header = struct.pack("!HHI12s", 0x0101, msg_len, cookie, tx_id)

        data = header + attr_header + attr_value
        ip, port = _parse_stun_mapped_address(data, tx_id)
        assert ip == "1.2.3.4"
        assert port == 5678

    def test_transaction_id_mismatch_raises(self):
        tx_id = b"\xaa" * 12
        cookie = 0x2112A442
        header = struct.pack("!HHI12s", 0x0101, 0, cookie, b"\xbb" * 12)
        with pytest.raises(ValueError, match="transaction id mismatch"):
            _parse_stun_mapped_address(header, tx_id)

    def test_response_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            _parse_stun_mapped_address(b"\x00" * 10, b"\x00" * 12)

    def test_no_xor_mapped_address_raises(self):
        """Response with attributes but no XOR-MAPPED-ADDRESS."""
        tx_id = b"\xcc" * 12
        cookie = 0x2112A442
        # Add a dummy attribute that is not XOR-MAPPED-ADDRESS
        dummy_type = 0x0001  # MAPPED-ADDRESS
        dummy_value = b"\x00" * 8
        attr_header = struct.pack("!HH", dummy_type, len(dummy_value))
        msg_len = len(attr_header) + len(dummy_value)
        header = struct.pack("!HHI12s", 0x0101, msg_len, cookie, tx_id)
        data = header + attr_header + dummy_value
        with pytest.raises(ValueError, match="no XOR-MAPPED-ADDRESS"):
            _parse_stun_mapped_address(data, tx_id)

    def test_skips_past_unaligned_attribute(self):
        """Padding is correctly computed when attribute value is not 4-byte aligned."""
        tx_id = b"\xee" * 12
        cookie = 0x2112A442

        # First attribute: 7-byte value + 1 byte padding → 8 bytes aligned
        dummy_type = 0x0001
        dummy_value = b"\x00" * 7
        dummy_attr = (
            struct.pack("!HH", dummy_type, len(dummy_value))
            + dummy_value
            + b"\x00"  # 1 byte padding to 4-byte boundary per RFC 5389
        )

        # Second attribute: XOR-MAPPED-ADDRESS
        family = 0x01
        xport = 7777 ^ (cookie >> 16)
        real_ip = b"\x0a\x00\x00\x01"
        xip = bytes(b ^ m for b, m in zip(real_ip, struct.pack("!I", cookie)))
        xor_value = struct.pack("!BBH", 0, family, xport) + xip
        xor_attr = struct.pack("!HH", 0x0020, len(xor_value)) + xor_value

        body = dummy_attr + xor_attr
        msg_len = len(body)
        header = struct.pack("!HHI12s", 0x0101, msg_len, cookie, tx_id)
        data = header + body

        ip, port = _parse_stun_mapped_address(data, tx_id)
        assert ip == "10.0.0.1"
        assert port == 7777

    def test_skips_past_non_matching_attributes(self):
        """XOR-MAPPED-ADDRESS after another attribute is still found."""
        tx_id = b"\xdd" * 12
        cookie = 0x2112A442

        # First attribute: dummy (8 bytes value)
        dummy_type = 0x0001
        dummy_value = b"\x00" * 8
        dummy_attr = struct.pack("!HH", dummy_type, len(dummy_value)) + dummy_value

        # Second attribute: XOR-MAPPED-ADDRESS
        family = 0x01
        xport = 9999 ^ (cookie >> 16)
        real_ip = b"\x0a\x0b\x0c\x0d"
        xip = bytes(b ^ m for b, m in zip(real_ip, struct.pack("!I", cookie)))
        xor_value = struct.pack("!BBH", 0, family, xport) + xip
        xor_attr = struct.pack("!HH", 0x0020, len(xor_value)) + xor_value

        body = dummy_attr + xor_attr
        msg_len = len(body)
        header = struct.pack("!HHI12s", 0x0101, msg_len, cookie, tx_id)
        data = header + body

        ip, port = _parse_stun_mapped_address(data, tx_id)
        assert ip == "10.11.12.13"
        assert port == 9999


# ---------------------------------------------------------------------------
# determine_nat_type (mocked STUN)
# ---------------------------------------------------------------------------

class TestDetermineNatType:
    def test_unreachable_stun_returns_unknown(self):
        """If the STUN server is unreachable, return UNKNOWN."""
        nt = NatTraversal()
        with mock.patch.object(nt, "_stun_probe", return_value=None):
            result = nt.determine_nat_type()
            assert result == NatType.UNKNOWN

    def test_symmetric_nat_detected(self):
        """Different mapped ports from different sockets → SYMMETRIC."""
        nt = NatTraversal()
        probes = [
            ("1.2.3.4", 1000),   # probe 1
            ("1.2.3.4", 2000),   # probe 2 — different port
        ]

        def fake_probe(host, port, reuse_port=True):
            if not probes:
                return None
            return probes.pop(0)

        with mock.patch.object(nt, "_stun_probe", side_effect=fake_probe):
            result = nt.determine_nat_type()
            assert result == NatType.SYMMETRIC

    def test_full_cone_detected(self):
        """Same mapped addr from two different destinations → FULL_CONE."""
        nt = NatTraversal()
        calls = 0

        def fake_probe(host, port, reuse_port=True):
            nonlocal calls
            calls += 1
            return ("5.6.7.8", 3000)  # Always same mapped address

        with mock.patch.object(nt, "_stun_probe", side_effect=fake_probe):
            result = nt.determine_nat_type()
            assert result == NatType.FULL_CONE

    def test_port_restricted_detected(self):
        """Different mapped addr to different destination → PORT_RESTRICTED."""
        nt = NatTraversal()
        responses = [
            ("9.9.9.9", 4000),   # probe 1: stun on default port
            ("9.9.9.9", 4000),   # probe 2: same as probe 1 (not symmetric)
            ("9.9.9.9", 4001),   # probe 3: different mapped port to alt destination
        ]

        def fake_probe(host, port, reuse_port=True):
            return responses.pop(0)

        with mock.patch.object(nt, "_stun_probe", side_effect=fake_probe):
            result = nt.determine_nat_type()
            assert result == NatType.PORT_RESTRICTED

    def test_restricted_detected_when_alt_fails(self):
        """Alt STUN port unreachable from socket A → RESTRICTED."""
        nt = NatTraversal()
        responses = [
            ("10.0.0.1", 5000),  # probe 1
            ("10.0.0.1", 5000),  # probe 2: same port (not symmetric)
            None,                 # probe 3: alt port unreachable
        ]

        def fake_probe(host, port, reuse_port=True):
            return responses.pop(0)

        with mock.patch.object(nt, "_stun_probe", side_effect=fake_probe):
            result = nt.determine_nat_type()
            assert result == NatType.RESTRICTED

    def test_exception_returns_unknown(self):
        """Any exception during classification returns UNKNOWN."""
        nt = NatTraversal()
        with mock.patch.object(nt, "_stun_probe", side_effect=OSError("boom")):
            result = nt.determine_nat_type()
            assert result == NatType.UNKNOWN

    def test_second_probe_none_returns_unknown(self):
        """If probe 2 fails, fallback to UNKNOWN."""
        nt = NatTraversal()
        responses = [
            ("1.1.1.1", 1234),  # probe 1 succeeds
            None,                # probe 2 fails
        ]

        def fake_probe(host, port, reuse_port=True):
            return responses.pop(0)

        with mock.patch.object(nt, "_stun_probe", side_effect=fake_probe):
            result = nt.determine_nat_type()
            assert result == NatType.UNKNOWN


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestPunchState:
    def test_all_states_defined(self):
        assert PunchState.IDLE.value == "IDLE"
        assert PunchState.PUNCHING.value == "PUNCHING"
        assert PunchState.CONNECTED.value == "CONNECTED"
        assert PunchState.FAILED.value == "FAILED"
        assert PunchState.CLOSED.value == "CLOSED"


class TestNatType:
    def test_all_types_defined(self):
        assert NatType.OPEN.value == "open"
        assert NatType.FULL_CONE.value == "full_cone"
        assert NatType.RESTRICTED.value == "restricted"
        assert NatType.PORT_RESTRICTED.value == "port_restricted"
        assert NatType.SYMMETRIC.value == "symmetric"
        assert NatType.UNKNOWN.value == "unknown"


# ---------------------------------------------------------------------------
# UDP_RECV_BUFFER constant
# ---------------------------------------------------------------------------

class TestUdpRecvBuffer:
    def test_buffer_large_enough_for_max_frame(self):
        """UDP recv buffer must accommodate a max payload + frame overhead."""
        assert UDP_RECV_BUFFER >= MAX_FRAME_PAYLOAD + 8 + 16


# ---------------------------------------------------------------------------
# punch_peer with ECDH payload
# ---------------------------------------------------------------------------

class TestPunchPayload:
    def test_echd_pubkey_embedded_in_punch(self):
        """PUNCH frames carry the caller's ECDH public key in the payload."""
        nt = NatTraversal()
        test_key = b"ecdh-key-material-32-bytes!!"

        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener_addr = listener.getsockname()

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        # Start punch_peer in a thread — it sends PUNCH with the key
        def do_punch():
            success, addr, key = nt.punch_peer(sender, [listener_addr], ecdh_pubkey=test_key, timeout=1.0)

        thread = threading.Thread(target=do_punch, daemon=True)
        thread.start()

        try:
            # The listener should receive a PUNCH frame with the key
            listener.settimeout(2.0)
            data, _ = listener.recvfrom(UDP_RECV_BUFFER)
            header, payload, _ = unpack_frame(data)
            assert header.type == FRAME_PUNCH
            assert payload == test_key
        finally:
            sender.close()
            listener.close()
            thread.join(timeout=3.0)
