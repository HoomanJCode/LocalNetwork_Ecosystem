"""Tests for server.registry — ClientRegistry."""

import time

from server.registry import ClientRegistry


def make_client(registry, client_id="client-0001", key="PEM-KEY", endpoint=("1.2.3.4", 5000)):
    record = registry.register(client_id, key)
    registry.update_endpoint(client_id, endpoint)
    registry.set_online(client_id)
    return record


class TestRegister:
    def test_register_get_found(self):
        registry = ClientRegistry()
        record = make_client(registry)
        assert registry.get("client-0001") is record
        assert record.online is True
        assert record.public_key_pem == "PEM-KEY"

    def test_register_twice_updates_key(self):
        registry = ClientRegistry()
        make_client(registry, key="KEY-1")
        make_client(registry, key="KEY-2")
        assert registry.get("client-0001").public_key_pem == "KEY-2"

    def test_unregister_marks_offline(self):
        registry = ClientRegistry()
        make_client(registry)
        registry.unregister("client-0001")
        record = registry.get("client-0001")
        assert record is not None
        assert record.online is False
        assert record.public_endpoint is None

    def test_forget_removes_record(self):
        registry = ClientRegistry()
        make_client(registry)
        registry.forget("client-0001")
        assert registry.get("client-0001") is None

    def test_online_count(self):
        registry = ClientRegistry()
        make_client(registry, "a")
        make_client(registry, "b")
        make_client(registry, "c")
        registry.unregister("b")
        assert registry.online_count == 2
        assert len(registry.get_online()) == 2


class TestHeartbeat:
    def test_heartbeat_updates_timestamp(self):
        registry = ClientRegistry()
        make_client(registry)
        record = registry.get("client-0001")
        old = record.last_heartbeat
        time.sleep(0.01)
        assert registry.heartbeat("client-0001") is True
        assert record.last_heartbeat > old

    def test_heartbeat_unknown_client(self):
        registry = ClientRegistry()
        assert registry.heartbeat("nobody") is False

    def test_heartbeat_revives_offline_client(self):
        registry = ClientRegistry()
        make_client(registry)
        registry.unregister("client-0001")
        assert registry.get("client-0001").online is False
        assert registry.heartbeat("client-0001") is True
        assert registry.get("client-0001").online is True


class TestPrune:
    def test_prune_removes_stale_clients(self):
        registry = ClientRegistry()
        make_client(registry, "stale-one")
        stale = registry.get("stale-one")
        stale.last_heartbeat = time.time() - 200  # artificially age it

        make_client(registry, "fresh-one")
        registry.get("fresh-one").last_heartbeat = time.time() - 5

        pruned = registry.prune_stale(timeout=60)
        assert pruned == ["stale-one"]
        assert registry.get("stale-one").online is False
        assert registry.get("fresh-one").online is True

    def test_prune_ignores_offline_clients(self):
        registry = ClientRegistry()
        make_client(registry, "a")
        registry.unregister("a")
        registry.get("a").last_heartbeat = time.time() - 1000
        assert registry.prune_stale(timeout=60) == []

    def test_prune_returns_empty_when_all_fresh(self):
        registry = ClientRegistry()
        make_client(registry, "a")
        assert registry.prune_stale(timeout=60) == []


class TestNetworks:
    def test_add_remove_network(self):
        registry = ClientRegistry()
        make_client(registry)
        registry.add_network("client-0001", "net-1")
        registry.add_network("client-0001", "net-2")
        assert registry.networks_for("client-0001") == {"net-1", "net-2"}
        registry.remove_network("client-0001", "net-1")
        assert registry.networks_for("client-0001") == {"net-2"}

    def test_members_in_networks(self):
        registry = ClientRegistry()
        make_client(registry, "a")
        make_client(registry, "b")
        make_client(registry, "c")
        registry.add_network("a", "net-1")
        registry.add_network("b", "net-1")
        registry.add_network("b", "net-2")
        registry.unregister("b")
        members = registry.members_in_networks(["net-1"])
        ids = {r.client_id for r in members}
        assert ids == {"a"}  # b is offline

    def test_banned_flag(self):
        registry = ClientRegistry()
        make_client(registry)
        registry.get("client-0001").banned = True
        assert registry.is_banned("client-0001")
        assert registry.set_online("client-0001") is False
