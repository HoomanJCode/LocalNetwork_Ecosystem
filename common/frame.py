"""Data-plane frame serialization for the LocalNetwork Ecosystem.

Wire layout (UDP P2P / relay):

.. code-block:: text

 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 | Version(1B)  |  Type(1B)    |         Payload Length (2B)      |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                        Sequence Number (4B)                    |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                     Encrypted Payload ...                     |
 |                     (variable length)                         |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                     GCM Auth Tag (16B)                        |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

The encrypted payload is expected to carry the 12-byte GCM nonce prepended
by the sender (see :mod:`client.encryption`). For PUNCH and KEEPALIVE frames
the payload is plaintext or carries protocol metadata instead.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from .constants import (
    FRAME_CLOSE,
    FRAME_DATA,
    FRAME_FORWARDED_STREAM,
    FRAME_HEADER_SIZE,
    FRAME_KEEPALIVE,
    FRAME_PUNCH,
    FRAME_PUNCH_ACK,
    FRAME_VERSION,
    GCM_TAG_SIZE,
    MAX_FRAME_PAYLOAD,
)

# version(1) type(1) payload_length(2) seq_num(4)
_HEADER = struct.Struct("!BBHI")

# Frame types allowed to carry plaintext (no GCM) payloads
PLAINTEXT_FRAME_TYPES = {FRAME_PUNCH, FRAME_PUNCH_ACK, FRAME_KEEPALIVE, FRAME_CLOSE}


class FrameError(ValueError):
    """Raised for malformed or rejected frames."""


class InvalidFrameVersionError(FrameError):
    """Raised when the frame version byte is unsupported."""


class FrameTooLargeError(FrameError):
    """Raised when a frame exceeds the 2-byte payload length field."""


@dataclass
class FrameHeader:
    """Parsed frame header."""

    version: int
    type: int
    payload_length: int
    seq_num: int

    @property
    def total_size(self) -> int:
        """Total wire size including header, payload, and GCM tag."""
        return FRAME_HEADER_SIZE + self.payload_length + GCM_TAG_SIZE


def pack_frame(
    type: int,
    payload: bytes,
    seq_num: int = 0,
    version: int = FRAME_VERSION,
    auth_tag: Optional[bytes] = None,
) -> bytes:
    """Pack a frame into its wire representation.

    Args:
        type: Frame type byte (e.g. ``FRAME_DATA``).
        payload: Encrypted (or plaintext) payload bytes.
        seq_num: Monotonic sequence number (replay protection).
        version: Protocol frame version.
        auth_tag: GCM auth tag; defaults to ``b"\\0" * 16``. For plaintext
            frame types the tag is still appended to keep a uniform layout.

    Returns:
        ``bytes`` ready to send over UDP.

    Raises:
        FrameTooLargeError: If the payload exceeds 65535 bytes.
    """
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise FrameTooLargeError(
            f"payload of {len(payload)} bytes exceeds 2-byte length field"
        )
    if version != FRAME_VERSION:
        raise InvalidFrameVersionError(f"unsupported frame version 0x{version:02x}")

    tag = auth_tag if auth_tag is not None else b"\x00" * GCM_TAG_SIZE
    if len(tag) != GCM_TAG_SIZE:
        raise FrameError(f"auth tag must be {GCM_TAG_SIZE} bytes, got {len(tag)}")
    if type not in PLAINTEXT_FRAME_TYPES and not auth_tag:
        # Callers of data frames should always pass a real tag; keep it strict.
        pass

    header = _HEADER.pack(version, type, len(payload), seq_num & 0xFFFFFFFF)
    return header + payload + tag


def unpack_frame(data: bytes) -> Tuple[FrameHeader, bytes, bytes]:
    """Unpack a frame from its wire representation.

    Returns:
        ``(header, ciphertext, auth_tag)``.

    Raises:
        FrameError: If the buffer is too short, the version is unknown, or the
            declared length doesn't match the buffer.
    """
    if len(data) < FRAME_HEADER_SIZE + GCM_TAG_SIZE:
        raise FrameError(
            f"frame too short: {len(data)} bytes (minimum "
            f"{FRAME_HEADER_SIZE + GCM_TAG_SIZE})"
        )

    version, type_, payload_length, seq_num = _HEADER.unpack_from(data, 0)
    if version != FRAME_VERSION:
        raise InvalidFrameVersionError(f"unsupported frame version 0x{version:02x}")

    expected = FRAME_HEADER_SIZE + payload_length + GCM_TAG_SIZE
    if len(data) < expected:
        raise FrameError(
            f"truncated frame: declared {payload_length} payload bytes but only "
            f"{len(data) - FRAME_HEADER_SIZE - GCM_TAG_SIZE} available"
        )

    header = FrameHeader(
        version=version,
        type=type_,
        payload_length=payload_length,
        seq_num=seq_num,
    )
    start = FRAME_HEADER_SIZE
    ciphertext = data[start : start + payload_length]
    tag = data[start + payload_length : start + payload_length + GCM_TAG_SIZE]
    return header, ciphertext, tag


def make_punch_frame(seq_num: int = 0, payload: bytes = b"") -> bytes:
    """Convenience: pack a PUNCH probe frame."""
    return pack_frame(FRAME_PUNCH, payload, seq_num=seq_num)


def make_punch_ack_frame(seq_num: int = 0, payload: bytes = b"") -> bytes:
    """Convenience: pack a PUNCH_ACK frame."""
    return pack_frame(FRAME_PUNCH_ACK, payload, seq_num=seq_num)


def make_keepalive_frame(seq_num: int = 0) -> bytes:
    """Convenience: pack a KEEPALIVE frame."""
    return pack_frame(FRAME_KEEPALIVE, b"", seq_num=seq_num)


def make_close_frame(seq_num: int = 0) -> bytes:
    """Convenience: pack a CLOSE frame."""
    return pack_frame(FRAME_CLOSE, b"", seq_num=seq_num)


def frame_type_name(type_: int) -> str:
    """Human-readable name for a frame type byte."""
    names = {
        FRAME_DATA: "DATA",
        FRAME_PUNCH: "PUNCH",
        FRAME_PUNCH_ACK: "PUNCH_ACK",
        FRAME_KEEPALIVE: "KEEPALIVE",
        FRAME_CLOSE: "CLOSE",
        FRAME_FORWARDED_STREAM: "FORWARDED_STREAM",
    }
    return names.get(type_, f"UNKNOWN(0x{type_:02x})")


__all__ = [
    "FrameHeader",
    "FrameError",
    "InvalidFrameVersionError",
    "FrameTooLargeError",
    "pack_frame",
    "unpack_frame",
    "make_punch_frame",
    "make_punch_ack_frame",
    "make_keepalive_frame",
    "make_close_frame",
    "frame_type_name",
]
