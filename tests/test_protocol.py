"""Tests for common.messages — serialize/deserialize round-trips."""

import base64
import json
import struct

import pytest

from common import constants
from common import messages as m


def roundtrip(msg: m.Message) -> m.Message:
    return m.deserialize(m.serialize(msg))


class TestSerializeRoundTrip:
    def test_generic_message_roundtrip(self):
        msg = m.Message(type=constants.MSG_HEARTBEAT, payload={"ts": 123.4})
        out = roundtrip(msg)
        assert out.type == constants.MSG_HEARTBEAT
        assert out.payload["ts"] == pytest.approx(123.4)

    def test_register_roundtrip(self):
        msg = m.make_message(
            m.RegisterMessage,
            client_id="uuid-1",
            public_key="PEM...",
            version="0.1.0",
        )
        out = roundtrip(msg)
        assert out.type == constants.MSG_REGISTER
        assert out.payload["client_id"] == "uuid-1"
        assert out.payload["public_key"] == "PEM..."

    def test_auth_challenge_roundtrip(self):
        msg = m.make_message(m.AuthChallenge, challenge="deadbeef", client_id="c1")
        out = roundtrip(msg)
        assert out.payload["challenge"] == "deadbeef"
        assert out.payload["client_id"] == "c1"

    def test_auth_response_roundtrip(self):
        msg = m.make_message(m.AuthResponse, signature="sig", challenge="chal")
        out = roundtrip(msg)
        assert out.payload["signature"] == "sig"

    def test_create_network_roundtrip(self):
        msg = m.make_message(
            m.CreateNetwork, name="testnet", password="pw", topology="mesh"
        )
        out = roundtrip(msg)
        assert out.payload["name"] == "testnet"
        assert out.payload["topology"] == "mesh"

    def test_join_network_roundtrip(self):
        msg = m.make_message(m.JoinNetwork, network_id="n1", password="pw")
        out = roundtrip(msg)
        assert out.payload["network_id"] == "n1"

    def test_peer_online_roundtrip(self):
        msg = m.make_message(
            m.PeerOnline, network_id="n1", peer_id="p2", peer_ip="25.0.0.2"
        )
        out = roundtrip(msg)
        assert out.payload["peer_id"] == "p2"
        assert out.payload["peer_ip"] == "25.0.0.2"

    def test_peer_endpoints_roundtrip(self):
        msg = m.make_message(
            m.PeerEndpoints, peer_id="p2", endpoints=[["1.2.3.4", 54000], ["127.0.0.1", 5555]]
        )
        out = roundtrip(msg)
        assert out.payload["endpoints"] == [["1.2.3.4", 54000], ["127.0.0.1", 5555]]

    def test_relay_frame_roundtrip(self):
        frame = base64.b64encode(b"\x01\x01\x00\x04" + b"data").decode()
        msg = m.make_message(m.RelayFrame, src_id="a", dst_id="b", frame_b64=frame)
        out = roundtrip(msg)
        assert base64.b64decode(out.payload["frame_b64"]) == b"\x01\x01\x00\x04data"

    def test_service_messages_roundtrip(self):
        cases = [
            m.make_message(m.ExposeService, name="mc", protocol="tcp", local_port=25565),
            m.make_message(m.ServiceExposed, service_id="s1", name="mc", local_port=25565),
            m.make_message(m.UnexposeService, service_id="s1"),
            m.make_message(m.MapService, service_id="s1", strategy="auto"),
            m.make_message(m.ServiceMapped, service_id="s1", local_port=25565),
            m.make_message(m.ServiceList, services=[{"service_id": "s1"}]),
            m.make_message(m.ServiceAdded, service={"service_id": "s1", "name": "mc"}),
            m.make_message(m.ServiceRemoved, service_id="s1"),
        ]
        for msg in cases:
            out = roundtrip(msg)
            assert out.type == msg.type
            assert out.payload == msg.payload

    def test_all_typed_messages_roundtrip(self):
        """Every registered message type round-trips through the wire format."""
        samples = [
            (m.RegisterMessage, {"client_id": "x", "public_key": "k"}),
            (m.AuthChallenge, {"challenge": "c", "client_id": "x"}),
            (m.AuthResponse, {"signature": "s", "challenge": "c"}),
            (m.AuthResult, {"ok": True, "message": "hi", "virtual_ip": "25.0.0.1"}),
            (m.CreateNetwork, {"name": "n", "password": "p", "topology": "mesh"}),
            (m.NetworkCreated, {"network_id": "n", "name": "n"}),
            (m.JoinNetwork, {"network_id": "n", "password": "p"}),
            (m.NetworkJoined, {"network_id": "n", "virtual_ip": "25.0.0.2"}),
            (m.LeaveNetwork, {"network_id": "n"}),
            (m.NetworkLeft, {"network_id": "n"}),
            (m.ListNetworks, {}),
            (m.NetworkList, {"networks": [{"network_id": "n"}]}),
            (m.NetworkPeers, {"network_id": "n", "peers": [{"client_id": "p"}]}),
            (m.PeerOnline, {"network_id": "n", "peer_id": "p"}),
            (m.PeerOffline, {"network_id": "n", "peer_id": "p"}),
            (m.RequestPeerConn, {"peer_id": "p"}),
            (m.PeerEndpoints, {"peer_id": "p", "endpoints": [["h", 1]]}),
            (m.RelayRequest, {"peer_id": "p"}),
            (m.RelayGranted, {"peer_id": "p", "path_id": "r"}),
            (m.RelayFrame, {"src_id": "a", "dst_id": "b", "frame_b64": "AA=="}),
            (m.Heartbeat, {"ts": 1.0}),
            (m.HeartbeatAck, {"ts": 1.0}),
            (m.ErrorMessage, {"code": "E", "message": "err"}),
            (m.ExposeService, {"name": "s", "protocol": "udp", "local_port": 5000}),
            (m.ServiceExposed, {"service_id": "s", "name": "s"}),
            (m.UnexposeService, {"service_id": "s"}),
            (m.ServiceUnexposed, {"service_id": "s"}),
            (m.ServiceList, {"services": []}),
            (m.ServiceAdded, {"service": {}}),
            (m.ServiceRemoved, {"service_id": "s"}),
            (m.MapService, {"service_id": "s", "strategy": "auto"}),
            (m.ServiceMapped, {"service_id": "s", "local_port": 1}),
            (m.UnmapService, {"service_id": "s"}),
            (m.ServiceUnmapped, {"service_id": "s"}),
        ]
        for cls, payload in samples:
            msg = m.make_message(cls, **payload)
            out = roundtrip(msg)
            assert out.type == msg.type, cls.__name__
            assert out.payload == msg.payload, cls.__name__


class TestWireFormat:
    def test_length_prefix_is_4_byte_big_endian(self):
        data = m.serialize(m.Message(type=constants.MSG_HEARTBEAT))
        length = struct.unpack("!I", data[:4])[0]
        assert length == len(data) - 4

    def test_payload_is_json(self):
        data = m.serialize(m.Message(type=constants.MSG_HEARTBEAT, payload={"a": 1}))
        body = json.loads(data[4:].decode("utf-8"))
        assert body["type"] == constants.MSG_HEARTBEAT
        assert body["payload"] == {"a": 1}

    def test_unicode_survives(self):
        msg = m.make_message(m.CreateNetwork, name="شبکه آزمایشی 🚀")
        out = roundtrip(msg)
        assert out.payload["name"] == "شبکه آزمایشی 🚀"

    def test_parse_stream_extracts_multiple_messages(self):
        buf = b""
        messages = []
        for i in range(3):
            buf += m.serialize(m.make_message(m.Heartbeat, ts=float(i)))
        while buf:
            msg, buf = m.parse_stream(buf)
            assert msg is not None
            messages.append(msg)
        assert len(messages) == 3
        assert [msg.payload["ts"] for msg in messages] == [0.0, 1.0, 2.0]

    def test_parse_stream_partial_message(self):
        data = m.serialize(m.make_message(m.Heartbeat, ts=1.0))
        msg, remaining = m.parse_stream(data[:6])  # only header
        assert msg is None
        assert remaining == data[:6]

    def test_parse_stream_empty(self):
        msg, remaining = m.parse_stream(b"")
        assert msg is None and remaining == b""

    def test_deserialize_too_large_declared(self):
        data = struct.pack("!I", 999_999_999) + b"{}"
        with pytest.raises(m.MessageTooLargeError):
            m.deserialize(data)

    def test_deserialize_truncated(self):
        data = struct.pack("!I", 100) + b"short"
        with pytest.raises(ValueError):
            m.deserialize(data)

    def test_deserialize_invalid_json(self):
        data = struct.pack("!I", 5) + b"nope!"
        with pytest.raises(ValueError):
            m.deserialize(data)

    def test_deserialize_missing_type(self):
        data = struct.pack("!I", 2) + b"{}"
        with pytest.raises(ValueError):
            m.deserialize(data)

    def test_serialize_rejects_non_message(self):
        with pytest.raises(TypeError):
            m.serialize("not a message")  # type: ignore[arg-type]

    def test_serialize_oversized_payload(self):
        big = {"blob": "x" * (constants.MAX_MESSAGE_SIZE + 1)}
        with pytest.raises(m.MessageTooLargeError):
            m.serialize(m.Message(type=constants.MSG_HEARTBEAT, payload=big))


class TestTypedHelpers:
    def test_message_to_dataclass(self):
        msg = m.make_message(m.JoinNetwork, network_id="n1", password="pw")
        join = m.message_to_dataclass(msg, m.MESSAGE_TYPES)
        assert isinstance(join, m.JoinNetwork)
        assert join.network_id == "n1"
        assert join.password == "pw"

    def test_message_to_dataclass_unknown_type(self):
        msg = m.Message(type="SOME_UNKNOWN_TYPE", payload={})
        assert m.message_to_dataclass(msg, m.MESSAGE_TYPES) is None

    def test_message_to_dataclass_ignores_extra_keys(self):
        msg = m.Message(type=constants.MSG_HEARTBEAT, payload={"ts": 1.0, "junk": 2})
        hb = m.message_to_dataclass(msg, m.MESSAGE_TYPES)
        assert isinstance(hb, m.Heartbeat)
        assert hb.ts == 1.0
