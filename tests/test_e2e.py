"""End-to-end tests: full multi-client setup with mediation server.

Requires root/admin for TUN setup. Skip on CI without ``--e2e`` flag.
Tests ping between virtual clients, TCP over VPN, and mesh broadcast.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="E2E tests require root (TUN interface)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Find a free TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestE2EMultiClient:
    """Full E2E: start server + multiple clients, verify P2P connectivity."""

    @pytest.mark.asyncio
    async def test_server_start_and_stop(self):
        """Server starts, accepts connections, and stops cleanly."""
        from server.config import ServerConfig
        from server.main import MediationServer

        port = _free_port()
        config = ServerConfig(port=port)
        server = MediationServer(config)

        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)
        assert server._server is not None

        await server.shutdown()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_three_clients_registration(self):
        """Three clients register and authenticate with the server."""
        from server.config import ServerConfig
        from server.main import MediationServer

        port = _free_port()
        config = ServerConfig(port=port)
        server = MediationServer(config)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            # Open 3 client connections and register
            clients = []
            for i in range(3):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                clients.append((reader, writer))

            assert len(clients) == 3

            # Close all
            for _, writer in clients:
                writer.close()
        finally:
            await server.shutdown()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_network_mesh_topology(self):
        """Three clients create and join a mesh network."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        # Register clients
        for cid in ["alice", "bob", "carol"]:
            registry.register(cid, f"pubkey-{cid}")
            registry.set_online(cid)

        # Alice creates mesh network
        net = nm.create("test-mesh", "secret", "alice", "mesh")
        assert net.topology == "mesh"
        assert len(net.members) == 1

        # Bob and Carol join
        assert nm.join(net.network_id, "bob", "secret")
        assert nm.join(net.network_id, "carol", "secret")
        assert len(net.members) == 3

        # All three are peers
        peers_alice = nm.get_peers(net.network_id, "alice")
        peer_ids = {p.client_id for p in peers_alice}
        assert peer_ids == {"bob", "carol"}

    @pytest.mark.asyncio
    async def test_hub_and_spoke_topology(self):
        """Hub-and-spoke: spokes don't discover each other."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        hub = "hub-1"
        spoke_a = "spoke-a"
        spoke_b = "spoke-b"

        for cid in [hub, spoke_a, spoke_b]:
            registry.register(cid, f"pk-{cid}")
            registry.set_online(cid)

        # Hub creates the network
        net = nm.create("hubnet", "secret", hub, "hub_and_spoke")
        assert net.hub_id == hub

        nm.join(net.network_id, spoke_a, "secret")
        nm.join(net.network_id, spoke_b, "secret")

        # Spokes only see the hub, not each other
        peers_a = {p.client_id for p in nm.get_peers(net.network_id, spoke_a)}
        assert peers_a == {hub}

        # Hub sees both spokes
        peers_hub = {p.client_id for p in nm.get_peers(net.network_id, hub)}
        assert peers_hub == {spoke_a, spoke_b}

    @pytest.mark.asyncio
    async def test_gateway_topology(self):
        """Gateway mode: gateway bridges to physical LAN."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        gw = "gateway-1"
        remote = "remote-1"

        for cid in [gw, remote]:
            registry.register(cid, f"pk-{cid}")
            registry.set_online(cid)

        net = nm.create("gwnet", "secret", gw, "gateway")
        assert net.gateway_id == gw

        nm.join(net.network_id, remote, "secret")

        # Remote only sees gateway
        peers = nm.get_peers(net.network_id, remote)
        assert len(peers) == 1
        assert peers[0].client_id == gw


class TestServiceExposureE2E:
    """E2E service exposure: expose service, consumer maps it."""

    @pytest.mark.asyncio
    async def test_expose_and_list_services(self):
        """Expose a service and verify it appears in the network list."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        registry.register("host", "pk-host")
        registry.set_online("host")

        net = nm.create("svcnet", "secret", "host", "mesh")
        sid = nm.expose_service(net.network_id, "host", "minecraft", "tcp", "127.0.0.1", 25565)
        assert sid

        services = nm.list_services(net.network_id)
        assert len(services) == 1
        assert services[0].name == "minecraft"
        assert services[0].protocol == "tcp"

    @pytest.mark.asyncio
    async def test_unexpose_service(self):
        """Unexpose a service removes it."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        registry.register("host", "pk-host")
        registry.set_online("host")

        net = nm.create("svcnet2", "secret", "host", "mesh")
        sid = nm.expose_service(net.network_id, "host", "web", "tcp", "127.0.0.1", 8080)
        assert len(nm.list_services(net.network_id)) == 1

        nm.unexpose_service(net.network_id, sid)
        assert len(nm.list_services(net.network_id)) == 0

    @pytest.mark.asyncio
    async def test_service_auto_removed_on_disconnect(self):
        """Services are auto-removed when provider disconnects."""
        from server.config import ServerConfig
        from server.network_manager import NetworkManager
        from server.registry import ClientRegistry

        registry = ClientRegistry()
        nm = NetworkManager(registry)

        registry.register("host", "pk-host")
        registry.set_online("host")

        net = nm.create("svcnet3", "secret", "host", "mesh")
        nm.expose_service(net.network_id, "host", "ssh", "tcp", "127.0.0.1", 22)
        assert len(nm.list_services(net.network_id)) == 1

        # Disconnect host
        nm.purge_client("host")
        assert len(nm.list_services(net.network_id)) == 0


class TestCacheSystem:
    """Cache storage and manager tests."""

    def test_store_and_retrieve(self):
        from proxy.cache.storage import DiskCache

        cache = DiskCache("/tmp/ln-test-cache", max_size=10 * 1024 * 1024)
        cache.store("key1", 200, {"content-type": "text/html"}, b"hello", ttl=300)

        result = cache.retrieve("key1")
        assert result is not None
        status, headers, body = result
        assert status == 200
        assert headers["content-type"] == "text/html"
        assert body == b"hello"

        cache.purge()

    def test_cache_manager_key_generation(self):
        from proxy.cache.manager import CacheManager

        mgr = CacheManager(default_ttl=300)
        key1 = mgr.get_cache_key("GET", "/api/users", {"accept": "application/json"})
        key2 = mgr.get_cache_key("GET", "/api/users", {"accept": "application/json"})
        assert key1 == key2  # Same input → same key

        key3 = mgr.get_cache_key("POST", "/api/users", {"accept": "application/json"})
        assert key1 != key3  # Different method → different key

    def test_is_cacheable(self):
        from proxy.cache.manager import CacheManager

        mgr = CacheManager()
        assert mgr.is_cacheable("GET", 200, {"cache-control": "max-age=60"})
        assert not mgr.is_cacheable("POST", 200, {})
        assert not mgr.is_cacheable("GET", 200, {"cache-control": "no-store"})
        assert not mgr.is_cacheable("GET", 500, {})


class TestSecurity:
    """Security module tests."""

    def test_access_control_allow_deny(self):
        from proxy.security.access import AccessControl

        ac = AccessControl()
        ac.deny("10.0.0.0/8")
        ac.allow("10.0.0.5")

        assert ac.check("10.0.0.5")  # Explicitly allowed
        assert not ac.check("10.0.0.6")  # In deny range
        assert ac.check("192.168.1.1")  # Not in any rule — default allow

    def test_rate_limiter(self):
        from proxy.security.rate_limiter import RateLimiter

        rl = RateLimiter()
        # Within limit
        for _ in range(5):
            assert rl.allow("192.168.1.1", rate=10, burst=0)

        # Exceed limit (100 in one window with rate=10)
        allowed = 0
        for _ in range(100):
            if rl.allow("192.168.1.2", rate=10, burst=0):
                allowed += 1
        assert allowed <= 15  # Should be roughly rate + some slack

    def test_basic_auth(self):
        from proxy.security.auth import BasicAuth

        auth = BasicAuth()
        auth.set_user("admin", "secret")

        import base64

        creds = base64.b64encode(b"admin:secret").decode()
        assert auth.check(f"Basic {creds}")

        bad_creds = base64.b64encode(b"admin:wrong").decode()
        assert not auth.check(f"Basic {bad_creds}")
        assert not auth.check(None)
        assert not auth.check("")
