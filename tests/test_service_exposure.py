"""Tests for service exposure (Phase 14)."""

from __future__ import annotations

import asyncio

import pytest

from client.service_exposure import (
    ServiceExposureManager,
    ServiceRecord,
    decode_stream_frame,
)
from client.service_consumer import ServiceConsumer, MappedService


# ---------------------------------------------------------------------------
# ServiceExposureManager
# ---------------------------------------------------------------------------
class TestServiceExposureManager:
    @pytest.mark.asyncio
    async def test_expose_without_server(self):
        """Expose works even without a control channel (offline mode)."""
        mgr = ServiceExposureManager()
        sid = await mgr.expose("test-service", "tcp", "127.0.0.1", 9999)
        assert sid
        services = mgr.list_exposed()
        assert len(services) == 1
        assert services[0].name == "test-service"
        assert services[0].protocol == "tcp"
        assert services[0].local_port == 9999

    @pytest.mark.asyncio
    async def test_unexpose_removes(self):
        mgr = ServiceExposureManager()
        sid = await mgr.expose("test", "tcp", "127.0.0.1", 8080)
        assert len(mgr.list_exposed()) == 1
        await mgr.unexpose(sid)
        assert len(mgr.list_exposed()) == 0

    @pytest.mark.asyncio
    async def test_expose_multiple(self):
        mgr = ServiceExposureManager()
        await mgr.expose("s1", "tcp", "127.0.0.1", 8001)
        await mgr.expose("s2", "tcp", "127.0.0.1", 8002)
        await mgr.expose("s3", "udp", "127.0.0.1", 8003)
        assert len(mgr.list_exposed()) == 3

    @pytest.mark.asyncio
    async def test_shutdown_clears_all(self):
        mgr = ServiceExposureManager()
        await mgr.expose("s1", "tcp", "127.0.0.1", 8001)
        mgr.shutdown()
        assert len(mgr.list_exposed()) == 0


# ---------------------------------------------------------------------------
# ServiceConsumer
# ---------------------------------------------------------------------------
class TestServiceConsumer:
    @pytest.mark.asyncio
    async def test_map_auto_port(self):
        consumer = ServiceConsumer()
        port = await consumer.map_service(
            service_id="svc-123",
            provider_id="peer-a",
            service_name="test",
            protocol="tcp",
            strategy="auto",
        )
        assert port >= 50000
        assert len(consumer.list_mapped()) == 1

    @pytest.mark.asyncio
    async def test_map_manual_port(self):
        consumer = ServiceConsumer()
        port = await consumer.map_service(
            service_id="svc-456",
            provider_id="peer-b",
            service_name="manual-test",
            protocol="tcp",
            local_port=55555,
            strategy="manual",
        )
        assert port == 55555
        mapped = consumer.list_mapped()
        assert mapped[0].local_port == 55555

    @pytest.mark.asyncio
    async def test_unmap_closes_listener(self):
        consumer = ServiceConsumer()
        port = await consumer.map_service(
            service_id="svc-789",
            provider_id="peer-c",
            protocol="tcp",
            strategy="auto",
        )
        mapped = consumer.list_mapped()
        assert len(mapped) == 1
        await consumer.unmap_service(mapped[0].map_id)
        assert len(consumer.list_mapped()) == 0

    @pytest.mark.asyncio
    async def test_auto_port_skips_used(self):
        """Auto port assignment skips already-used ports."""
        consumer = ServiceConsumer()
        port1 = await consumer.map_service("a", "p1", protocol="tcp", strategy="auto")
        port2 = await consumer.map_service("b", "p2", protocol="tcp", strategy="auto")
        assert port1 != port2
        assert port1 >= 50000
        assert port2 >= 50000

    @pytest.mark.asyncio
    async def test_map_same_port_uses_remote_port(self, unused_port):
        """The 'same' strategy maps to the remote service's port when free."""
        free = unused_port()
        consumer = ServiceConsumer()
        port = await consumer.map_service(
            "svc-same",
            "peer-d",
            protocol="tcp",
            strategy="same",
            remote_port=free,
        )
        assert port == free
        mapped = consumer.list_mapped()
        assert mapped[0].local_port == free
        assert mapped[0].remote_port == free

    @pytest.mark.asyncio
    async def test_same_port_falls_back_to_auto_when_taken(self):
        """The 'same' strategy falls back to auto when the port is occupied."""
        consumer = ServiceConsumer()
        await consumer.map_service(
            "a", "p1", protocol="tcp", strategy="manual", local_port=30000
        )
        port = await consumer.map_service(
            "b", "p2", protocol="tcp", strategy="same", remote_port=30000
        )
        assert port != 30000
        assert port >= 50000

    @pytest.mark.asyncio
    async def test_same_port_without_remote_port_falls_back_to_auto(self):
        """The 'same' strategy needs a remote port; otherwise it uses auto."""
        consumer = ServiceConsumer()
        port = await consumer.map_service("x", "p3", protocol="tcp", strategy="same")
        assert port >= 50000

    def test_invalid_strategy_raises(self):
        consumer = ServiceConsumer()
        with pytest.raises(ValueError):
            asyncio.run(consumer.map_service("x", "y", strategy="invalid"))

    @pytest.mark.asyncio
    async def test_shutdown_unmaps_all(self):
        consumer = ServiceConsumer()
        await consumer.map_service("a", "p1", protocol="tcp", strategy="auto")
        await consumer.map_service("b", "p2", protocol="tcp", strategy="auto")
        assert len(consumer.list_mapped()) == 2
        await consumer.shutdown()
        assert len(consumer.list_mapped()) == 0


# ---------------------------------------------------------------------------
# Stream frame encoding
# ---------------------------------------------------------------------------
def test_decode_stream_frame():
    sid = "abc123-def456-ghi789".ljust(36)
    stid = "stream-001".ljust(36)
    data = b"hello world"
    frame = sid.encode() + stid.encode() + data
    svc_id, strm_id, payload = decode_stream_frame(frame)
    assert svc_id.strip() == "abc123-def456-ghi789"
    assert strm_id.strip() == "stream-001"
    assert payload == data


def test_decode_stream_frame_too_short():
    svc_id, strm_id, data = decode_stream_frame(b"short")
    assert svc_id == ""
    assert data == b"short"
