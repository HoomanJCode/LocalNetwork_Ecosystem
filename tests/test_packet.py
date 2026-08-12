"""Tests for common.frame — pack/unpack round-trips and validation."""

import struct

import pytest

from common import constants
from common.frame import (
    FRAME_HEADER_SIZE,
    FrameError,
    FrameHeader,
    FrameTooLargeError,
    InvalidFrameVersionError,
    frame_type_name,
    make_close_frame,
    make_keepalive_frame,
    make_punch_ack_frame,
    make_punch_frame,
    pack_frame,
    unpack_frame,
)

TAG_16 = b"\xab" * constants.GCM_TAG_SIZE


class TestPackUnpack:
    def test_roundtrip_data_frame(self):
        payload = b"\x45\x00\x00\x2a" + b"\x00" * 38  # fake IP header
        raw = pack_frame(constants.FRAME_DATA, payload, seq_num=7, auth_tag=TAG_16)
        header, cipher, tag = unpack_frame(raw)
        assert header.version == constants.FRAME_VERSION
        assert header.type == constants.FRAME_DATA
        assert header.payload_length == len(payload)
        assert header.seq_num == 7
        assert cipher == payload
        assert tag == TAG_16

    def test_roundtrip_empty_payload(self):
        raw = pack_frame(constants.FRAME_KEEPALIVE, b"", seq_num=0)
        header, cipher, tag = unpack_frame(raw)
        assert header.payload_length == 0
        assert cipher == b""
        assert len(tag) == constants.GCM_TAG_SIZE

    def test_punch_frame(self):
        raw = make_punch_frame(seq_num=1, payload=b"ECDH-key")
        header, cipher, _ = unpack_frame(raw)
        assert header.type == constants.FRAME_PUNCH
        assert cipher == b"ECDH-key"

    def test_punch_ack_frame(self):
        raw = make_punch_ack_frame(seq_num=2)
        header, _, _ = unpack_frame(raw)
        assert header.type == constants.FRAME_PUNCH_ACK

    def test_keepalive_and_close(self):
        assert unpack_frame(make_keepalive_frame())[0].type == constants.FRAME_KEEPALIVE
        assert unpack_frame(make_close_frame())[0].type == constants.FRAME_CLOSE

    def test_sequence_wraparound(self):
        raw = pack_frame(constants.FRAME_KEEPALIVE, b"", seq_num=0xFFFFFFFF + 5)
        header, _, _ = unpack_frame(raw)
        assert header.seq_num == 4  # masked to 32 bits

    def test_header_size_constant(self):
        assert FRAME_HEADER_SIZE == 8
        raw = pack_frame(constants.FRAME_KEEPALIVE, b"", seq_num=0)
        assert len(raw) == FRAME_HEADER_SIZE + constants.GCM_TAG_SIZE


class TestValidation:
    def test_rejects_bad_version(self):
        raw = pack_frame(constants.FRAME_KEEPALIVE, b"", seq_num=0)
        bad = bytes([0xFF]) + raw[1:]
        with pytest.raises(InvalidFrameVersionError):
            unpack_frame(bad)

    def test_pack_rejects_bad_version(self):
        with pytest.raises(InvalidFrameVersionError):
            pack_frame(constants.FRAME_KEEPALIVE, b"", seq_num=0, version=0x99)

    def test_rejects_too_short_buffer(self):
        with pytest.raises(FrameError):
            unpack_frame(b"\x01\x02")

    def test_rejects_truncated_payload(self):
        payload = b"A" * 100
        raw = pack_frame(constants.FRAME_DATA, payload, seq_num=1, auth_tag=TAG_16)
        with pytest.raises(FrameError):
            unpack_frame(raw[: len(raw) - 10])

    def test_rejects_oversized_payload(self):
        with pytest.raises(FrameTooLargeError):
            pack_frame(constants.FRAME_DATA, b"x" * 70000, seq_num=1)

    def test_rejects_wrong_tag_size(self):
        with pytest.raises(FrameError):
            pack_frame(constants.FRAME_DATA, b"data", seq_num=1, auth_tag=b"short")

    def test_declared_length_mismatch(self):
        # Header claims 5 payload bytes but only 2 present (plus tag)
        raw = (
            struct.pack("!BBHI", constants.FRAME_VERSION, constants.FRAME_DATA, 5, 1)
            + b"\x01\x02"
            + TAG_16
        )
        with pytest.raises(FrameError):
            unpack_frame(raw)


class TestHelpers:
    def test_frame_type_name(self):
        assert frame_type_name(constants.FRAME_DATA) == "DATA"
        assert frame_type_name(constants.FRAME_FORWARDED_STREAM) == "FORWARDED_STREAM"
        assert "UNKNOWN" in frame_type_name(0xEE)

    def test_header_total_size(self):
        header = FrameHeader(
            version=constants.FRAME_VERSION,
            type=constants.FRAME_DATA,
            payload_length=100,
            seq_num=1,
        )
        assert header.total_size == FRAME_HEADER_SIZE + 100 + constants.GCM_TAG_SIZE

    def test_max_payload_boundary(self):
        payload = b"x" * constants.MAX_FRAME_PAYLOAD
        raw = pack_frame(constants.FRAME_KEEPALIVE, payload, seq_num=0)
        header, cipher, _ = unpack_frame(raw)
        assert header.payload_length == constants.MAX_FRAME_PAYLOAD
        assert cipher == payload
