"""Tests for server.network_manager — NetworkManager."""

import pytest

from server.network_manager import NetworkManager
from server.registry import ClientRegistry


@pytest.fixture
def manager():
    registry = ClientRegistry()
    return NetworkManager(registry), registry


def add_client(registry, client_id, ip="127.0.0.1", port=10000):
    registry.register(client_id, "PEM-" + client_id)
    registry.update_endpoint(client_id, (ip, port))
    registry.set_online(client_id)
    return registry.get(client_id)


class TestCreate:
    def test_create_network_adds_owner(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        record = nm.create("testnet", "secret", "owner-1")
        assert record.owner_id == "owner-1"
        assert record.network_id
        assert "owner-1" in record.members
        assert registry.networks_for("owner-1") == {record.network_id}

    def test_create_sets_hub_and_gateway(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        hub = nm.create("hubnet", "pw", "owner-1", topology="hub_and_spoke")
        assert hub.hub_id == "owner-1"
        gw = nm.create("gwnet", "pw", "owner-1", topology="gateway")
        assert gw.gateway_id == "owner-1"

    def test_create_rejects_bad_topology(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        with pytest.raises(ValueError):
            nm.create("x", "pw", "owner-1", topology="star")


class TestJoinLeave:
    def test_join_with_correct_password(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        add_client(registry, "member-2")
        record = nm.create("testnet", "secret", "owner-1")
        assert nm.join(record.network_id, "member-2", "secret") is True
        assert "member-2" in record.members
        assert record.network_id in registry.networks_for("member-2")

    def test_join_with_wrong_password(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        add_client(registry, "member-2")
        record = nm.create("testnet", "secret", "owner-1")
        assert nm.join(record.network_id, "member-2", "wrong") is False
        assert "member-2" not in record.members

    def test_join_missing_network(self, manager):
        nm, registry = manager
        add_client(registry, "member-2")
        assert nm.join("missing", "member-2", "pw") is False

    def test_leave_network(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        add_client(registry, "member-2")
        record = nm.create("testnet", "secret", "owner-1")
        nm.join(record.network_id, "member-2", "secret")
        assert nm.leave(record.network_id, "member-2") is True
        assert "member-2" not in record.members
        assert registry.networks_for("member-2") == set()

    def test_leave_non_member_returns_false(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        record = nm.create("testnet", "secret", "owner-1")
        assert nm.leave(record.network_id, "stranger") is False


class TestPeers:
    def test_get_peers_excludes_self(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        add_client(registry, "c")
        record = nm.create("net", "pw", "a")
        nm.join(record.network_id, "b", "pw")
        nm.join(record.network_id, "c", "pw")
        peers = nm.get_peers(record.network_id, "a")
        assert {p.client_id for p in peers} == {"b", "c"}

    def test_get_peers_excludes_offline(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        record = nm.create("net", "pw", "a")
        nm.join(record.network_id, "b", "pw")
        registry.unregister("b")
        assert nm.get_peers(record.network_id, "a") == []

    def test_list_for_client(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        r1 = nm.create("net-1", "pw", "a")
        r2 = nm.create("net-2", "pw", "a")
        nm.join(r2.network_id, "b", "pw")
        assert {r.network_id for r in nm.list_for_client("a")} == {r1.network_id, r2.network_id}
        assert {r.network_id for r in nm.list_for_client("b")} == {r2.network_id}


class TestDelete:
    def test_delete_by_owner(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        add_client(registry, "member-2")
        record = nm.create("net", "pw", "owner-1")
        nm.join(record.network_id, "member-2", "pw")
        assert nm.delete(record.network_id, "owner-1") is True
        assert nm.get(record.network_id) is None
        assert registry.networks_for("member-2") == set()

    def test_delete_by_non_owner_rejected(self, manager):
        nm, registry = manager
        add_client(registry, "owner-1")
        add_client(registry, "member-2")
        record = nm.create("net", "pw", "owner-1")
        nm.join(record.network_id, "member-2", "pw")
        assert nm.delete(record.network_id, "member-2") is False
        assert nm.get(record.network_id) is not None

    def test_purge_client(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        add_client(registry, "c")
        record = nm.create("net", "pw", "a")
        nm.join(record.network_id, "b", "pw")
        nm.join(record.network_id, "c", "pw")
        removed = nm.purge_client("b")
        assert removed == [record.network_id]
        assert "b" not in record.members
        assert registry.networks_for("b") == set()

    def test_purge_client_deletes_empty_network(self, manager):
        nm, registry = manager
        add_client(registry, "only")
        record = nm.create("net", "pw", "only")
        nm.purge_client("only")
        assert nm.get(record.network_id) is None


class TestSharedEndpoints:
    def test_mesh_shared_endpoints(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        record = nm.create("net", "pw", "a")
        nm.join(record.network_id, "b", "pw")
        endpoints = nm.shared_endpoints_for(record.network_id, "a", "b")
        assert endpoints == [("127.0.0.1", 10000)]

    def test_hub_spoke_hides_spoke_endpoints(self, manager):
        nm, registry = manager
        add_client(registry, "hub")
        add_client(registry, "spoke-1")
        add_client(registry, "spoke-2")
        record = nm.create("net", "pw", "hub", topology="hub_and_spoke")
        nm.join(record.network_id, "spoke-1", "pw")
        nm.join(record.network_id, "spoke-2", "pw")
        # Spoke-1 may learn the hub's endpoint
        assert nm.shared_endpoints_for(record.network_id, "spoke-1", "hub") is not None
        # But not spoke-2's endpoint
        assert nm.shared_endpoints_for(record.network_id, "spoke-1", "spoke-2") is None

    def test_no_endpoints_for_offline_peer(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        record = nm.create("net", "pw", "a")
        nm.join(record.network_id, "b", "pw")
        registry.unregister("b")
        assert nm.shared_endpoints_for(record.network_id, "a", "b") is None

    def test_non_members_get_nothing(self, manager):
        nm, registry = manager
        add_client(registry, "a")
        add_client(registry, "b")
        record = nm.create("net", "pw", "a")
        assert nm.shared_endpoints_for(record.network_id, "b", "a") is None
