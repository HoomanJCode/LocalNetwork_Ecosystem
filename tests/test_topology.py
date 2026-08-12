"""Tests for network topology managers."""

from __future__ import annotations

import pytest

from client.topology import (
    GatewayTopology,
    HubAndSpokeTopology,
    MeshTopology,
    create_topology_manager,
)
from common.constants import (
    TOPOLOGY_GATEWAY,
    TOPOLOGY_HUB_AND_SPOKE,
    TOPOLOGY_MESH,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_create_mesh():
    topo = create_topology_manager(TOPOLOGY_MESH)
    assert isinstance(topo, MeshTopology)
    assert not topo.is_hub
    assert not topo.is_gateway


def test_create_hub_and_spoke():
    topo = create_topology_manager(
        TOPOLOGY_HUB_AND_SPOKE, hub_client_id="hub-1", is_hub=True
    )
    assert isinstance(topo, HubAndSpokeTopology)
    assert topo.is_hub


def test_create_gateway():
    topo = create_topology_manager(
        TOPOLOGY_GATEWAY, gateway_client_id="gw-1", is_gateway=True
    )
    assert isinstance(topo, GatewayTopology)
    assert topo.is_gateway


def test_create_unknown_raises():
    with pytest.raises(ValueError):
        create_topology_manager("invalid")


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------
class TestMeshTopology:
    @pytest.mark.asyncio
    async def test_all_peers_connected(self):
        topo = MeshTopology()
        assert await topo.on_peer_online("a", "25.0.0.1", "n1")
        assert await topo.on_peer_online("b", "25.0.0.2", "n1")
        assert await topo.on_peer_online("c", "25.0.0.3", "n1")
        assert topo.get_peers_to_connect() == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_peer_offline_removes(self):
        topo = MeshTopology()
        await topo.on_peer_online("a", "25.0.0.1", "n1")
        assert await topo.on_peer_offline("a", "n1")
        assert topo.get_peers_to_connect() == set()

    @pytest.mark.asyncio
    async def test_always_returns_true(self):
        """Mesh always wants tunnels to/from all peers."""
        topo = MeshTopology()
        assert await topo.on_peer_online("any", "25.0.0.99", "n1")
        assert await topo.on_peer_offline("any", "n1")


# ---------------------------------------------------------------------------
# Hub-and-Spoke
# ---------------------------------------------------------------------------
class TestHubAndSpoke:
    @pytest.mark.asyncio
    async def test_hub_accepts_all_spokes(self):
        topo = HubAndSpokeTopology(hub_client_id="hub-1", is_hub=True)
        assert await topo.on_peer_online("spoke-a", "25.0.0.2", "n1")
        assert await topo.on_peer_online("spoke-b", "25.0.0.3", "n1")
        assert topo.get_peers_to_connect() == {"spoke-a", "spoke-b"}
        assert topo.get_spoke_ip("spoke-a") == "25.0.0.2"

    @pytest.mark.asyncio
    async def test_spoke_only_connects_to_hub(self):
        topo = HubAndSpokeTopology(hub_client_id="hub-1", is_hub=False)
        assert await topo.on_peer_online("hub-1", "25.0.0.1", "n1")
        assert not await topo.on_peer_online("spoke-b", "25.0.0.3", "n1")
        assert topo.get_peers_to_connect() == {"hub-1"}

    @pytest.mark.asyncio
    async def test_spoke_ignores_other_spokes(self):
        """A spoke should not create tunnels to other spokes."""
        topo = HubAndSpokeTopology(hub_client_id="hub-1", is_hub=False)
        # Hub online → connect
        assert await topo.on_peer_online("hub-1", "25.0.0.1", "n1")
        # Another spoke online → ignore
        assert not await topo.on_peer_online("spoke-other", "25.0.0.5", "n1")
        assert topo.get_peers_to_connect() == {"hub-1"}


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class TestGatewayTopology:
    @pytest.mark.asyncio
    async def test_gateway_connects_to_all_peers(self):
        topo = GatewayTopology(gateway_client_id="gw-1", is_gateway=True)
        assert await topo.on_peer_online("remote-a", "25.0.0.2", "n1")
        assert await topo.on_peer_online("remote-b", "25.0.0.3", "n1")
        assert topo.get_peers_to_connect() == {"remote-a", "remote-b"}

    @pytest.mark.asyncio
    async def test_remote_only_connects_to_gateway(self):
        topo = GatewayTopology(gateway_client_id="gw-1", is_gateway=False)
        assert await topo.on_peer_online("gw-1", "25.0.0.1", "n1")
        assert not await topo.on_peer_online("other-remote", "25.0.0.5", "n1")
        assert topo.get_peers_to_connect() == {"gw-1"}

    @pytest.mark.asyncio
    async def test_arp_table(self):
        topo = GatewayTopology(gateway_client_id="gw-1", is_gateway=True)
        topo.set_arp_entry("25.0.0.10", "aa:bb:cc:dd:ee:ff")
        assert topo.get_arp_entry("25.0.0.10") == "aa:bb:cc:dd:ee:ff"
        assert topo.get_arp_entry("25.0.0.99") is None

    @pytest.mark.asyncio
    async def test_peer_ip_lookup(self):
        topo = GatewayTopology(gateway_client_id="gw-1", is_gateway=True)
        await topo.on_peer_online("remote-a", "25.0.0.2", "n1")
        assert topo.get_peer_ip("remote-a") == "25.0.0.2"
        assert topo.get_peer_ip("nonexistent") is None
