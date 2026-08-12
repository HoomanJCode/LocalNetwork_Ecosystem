"""Tests for server.relay — RelayForwarder."""

import asyncio

import pytest

from common import constants
from common.messages import (
    ErrorMessage,
    deserialize,
    make_message,
    serialize,
)
from server.main import MediationServer
from server.relay import RelayForwarder
from server.config import ServerConfig


class DummyServer:
    """Minimal stand-in for MediationServer used by RelayForwarder tests."""

    def __init__(self):
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        self.registry = ClientRegistry()
        self.networks = NetworkManager(self.registry)
        self._writers = {}
        self.sent = {}  # client_id -> list[bytes]

    async def _send(self, writer, msg):
        key = writer
        self.sent.setdefault(key, []).append(serialize(msg))

    async def _send_error(self, writer, code, message):
        await self._send(
            writer, make_message(ErrorMessage, code=code, message=message)
        )


@pytest.fixture
def relay_and_server():
    server = DummyServer()
    relay = RelayForwarder(server)
    return relay, server


def setup_online_pair(server, a_id="client-a", b_id="client-b"):
    server.registry.register(a_id, "PEM-A")
    server.registry.set_online(a_id)
    server.registry.update_endpoint(a_id, ("127.0.0.1", 1111))
    server.registry.register(b_id, "PEM-B")
    server.registry.set_online(b_id)
    server.registry.update_endpoint(b_id, ("127.0.0.1", 2222))
    network = server.networks.create("net", "pw", a_id)
    assert server.networks.join(network.network_id, b_id, "pw")
    return network


class TestPaths:
    def test_register_path_creates_queues(self, relay_and_server):
        relay, _ = relay_and_server
        assert relay.register_relay_path("a", "b") is True
        assert relay.has_path("a", "b")
        assert relay.has_path("b", "a")  # bidirectional
        assert relay.register_relay_path("a", "b") is False  # already exists

    def test_relay_frame_requires_path(self, relay_and_server):
        relay, _ = relay_and_server
        assert relay.relay_frame("a", "b", b"\x01\x02") is False

    def test_relay_frame_roundtrip(self, relay_and_server):
        relay, _ = relay_and_server
        relay.register_relay_path("a", "b")
        frame = b"\x01\x01\x00\x04\x00\x00\x00\x01datadata"
        assert relay.relay_frame("a", "b", frame) is True
        pending = relay.pending_frames("b")
        assert len(pending) == 1
        src, b64 = pending[0]
        assert src == "a"
        import base64
        assert base64.b64decode(b64) == frame

    def test_ordering_preserved(self, relay_and_server):
        relay, _ = relay_and_server
        relay.register_relay_path("a", "b")
        for i in range(5):
            relay.relay_frame("a", "b", bytes([i]))
        frames = [base64_decode(b) for _, b in relay.pending_frames("b")]
        assert frames == [bytes([i]) for i in range(5)]


def base64_decode(b64):
    import base64
    return base64.b64decode(b64)


class TestHandleRequest:
    @pytest.mark.asyncio
    async def test_grant_flow(self, relay_and_server):
        relay, server = relay_and_server
        network = setup_online_pair(server)
        relay.server._writers["client-b"] = "writer-b"

        await relay.handle_relay_request("client-a", "client-b", "writer-a")

        # A receives RELAY_GRANTED
        sent_a = server.sent.get("writer-a", [])
        assert sent_a, "A should receive a RELAY_GRANTED"
        msg = deserialize(sent_a[-1])
        assert msg.type == constants.MSG_RELAY_GRANTED
        assert msg.payload["peer_id"] == "client-b"

        # B also receives RELAY_GRANTED
        sent_b = server.sent.get("writer-b", [])
        assert sent_b
        msg_b = deserialize(sent_b[-1])
        assert msg_b.type == constants.MSG_RELAY_GRANTED
        assert msg_b.payload["peer_id"] == "client-a"

        # Path is now usable in both directions
        assert relay.has_path("client-a", "client-b")
        assert relay.has_path("client-b", "client-a")

    @pytest.mark.asyncio
    async def test_rejects_unknown_peer(self, relay_and_server):
        relay, server = relay_and_server
        setup_online_pair(server)
        await relay.handle_relay_request("client-a", "ghost", "writer-a")
        msg = deserialize(server.sent["writer-a"][-1])
        assert msg.type == constants.MSG_ERROR
        assert msg.payload["code"] == "RELAY_FAILED"

    @pytest.mark.asyncio
    async def test_rejects_offline_peer(self, relay_and_server):
        relay, server = relay_and_server
        setup_online_pair(server)
        server.registry.unregister("client-b")
        await relay.handle_relay_request("client-a", "client-b", "writer-a")
        msg = deserialize(server.sent["writer-a"][-1])
        assert msg.payload["code"] == "RELAY_FAILED"

    @pytest.mark.asyncio
    async def test_rejects_no_shared_network(self, relay_and_server):
        relay, server = relay_and_server
        server.registry.register("x", "PEM-X")
        server.registry.set_online("x")
        server.registry.register("y", "PEM-Y")
        server.registry.set_online("y")
        await relay.handle_relay_request("x", "y", "w-x")
        msg = deserialize(server.sent["w-x"][-1])
        assert msg.payload["code"] == "RELAY_FAILED"


class TestDelivery:
    @pytest.mark.asyncio
    async def test_deliver_relayed_sends_relay_frames(self, relay_and_server):
        relay, server = relay_and_server
        relay.register_relay_path("client-a", "client-b")
        frame = b"\x01\x01\x00\x00\x00\x00\x00\x09"
        relay.relay_frame("client-a", "client-b", frame)

        await relay.deliver_relayed("client-b", "writer-b")
        msgs = server.sent["writer-b"]
        assert len(msgs) == 1
        msg = deserialize(msgs[0])
        assert msg.type == constants.MSG_RELAY_FRAME
        assert msg.payload["src_id"] == "client-a"
        import base64
        assert base64.b64decode(msg.payload["frame_b64"]) == frame
        # Queue is drained
        assert relay.pending_frames("client-b") == []

    def test_drop_client_removes_paths(self, relay_and_server):
        relay, _ = relay_and_server
        relay.register_relay_path("a", "b")
        relay.relay_frame("a", "b", b"\x00")
        relay.drop_client("a")
        assert not relay.has_path("a", "b")
        assert relay.pending_frames("b") == []  # queue removed

    def test_stats(self, relay_and_server):
        relay, _ = relay_and_server
        relay.register_relay_path("a", "b")
        relay.relay_frame("a", "b", b"\x00" * 100)
        stats = relay.stats()
        assert stats["paths"] == 1
        assert stats["bytes_relayed"] == 100


class TestIntegrationWithMediationServer:
    @pytest.mark.asyncio
    async def test_server_owns_relay(self):
        config = ServerConfig(host="127.0.0.1", port=0, web_port=0)
        server = MediationServer(config)
        assert server.relay is not None
        assert isinstance(server.relay, RelayForwarder)
