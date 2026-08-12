"""Tests for the reverse proxy worker (Phase 21/22) — real HTTP proxying."""

from __future__ import annotations

import asyncio
import socket

import pytest

from proxy.config import (
    LocationBlock,
    ProxyConfig,
    UpstreamBlock,
    UpstreamServer,
)
from proxy.worker import WorkerProcess


def _make_config(upstream_port: int) -> ProxyConfig:
    """Build a config routing "/" to a single upstream on 127.0.0.1."""
    config = ProxyConfig()
    config.upstreams = [
        UpstreamBlock(
            name="app",
            algorithm="round_robin",
            servers=[UpstreamServer(host="127.0.0.1", port=upstream_port)],
        )
    ]
    config.locations = [LocationBlock(path="/", upstream="app")]
    return config


def _make_listen_socket() -> tuple[socket.socket, int]:
    """Create a bound, listening, non-blocking socket; return (sock, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    return sock, sock.getsockname()[1]


class TestWorkerRuntime:
    def test_worker_builds_runtime_components(self):
        config = ProxyConfig()
        config.upstreams = [
            UpstreamBlock(
                name="app",
                algorithm="least_conn",
                servers=[UpstreamServer(host="h", port=80)],
            )
        ]
        worker = WorkerProcess(0, config, {})
        assert worker.upstreams["app"].name == "app"
        assert "app" in worker.balancers
        assert worker.health_monitor is not None
        assert worker.status_collector is not None
        assert worker.compressor is not None  # gzip enabled by default
        assert worker.access_logger is None  # no access log configured


class TestWorkerProxying:
    @pytest.mark.asyncio
    async def test_proxies_http_request_to_upstream(self):
        # 1. Start a real upstream HTTP server.
        async def upstream_handle(reader, writer):
            await reader.read(65536)
            body = b"hello from upstream"
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            writer.write(header + body)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(upstream_handle, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]

        # 2. Build a worker serving on a loopback listen socket.
        listen_sock, port = _make_listen_socket()
        config = _make_config(upstream_port)
        worker = WorkerProcess(0, config, {port: listen_sock})
        worker_task = asyncio.create_task(worker._serve())

        try:
            # 3. Send a request through the proxy.
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=5
            )
            writer.write(
                b"GET / HTTP/1.1\r\n"
                b"Host: example.com\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()

            assert b"200 OK" in response
            assert b"hello from upstream" in response

            # 4. Status collector recorded the request.
            stats = worker.status_collector.get_stats()
            assert stats["accepted_connections"] == 1
            assert stats["total_requests"] == 1
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            listen_sock.close()
            upstream.close()
            await upstream.wait_closed()
