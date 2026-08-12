"""Tests for proxy configuration loader."""

from __future__ import annotations

import os
import tempfile

import pytest

from proxy.config import (
    ProxyConfig,
    UpstreamServer,
    UpstreamBlock,
    LocationBlock,
    SSLBlock,
    load_config,
)


class TestLoadConfig:
    def test_minimal_config(self):
        """Load a minimal valid config; defaults should be filled."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("http: [8080]\n")
            f.write("upstreams:\n")
            f.write("  - name: backend\n")
            f.write("    servers:\n")
            f.write("      - localhost:3000\n")
            f.write("locations:\n")
            f.write("  - path: /\n")
            f.write("    upstream: backend\n")
            path = f.name

        try:
            config = load_config(path)
            assert config.http == [8080]
            assert config.workers == 0  # default: auto
            assert len(config.upstreams) == 1
            assert config.upstreams[0].name == "backend"
            assert config.upstreams[0].servers[0].host == "localhost"
            assert config.upstreams[0].servers[0].port == 3000
            assert len(config.locations) == 1
        finally:
            os.unlink(path)

    def test_full_config(self):
        """Load a full config with all fields."""
        yaml_text = """\
workers: 4
worker_connections: 2048
http: [80, 8080]
https: [443]
upstreams:
  - name: app
    algorithm: least_conn
    max_failures: 5
    fail_timeout: 30
    servers:
      - host: 10.0.0.1
        port: 3000
        weight: 3
      - host: 10.0.0.2
        port: 3000
        weight: 1
        backup: true
      - host: 10.0.0.3
        port: 3000
        down: true
locations:
  - path: /api
    upstream: app
    rate_limit: 100
    compress: true
  - path: /static
    root: /var/www/static
    cache: true
    cache_ttl: 600
ssl:
  443:
    cert: /etc/certs/server.crt
    key: /etc/certs/server.key
cache:
  path: /tmp/ln-cache
  max_size: 500000000
gzip:
  enabled: true
  min_length: 512
  level: 9
access_log: /var/log/lnproxy/access.log
error_log: /var/log/lnproxy/error.log
log_format: json
admin:
  port: 9999
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_text)
            path = f.name

        try:
            config = load_config(path)
            assert config.workers == 4
            assert config.worker_connections == 2048
            assert config.http == [80, 8080]
            assert config.https == [443]

            # Upstreams
            assert len(config.upstreams) == 1
            up = config.upstreams[0]
            assert up.name == "app"
            assert up.algorithm == "least_conn"
            assert up.max_failures == 5
            assert len(up.servers) == 3
            assert up.servers[1].backup is True
            assert up.servers[2].down is True

            # Locations
            assert len(config.locations) == 2
            assert config.locations[0].rate_limit == 100
            assert config.locations[1].root == "/var/www/static"
            assert config.locations[1].cache_ttl == 600

            # SSL
            assert 443 in config.ssl_blocks
            assert config.ssl_blocks[443].cert_path == "/etc/certs/server.crt"

            # Cache
            assert config.cache_enabled is True
            assert config.cache_path == "/tmp/ln-cache"
            assert config.cache_max_size == 500000000

            # Gzip
            assert config.gzip_min_length == 512
            assert config.gzip_level == 9

            # Logging
            assert config.access_log == "/var/log/lnproxy/access.log"
            assert config.log_format == "json"

            # Admin
            assert config.admin_port == 9999
        finally:
            os.unlink(path)

    def test_missing_file(self):
        """Loading a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/proxy.yaml")

    def test_empty_servers_string_format(self):
        """Servers can be specified as 'host:port' strings."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("upstreams:\n")
            f.write("  - name: str-servers\n")
            f.write("    servers:\n")
            f.write("      - 10.0.0.1:8080\n")
            f.write("      - 10.0.0.2:8080\n")
            f.write("locations:\n")
            f.write("  - path: /\n")
            f.write("    upstream: str-servers\n")
            path = f.name

        try:
            config = load_config(path)
            up = config.upstreams[0]
            assert up.servers[0].host == "10.0.0.1"
            assert up.servers[0].port == 8080
        finally:
            os.unlink(path)


class TestLoadBalancer:
    def test_round_robin(self):
        from proxy.load_balancer import RoundRobinBalancer, UpstreamServer

        lb = RoundRobinBalancer()
        servers = [
            UpstreamServer(host="a", port=80),
            UpstreamServer(host="b", port=80),
            UpstreamServer(host="c", port=80),
        ]
        results = [lb.select(servers) for _ in range(6)]
        hosts = [s.host for s in results]
        assert hosts[:3] == ["a", "b", "c"]

    def test_least_conn(self):
        from proxy.load_balancer import LeastConnBalancer, UpstreamServer

        lb = LeastConnBalancer()
        servers = [
            UpstreamServer(host="a", port=80, weight=1),
            UpstreamServer(host="b", port=80, weight=1),
        ]
        lb.increment(servers[0])
        result = lb.select(servers)
        assert result.host == "b"

    def test_ip_hash(self):
        from proxy.load_balancer import IpHashBalancer, UpstreamServer

        lb = IpHashBalancer()
        servers = [
            UpstreamServer(host="a", port=80),
            UpstreamServer(host="b", port=80),
        ]
        r1 = lb.select(servers, client_ip="192.168.1.1")
        r2 = lb.select(servers, client_ip="192.168.1.1")
        assert r1.host == r2.host  # Same IP → same server

    def test_random(self):
        from proxy.load_balancer import RandomBalancer, UpstreamServer

        lb = RandomBalancer()
        servers = [UpstreamServer(host="a", port=80)]
        result = lb.select(servers)
        assert result.host == "a"

    def test_unknown_algorithm_raises(self):
        from proxy.load_balancer import create_balancer

        with pytest.raises(ValueError):
            create_balancer("nonexistent")


class TestHealthCheck:
    def test_consecutive_failures_mark_unavailable(self):
        from proxy.health_check import HealthMonitor

        monitor = HealthMonitor(max_failures=3, fail_timeout=60)
        assert monitor.is_available("10.0.0.1", 80)

        monitor.record_failure("10.0.0.1", 80)
        assert monitor.is_available("10.0.0.1", 80)

        monitor.record_failure("10.0.0.1", 80)
        assert monitor.is_available("10.0.0.1", 80)

        monitor.record_failure("10.0.0.1", 80)
        assert not monitor.is_available("10.0.0.1", 80)

    def test_success_resets(self):
        from proxy.health_check import HealthMonitor

        monitor = HealthMonitor(max_failures=3)
        monitor.record_failure("10.0.0.1", 80)
        monitor.record_failure("10.0.0.1", 80)
        monitor.record_success("10.0.0.1", 80)
        assert monitor.is_available("10.0.0.1", 80)
