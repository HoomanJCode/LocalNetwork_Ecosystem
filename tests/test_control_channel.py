"""Tests for client.control_channel — full flows against a live test server."""

import asyncio
import uuid

import pytest

from client import identity
from client.control_channel import ControlChannel
from common import constants
from server.config import ServerConfig
from server.main import MediationServer


def make_identity(identity_dir):
    """Generate an identity, persist it, return (private, public, client_id)."""
    private_key, public_key = identity.generate_identity()
    identity.save_identity(private_key, public_key, path=identity_dir)
    from cryptography.hazmat.primitives import serialization

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    client_id = identity.client_id_for_public_key(public_key)
    return private_key, public_pem, client_id


@pytest.fixture
async def server_port(unused_port):
    """Start a real MediationServer on an ephemeral port; yield the port."""
    port = unused_port()
    config = ServerConfig(
        host="127.0.0.1", port=port, web_port=0, heartbeat_timeout=600
    )
    server = MediationServer(config)
    task = asyncio.create_task(server.start())
    # Wait until it accepts connections
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            break
        except OSError:
            await asyncio.sleep(0.05)
    yield port, server
    await server.shutdown()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def connect_client(port, identity_dir, name):
    private_key, public_pem, client_id = make_identity(identity_dir / name)
    channel = ControlChannel(host="127.0.0.1", port=port, client_id=client_id)
    await channel.connect()
    await channel.authenticate(private_key, public_pem)
    return channel, client_id


class TestRegistrationFlow:
    @pytest.mark.asyncio
    async def test_full_register_auth_flow(self, server_port, tmp_path):
        port, server = server_port
        channel, client_id = await connect_client(port, tmp_path, "a")
        assert channel.authenticated is True
        assert server.registry.get(client_id) is not None
        assert server.registry.get(client_id).online is True
        await channel.close()

    @pytest.mark.asyncio
    async def test_create_network_returns_id(self, server_port, tmp_path):
        port, _ = server_port
        channel, _ = await connect_client(port, tmp_path, "a")
        network_id = await channel.create_network("testnet", "secret", "mesh")
        assert network_id
        networks = await channel.list_networks()
        assert any(n["network_id"] == network_id for n in networks)
        await channel.close()

    @pytest.mark.asyncio
    async def test_join_with_wrong_password_fails(self, server_port, tmp_path):
        port, _ = server_port
        channel_a, _ = await connect_client(port, tmp_path, "a")
        network_id = await channel_a.create_network("testnet", "secret")

        channel_b, _ = await connect_client(port, tmp_path, "b")
        with pytest.raises(Exception):
            await channel_b.join_network(network_id, "wrong")
        await channel_a.close()
        await channel_b.close()


class TestPeerFlow:
    @pytest.mark.asyncio
    async def test_peer_online_notification_and_endpoints(
        self, server_port, tmp_path
    ):
        port, server = server_port
        channel_a, client_a = await connect_client(port, tmp_path, "a")
        network_id = await channel_a.create_network("testnet", "secret")

        # Start consuming events before B joins
        event_task = asyncio.create_task(channel_a.listen_events().__anext__())

        channel_b, client_b = await connect_client(port, tmp_path, "b")
        await channel_b.join_network(network_id, "secret")

        # A should receive PEER_ONLINE for B
        event = await asyncio.wait_for(event_task, timeout=5)
        assert event.type == constants.MSG_PEER_ONLINE
        assert event.payload["peer_id"] == client_b
        assert event.payload["network_id"] == network_id

        # A can request B's endpoints (both on localhost)
        endpoints = await channel_a.request_peer_endpoints(client_b)
        assert endpoints, "expected at least one endpoint for peer B"
        host, port_num = endpoints[0]
        assert host == "127.0.0.1"
        assert isinstance(port_num, int)

        await channel_a.close()
        await channel_b.close()

    @pytest.mark.asyncio
    async def test_leave_network(self, server_port, tmp_path):
        port, _ = server_port
        channel_a, _ = await connect_client(port, tmp_path, "a")
        network_id = await channel_a.create_network("testnet", "secret")
        await channel_a.leave_network(network_id)
        networks = await channel_a.list_networks()
        assert all(n["network_id"] != network_id for n in networks)
        await channel_a.close()


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_keeps_connection_alive(self, server_port, tmp_path):
        port, server = server_port
        channel, client_id = await connect_client(port, tmp_path, "a")
        await channel.send_heartbeat()
        record = server.registry.get(client_id)
        assert record.online is True
        # Bump heartbeat through the request pipeline (uses background reader)
        await asyncio.sleep(0.05)
        await channel.send_heartbeat()
        await channel.close()


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnects_after_server_restart(self, server_port, tmp_path):
        port, server = server_port
        private_key, public_pem, client_id = make_identity(tmp_path / "a")
        channel = ControlChannel(host="127.0.0.1", port=port, client_id=client_id)
        await channel.connect()
        await channel.authenticate(private_key, public_pem)
        assert channel.connected

        # Kill the server; the channel should notice and schedule reconnect
        await server.shutdown()
        await asyncio.sleep(0.5)
        assert channel.connected is False

        # Restart the server on the same port
        config = ServerConfig(
            host="127.0.0.1", port=port, web_port=0, heartbeat_timeout=600
        )
        server2 = MediationServer(config)
        task = asyncio.create_task(server2.start())
        for _ in range(100):
            if channel.connected:
                break
            await asyncio.sleep(0.1)

        # Give the reconnect loop a few tries
        for _ in range(50):
            if channel.connected:
                break
            await asyncio.sleep(0.2)

        assert channel.connected is True, "channel should reconnect"
        assert channel.authenticated is True
        await channel.close()
        await server2.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
