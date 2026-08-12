"""UDP hole punching and NAT diagnostics.

State machine (DESIGN.md §4.4)::

    IDLE ──► PUNCHING ──► CONNECTED ──► CLOSED
               │              │
               └── FAILED ────┘

Hole-punch process:

1. Client A asks the server for client B's endpoints.
2. Both clients send PUNCH frames (carrying their ephemeral ECDH public key)
   to each other's public endpoints simultaneously.
3. The first to receive a PUNCH replies with PUNCH_ACK.
4. The tunnel transitions to CONNECTED. If nobody responds within
   :data:`common.constants.HOLE_PUNCH_TIMEOUT`, the caller falls back to relay.
"""

from __future__ import annotations

import enum
import random
import socket
import struct
import time
from typing import List, Optional, Tuple

from common.constants import FRAME_PUNCH_ACK, HOLE_PUNCH_TIMEOUT, MAX_FRAME_PAYLOAD
from common.frame import (
    InvalidFrameVersionError,
    frame_type_name,
    make_punch_ack_frame,
    make_punch_frame,
    unpack_frame,
)

UDP_RECV_BUFFER = MAX_FRAME_PAYLOAD + 32


class PunchState(enum.Enum):
    """State of the hole-punch state machine."""

    IDLE = "IDLE"
    PUNCHING = "PUNCHING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class NatType(enum.Enum):
    """NAT classification for diagnostics (RFC 3489 style)."""

    OPEN = "open"                 # no NAT: public address reachable directly
    FULL_CONE = "full_cone"
    RESTRICTED = "restricted"     # filters by source IP
    PORT_RESTRICTED = "port_restricted"
    SYMMETRIC = "symmetric"
    UNKNOWN = "unknown"


def _is_frame_type(data: bytes, expected_type: int) -> bool:
    """Cheap type check that tolerates truncated frames (returns False)."""
    try:
        header, _, _ = unpack_frame(data)
    except (ValueError, InvalidFrameVersionError, IndexError):
        return False
    return header.type == expected_type


def _try_extract_payload(data: bytes) -> bytes:
    """Safely extract the payload from a valid frame; returns b"" on failure."""
    try:
        _, payload, _ = unpack_frame(data)
        return payload
    except Exception:
        return b""


class NatTraversal:
    """UDP hole-punching engine.

    The same instance is shared by all tunnels of a client; the per-tunnel
    UDP socket is created by :meth:`bind_udp_socket`.
    """

    def __init__(
        self,
        local_port_range: Tuple[int, int] = (49152, 65535),
        punch_timeout: float = HOLE_PUNCH_TIMEOUT,
        punch_retries: int = 3,
    ) -> None:
        if not (0 < local_port_range[0] <= local_port_range[1] < 65536):
            raise ValueError(f"invalid port range: {local_port_range}")
        self.local_port_range = local_port_range
        self.punch_timeout = punch_timeout
        self.punch_retries = punch_retries

    # ------------------------------------------------------------------
    # Socket helpers
    # ------------------------------------------------------------------
    def bind_udp_socket(self, host: str = "0.0.0.0") -> socket.socket:
        """Bind a UDP socket to the first free port in the configured range."""
        low, high = self.local_port_range
        candidates = list(range(low, high + 1))
        random.shuffle(candidates)
        last_error: Optional[OSError] = None
        for port in candidates[:64]:  # avoid scanning the whole range
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((host, port))
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        raise OSError(
            f"no free UDP port in range {low}-{high}: {last_error}"
        ) from last_error

    # ------------------------------------------------------------------
    # Active punch
    # ------------------------------------------------------------------
    def punch_peer(
        self,
        our_socket: socket.socket,
        peer_endpoints: List[Tuple[str, int]],
        ecdh_pubkey: bytes = b"",
        timeout: Optional[float] = None,
    ) -> Tuple[bool, Optional[Tuple[str, int]], bytes]:
        """Actively punch toward ``peer_endpoints``.

        Sends PUNCH frames (with the caller's ephemeral ECDH public key in the
        payload) to every endpoint, then listens for a PUNCH or PUNCH_ACK.
        Frames are re-sent on each timeout to keep the NAT mapping alive.

        Returns:
            ``(success, peer_addr, peer_pubkey)`` — peer_pubkey is the
            payload of the received PUNCH/PUNCH_ACK frame (peer's ECDH key).
        """
        if not peer_endpoints:
            return False, None, b""
        timeout = timeout or self.punch_timeout
        punch = make_punch_frame(payload=ecdh_pubkey)
        deadline = time.monotonic() + timeout
        # Timeout per recv cycle; re-send punches when it expires.
        cycle = max(0.05, min(timeout / self.punch_retries, 1.0))

        our_socket.settimeout(cycle)
        self._send_to_all(our_socket, punch, peer_endpoints)

        while time.monotonic() < deadline:
            try:
                data, addr = our_socket.recvfrom(UDP_RECV_BUFFER)
            except socket.timeout:
                # Re-send to keep the NAT mapping / port open.
                self._send_to_all(our_socket, punch, peer_endpoints)
                continue
            except OSError:
                return False, None, b""
            if _is_frame_type(data, FRAME_PUNCH_ACK):
                return True, addr, _try_extract_payload(data)
            try:
                header, _, _ = unpack_frame(data)
            except Exception:
                continue
            if header.type == 0x02:  # FRAME_PUNCH — peer punched us back
                payload = _try_extract_payload(data)
                our_socket.sendto(make_punch_ack_frame(), addr)
                return True, addr, payload
        return False, None, b""

    @staticmethod
    def _send_to_all(
        sock: socket.socket,
        frame: bytes,
        endpoints: List[Tuple[str, int]],
    ) -> None:
        for host, port in endpoints:
            try:
                sock.sendto(frame, (host, int(port)))
            except OSError:
                continue

    # ------------------------------------------------------------------
    # Passive accept
    # ------------------------------------------------------------------
    def accept_punch(
        self,
        sock: socket.socket,
        ecdh_pubkey: bytes = b"",
        timeout: Optional[float] = None,
    ) -> Optional[Tuple[Tuple[str, int], bytes]]:
        """Passive side: wait for a PUNCH frame and reply with PUNCH_ACK.

        Returns:
            ``((host, port), peer_pubkey)``, or None on timeout.  The
            peer_pubkey is the payload of the incoming PUNCH frame.
        """
        timeout = timeout or self.punch_timeout
        sock.settimeout(timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(UDP_RECV_BUFFER)
            except socket.timeout:
                return None
            except OSError:
                return None
            if _is_frame_type(data, 0x02):  # FRAME_PUNCH
                payload = _try_extract_payload(data)
                sock.sendto(make_punch_ack_frame(payload=ecdh_pubkey), addr)
                return ((addr), payload)
        return None

    # ------------------------------------------------------------------
    # NAT diagnostics (optional, STUN-based)
    # ------------------------------------------------------------------
    def determine_nat_type(
        self, stun_server: str = "stun.l.google.com", stun_port: int = 19302
    ) -> NatType:
        """Best-effort STUN classification (RFC 5389 Binding Request).

        Three probes are used:

        1. Send a binding request from socket A → learn the mapped address.
        2. Send from a different socket B → if the mapped port differs, the
           NAT maps each flow to a new port (symmetric).
        3. Send from socket A to a second STUN server address → if the mapped
           address differs across destinations, it's port-restricted.

        Any failure returns :data:`NatType.UNKNOWN` (never raises).
        """
        try:
            mapped_1 = self._stun_probe(stun_server, stun_port)
            if mapped_1 is None:
                return NatType.UNKNOWN
            mapped_2 = self._stun_probe(stun_server, stun_port, reuse_port=False)
            if mapped_2 is None:
                return NatType.UNKNOWN
            if mapped_2[1] != mapped_1[1]:
                return NatType.SYMMETRIC
            # Probe a second destination from socket A
            mapped_3 = self._stun_probe(stun_server, stun_port + 1)
            if mapped_3 is None:
                return NatType.RESTRICTED
            if mapped_3[0] != mapped_1[0] or mapped_3[1] != mapped_1[1]:
                return NatType.PORT_RESTRICTED
            return NatType.FULL_CONE
        except Exception:
            return NatType.UNKNOWN

    def _stun_probe(
        self, host: str, port: int, reuse_port: bool = True
    ) -> Optional[Tuple[str, int]]:
        """One STUN binding request; returns the XOR-MAPPED-ADDRESS or None."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if reuse_port:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except OSError:
                    pass
            sock.settimeout(2.0)
            transaction = random.randbytes(12)
            request = _stun_binding_request(transaction)
            sock.sendto(request, (host, port))
            data, _ = sock.recvfrom(UDP_RECV_BUFFER)
            return _parse_stun_mapped_address(data, transaction)
        except (OSError, socket.timeout, struct.error, ValueError):
            return None
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# STUN helpers (RFC 5389)
# ---------------------------------------------------------------------------
_STUN_MAGIC_COOKIE = 0x2112A442
_STUN_BINDING = 0x0001
_ATTR_XOR_MAPPED_ADDRESS = 0x0020


def _stun_binding_request(transaction_id: bytes) -> bytes:
    """Build a STUN Binding Request with a random 12-byte transaction id."""
    header = struct.pack(
        "!HHI12s",
        _STUN_BINDING,
        0,
        _STUN_MAGIC_COOKIE,
        transaction_id,
    )
    return header


def _parse_stun_mapped_address(data: bytes, transaction_id: bytes) -> Tuple[str, int]:
    """Extract the XOR-MAPPED-ADDRESS from a STUN binding response."""
    if len(data) < 20:
        raise ValueError("stun response too short")
    msg_type, length, cookie, tx = struct.unpack("!HHI12s", data[:20])
    if tx != transaction_id:
        raise ValueError("stun transaction id mismatch")
    pos = 20
    end = min(len(data), 20 + length)
    while pos + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[pos : pos + 4])
        value_start = pos + 4
        value_end = value_start + attr_len
        if value_end > end:
            break
        if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
            value = data[value_start:value_end]
            family = value[1]
            xport = struct.unpack("!H", value[2:4])[0]
            port = xport ^ (_STUN_MAGIC_COOKIE >> 16)
            if family == 0x01:  # IPv4
                ip = socket.inet_ntoa(
                    bytes(b ^ m for b, m in zip(value[4:8], struct.pack("!I", _STUN_MAGIC_COOKIE)))
                )
                return ip, port
            raise ValueError("IPv6 XOR-MAPPED-ADDRESS unsupported")
        pos = value_end + ((4 - attr_len % 4) % 4)
    raise ValueError("no XOR-MAPPED-ADDRESS in stun response")


__all__ = [
    "PunchState",
    "NatType",
    "NatTraversal",
    "UDP_RECV_BUFFER",
]
