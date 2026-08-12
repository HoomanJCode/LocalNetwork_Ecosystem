"""Tests for the proxy status collector and /proxy-status endpoint (Phase 21)."""

from __future__ import annotations

import pytest

from proxy.config import ProxyConfig, UpstreamBlock, UpstreamServer
from proxy.health_check import HealthMonitor
from proxy.load_balancer import LeastConnBalancer
from proxy.status import (
    STATE_READING,
    STATE_WAITING,
    STATE_WRITING,
    StatusCollector,
    upstream_summary,
)


class TestStatusCollector:
    def test_initial_stats(self):
        sc = StatusCollector(start_time=1000.0)
        stats = sc.get_stats()
        assert stats["active_connections"] == 0
        assert stats["accepted_connections"] == 0
        assert stats["handled_connections"] == 0
        assert stats["total_requests"] == 0
        assert stats["reading"] == 0
        assert stats["writing"] == 0
        assert stats["waiting"] == 0

    def test_connection_lifecycle(self):
        sc = StatusCollector()
        sc.increment_accepted()
        sc.increment_accepted()
        assert sc.get_stats()["active_connections"] == 2
        assert sc.get_stats()["accepted_connections"] == 2
        sc.decrement_active()
        assert sc.get_stats()["active_connections"] == 1
        sc.increment_handled()
        sc.increment_handled()
        assert sc.get_stats()["handled_connections"] == 2
        assert sc.get_stats()["total_requests"] == 2

    def test_state_categories(self):
        sc = StatusCollector()
        sc.enter_state(STATE_READING)
        sc.enter_state(STATE_READING)
        sc.enter_state(STATE_WAITING)
        assert sc.get_stats()["reading"] == 2
        assert sc.get_stats()["waiting"] == 1
        sc.leave_state(STATE_READING)
        assert sc.get_stats()["reading"] == 1

    def test_transition_state(self):
        sc = StatusCollector()
        sc.transition_state(None, STATE_READING)
        assert sc.get_stats()["reading"] == 1
        sc.transition_state(STATE_READING, STATE_WAITING)
        assert sc.get_stats()["reading"] == 0
        assert sc.get_stats()["waiting"] == 1
        sc.transition_state(STATE_WAITING, STATE_WRITING)
        assert sc.get_stats()["waiting"] == 0
        assert sc.get_stats()["writing"] == 1
        sc.transition_state(STATE_WRITING, None)
        assert sc.get_stats()["writing"] == 0

    def test_leave_state_never_negative(self):
        sc = StatusCollector()
        sc.leave_state(STATE_READING)
        assert sc.get_stats()["reading"] == 0


class TestUpstreamSummary:
    def test_basic_summary(self):
        upstreams = [
            UpstreamBlock(
                name="app_backend",
                servers=[
                    UpstreamServer(host="10.0.0.1", port=3000),
                    UpstreamServer(host="10.0.0.2", port=3000),
                ],
            )
        ]
        summary = upstream_summary(upstreams)
        assert len(summary) == 1
        assert summary[0]["name"] == "app_backend"
        assert summary[0]["servers"][0]["host"] == "10.0.0.1:3000"
        assert summary[0]["servers"][0]["state"] == "up"
        assert summary[0]["servers"][0]["failures"] == 0

    def test_down_and_unavailable_states(self):
        upstreams = [
            UpstreamBlock(
                name="app",
                servers=[
                    UpstreamServer(host="10.0.0.1", port=80, down=True),
                    UpstreamServer(host="10.0.0.2", port=80),
                ],
            )
        ]
        health = HealthMonitor()
        for _ in range(3):
            health.record_failure("10.0.0.2", 80)
        summary = upstream_summary(upstreams, health_monitor=health)
        states = {s["host"]: s["state"] for s in summary[0]["servers"]}
        assert states["10.0.0.1:80"] == "down"
        assert states["10.0.0.2:80"] == "unavailable"

    def test_active_from_balancer(self):
        upstreams = [
            UpstreamBlock(
                name="app",
                servers=[UpstreamServer(host="10.0.0.1", port=80)],
            )
        ]
        balancer = LeastConnBalancer()
        server = upstreams[0].servers[0]
        balancer.increment(server)
        balancer.increment(server)
        summary = upstream_summary(upstreams, balancers={"app": balancer})
        assert summary[0]["servers"][0]["active"] == 2

    def test_empty(self):
        assert upstream_summary(None) == []
        assert upstream_summary([]) == []


class TestProxyStatusEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_stats(self):
        from aiohttp.test_utils import TestClient, TestServer

        from proxy.web.app import create_app

        sc = StatusCollector()
        sc.increment_accepted()
        sc.increment_handled()
        config = ProxyConfig()
        config.upstreams = [
            UpstreamBlock(
                name="app",
                servers=[UpstreamServer(host="10.0.0.1", port=80)],
            )
        ]
        app = create_app(status_collector=sc, config=config)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/proxy-status")
            assert resp.status == 200
            data = await resp.json()
        assert data["active_connections"] == 1
        assert data["total_requests"] == 1
        assert data["reading"] == 0
        assert data["writing"] == 0
        assert data["waiting"] == 0
        assert data["upstreams"][0]["name"] == "app"
        assert data["upstreams"][0]["servers"][0]["host"] == "10.0.0.1:80"
